"""
core/brain/executor.py — run a structured ToolCall and say what happened.

INVARIANT 4 IS ENFORCED BY SHAPE: this takes a tool NAME and an ARGS dict and
dispatches through a fixed table. There is no path here that accepts a command
string, so there is no path that could execute one.

THIS IS WHERE THE POSSESSIVE REGISTER FINALLY LIVES. `action_done()` and
`action_done(he_did_it_himself=True)` were written two prompts ago and nothing
ever called them — the character Gerald asked for has never once been heard. It
fires here, on ACTIONS only, and only when `memory.he_opened_it_himself()` has
real evidence: a Recent shortcut he created, and no record of her opening it.
No evidence means the plain confirmation. She does not perform the line.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import memory
from .router import action_done, action_failed
from .tools_local import (
    ToolCall,
    listening_on_port,
    open_in_vscode,
    open_path,
    open_url,
    tool_version,
)


class Executor:
    """
    `on_state` is called with 'working' before a tool runs and 'idle' after.

    Local tools are instant so it flashes past; browser automation will take
    real seconds, and that is exactly when Gerald needs the sphere to show
    something other than the state it was in before he spoke.
    """

    def __init__(self, on_state: Callable[[str], None] | None = None) -> None:
        self._on_state = on_state

    def _state(self, s: str) -> None:
        if self._on_state is not None:
            self._on_state(s)

    def run(self, call: ToolCall) -> str:
        self._state("working")
        try:
            return self._dispatch(call)
        except Exception as exc:  # noqa: BLE001
            # Never a bare failure. zoey.md bans vagueness: name what broke and
            # offer the nearest real thing.
            return action_failed(f"{type(exc).__name__}: {exc}",
                                 "Tell me another way and I will try again.")
        finally:
            self._state("idle")

    def _dispatch(self, call: ToolCall) -> str:
        name, args = call.name, call.args

        if name == "app.open_folder":
            path = Path(str(args["path"]))
            if not path.exists():
                return action_failed(f"{path} is not there",
                                     "Give me another path and I will open it.")
            himself = memory.he_opened_it_himself(str(path))
            open_path(path)
            memory.record(name, str(path))
            return action_done(he_did_it_himself=himself)

        if name == "app.open":
            app = str(args["app"])
            himself = memory.he_opened_it_himself(app)
            from .tools_local import index_start_menu
            lnk = index_start_menu().get(app)
            if lnk is None:
                return action_failed(f"I cannot find {app}", "Say the name again?")
            open_path(lnk)
            memory.record(name, app)
            return action_done(he_did_it_himself=himself)

        if name == "app.open_vscode":
            target = str(args.get("path") or "")
            if not target:
                exe = shutil.which("code") or shutil.which("code.cmd")
                if exe is None:
                    return action_failed("VS Code is not on PATH",
                                         "I can open the folder in Explorer instead.")
                subprocess.Popen([exe], shell=False)
                memory.record(name, "vscode")
                return action_done()
            ok, detail = open_in_vscode(Path(target))
            if not ok:
                return action_failed(detail, "I can open it in Explorer instead.")
            himself = memory.he_opened_it_himself(target)
            memory.record(name, target)
            return action_done(he_did_it_himself=himself)

        if name == "app.open_url":
            ok, detail = open_url(str(args["url"]), args.get("browser"))
            if not ok:
                return action_failed(detail, "Give me a full web address.")
            memory.record(name, str(args["url"]))
            return action_done()

        if name == "sys.port_owner":
            port = int(args["port"])
            rows = listening_on_port(port)
            if not rows:
                return f"Nothing is on port {port}, Emperor."
            r = rows[0]
            return f"Port {port} is {r['name']}, Emperor. Process {r['pid']}."

        if name == "sys.tool_version":
            ok, detail = tool_version(str(args["tool"]))
            if not ok:
                return action_failed(detail, "It may not be installed.")
            return f"{detail}, Emperor."

        if name in ("sys.disk", "sys.memory", "sys.battery", "sys.uptime"):
            return self._machine(name)

        if name in ("sys.process_list", "sys.top_processes"):
            return self._processes(int(args.get("n", 5)))

        if name in ("sys.volume", "sys.media", "sys.lock"):
            return self._control(name, args)

        if name == "sys.kill_port":
            # AMBER, and it holds. The executor never kills on the first ask —
            # confirmation is the caller's to obtain (zoey.md: she says it and
            # HOLDS, he confirms a second time).
            port = int(args["port"])
            rows = listening_on_port(port)
            if not rows:
                return f"Nothing is on port {port}, Emperor. Nothing to kill."
            r = rows[0]
            from .router import destructive_hold
            return destructive_hold(
                f"Port {port} is {r['name']}, process {r['pid']}")

        return action_failed(f"{name} is not wired yet", "Ask me something else.")

    # ── read-only machine state ──────────────────────────────────────────────

    def _machine(self, name: str) -> str:
        import psutil

        if name == "sys.disk":
            u = psutil.disk_usage("C:\\")
            return (f"{u.free / 1e9:.1f} gigabytes free, Emperor. "
                    f"That is {100 - u.percent:.0f} percent of the drive.")
        if name == "sys.memory":
            m = psutil.virtual_memory()
            return (f"{m.available / 1e9:.1f} gigabytes free, Emperor. "
                    f"{m.percent:.0f} percent in use.")
        if name == "sys.battery":
            b = psutil.sensors_battery()
            if b is None:
                return "I cannot read a battery on this machine, sir."
            plugged = "on mains" if b.power_plugged else "on battery"
            return f"{b.percent:.0f} percent, Emperor. You are {plugged}."
        seconds = int(psutil.time.time() - psutil.boot_time())
        hours, rem = divmod(seconds, 3600)
        return f"Up {hours} hours and {rem // 60} minutes, Emperor."

    def _processes(self, n: int) -> str:
        import psutil

        rows: list[tuple[float, str]] = []
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                rows.append((p.info["memory_info"].rss, p.info["name"]))
            except Exception:  # noqa: BLE001
                continue
        rows.sort(reverse=True)
        top = ", ".join(f"{nm} at {rss / 1e6:.0f} megabytes" for rss, nm in rows[:n])
        return f"Heaviest first, Emperor. {top}."

    def _control(self, name: str, args: dict[str, Any]) -> str:
        # Windows media/volume keys, sent through the shell's own key API.
        # Structured constants, never a string from anywhere else.
        import ctypes

        VK = {"up": 0xAF, "down": 0xAE, "mute": 0xAD,
              "playpause": 0xB3, "next": 0xB0}
        if name == "sys.lock":
            ctypes.windll.user32.LockWorkStation()
            return "Locking, Emperor."
        key = VK.get(str(args.get("direction") or args.get("action") or ""))
        if key is None:
            return action_failed("I did not catch which control", "Say it again?")
        ctypes.windll.user32.keybd_event(key, 0, 0, 0)
        ctypes.windll.user32.keybd_event(key, 0, 2, 0)
        return action_done()
