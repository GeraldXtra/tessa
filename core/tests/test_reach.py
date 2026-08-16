"""
core/tests/test_reach.py — full reach, the greeting, and the near-miss boundary.

Four real faults from his own use, and the invariants that must hold after
fixing them:

  1. "Open my Chrome browser" offered Opera, Samsung and Epic. The diagnosis he
     was given (only the per-user Start Menu is indexed) was WRONG — both roots
     were indexed and Chrome was in there. The fault was the word "browser"
     surviving into the query, where a fuzzy ratio beat a whole-word match.
  2. Whisper fragments "stop listening".
  3. "Stop List Me." reached `fs.list` with path="Me" and she answered "Me is
     not there" — a mistranscription turned into an action.
  4. "Stop listening." answered correctly. That one must not regress.

    python core/tests/test_reach.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.brain.appindex import _identity, _norm_query, get_index  # noqa: E402
from core.brain.executor import Executor  # noqa: E402
from core.brain.intents import _explicit_path  # noqa: E402
from core.brain.phrasings import _is_nameable  # noqa: E402
from core.brain.router import RETURN_GAP_S, Intent, Router  # noqa: E402
from core.brain.tools_local import ToolCall  # noqa: E402
from core.security.audit import AuditLog  # noqa: E402
from core.voice.loop import TurnTiming  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  <- {detail}" if detail else ""))


def route(utt: str) -> tuple[str, list, str]:
    out = Router().route(utt)
    return out.intent.value, [(c.name, c.args) for c in out.calls], out.speech


print("full reach, the greeting, and the near-miss boundary")

# ── 1. THE CHROME FAULT ──────────────────────────────────────────────────────
print("\n1. the category noun, which is what actually broke Chrome")
check("'browser' is stripped from the query",
      _norm_query("open my chrome browser") == "chrome",
      _norm_query("open my chrome browser"))
check("...and so are 'app' and 'application'",
      _norm_query("open the spotify app") == "spotify",
      _norm_query("open the spotify app"))
check("a bare category word SURVIVES when it is the whole request",
      _norm_query("open my browser") == "browser",
      _norm_query("open my browser"))

for utt in ("Open my Chrome browser", "open chrome", "open google chrome",
            "Open my chrome browser please"):
    intent, calls, speech = route(utt)
    got = calls[0][1].get("app") if calls else None
    check(f"{utt!r} -> Chrome",
          bool(calls) and calls[0][0] == "app.open" and "chrome" in str(got),
          f"{intent} {calls} {speech[:60]}")

_, calls, speech = route("Open my Chrome browser")
check("...and NOT Opera, Samsung or Epic",
      not any(w in speech.lower() for w in ("opera", "samsung", "epic")), speech[:70])

# ── 2. THE INDEX ─────────────────────────────────────────────────────────────
print("\n2. the index reaches beyond the Start Menu")
idx = get_index()
entries = idx.entries
sources = {e.source for e in entries}
check("both Start Menu roots are indexed",
      {"start-menu-machine", "start-menu-user"} <= sources, str(sorted(sources)))
check("App Paths is indexed (installs with no shortcut)",
      "app-paths" in sources, str(sorted(sources)))
check("shell built-ins are indexed (Settings, Task Manager)",
      "shell-builtin" in sources)
check("the index is not trivially small", len(entries) > 200, str(len(entries)))
check("Chrome is present", any(e.key == "chrome" or e.key == "google chrome"
                               for e in entries))

# Item 1d — a dead shortcut must never be OFFERED.
dead = [e for e in entries if not e.alive]
check("dead shortcuts are marked, not silently kept",
      all(e.target for e in dead), f"{len(dead)} dead")
for e in dead:
    hits, _how = idx._rank(e.key)
    check(f"dead entry {e.name!r} is never returned",
          all(h.target != e.target or h.alive for h in hits), str(hits[:1]))

check("identity collapses a .lnk and its App Paths twin",
      _identity(type(entries[0])(key="k", name="n", launch="x.lnk", kind="lnk",
                                 source="s", target=r"C:\a\notepad.exe"))
      == "notepad.exe")

# ── 3. FILES, FOLDERS AND DRIVES ─────────────────────────────────────────────
print("\n3. paths, folders and drives — not only applications")
check("an absolute path is recognised",
      _explicit_path(r"open C:\dev\zoey") == Path(r"C:\dev\zoey"))
check("a bare drive is recognised",
      _explicit_path("open the D drive") == Path("D:\\"))
check("ordinary words are NOT a path", _explicit_path("open my downloads") is None)

intent, calls, speech = route(r"open C:\dev\zoey")
check("an existing path opens as itself",
      calls and calls[0][0] == "app.open_folder", str(calls))
intent, calls, speech = route(r"open C:\nope\missing")
check("a NONEXISTENT path is an honest miss, not silence",
      not calls and "nothing at" in speech.lower(), f"{calls} {speech[:60]}")
check("...and it does NOT fall through to a fuzzy app match",
      not calls, str(calls))

intent, calls, _ = route("how much disk space do I have")
check("a disk QUESTION still reaches sys.disk",
      calls and calls[0][0] == "sys.disk", str(calls))

# ── 4. THE NEAR-MISS BOUNDARY ────────────────────────────────────────────────
print("\n4. a garbled phrase must not become an action")
check("'Me' cannot name a thing", not _is_nameable("Me"))
check("'that' cannot name a thing", not _is_nameable("that"))
check("'downloads' CAN name a thing", _is_nameable("downloads"))
check("a path shape always can", _is_nameable(r"C:\dev"))
check("'my invoices folder' can — he may name a folder that is missing",
      _is_nameable("invoices"))

intent, calls, speech = route("Stop List Me.")
check("'Stop List Me.' fires NO tool", not calls, str(calls))
check("...and never fs.list with path='Me'",
      not any(c[0] == "fs.list" and c[1].get("path") == "Me" for c in calls))

for utt in ("read me that", "list it", "open this", "open it", "show me that"):
    _i, calls, _s = route(utt)
    check(f"{utt!r} fires no tool", not calls, str(calls))

print("\n   ...without breaking the commands that work")
for utt, want in (("what is in my downloads", "fs.list"),
                  (r"list C:\dev", "fs.list"),
                  ("find a file called invoice", "fs.search"),
                  ("read my clipboard", "clip.read"),
                  ("open my downloads", "app.open_folder"),
                  ("open chrome", "app.open")):
    _i, calls, _s = route(utt)
    check(f"{utt!r} still reaches {want}",
          bool(calls) and calls[0][0] == want, str(calls))

check("a SEARCH TERM is still allowed to name nothing",
      route("find a file called zzzznotreal")[1][0][0] == "fs.search")

# ── 5. WORD BOUNDARIES ───────────────────────────────────────────────────────
print("\n5. intent keywords are words, not substrings")
check("'this' does not match PRESENCE's 'hi'",
      route("open this")[0] != "presence", route("open this")[0])
check("'hi' still matches PRESENCE", route("hi")[0] == "presence")

# ── 6. THE GREETING ──────────────────────────────────────────────────────────
print("\n6. 'Hey Zoey' alone is a greeting")
for utt in ("Hey Zoey", "Hi Zoey", "Hello Zoey", "Zoey", "hello", "hi"):
    intent, calls, speech = route(utt)
    check(f"{utt!r} greets", intent == "presence" and bool(speech),
          f"{intent} {speech[:40]}")

TIMES = [(datetime(2026, 8, 16, 8, 30), ("morning",)),
         (datetime(2026, 8, 16, 13, 15), ("afternoon",)),
         (datetime(2026, 8, 16, 19, 40), ("evening",)),
         (datetime(2026, 8, 16, 3, 20), ("am", "late"))]
for when, words in TIMES:
    said = Router().address_only(when).speech.lower()
    check(f"{when:%H:%M} greets appropriately",
          any(w in said for w in words), said)

print("\n   a command after the phrase gets NO greeting")
for utt in ("Hey Zoey, open my downloads", "Hey Zoey, open chrome"):
    _i, calls, speech = route(utt)
    check(f"{utt!r} acts", bool(calls), str(calls))
    check("...with no greeting in front",
          not any(w in speech.lower() for w in ("morning", "afternoon", "evening")),
          speech[:50])

print(f"\n   the return gap is {RETURN_GAP_S / 60:.0f} minutes")
r = Router()
base = datetime(2026, 8, 16, 19, 0)
first = r.address_only(base).speech
check("first contact greets in full",
      any(w in first for w in ("Good morning", "Good afternoon", "Good evening",
                               "It is")), first)
inside = r.address_only(base + timedelta(minutes=5)).speech
check("inside the gap she ACKNOWLEDGES, never repeats",
      inside != first and not any(w in inside for w in ("Good ",)), inside)
after = r.address_only(base + timedelta(minutes=45)).speech
check("after the gap she greets again", "Good " in after or "Emperor." in after, after)
check("...and it is not the acknowledgement", after != inside, after)

seen = {Router().route("stop listening").speech for _ in range(40)}
check("every sleep phrasing still names the way back",
      all("chord" in s.lower() or "push-to-talk" in s.lower() for s in seen),
      str(sorted(seen)))

# ── 7. evt.agent.state.detail ────────────────────────────────────────────────
print("\n7. evt.agent.state.detail — redacted before broadcast")
d = Executor.state_detail(ToolCall("fs.delete", {"path": r"C:\Users\x\old"}))
check("carries the tool", d.get("tool") == "fs.delete", str(d))
check("carries the target", "old" in d.get("target", ""), str(d))

secret = "sk-ant-api03-" + "A" * 40
d2 = Executor.state_detail(ToolCall("shell.execute", {"command": f"curl -H 'x: {secret}'"}))
check("A SECRET NEVER REACHES THE WIRE", secret not in d2.get("target", ""), str(d2))
check("...and it is visibly redacted", "REDACTED" in d2.get("target", ""), str(d2))

d3 = Executor.state_detail(ToolCall("fs.read", {"path": "C:/" + "y" * 400}))
check("target is truncated", len(d3.get("target", "")) <= 130, str(len(d3.get("target", ""))))
check("a tool with no target omits the field entirely",
      "target" not in Executor.state_detail(ToolCall("sys.battery", {})))

# ── 8. evt.turn.timing ───────────────────────────────────────────────────────
print("\n8. evt.turn.timing — a CLOSED vocabulary, no transcript content")
t = TurnTiming(stt_s=1.2, route_s=0.003, tool_s=0.4, tts_s=0.3, playback_start_s=0.05)
names = [s["name"] for s in t.stages()]
check("only the five allowed names appear",
      set(names) <= {"stt", "route", "tool", "tts", "playback"}, str(names))
check("every stage has a numeric ms",
      all(isinstance(s["ms"], (int, float)) for s in t.stages()))
check("no stage name carries free text",
      all(" " not in s["name"] and "'" not in s["name"] for s in t.stages()))
empty = TurnTiming(stt_s=1.0, route_s=0.002)
check("zero-length stages are omitted, not sent as 0",
      {s["name"] for s in empty.stages()} == {"stt", "route"},
      str(empty.stages()))
check("tool time is no longer folded into tts",
      TurnTiming(tool_s=0.5).stages()[0]["name"] == "tool")

# ── 9. chainVerified ─────────────────────────────────────────────────────────
print("\n9. chainVerified — incremental, latching, and no false alarms")
tmp = Path(tempfile.mkdtemp()) / "audit.jsonl"
log = AuditLog(tmp)
for i in range(60):
    log.append(actor="system", tool="t", tier="green", summary=f"e{i}")

ok1, _ = log.verify_incremental()
ok2, _ = log.verify_incremental()
check("a good chain verifies", ok1)
check("...and STAYS good when called again (the tell() bug)", ok2)
log.append(actor="system", tool="t", tier="green", summary="more")
ok3, _ = log.verify_incremental()
check("...and after new entries", ok3)

t0 = time.perf_counter()
for _ in range(20):
    log.verify_incremental()
steady = (time.perf_counter() - t0) / 20 * 1000
t0 = time.perf_counter()
log.verify()
full = (time.perf_counter() - t0) * 1000
check("the steady-state check is cheaper than a full walk",
      steady < full, f"incremental {steady:.2f} ms vs full {full:.2f} ms")

lines = tmp.read_text(encoding="utf-8").splitlines()
lines[-1] = lines[-1].replace('"more"', '"TAMPERED"')
tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
bad = AuditLog(tmp)
okb, why = bad.verify_incremental()
check("tampering is caught", not okb, str(why))
check("...and latches rather than healing", not bad.verify_incremental()[0])

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
