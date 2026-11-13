from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.security import gate

from .base import ToolError


def _entry(app: str, launch: str) -> Any:
    from core.brain.appindex import get_index
    from core.system.abilities import code as vscode

    for e in get_index().entries():
        if gate.norm(e.launch) == gate.norm(launch):
            return e
    found = vscode._launcher()
    if found is not None and gate.norm(found[0]) == gate.norm(launch):
        return SimpleNamespace(kind="exe", name="Visual Studio Code", launch=found[0])
    raise ToolError(f"{app or launch} is not in my app index any more", "Say the app's name again.")


def _argv(launch: str, opened: str) -> list[str] | None:
    if not opened:
        return None
    from core.system.abilities import code as vscode

    argv = vscode.checked_argv(opened)
    if gate.norm(argv[0]) != gate.norm(launch):
        raise ToolError("VS Code's launcher moved since the card was raised", "Ask me again and read the new card.")
    return argv


def resolve_app(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app") or "").strip()
    launch = str(args.get("launch") or "").strip()
    opened = str(args.get("open") or "").strip()
    if not launch:
        raise ToolError("no app came through", "Say the app's name.")
    entry = _entry(app, launch)
    _argv(launch, opened)
    verdict = gate.app_verdict(entry)
    if not verdict["files"]:
        raise ToolError(f"I could not check {entry.name}: {verdict['why'] or 'there is nothing to check'}",
                        "Open it yourself.")
    card = {"app": str(entry.name), "launch": launch, "open": opened, "program": verdict["target"],
            "arguments": verdict["arguments"], "workdir": verdict["workdir"], "files": verdict["files"],
            "set_sha256": gate.app_set_sha(verdict), "why": verdict["why"] or "it is not one I open without a card"}
    if verdict.get("signature"):
        card["signature"] = dict(verdict["signature"])
    if verdict.get("publisher"):
        card["publisher"] = str(verdict["publisher"])
        card["approving"] = (f"Approving also trusts {verdict['publisher']}: apps it signs in your own folders open "
                             f"with no card from then on, after updates too.")
    return card


def describe_app(args: dict[str, Any]) -> str:
    line = f"open {args.get('app') or 'the app'}: it starts {args.get('program') or 'a program'}"
    if args.get("publisher"):
        line += f", signed by {args['publisher']}"
    if args.get("arguments"):
        line += f" with {args['arguments']}"
    if args.get("open"):
        line += f", opening {args['open']}"
    n = len(list(args.get("files") or []))
    line += f". The card lists {n} file{'s' if n != 1 else ''} with their sha256, because {args.get('why')}"
    return line


def launch_app(app: str = "", launch: str = "", open: str = "", program: str = "", arguments: str = "",
               workdir: str = "", files: Any = (), set_sha256: str = "", why: str = "", signature: Any = None,
               publisher: str = "", approving: str = "", *,
               _approved_by_surface: bool = False, _request_id: str = "") -> dict[str, Any]:
    if not _approved_by_surface:
        raise ToolError("an app outside the program folders opens only from the card", "Approve it on the card.")
    entry = _entry(app, launch)
    argv = _argv(launch, open)
    verdict = gate.app_verdict(entry)
    if not set_sha256 or gate.app_set_sha(verdict) != set_sha256:
        raise ToolError(f"{app} changed after you approved the card, so I did not open it",
                        "Ask me again and read the new card.")
    with gate.app_pass(set_sha256):
        gate.start(gate.Start(kind=gate.APP, why="opened from the card", entry=entry, argv=argv))
    gate.store().approve(set_sha256, gate.APP, launch, [{"rel": r["rel"], "sha256": r["sha256"]} for r in verdict["files"]],
                         [str(verdict["target"]), str(verdict["arguments"]), str(verdict["workdir"])],
                         _request_id or "card")
    signed = (verdict.get("signature") or {}).get("status") == gate.SIGNED_OK
    if publisher and signed and verdict.get("publisher") == publisher:
        gate.approve_publisher(str(verdict.get("subject") or ""), _request_id or "card")
    return {"name": app, "program": verdict["target"]}


PASTE = frozenset({"ctrl+v", "control+v", "shift+insert"})


def _typing_what(args: dict[str, Any]) -> dict[str, Any]:
    from core.system.abilities import input_control
    from core.system.winapi import inputs

    op = str(args.get("op") or "")
    if op == "type":
        text = str(args.get("text") or "")
        if not text:
            raise ToolError("there is nothing to type", "Say what to type.")
        if len(text) > inputs.MAX_TEXT:
            raise ToolError(f"that is {len(text)} characters; I type at most {inputs.MAX_TEXT} at once",
                            "Give me something shorter.")
        return {"op": "type", "text": text}
    if op == "key":
        parts = input_control.key_parts(str(args.get("keys") or ""))
        reason = inputs.check_combo(parts) if parts else "no key came through"
        if reason:
            raise ToolError(reason, "Tell me what you want to happen and I will find another way.")
        return {"op": "key", "keys": "+".join(k.strip().lower() for k in parts)}
    raise ToolError(f"I do not know the keyboard act {op!r}", "Nothing was typed.")


def _card_target(t: dict[str, Any]) -> dict[str, Any]:
    return {"window": str(t.get("title") or ""), "process": str(t.get("process") or ""),
            "program": str(t.get("process_path") or ""), "pid": int(t.get("pid") or 0), "hwnd": int(t.get("hwnd") or 0),
            "window_class": str(t.get("class") or ""), "focus": str(t.get("focus_class") or ""),
            "focus_hwnd": int(t.get("focus_hwnd") or 0)}


def resolve_typing(args: dict[str, Any]) -> dict[str, Any]:
    from core.security import allow
    from core.system.winapi import inputs

    out = _typing_what(args)
    t = inputs.target_now()
    if t["refuse"]:
        raise ToolError(t["refuse"], "Put the cursor where you want it and ask me again.")
    if not t.get("hwnd"):
        raise ToolError("no window has the keyboard", "Click where you want it and ask me again.")
    out.update(_card_target(t))
    out["why"] = ("you asked for this card" if allow.typing_allowed(t)
                  else "it is not on the short list of places I type into without asking")
    if out["op"] == "key" and out["keys"] in PASTE:
        out["pastes"] = "This pastes whatever is on the clipboard."
    return out


def describe_typing(args: dict[str, Any]) -> str:
    if args.get("op") == "type":
        n = len(str(args.get("text") or ""))
        what = f"type {n} character{'s' if n != 1 else ''}"
    else:
        what = f"press {args.get('keys')}"
    return (f"{what} into {args.get('window') or 'a window with no title'} ({args.get('process') or 'unknown'}, window "
            f"class {args.get('window_class') or 'none'}, focused element {args.get('focus') or 'none'})")


def _front_trace(front: Any) -> str:
    from core.system.winapi import inputs

    if front == "already":
        return "the window was already in front"
    last = inputs.FRONT_LAST
    ms = float(last.get("seconds") or 0.0) * 1000.0
    if front == inputs.FRONT_DIRECT:
        return f"the window came to the front on a direct request in {ms:.0f} ms"
    was = "accepted" if last.get("direct_accepted") else "refused"
    return (f"the window came to the front through the front window's input in {ms:.0f} ms, after Windows {was} the "
            f"direct request and it did not come forward in {inputs.FRONT_WAIT_S:.0f} s")


def type_checked(op: str = "", text: str = "", keys: str = "", window: str = "", process: str = "", program: str = "",
                 pid: int = 0, hwnd: int = 0, window_class: str = "", focus: str = "", focus_hwnd: int = 0,
                 why: str = "", pastes: str = "", *, _approved_by_surface: bool = False) -> dict[str, Any]:
    from core.security import allow
    from core.system.abilities import input_control
    from core.system.winapi import inputs
    from core.tools.base import ToolRedirect

    if not _approved_by_surface:
        raise ToolError("typing outside the short list happens only from the card", "Approve it on the card.")
    what = _typing_what({"op": op, "text": text, "keys": keys})
    card = {"hwnd": hwnd, "pid": pid, "class": window_class, "focus_hwnd": focus_hwnd, "focus_class": focus,
            "process_path": program}
    now = inputs.target_now()
    front = "already"
    if not allow.same_target(now, card) and int(now.get("hwnd") or 0) != int(hwnd or 0):
        front = inputs.bring_to_front(int(hwnd or 0), int(pid or 0))
        if not front:
            raise ToolError(f"I could not bring {window or process or 'the window on the card'} back to the front, so I "
                            f"typed nothing", "Click into it and ask me again.")
        now = inputs.wait_target(lambda t: allow.same_target(t, card))
    if now["refuse"]:
        raise ToolError(now["refuse"], "Nothing was typed.")
    if not allow.same_target(now, card):
        raise ToolRedirect("input.type_checked", what,
                           f"the keyboard is now in {now.get('focus_class') or 'another part'} of "
                           f"{now.get('title') or 'another window'}, not where the card said")
    try:
        if op == "type":
            got = inputs.send_text(text, expect=now)
            said = f"Typed {got.get('chars')} characters into {window or 'the window'}."
        else:
            got = inputs.send_keys(input_control.key_parts(keys), expect=now)
            said = f"Pressed {got.get('keys')} in {window or 'the window'}."
    except inputs.InputRefused as exc:
        raise ToolError(str(exc), "Ask me again and read the new card.") from None
    return {**got, "said": said, "front": front, "trace": _front_trace(front)}
