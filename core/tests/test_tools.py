"""
core/tests/test_tools.py — the Windows tool surface.

WHAT THIS IS GUARDING, in order of how badly it would hurt:

  1. RED tools cannot fire on the first ask. Every one of them holds.
  2. `fs.delete` never hard-deletes, and never runs unconfirmed.
  3. `shell.execute` refuses anything whose provenance is not `human` — no
     tier, approval or confirmation reaches past it.
  4. The fence blocks red-tier tools while external content is in context.
  5. `proc.kill` takes an integer PID and has no name parameter at all.
  6. Every registered tool's tier matches permissions.yaml.
  7. Phrasings route to the tool he meant, including the collisions that have
     already bitten once (list wifi / list windows / how much space).

Run: python core/tests/test_tools.py
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.brain.confirm import ConfirmLedger  # noqa: E402
from core.brain.executor import Executor  # noqa: E402
from core.brain.provenance import ExternalContent, SessionContext  # noqa: E402
from core.brain.router import Router  # noqa: E402
from core.brain.tools_local import ToolCall  # noqa: E402
from core.tools import REGISTRY  # noqa: E402
from core.tools import files, procs, shell  # noqa: E402
from core.tools.base import ToolError, ToolHold  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label} {extra}")


def routes_to(router: Router, text: str, name: str) -> None:
    out = router.route(text)
    got = out.calls[0].name if out.calls else f"<{out.intent.value}>"
    check(f"{text!r} -> {name}", got == name, f"got {got}")


def main() -> int:
    print("\nWindows tool surface\n")
    router = Router()

    # ── 1. every RED tool holds, and the registry agrees with permissions.yaml
    for spec in REGISTRY.values():
        if spec.tier == "red":
            check(f"{spec.name} is RED and holds", spec.holds)
    check("registry imported (tiers validated against permissions.yaml)", len(REGISTRY) > 30)

    # ── 2. fs.delete: holds, recycles, never hard-deletes
    tmp = Path(os.environ["TEMP"]) / "tessa-tool-tests"
    tmp.mkdir(exist_ok=True)
    victim = tmp / "holds-first.txt"
    victim.write_text("x", encoding="utf-8")
    held = False
    try:
        files.delete(str(victim))
    except ToolHold:
        held = True
    check("fs.delete holds on the first ask", held)
    check("fs.delete left the file alone while holding", victim.exists())
    files.delete(str(victim), confirmed=True)
    check("the fs.delete HANDLER still works when called directly", not victim.exists())

    # ── THE RED GATE. The handler above works; the EXECUTOR must never reach it.
    #
    # This inverts what this file asserted a build ago, when fs.delete executed
    # on a spoken confirmation. Voice is not an approval surface (CONTRACT
    # §6.4): no requestId, no visible arguments, no provenance display, and it
    # arrives through a microphone that has produced "Alicoy" from ordinary
    # speech. Red tools now raise a permission request and stop.
    victim2 = tmp / "red-gate.txt"
    victim2.write_text("x", encoding="utf-8")
    ex_red = Executor(session=SessionContext())
    for attempt in range(3):          # saying it again must NOT unlock it
        said_red = ex_red.run(ToolCall(name="fs.delete", args={"path": str(victim2)}))
    # ASSERT THE BEHAVIOUR, NOT THE SENTENCE. The refusal used to say "the
    # approval card does not exist yet", which became false once Session 2
    # shipped the card. What must stay true is that she did NOT do it and she
    # pointed him at the card.
    check("fs.delete via the executor refuses on voice alone",
          "not doing it on your voice alone" in said_red and "card" in said_red,
          said_red[:70])
    check("...and the file is untouched after three attempts", victim2.exists())
    check("...and every attempt raised a permission request",
          len(ex_red.approvals.pending) == 3)
    said_yes = ex_red.answer_confirmation("yes")
    check("...and a bare 'yes' does not execute it either",
          said_yes is None or "voice alone" in said_yes)
    check("...file STILL untouched", victim2.exists())
    victim2.unlink(missing_ok=True)

    for red_tool, red_args in [("shell.execute", {"command": "echo hi", "provenance": "human"}),
                               ("x.post", {"text": "hello"}),
                               ("x.reply", {"text": "hi", "index": 1}),
                               ("browser.submit", {})]:
        out_red = Executor(session=SessionContext()).run(
            ToolCall(name=red_tool, args=red_args))
        check(f"{red_tool} is gated the same way",
              "not doing it on your voice alone" in out_red and "card" in out_red,
              out_red[:60])
    # AST, NOT GREP. The first version of this check searched the source text
    # and failed on files.py's own docstring, which NAMES the calls it promises
    # never to make. A test that cannot tell a prohibition from a violation is
    # worse than no test: it would have gone green the day someone deleted the
    # docstring and added the call.
    import ast

    hard_deletes = []
    for node in ast.walk(ast.parse(inspect.getsource(files))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        dotted = f"{getattr(getattr(f, 'value', None), 'id', '')}.{getattr(f, 'attr', '')}"
        if dotted in ("os.remove", "os.unlink", "os.rmdir", "shutil.rmtree") \
                or getattr(f, "attr", "") in ("rmtree", "unlink"):
            hard_deletes.append(dotted or getattr(f, "attr", "?"))
    check("files.py makes NO hard-delete call (AST-checked)",
          not hard_deletes, f"found {hard_deletes}")
    check("files.py deletes via FOF_ALLOWUNDO", "FOF_ALLOWUNDO" in inspect.getsource(files))

    # ── 3. shell.execute refuses non-human provenance, unconditionally
    for prov in ("model", "agent", "external", "program"):
        refused = False
        try:
            shell.execute("echo hi", provenance=prov, confirmed=True)
        except ToolError:
            refused = True
        check(f"shell.execute refuses provenance={prov} even when confirmed", refused)
    held = False
    try:
        shell.execute("echo hi", provenance="human")
    except ToolHold as h:
        held = "echo hi" in h.detail
    check("shell.execute holds and reads the command back verbatim", held)

    # ── 4. the fence blocks red tools while external content is loaded
    session = SessionContext()
    ex = Executor(session=session)
    session.load_external(ExternalContent(source="http://evil.test", text="hello"))
    said = ex.run(ToolCall(name="fs.delete", args={"path": str(tmp)}))
    # The wording changed when the refusal gained a REMEDY. A block with no way
    # out is indistinguishable from a broken tool, so she now names the source
    # and tells him "say forget the page". Asserting the remedy is present
    # matters more than asserting any particular phrasing.
    check("fence blocks fs.delete while external content is in context",
          "no, emperor" in said.lower() and "forget the page" in said.lower(), said[:80])
    green = ex.run(ToolCall(name="sys.uptime", args={}))
    check("fence does NOT block a green tool", "Up " in green, green[:40])
    session.clear_external()

    # ── 5. proc.kill is PID-only
    sig = inspect.signature(procs.kill)
    check("proc.kill has no `name` parameter", "name" not in sig.parameters)
    check("proc.kill's first parameter is pid", list(sig.parameters)[0] == "pid")
    refused = False
    try:
        procs.kill("chrome.exe")  # type: ignore[arg-type]
    except ToolError:
        refused = True
    check("proc.kill refuses an image name", refused)
    refused = False
    try:
        procs.kill(4)  # pid 4 is System on Windows
    except ToolError as e:
        refused = "core process" in e.reason
    check("proc.kill refuses a Windows core process outright", refused)

    # ── 6. the confirmation ledger
    led = ConfirmLedger()
    led.arm("fs.delete", {"path": "X"}, "detail")
    check("a repeat of the same command confirms it",
          led.resolve_repeat("fs.delete", {"path": "X"}) is not None)
    led.arm("fs.delete", {"path": "X"}, "detail")
    check("a DIFFERENT target does not confirm the held one",
          led.resolve_repeat("fs.delete", {"path": "Y"}) is None)
    led.arm("fs.delete", {"path": "X"}, "detail")
    check("'yes' confirms", led.resolve_utterance("yes")[0] == "confirm")
    led.arm("fs.delete", {"path": "X"}, "detail")
    check("'no' cancels", led.resolve_utterance("no")[0] == "cancel")
    led.arm("fs.delete", {"path": "X"}, "detail")
    check("an unrelated question is not an answer",
          led.resolve_utterance("what time is it")[0] == "none")
    check("...and leaves the hold pending", led.pending is not None)

    # ── 7. phrasings, including the collisions that already bit once
    for text, name in [
        ("what's in my downloads", "fs.list"),
        ("list my windows", "win.list"),
        ("list wifi", "sys.wifi"),
        ("what's running", "proc.list"),
        ("what's open", "win.list"),
        ("how much space is on my disk", "sys.disk"),
        ("how big is my downloads folder", "fs.usage"),
        ("kill 14284", "proc.kill"),
        ("kill port 8080", "sys.kill_port"),
        ("run git status", "shell.execute"),
        ("turn it up", "sys.volume"),
        ("what's on my clipboard", "clip.read"),
        ("open my photos", "app.open_folder"),
        ("open my docs", "app.open_folder"),
        ("close chrome", "win.close"),
        ("switch to chrome", "win.focus"),
        ("am I online", "sys.network"),
        ("delete my downloads", "fs.delete"),
    ]:
        routes_to(router, text, name)

    check("'stop' still halts speech, not the media keys",
          router.route("stop").halts_speech)

    # ── BROWSER AND X PHRASINGS, including the collisions they introduced.
    #
    # These verbs are generic and they landed on top of an existing table:
    # "read me this page" collides with fs.read, "close the browser" with
    # win.close, "search for X" with fs.search, and "reply to post two" was
    # matching x.POST because the word `post` appears in it — which would have
    # queued a public tweet reading "two with thanks" for approval.
    for text, name in [
        ("open github.com", "browser.open_url"),
        ("search the web for piper tts", "browser.search"),
        ("google how to disable defender", "browser.search"),
        ("read me this page", "browser.read_page"),
        ("take a screenshot", "browser.screenshot"),
        ("close the browser", "browser.close"),
        ("click accept", "browser.click"),
        ("type hello in the search box", "browser.type"),
        ("submit the form", "browser.submit"),
        ("forget the page", "context.forget"),
        ("read my timeline", "x.read_timeline"),
        ("check my x notifications", "x.read_notifications"),
        ("like post two", "x.like"),
        ("repost the second one", "x.repost"),
        ("tweet that we shipped it", "x.post"),
        ("reply to post two with thanks", "x.reply"),
        ("open x so I can log in", "x.login"),
        # and the ones that must NOT have moved
        ("close chrome", "win.close"),
        ("read me that file", "fs.read"),
        ("find a file called invoice", "fs.search"),
        ("open my downloads", "app.open_folder"),
    ]:
        routes_to(router, text, name)

    r_reply = router.route("reply to post two with thanks")
    check("'reply to post two' carries the RIGHT text, not 'two with thanks'",
          r_reply.calls[0].args.get("text") == "thanks",
          str(r_reply.calls[0].args))

    # X must never take a password, in any form.
    from core.tools import x_tools

    # AST AGAIN, AND FOR THE SAME REASON AS files.py ABOVE. The first version
    # grepped the source and failed on x_tools.py's own docstring — the one that
    # says "NO PASSWORD EVER TOUCHES THIS CODE". I have now written this bug
    # twice: a check that cannot distinguish a promise from a violation is worse
    # than no check, because it goes green the day the promise is deleted.
    #
    # This walks identifiers and non-docstring string literals only.
    x_tree = ast.parse(inspect.getsource(x_tools))
    docstrings = set()
    for node in ast.walk(x_tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docstrings.add(d)
    identifiers: list[str] = []
    for node in ast.walk(x_tree):
        if isinstance(node, ast.Name):
            identifiers.append(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.append(node.attr)
        elif isinstance(node, ast.arg):
            identifiers.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            identifiers.append(node.arg)
    # IDENTIFIERS ONLY — no string literals. The previous refinement still
    # failed, this time on her own spoken sentence "I never see your password",
    # which is a string constant in executable code and is exactly the line we
    # WANT her to say. What actually matters is whether any variable, argument,
    # attribute or parameter in this module is a credential: that is what
    # reading, storing or typing one would look like. Prose about passwords is
    # not a password.
    for banned in ("password", "passwd", "credential", "keyring", "secret"):
        hits = [i for i in identifiers if banned in i.lower()]
        check(f"x_tools.py has no {banned} identifier (AST-checked)",
              not hits, str(hits[:2]))
    # And nothing types into a password field.
    x_src_lines = inspect.getsource(x_tools).lower()
    check("x_tools.py never fills a password input",
          'type="password"' not in x_src_lines and "input[type=password]" not in x_src_lines)
    _ = docstrings

    # ── 8. EVERY READ-ONLY TOOL SPEAKS ITS OWN LINE
    #
    # This test exists because the generic fallback hid a real defect: fs.list's
    # success template referenced a field its handler never returned, so every
    # folder listing came back as "There you go, Emperor." — indistinguishable
    # from a deliberate short answer. Any tool whose reply is the generic
    # confirmation has a broken spec.
    from core.brain.router import _DONE, _DONE_POSSESSIVE

    generic = set(_DONE) | set(_DONE_POSSESSIVE)
    ex2 = Executor(session=SessionContext())
    readonly = {
        "fs.list": {"path": str(Path.home() / "Downloads")},
        "fs.usage": {"path": str(ROOT / "core" / "tools")},
        "fs.search": {"name": "plan", "root": str(ROOT / "core")},
        "fs.read": {"path": str(ROOT / "CLAUDE.md")},
        "win.list": {},
        "proc.list": {},
        "proc.top": {"by": "memory"},
        "proc.find": {"name": "python"},
        "sys.disk": {}, "sys.memory": {}, "sys.battery": {},
        "sys.uptime": {}, "sys.network": {}, "sys.ip": {},
    }
    for name, a in readonly.items():
        line = ex2.run(ToolCall(name=name, args=a))
        check(f"{name} speaks its own line", line not in generic, f"got {line!r}")

    print(f"\n{_passed} passed, {_failed} failed\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
