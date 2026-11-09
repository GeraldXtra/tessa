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
    return {"app": str(entry.name), "launch": launch, "open": opened, "program": verdict["target"],
            "arguments": verdict["arguments"], "workdir": verdict["workdir"], "files": verdict["files"],
            "set_sha256": gate.app_set_sha(verdict), "why": verdict["why"] or "it is not one I open without a card"}


def describe_app(args: dict[str, Any]) -> str:
    line = f"open {args.get('app') or 'the app'}: it starts {args.get('program') or 'a program'}"
    if args.get("arguments"):
        line += f" with {args['arguments']}"
    if args.get("open"):
        line += f", opening {args['open']}"
    n = len(list(args.get("files") or []))
    line += f". The card lists {n} file{'s' if n != 1 else ''} with their sha256, because {args.get('why')}"
    return line


def launch_app(app: str = "", launch: str = "", open: str = "", program: str = "", arguments: str = "",
               workdir: str = "", files: Any = (), set_sha256: str = "", why: str = "", *,
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
    return {"name": app, "program": verdict["target"]}
