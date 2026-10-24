from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable

from core.exam import rehearsal as R

SAMPLE: dict[str, Any] = {
    "url": "https://example.invalid/", "query": "rehearsal", "text": "rehearsal", "name": "rehearsal",
    "app": "notepad", "field": "search", "handle": "rehearsal", "window": "focused", "pid": 999999,
    "post_id": "1234567890123", "reply_to_id": "1234567890123", "quoted_id": "1234567890123",
    "posts": ["one", "two"], "command": "echo rehearsal", "id": "VideoLAN.VLC",
    "key": "HKCU\\Control Panel\\Desktop", "value": "400", "side": "left", "direction": "up",
    "action": "playpause", "tool": "node", "port": 9, "seconds": 1, "keys": "enter", "site": "x.com",
    "level": 40, "topic": "rehearsal", "guidance": "", "display": "", "radio": "bluetooth", "state": "off",
    "mode": "manual", "muted": True, "index": 1, "nth": 1, "delay_s": 60, "by": "memory",
}


def _sample_args(env: R.Rehearsal, spec: Any) -> dict[str, Any]:
    import inspect

    cap = getattr(spec.handler, "__wrapped_capability__", None)
    out: dict[str, Any] = {}
    if cap is not None:
        for p in cap.params:
            if p.choices:
                out[p.name] = p.choices[0]
            elif p.name in ("path", "to"):
                out[p.name] = str(env.out_dir / ("selftest-target.txt" if p.name == "path" else "selftest-dest"))
            elif p.name in SAMPLE:
                out[p.name] = SAMPLE[p.name]
            elif getattr(p, "required", False):
                out[p.name] = 1 if p.kind is int else 1.0 if p.kind is float else "rehearsal"
        return out
    try:
        params = inspect.signature(spec.handler).parameters.values()
    except (TypeError, ValueError):
        return out
    for p in params:
        if p.name in ("provenance", "confirmed", "_approved_by_surface", "_request_id"):
            continue
        if p.name in ("path", "to"):
            out[p.name] = str(env.out_dir / ("selftest-target.txt" if p.name == "path" else "selftest-dest"))
        elif p.name in SAMPLE:
            out[p.name] = SAMPLE[p.name]
        elif p.default is inspect.Parameter.empty:
            out[p.name] = "rehearsal"
    return out


def _probes(env: R.Rehearsal) -> list[tuple[str, Callable[[], Any], type]]:
    import psutil
    import winreg
    from core import lifecycle
    from core.brain.llm.local import LocalLLM
    from core.security import runtime as rt
    from core.security.audit import AuditLog
    from core.tools import browser

    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Tessa"
    blocked = sorted(m for m in R.BLOCKED_IMPORTS if m not in __import__("sys").modules)
    probe_mod = blocked[0] if blocked else "piper"
    return [
        ("connect to her loopback port", lambda: socket.create_connection(("127.0.0.1", 9), timeout=1), R.IsolationBreach),
        ("raw socket connect to 127.0.0.1", lambda: socket.socket().connect(("127.0.0.1", 9)), R.IsolationBreach),
        ("resolve x.com", lambda: socket.getaddrinfo("x.com", 443), R.IsolationBreach),
        ("resolve the model host outside a model call", lambda: socket.getaddrinfo("generativelanguage.googleapis.com", 443), R.IsolationBreach),
        ("urllib fetch", lambda: urllib.request.urlopen("http://127.0.0.1:9/"), R.IsolationBreach),
        ("start a process", lambda: subprocess.Popen(["cmd.exe", "/c", "exit", "0"]), R.IsolationBreach),
        ("subprocess.run", lambda: subprocess.run(["cmd.exe", "/c", "exit", "0"]), R.IsolationBreach),
        ("os.system", lambda: os.system("exit 0"), R.IsolationBreach),
        ("os.startfile", lambda: os.startfile(str(env.out_dir)), R.IsolationBreach),
        ("append to her audit log", lambda: open(R.DATA / "audit.log", "ab"), R.IsolationBreach),
        ("write her conversation", lambda: (R.DATA / "memory" / "conversation.json").write_text("x", encoding="utf-8"), R.IsolationBreach),
        ("read her runtime.json", lambda: open(local / "runtime.json", "rb"), R.IsolationBreach),
        ("read her vault", lambda: open(local / "vault.json", "rb"), R.IsolationBreach),
        ("read her live audit", lambda: (R.DATA / "audit.log").read_bytes(), R.IsolationBreach),
        ("create a file in data", lambda: open(R.DATA / "rehearsal-probe.txt", "w"), R.IsolationBreach),
        ("delete outside the exam folder", lambda: os.remove(str(R.DATA / "rehearsal-probe-none.txt")), R.IsolationBreach),
        ("make a folder outside", lambda: os.mkdir(str(R.DATA / "rehearsal-probe-dir")), R.IsolationBreach),
        ("registry write", lambda: winreg.SetValueEx(None, "x", 0, 1, "x"), R.IsolationBreach),
        ("lock the workstation", lambda: ctypes.windll.user32.LockWorkStation(), R.IsolationBreach),
        ("sleep the machine", lambda: ctypes.windll.powrprof.SetSuspendState(False, False, False), R.IsolationBreach),
        ("send a key", lambda: ctypes.windll.user32.keybd_event(0xAD, 0, 0, 0), R.IsolationBreach),
        ("kill a process", lambda: psutil.Process.kill(None), R.IsolationBreach),
        ("open a web page", lambda: webbrowser.open("about:blank"), R.IsolationBreach),
        ("launch her browser", lambda: browser.SESSION.context(), R.IsolationBreach),
        ("take her single-instance guard", lambda: lifecycle.take_guard(env.out_dir, env.out_dir), R.IsolationBreach),
        ("write runtime.json", lambda: rt.write_runtime_file(9, "0" * 64), R.IsolationBreach),
        ("append to a real audit chain", lambda: AuditLog.append(None, actor="system", tool="x", tier="green", summary="x"), R.IsolationBreach),
        (f"import {probe_mod}", lambda: __import__(probe_mod), R.IsolationBreach),
        ("answer on the local model", lambda: list(LocalLLM.stream(None, "", [])), R.QuotaStop),
    ]


def _drive(env: R.Rehearsal, name: str, spec: Any) -> dict[str, Any]:
    from core.brain.approvals import ApprovalError
    from core.brain.tools_local import ToolCall

    R.REC.take()
    parts = env.new_turn_env({"id": f"selftest:{name}"})
    ex = parts["executor"]
    args = _sample_args(env, spec) if spec is not None else {}
    if name == "app.open_folder":
        args = {"path": str(env.out_dir)}
    elif name == "app.open":
        args = {"app": "notepad"}
    elif name == "app.open_vscode":
        args = {"path": ""}
    elif name == "app.open_url":
        args = {"url": "https://example.invalid/"}
    elif name in ("sys.port_owner", "sys.kill_port"):
        args = {"port": 9}
    elif name == "sys.tool_version":
        args = {"tool": "node"}
    said = ""
    route = "run"
    error = None
    try:
        said = ex.run(ToolCall(name=name, args=args, origin="human", confirmed=True))
        pending = list(ex.approvals.pending.values())
        if pending:
            route = "card, then execute_approved"
            try:
                ex.execute_approved(pending[0].request_id)
            except ApprovalError as exc:
                said = f"{said} | approval: {exc}"
    except R.Halt as h:
        error = f"{type(h).__name__}: {h}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    events = R.REC.take()
    hits = [e for e in events if e["kind"] == "trap"]
    own = [e for e in hits if e["tool"] == name or (spec is None)]
    verbs = [str(e.get("summary") or "").split(" ", 1)[0] for e in events if e["kind"] == "audit"]
    breaches = [e for e in events if e["kind"] == "breach"]
    ran = "ran" in verbs
    reached = bool(own)
    if name == "context.forget":
        outcome = "handled inside the executor, no handler call"
    elif reached:
        outcome = f"trap reached via {route}"
    else:
        outcome = "stopped before its trap: " + (", ".join(v for v in verbs if v) or (error or said)[:160])
    return {"tool": name, "route": route, "reached_trap": reached, "trap_parts": sorted({e["part"] for e in hits}),
            "ran": ran, "breaches": len(breaches), "verbs": verbs, "outcome": outcome,
            "said": (said or "")[:300], "error": error,
            "mark_in_answer": R.MARK in (said or "") or R.MARK in (error or "")}


def run(env: R.Rehearsal) -> dict[str, Any]:
    from core.brain import appindex, executor as ex_mod, memory
    from core.system.abilities import service_control, software_change
    from core.tools import REGISTRY, x_tools

    t0 = time.perf_counter()
    before = R.snapshot()
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("environment built and fenced", env.built and env.fenced)
    untrapped = []
    for name, spec in REGISTRY.items():
        cap = getattr(spec.handler, "__wrapped_capability__", None)
        if cap is not None:
            if not isinstance(cap.run, R.Trap):
                untrapped.append(f"{name}: capability.run")
            if cap.describe is not None and name not in R.PURE_DESCRIBE and not isinstance(cap.describe, R.Trap):
                untrapped.append(f"{name}: capability.describe")
        elif not isinstance(spec.handler, R.Trap):
            untrapped.append(f"{name}: handler")
        if spec.resolve is not None and not isinstance(spec.resolve, R.Trap):
            untrapped.append(f"{name}: resolve")
        if spec.describe is not None and not isinstance(spec.describe, R.Trap):
            untrapped.append(f"{name}: describe")
    for attr in ("open_path", "open_url", "open_in_vscode", "listening_on_port", "tool_version"):
        if not isinstance(getattr(ex_mod, attr), R.Trap):
            untrapped.append(f"executor.{attr}")
    if not isinstance(ex_mod.shutil.which, R.Trap) or not isinstance(ex_mod.subprocess.Popen, R.Trap):
        untrapped.append("executor shutil/subprocess")
    for attr in ("record", "he_opened_it_himself"):
        if not isinstance(getattr(memory, attr), R.Trap):
            untrapped.append(f"memory.{attr}")
    if not isinstance(appindex.launch, R.Trap):
        untrapped.append("appindex.launch")
    for attr in ("_machine", "_processes", "_control"):
        if not getattr(getattr(ex_mod.Executor, attr), "__rehearsal__", False):
            untrapped.append(f"Executor.{attr}")
    if not isinstance(x_tools.plan_targets, R.Trap):
        untrapped.append("x_tools.plan_targets")
    for mod, attr in ((software_change, "resolve"), (service_control, "resolve"), (service_control, "table")):
        if getattr(getattr(mod, attr), "__module__", "") != R.__name__:
            untrapped.append(f"{mod.__name__}.{attr}")
    check(f"every registered tool's real function is a trap ({len(REGISTRY)} registered + {len(R.LEGACY)} legacy names, "
          f"{len(env.traps)} traps)", not untrapped, untrapped)

    fired = 0
    misfired = []
    for t in env.traps:
        R.REC.take()
        try:
            if isinstance(t, R.ResolveTrap):
                out = t({"probe": 1})
                ok = isinstance(out, dict) and R.MARK in str(out.get("rehearsal", ""))
            else:
                t()
                ok = False
        except R.RehearsalStop:
            ok = not isinstance(t, R.DescribeTrap)
        except R.RehearsalDescribe:
            ok = isinstance(t, R.DescribeTrap)
        except BaseException as exc:
            ok = False
            misfired.append(f"{t.tool}/{t.part}: {type(exc).__name__}")
        events = R.REC.take()
        if ok and any(e["kind"] == "trap" for e in events):
            fired += 1
        elif not misfired or not misfired[-1].startswith(f"{t.tool}/{t.part}"):
            misfired.append(f"{t.tool}/{t.part}")
    check(f"every trap records and refuses when fired directly ({fired}/{len(env.traps)})",
          fired == len(env.traps), misfired)

    fences = []
    R.REC.expecting = True
    try:
        for label, fn, want in _probes(env):
            got = "returned (NOT REFUSED)"
            ok = False
            try:
                out = fn()
                try:
                    if hasattr(out, "close"):
                        out.close()
                except Exception:
                    pass
            except want:
                got = f"refused ({want.__name__})"
                ok = True
            except BaseException as exc:
                got = f"{type(exc).__name__}: {str(exc)[:120]}"
            fences.append({"probe": label, "ok": ok, "result": got})
    finally:
        R.REC.expecting = False
        R.REC.take()
    check(f"every fence refuses ({sum(f['ok'] for f in fences)}/{len(fences)} probes)",
          all(f["ok"] for f in fences), [f for f in fences if not f["ok"]])

    drives = []
    for name, spec in sorted(REGISTRY.items()):
        drives.append(_drive(env, name, spec))
    for name in R.LEGACY:
        drives.append(_drive(env, name, None))
    reached = sum(1 for d in drives if d["reached_trap"])
    bad = [d for d in drives if d["ran"] or d["breaches"]]
    check(f"driven through her real executor: nothing ran, nothing breached ({len(drives)} tools; "
          f"{reached} reached their trap, {len(drives) - reached} stopped earlier by her own gates)", not bad,
          [{"tool": d["tool"], "ran": d["ran"], "breaches": d["breaches"]} for d in bad])
    leftover = R.REC.breaches[:]
    check("no unexpected breach recorded during the self-test", not leftover, leftover)
    after_rows = R.compare(before)
    changed = [r for r in after_rows if r["verdict"] not in ("byte-identical", "absent, still absent", "appended")]
    check("her live files: none changed by the self-test (appends are hers)", not changed, after_rows)
    probe_left = [p for p in (R.DATA / "rehearsal-probe.txt", R.DATA / "rehearsal-probe-dir") if p.exists()]
    check("no probe file or folder exists outside the exam folder", not probe_left, [str(p) for p in probe_left])
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks, "fences": fences, "drives": drives,
            "expected_breaches": len(R.REC.expected_breaches), "ms": round((time.perf_counter() - t0) * 1000, 1),
            "inventory": env.inventory, "stores": env.store_notes, "blocked_already_loaded": env.loaded_at_build}
