from __future__ import annotations

import asyncio
import builtins
import ctypes
import dataclasses
import fnmatch
import hashlib
import importlib.abc
import inspect
import io
import json
import os
import random
import shutil
import site
import socket
import subprocess
import sys
import threading
import time
import types
import webbrowser
from pathlib import Path
from typing import Any, Callable

from core.tools.base import ToolError

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
EXAM_DIR = DATA / "exam"
MARK = "REHEARSAL, NOT RUN"
MODEL_HOSTS = frozenset({"generativelanguage.googleapis.com"})
PURE_DESCRIBE = frozenset({"system.software.install", "system.software.uninstall"})
LEGACY = ("app.open_folder", "app.open", "app.open_vscode", "app.open_url", "sys.port_owner",
          "sys.tool_version", "sys.process_list", "sys.top_processes", "sys.kill_port")
BLOCKED_IMPORTS = frozenset({"sounddevice", "piper", "faster_whisper", "ctranslate2", "playwright",
                             "pyaudio", "openwakeword", "onnxruntime"})
DANGEROUS_DLL = {
    "user32": ("keybd_event", "SendInput", "mouse_event", "SetCursorPos", "LockWorkStation", "PostMessageW",
               "SendMessageW", "SendMessageTimeoutW", "SetForegroundWindow", "ShowWindow", "SetWindowPos",
               "ExitWindowsEx", "BlockInput", "SystemParametersInfoW", "ChangeDisplaySettingsExW",
               "SetClipboardData", "EmptyClipboard", "OpenClipboard", "MoveWindow", "CloseWindow"),
    "kernel32": ("TerminateProcess", "CreateProcessW", "SetPriorityClass", "SetSystemPowerState", "CreateJobObjectW",
                 "AssignProcessToJobObject", "TerminateJobObject"),
    "ntdll": ("NtResumeProcess",),
    "shell32": ("ShellExecuteW", "ShellExecuteExW", "SHFileOperationW"),
    "powrprof": ("SetSuspendState",),
    "advapi32": ("InitiateSystemShutdownExW", "RegSetValueExW", "RegCreateKeyExW", "RegDeleteValueW",
                 "RegDeleteKeyW", "ChangeServiceConfigW", "StartServiceW", "ControlService"),
}


_REAL_OPEN = io.open


def _read(path: Path) -> bytes:
    with _REAL_OPEN(path, "rb") as fh:
        return fh.read()


def live_files() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Tessa"
    out = [DATA / "audit.log", DATA / "memory" / "conversation.json", DATA / "memory" / "style.json",
           DATA / "memory" / "claims.json", DATA / "tessa-actions.jsonl", DATA / "appindex.json",
           DATA / "fileindex.txt", DATA / "fileindex.meta.json", DATA / "x-style.json",
           DATA / "cost-ledger.jsonl", local / "runtime.json", local / "vault.json", local / "schedule.json"]
    logs = DATA / "logs"
    if logs.is_dir():
        out.extend(sorted(logs.glob("daemon-*.log"))[-2:])
    return out


def snapshot(paths: list[Path] | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in paths or live_files():
        try:
            b = _read(p)
        except FileNotFoundError:
            out[str(p)] = {"exists": False}
            continue
        out[str(p)] = {"exists": True, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    return out


def compare(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = []
    for path, b in before.items():
        p = Path(path)
        try:
            data = _read(p)
        except FileNotFoundError:
            data = None
        if not b.get("exists"):
            rows.append({"path": path, "verdict": "absent, still absent" if data is None else "CREATED"})
            continue
        if data is None:
            rows.append({"path": path, "verdict": "REMOVED"})
            continue
        whole = hashlib.sha256(data).hexdigest()
        if whole == b["sha256"]:
            rows.append({"path": path, "verdict": "byte-identical", "size": len(data)})
            continue
        prefix = hashlib.sha256(data[:b["size"]]).hexdigest()
        if len(data) > b["size"] and prefix == b["sha256"]:
            rows.append({"path": path, "verdict": "appended", "grew": len(data) - b["size"]})
        else:
            rows.append({"path": path, "verdict": "CHANGED", "size": len(data)})
    return rows


class Halt(BaseException):
    def __init__(self, kind: str, what: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(f"{kind}: {what}")
        self.kind = kind
        self.what = what
        self.detail = dict(detail or {})


class RoutingHalt(Halt):
    pass


class IsolationBreach(Halt):
    pass


class BudgetStop(Halt):
    pass


class NeedsModel(Halt):
    pass


class QuotaStop(Halt):
    pass


class NetworkStop(Halt):
    pass


class RehearsalStop(ToolError):
    def __init__(self, what: str) -> None:
        super().__init__(f"{MARK}: {what} was stopped before it ran", "Nothing happened.")
        self.what = what


class RehearsalDescribe(RuntimeError):
    pass


def plain(obj: Any, depth: int = 0) -> Any:
    if depth > 6:
        return str(obj)[:200]
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj if len(obj) <= 2000 else obj[:2000] + "..."
    if isinstance(obj, dict):
        return {str(k): plain(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [plain(v, depth + 1) for v in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: plain(getattr(obj, f.name, None), depth + 1) for f in dataclasses.fields(obj)}
    if hasattr(obj, "value") and isinstance(getattr(obj, "value"), str):
        return obj.value
    return str(obj)[:500]


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.events: list[dict[str, Any]] = []
        self.breaches: list[dict[str, Any]] = []
        self.expected_breaches: list[dict[str, Any]] = []
        self.expecting = False

    def note(self, kind: str, **data: Any) -> None:
        with self.lock:
            self.events.append({"kind": kind, "at": time.perf_counter(), **data})

    def take(self) -> list[dict[str, Any]]:
        with self.lock:
            out, self.events = self.events, []
            return out

    def breach(self, row: dict[str, Any]) -> None:
        with self.lock:
            (self.expected_breaches if self.expecting else self.breaches).append(row)
            self.events.append({"kind": "breach", "at": time.perf_counter(), **row})


REC = Recorder()
ENV: "Rehearsal | None" = None
_TLS = threading.local()
_FENCE_STATE: dict[str, Any] = {"on": False, "dlls": 0}


class _Current:
    def __getattr__(self, name: str) -> Any:
        if ENV is None:
            raise IsolationBreach("environment", f"fence used with no rehearsal environment ({name})")
        return getattr(ENV, name)


def _breach(kind: str, what: Any) -> None:
    text = str(what)[:300]
    REC.breach({"breach": kind, "what": text, "item": getattr(ENV, "current_item", None)})
    raise IsolationBreach(kind, text)


class Trap:
    def __init__(self, tool: str, part: str, real: Any = None) -> None:
        self.tool = tool
        self.part = part
        self.real_name = (f"{getattr(real, '__module__', '?')}.{getattr(real, '__qualname__', type(real).__name__)}"
                          if real is not None else "")
        if real is not None:
            try:
                self.__signature__ = inspect.signature(real)
            except (TypeError, ValueError):
                pass
        self.hits = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.hits += 1
        REC.note("trap", tool=self.tool, part=self.part, args=plain(list(args)), kwargs=plain(kwargs))
        raise RehearsalStop(f"{self.tool} ({self.part})")


class WriteTrap(Trap):
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.hits += 1
        task, language, fix = (str(kwargs.get(k) or "") for k in ("task", "language", "fix"))
        generated = None
        if task and language and not fix and ENV is not None:
            from core.tools import coding, langs
            key = langs.canonical(language)
            if key is not None:
                generated = coding.generate(key, task, None, brain=ENV.brain)
        kw = plain(kwargs)
        if generated is not None:
            kw["generated"] = generated
        REC.note("trap", tool=self.tool, part=self.part, args=plain(list(args)), kwargs=kw)
        raise RehearsalStop(f"{self.tool} ({self.part})")


class ResolveTrap(Trap):
    def __call__(self, args: dict[str, Any], *a: Any, **k: Any) -> dict[str, Any]:
        self.hits += 1
        REC.note("trap", tool=self.tool, part=self.part, args=plain(args), kwargs={})
        out = dict(args or {})
        out["rehearsal"] = f"{MARK}: the target was not resolved"
        return out


class DescribeTrap(Trap):
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.hits += 1
        REC.note("trap", tool=self.tool, part=self.part, args=plain(list(args)), kwargs=plain(kwargs))
        raise RehearsalDescribe(f"{MARK}: {self.tool} describe")


class RecAudit:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append(self, *, actor: str, tool: str, tier: str, summary: str, detail: Any = None,
               provenance: str | None = None, **extra: Any) -> dict[str, Any]:
        row = {"seq": len(self.rows) + 1, "actor": actor, "tool": tool, "tier": tier,
               "summary": str(summary), "detail": plain(detail), "provenance": provenance}
        self.rows.append(row)
        REC.note("audit", **row)
        return row


class _BlockLoader(importlib.abc.Loader):
    def create_module(self, spec: Any) -> Any:
        _breach("import", spec.name)

    def exec_module(self, module: Any) -> None:
        _breach("import", getattr(module, "__name__", "?"))


class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname.split(".", 1)[0] not in BLOCKED_IMPORTS:
            return None
        import importlib.machinery
        real = importlib.machinery.PathFinder.find_spec(fullname, path)
        if real is None:
            return None
        return importlib.machinery.ModuleSpec(fullname, _BlockLoader(), origin=real.origin)


def _under(path: str, roots: list[str]) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    for r in roots:
        if p == r or p.startswith(r.rstrip("\\/") + os.sep):
            return True
    return False


def _norm_roots(paths: list[Any]) -> list[str]:
    out = []
    for p in paths:
        if p:
            out.append(os.path.normcase(os.path.abspath(str(p))))
    return sorted(set(out))


class Rehearsal:
    def __init__(self, out_dir: Path, *, brain: Any = None, intent_engine: Any = None,
                 budget: int = 0, gap_model_s: float = 6.5, gap_any_s: float = 3.0,
                 role_budgets: dict[str, int] | None = None) -> None:
        self.out_dir = Path(out_dir)
        self.stores_dir = self.out_dir / "stores"
        self.brain = brain
        self.intent_engine = intent_engine
        self.budget = int(budget)
        self.role_budgets = {str(k): int(v) for k, v in role_budgets.items()} if role_budgets is not None else None
        self.model_budgets: dict[str, int] | None = None
        self.model_roles: dict[str, str] = {}
        self.calls_by_model: dict[str, int] = {}
        self.skipped_calls: list[dict[str, Any]] = []
        self.gap_model_s = float(gap_model_s)
        self.gap_any_s = float(gap_any_s)
        self.calls_used = 0
        self.model_calls: list[dict[str, Any]] = []
        self.lock = threading.RLock()
        self.in_flight = 0
        self.last_start: dict[str, float] = {}
        self.last_any = -1e9
        self.current_item: str | None = None
        self.allowed_ips: set[str] = set()
        self.inventory: list[dict[str, Any]] = []
        self.traps: list[Trap] = []
        self.built = False
        self.fenced = False
        self.loaded_at_build: list[str] = []
        self.store_notes: dict[str, Any] = {}

    def build(self) -> "Rehearsal":
        global ENV
        if self.built:
            return self
        sys.dont_write_bytecode = True
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.stores_dir.mkdir(parents=True, exist_ok=True)
        self._copy_stores()
        from core import server as server_mod
        from core.brain.llm import make_engine
        from core.brain import intent_model
        self.server = server_mod
        self.settings = server_mod.load_settings(ROOT / "core" / "config" / "settings.yaml")
        if self.brain is None:
            self.brain = make_engine(self.settings)
        if self.intent_engine is not None:
            model = str(intent_model.settings().get("model") or "").strip()
            intent_model._ENGINES[model] = self.intent_engine
        intent_model._engine_for(self.brain)
        self.model_roles = {"intent": str(intent_model.settings().get("model") or "").strip(),
                            "chat": str(getattr(getattr(self.brain, "primary", self.brain), "model_main", "") or "")}
        if self.role_budgets is not None:
            self.model_budgets = {}
            for role, n in self.role_budgets.items():
                name = self.model_roles.get(role) or role
                self.model_budgets[name] = min(n, self.model_budgets.get(name, n))
        self._install_model_meter()
        self._install_registry_traps()
        self._install_legacy_traps()
        self.traps = current_traps()
        self._install_routing_halts()
        self._install_stores()
        self._install_instruments()
        self.loop = asyncio.new_event_loop()
        self.loaded_at_build = sorted(m for m in BLOCKED_IMPORTS if m in sys.modules)
        ENV = self
        self._install_fences()
        self.built = True
        return self

    def _copy_stores(self) -> None:
        pairs = {"appindex.json": DATA / "appindex.json", "fileindex.txt": DATA / "fileindex.txt",
                 "fileindex.meta.json": DATA / "fileindex.meta.json", "style.json": DATA / "memory" / "style.json",
                 "claims.json": DATA / "memory" / "claims.json"}
        for name, src in pairs.items():
            dst = self.stores_dir / name
            if src.exists():
                data = _read(src)
                with _REAL_OPEN(dst, "wb") as fh:
                    fh.write(data)
                self.store_notes[name] = {"from": str(src), "bytes": len(data),
                                          "sha256": hashlib.sha256(data).hexdigest()}
            else:
                self.store_notes[name] = {"from": str(src), "absent": True}

    def before_call(self, model: str) -> float:
        with self.lock:
            if self.model_budgets is not None:
                limit = self.model_budgets.get(model, 0)
                used = self.calls_by_model.get(model, 0)
                if used >= limit:
                    row = {"model": model, "budget": limit, "used": used, "item": self.current_item}
                    self.skipped_calls.append(row)
                    REC.note("needs-model", **row)
                    raise NeedsModel("model", f"{model}: its budget of {limit} calls is spent, so this call was "
                                              f"not made", {"model": model, "budget": limit})
            if self.calls_used >= self.budget:
                raise BudgetStop("budget", f"{self.budget} model calls used; the next one would exceed the budget")
            now = time.monotonic()
            due = max(self.last_start.get(model, -1e9) + self.gap_model_s, self.last_any + self.gap_any_s)
            wait = max(0.0, due - now)
            self.calls_used += 1
            self.calls_by_model[model] = self.calls_by_model.get(model, 0) + 1
            self.last_start[model] = now + wait
            self.last_any = now + wait
        if wait:
            time.sleep(wait)
        return wait

    def _install_model_meter(self) -> None:
        from core.brain.llm import LLMUnavailable, RateLimited
        from core.brain.llm.gemini import GeminiLLM
        from core.brain.llm.local import LocalLLM
        from core.brain.llm.anthropic_llm import AnthropicLLM
        real = GeminiLLM.stream
        if getattr(real, "__rehearsal__", False):
            return

        def stream(self: Any, system: str, messages: list[Any], **kw: Any) -> Any:
            env = ENV
            if env is None:
                _breach("model", "a model call with no rehearsal environment")
            model = str(getattr(self, "model_main", "?"))
            rec: dict[str, Any] = {"model": model, "purpose": "intent" if kw.get("json_object") else "chat",
                                   "item": env.current_item, "max_tokens": kw.get("max_tokens"),
                                   "quality": kw.get("quality"), "system_chars": len(system or ""),
                                   "message_chars": sum(len(getattr(m, "content", "") or "") for m in messages)}
            wait = env.before_call(model)
            rec["wait_ms"] = round(wait * 1000, 1)
            with env.lock:
                env.in_flight += 1
            t0 = time.perf_counter()
            first = None
            chars = 0
            outcome = "ok"
            try:
                for delta in real(self, system, messages, **kw):
                    if first is None:
                        first = time.perf_counter()
                    chars += len(delta)
                    yield delta
            except RateLimited as exc:
                outcome = "rate-limited"
                rec.update(quota_id=getattr(exc, "quota_id", ""), transient=getattr(exc, "transient", None),
                           retry_after=getattr(exc, "retry_after", None))
                raise QuotaStop("quota", f"{model}: {exc}", {"quota_id": getattr(exc, "quota_id", "")}) from None
            except LLMUnavailable as exc:
                msg = str(exc)
                if "timed out" in msg or "transport error" in msg:
                    outcome = "network"
                    raise NetworkStop("network", f"{model}: {msg}") from None
                outcome = f"unavailable: {msg[:160]}"
                raise
            except GeneratorExit:
                outcome = "abandoned"
                raise
            except Halt as h:
                outcome = f"halt {h.kind}"
                raise
            finally:
                with env.lock:
                    env.in_flight -= 1
                end = time.perf_counter()
                usage = getattr(self, "last_usage", None)
                rec.update(ms=round((end - t0) * 1000, 1),
                           ttft_ms=round((first - t0) * 1000, 1) if first is not None else None,
                           out_chars=chars, outcome=outcome,
                           in_tokens=getattr(usage, "input_tokens", None) if outcome == "ok" else None,
                           out_tokens=getattr(usage, "output_tokens", None) if outcome == "ok" else None)
                with env.lock:
                    env.model_calls.append(rec)
                REC.note("model", **rec)

        stream.__rehearsal__ = True
        GeminiLLM.stream = stream

        def no_local(self: Any, *a: Any, **k: Any) -> Any:
            REC.note("model-blocked", engine=str(getattr(self, "name", "local")))
            raise QuotaStop("local", "the chat would have fallen back to the local model")
            yield ""

        def no_anthropic(self: Any, *a: Any, **k: Any) -> Any:
            _breach("model", "anthropic engine called")
            yield ""

        LocalLLM.stream = no_local
        AnthropicLLM.stream = no_anthropic

    def _install_registry_traps(self) -> None:
        from core.tools import REGISTRY
        from core.capabilities.vault.tools import make_status_spec
        if "vault.status" not in REGISTRY:
            REGISTRY["vault.status"] = make_status_spec(None)
        for name, spec in list(REGISTRY.items()):
            cap = getattr(spec.handler, "__wrapped_capability__", None)
            changes: dict[str, Any] = {}
            row: dict[str, Any] = {"tool": name, "tier": spec.tier, "holds": bool(spec.holds)}
            if cap is not None:
                if not isinstance(cap.run, Trap):
                    object.__setattr__(cap, "run", Trap(name, "capability.run", cap.run))
                row["kind"] = "capability"
                row["real"] = cap.run.real_name
                if cap.describe is not None:
                    if name in PURE_DESCRIBE:
                        row["describe"] = "kept: decided from its arguments alone"
                    else:
                        if not isinstance(cap.describe, Trap):
                            object.__setattr__(cap, "describe", DescribeTrap(name, "capability.describe", cap.describe))
                        row["describe"] = "trapped"
            else:
                if not isinstance(spec.handler, Trap):
                    changes["handler"] = (WriteTrap if name == "code.write" else Trap)(name, "handler", spec.handler)
                row["kind"] = "handler"
                row["real"] = (changes.get("handler") or spec.handler).real_name
            if spec.resolve is not None:
                if not isinstance(spec.resolve, Trap):
                    changes["resolve"] = ResolveTrap(name, "resolve", spec.resolve)
                row["resolve"] = "trapped: args returned unresolved and marked"
            if spec.describe is not None:
                if not isinstance(spec.describe, Trap):
                    changes["describe"] = DescribeTrap(name, "describe", spec.describe)
                row["describe"] = "trapped"
            if changes:
                REGISTRY[name] = dataclasses.replace(spec, **changes)
            self.inventory.append(row)

    def _install_legacy_traps(self) -> None:
        from core.brain import executor as ex_mod, memory, appindex
        from core.tools import x_tools
        pairs = [("open_path", "app.open_folder"), ("open_url", "app.open_url"),
                 ("open_in_vscode", "app.open_vscode"), ("listening_on_port", "sys.port_owner/sys.kill_port"),
                 ("tool_version", "sys.tool_version")]
        for attr, tool in pairs:
            real = getattr(ex_mod, attr)
            if not isinstance(real, Trap):
                setattr(ex_mod, attr, Trap(tool, f"executor.{attr}", real))
        if not isinstance(getattr(ex_mod.shutil, "which", None), Trap):
            ex_mod.shutil = types.SimpleNamespace(which=Trap("app.open_vscode", "executor.shutil.which", shutil.which))
        if not isinstance(getattr(ex_mod.subprocess, "Popen", None), Trap):
            ex_mod.subprocess = types.SimpleNamespace(Popen=Trap("app.open_vscode", "executor.subprocess.Popen"))
        for attr in ("record", "he_opened_it_himself", "she_has_opened"):
            real = getattr(memory, attr, None)
            if real is not None and not isinstance(real, Trap):
                setattr(memory, attr, Trap("legacy-open", f"memory.{attr}", real))
        memory.ACTION_LOG = self.stores_dir / "tessa-actions.jsonl"
        if not isinstance(appindex.launch, Trap):
            appindex.launch = Trap("app.open", "appindex.launch", appindex.launch)
        for attr, tool in (("_machine", "sys.status"), ("_processes", "sys.process_list"), ("_control", "sys.control")):
            def method(_self: Any, *a: Any, _tool: str = tool, _attr: str = attr, **k: Any) -> str:
                REC.note("trap", tool=_tool, part=f"Executor.{_attr}", args=plain(list(a)), kwargs=plain(k))
                raise RehearsalStop(f"{_tool} (Executor.{_attr})")
            method.__rehearsal__ = True
            setattr(ex_mod.Executor, attr, method)
        if not isinstance(x_tools.plan_targets, Trap):
            x_tools.plan_targets = Trap("agent.plan", "x_tools.plan_targets", x_tools.plan_targets)
        self.inventory.extend({"tool": n, "kind": "legacy", "tier": ex_mod.Executor._LEGACY_TIERS.get(n, "red")}
                              for n in LEGACY)

    def _install_routing_halts(self) -> None:
        from core.system.abilities import software_change, service_control

        def software(name: Any, scope: str = "catalog") -> Any:
            REC.note("halt", halt="software", what=str(name), scope=scope)
            raise RoutingHalt("software", str(name), {"scope": scope})

        def service(spoken: Any, rows: Any = None) -> Any:
            REC.note("halt", halt="services", what=str(spoken))
            raise RoutingHalt("services", str(spoken))

        def table() -> Any:
            REC.note("halt", halt="services", what="the service table")
            raise RoutingHalt("services", "the service table")

        software_change.resolve = software
        service_control.resolve = service
        service_control.table = table

    def _install_stores(self) -> None:
        from core.brain import appindex, claims, style
        from core.brain.claims import ClaimStore
        from core.brain.style import StyleStore, resolve_owner_handle
        from core.system.winapi import fileindex
        from core.brain import x_voice
        from core.system import schedule

        def noop(name: str) -> Callable[..., None]:
            def f(*a: Any, **k: Any) -> None:
                REC.note("store", store=name, write="skipped")
            return f

        idx = appindex.AppIndex(cache_path=self.stores_dir / "appindex.json")
        for attr in ("build_fast", "build_slow", "build_async", "_save"):
            setattr(idx, attr, noop(f"appindex.{attr}"))
        idx._load()
        appindex._INDEX = idx
        self.store_notes["appindex.loaded"] = len(idx.entries)
        fi = fileindex.FileIndex(cache_path=self.stores_dir / "fileindex.txt")
        for attr in ("build_fast", "build_full", "build_async", "_save"):
            setattr(fi, attr, noop(f"fileindex.{attr}"))
        self.store_notes["fileindex.loaded"] = bool(fi._load())
        fileindex._INDEX = fi
        self.claims = ClaimStore(path=self.stores_dir / "claims.json")
        self.claims.save = noop("claims")
        self.style = StyleStore(path=self.stores_dir / "style.json",
                                owner_handle=resolve_owner_handle(self.settings))
        self.style.save = noop("style")
        style.bind(self.style)
        claims.bind(self.claims, None)
        x_voice.CACHE_PATH = self.stores_dir / "x-style.json"
        schedule.DEFAULT_PATH = self.stores_dir / "schedule.json"
        from core.tools import browser
        browser.PROFILE_ROOT = self.stores_dir / "browser-profiles"
        browser.DEFAULT_PROFILE = browser.PROFILE_ROOT / "default"
        browser.PID_FILE = browser.PROFILE_ROOT / "chrome.pid"

    def _install_instruments(self) -> None:
        from core.brain import humanness, intent_model, typed_turn
        server_mod = self.server

        def log(msg: str) -> None:
            REC.note("log", line=str(msg)[:600])

        server_mod.log = log

        def timed(stage: str, fn: Callable[..., Any], info: Callable[[tuple, dict, Any], Any] | None = None) -> Any:
            if getattr(fn, "__rehearsal__", False):
                return fn

            def wrapper(*a: Any, **k: Any) -> Any:
                t0 = time.perf_counter()
                out = None
                err = None
                try:
                    out = fn(*a, **k)
                    return out
                except BaseException as exc:
                    err = f"{type(exc).__name__}: {str(exc)[:300]}"
                    raise
                finally:
                    data = None
                    if info is not None:
                        try:
                            data = info(a, k, out) if err is None else info(a, k, None)
                        except Exception as exc:
                            data = {"info_error": f"{type(exc).__name__}: {exc}"}
                    REC.note("stage", stage=stage, ms=round((time.perf_counter() - t0) * 1000, 3),
                             info=plain(data), error=err)

            wrapper.__rehearsal__ = True
            wrapper.__wrapped__ = fn
            return wrapper

        def res_info(a: tuple, k: dict, out: Any) -> Any:
            if out is None:
                return {"text": a[0] if a else None}
            call = getattr(out, "call", None)
            return {"kind": out.kind, "tool": getattr(call, "name", None), "args": plain(getattr(call, "args", None)),
                    "origin": getattr(call, "origin", None), "question": out.question, "why": out.why,
                    "error": out.error, "engine": out.engine, "ms": out.ms, "spoken": out.spoken,
                    "dropped": list(out.dropped_args), "raw": (out.raw or "")[:600], "hint": plain(k.get("hint"))}

        def second_info(a: tuple, k: dict, out: Any) -> Any:
            return {"doubt": getattr(a[0], "doubt", "") if a else "", "result": routed_info(out)}

        def chat_info(a: tuple, k: dict, out: Any) -> Any:
            return {"question": a[0] if a else None, "shown": out[0] if out else None, "spoken": out[1] if out else None}

        def human_info(a: tuple, k: dict, out: Any) -> Any:
            if out is None:
                return {"raw": a[0] if a else None}
            return {"raw": a[0] if a else None, "text": out.text, "clean": out.clean, "describe": humanness.describe(out)}

        def turn_info(a: tuple, k: dict, out: Any) -> Any:
            if out is None:
                return None
            return {"heard": out.heard, "said": out.said, "intent": out.intent, "tools": list(out.tools),
                    "flagged": list(out.flagged), "awaiting_approval": out.awaiting_approval, "ms": out.ms,
                    "spoken": out.spoken, "ends_session": out.ends_session}

        intent_model.resolve = timed("intent", intent_model.resolve, res_info)
        intent_model.second_opinion = timed("second_opinion", intent_model.second_opinion, second_info)
        typed_turn._ask_brain = timed("chat", typed_turn._ask_brain, chat_info)
        typed_turn.run_typed_turn = timed("turn", typed_turn.run_typed_turn, turn_info)
        humanness.humanise = timed("humanise", humanness.humanise, human_info)
        self._timed = timed

    def _install_fences(self) -> None:
        env = _Current()
        read_roots = [ROOT / "core", ROOT / "packages", self.out_dir, sys.prefix, sys.base_prefix,
                      sys.exec_prefix, os.path.dirname(os.__file__)]
        try:
            read_roots.extend(site.getsitepackages())
            read_roots.append(site.getusersitepackages())
        except Exception:
            pass
        try:
            import certifi
            read_roots.append(os.path.dirname(certifi.where()))
        except Exception:
            pass
        self.read_roots = _norm_roots(read_roots)
        self.write_roots = _norm_roots([self.out_dir])
        if _FENCE_STATE["on"]:
            self.fenced_dlls = _FENCE_STATE["dlls"]
            self.fenced = True
            return
        real_open = io.open

        def fenced_open(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
            if isinstance(file, int):
                return real_open(file, mode, *a, **k)
            path = os.fspath(file)
            if isinstance(path, bytes):
                path = path.decode("utf-8", "replace")
            writing = any(c in str(mode) for c in "wax+")
            if writing:
                if _under(path, env.write_roots):
                    return real_open(file, mode, *a, **k)
                _breach("file-write", path)
            if _under(path, env.read_roots):
                return real_open(file, mode, *a, **k)
            _breach("file-read", path)

        builtins.open = fenced_open
        io.open = fenced_open
        self._real_open = real_open

        def path_fence(name: str, real: Callable[..., Any], n_paths: int = 1, first: int = 0) -> Callable[..., Any]:
            def fenced(*a: Any, **k: Any) -> Any:
                paths = [a[i] for i in range(first, min(n_paths, len(a)))]
                for p in paths:
                    try:
                        s = os.fspath(p)
                    except TypeError:
                        _breach("file-write", f"os.{name} {p!r}")
                    if isinstance(s, bytes):
                        s = s.decode("utf-8", "replace")
                    if not _under(s, env.write_roots):
                        _breach("file-write", f"os.{name} {s}")
                return real(*a, **k)
            return fenced

        for name, n in (("remove", 1), ("unlink", 1), ("rmdir", 1), ("removedirs", 1), ("rename", 2),
                        ("renames", 2), ("replace", 2), ("mkdir", 1), ("makedirs", 1), ("chmod", 1),
                        ("utime", 1), ("truncate", 1), ("link", 2), ("symlink", 2)):
            real = getattr(os, name, None)
            if real is not None:
                setattr(os, name, path_fence(name, real, n))

        def deny(kind: str, what: str) -> Callable[..., Any]:
            def f(*a: Any, **k: Any) -> Any:
                _breach(kind, f"{what} {plain(list(a))!r}")
            f.__rehearsal_fence__ = True
            return f

        for name in ("system", "startfile", "popen", "kill", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
                     "spawnve", "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve",
                     "execvp", "execvpe"):
            if hasattr(os, name):
                setattr(os, name, deny("process", f"os.{name}"))
        for name, n, first in (("rmtree", 1, 0), ("move", 2, 0), ("copy", 2, 1), ("copy2", 2, 1),
                               ("copyfile", 2, 1), ("copytree", 2, 1)):
            setattr(shutil, name, path_fence(name, getattr(shutil, name), n, first))

        def popen_init(self_: Any, args: Any, *a: Any, **k: Any) -> None:
            self_._child_created = False
            _breach("process", f"subprocess.Popen {plain(args)!r}")

        subprocess.Popen.__init__ = popen_init

        real_gai = socket.getaddrinfo

        def gai(host: Any, port: Any, *a: Any, **k: Any) -> Any:
            h = (host.decode() if isinstance(host, bytes) else str(host or "")).lower().rstrip(".")
            if h in MODEL_HOSTS and env.in_flight > 0:
                res = real_gai(host, port, *a, **k)
                for r in res:
                    env.allowed_ips.add(str(r[4][0]))
                return res
            _breach("network", f"getaddrinfo {host}:{port}")

        socket.getaddrinfo = gai
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex

        def allowed(address: Any) -> bool:
            if getattr(_TLS, "pair", False):
                return True
            ip = str(address[0]) if isinstance(address, tuple) else str(address)
            return ip in env.allowed_ips and env.in_flight > 0

        def connect(self_: Any, address: Any) -> Any:
            if allowed(address):
                return real_connect(self_, address)
            _breach("network", f"connect {address!r}")

        def connect_ex(self_: Any, address: Any) -> Any:
            if allowed(address):
                return real_connect_ex(self_, address)
            _breach("network", f"connect_ex {address!r}")

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        real_pair = socket.socketpair

        def pair(*a: Any, **k: Any) -> Any:
            _TLS.pair = True
            try:
                return real_pair(*a, **k)
            finally:
                _TLS.pair = False

        socket.socketpair = pair
        import urllib.request
        urllib.request.urlopen = deny("network", "urllib.request.urlopen")
        for name in ("open", "open_new", "open_new_tab"):
            setattr(webbrowser, name, deny("browser", f"webbrowser.{name}"))
        import winreg
        for name in ("SetValue", "SetValueEx", "CreateKey", "CreateKeyEx", "DeleteKey", "DeleteKeyEx", "DeleteValue"):
            if hasattr(winreg, name):
                setattr(winreg, name, deny("registry-write", f"winreg.{name}"))
        dlls: list[Any] = []
        for dll_name in DANGEROUS_DLL:
            try:
                dlls.append((dll_name, getattr(ctypes.windll, dll_name)))
            except OSError:
                pass
        for mod_name, mod in list(sys.modules.items()):
            if not mod_name.startswith("core.") or mod is None:
                continue
            for value in list(vars(mod).values()):
                if isinstance(value, ctypes.CDLL):
                    base = os.path.basename(str(getattr(value, "_name", "") or "")).lower().split(".")[0]
                    if base in DANGEROUS_DLL:
                        dlls.append((base, value))
        self.fenced_dlls = 0
        for base, dll in dlls:
            for fname in DANGEROUS_DLL[base]:
                try:
                    setattr(dll, fname, deny("win32", f"{base}.{fname}"))
                except Exception:
                    continue
            self.fenced_dlls += 1
        try:
            import psutil
            for name in ("kill", "terminate", "suspend", "resume", "send_signal"):
                setattr(psutil.Process, name, deny("process", f"psutil.Process.{name}"))
            real_nice = psutil.Process.nice

            def nice(self_: Any, value: Any = None) -> Any:
                if value is not None:
                    _breach("process", f"psutil.Process.nice({value})")
                return real_nice(self_)

            psutil.Process.nice = nice
        except ImportError:
            pass
        from core.tools import browser
        browser.SESSION.context = deny("browser", "browser.SESSION.context")
        browser.SESSION._require_playwright = deny("browser", "browser.SESSION._require_playwright")
        from core import lifecycle
        lifecycle.take_guard = deny("guard", "lifecycle.take_guard")
        from core.security import runtime as rt
        for name in ("write_runtime_file", "remove_runtime_file", "preflight_runtime_file", "migrate_local_appdata"):
            setattr(rt, name, deny("runtime", f"runtime.{name}"))
        from core.security.audit import AuditLog
        AuditLog.append = deny("audit-write", "AuditLog.append")
        sys.meta_path.insert(0, _Blocker())
        _FENCE_STATE.update(on=True, dlls=self.fenced_dlls)
        self.fenced = True

    def new_turn_env(self, item: dict[str, Any]) -> dict[str, Any]:
        from core.brain import claims, relevance
        from core.brain.conversation import Conversation
        from core.brain.executor import Executor
        from core.brain.provenance import SessionContext
        from core.brain.router import Router
        conversation = Conversation(path=self.stores_dir / "conversation-rehearsal.json")
        conversation.save = lambda: REC.note("store", store="conversation", write="skipped")
        for role, text in item.get("before") or []:
            conversation.add(role, text)
        claims.bind(self.claims, conversation)
        rel = relevance.from_settings(self.settings, conversation)
        audit = RecAudit()

        def on_state(s: str, detail: Any = None) -> None:
            REC.note("state", state=s, detail=plain(detail))

        def on_card(payload: dict[str, Any]) -> None:
            REC.note("card", **plain(payload))

        executor = Executor(on_state=on_state, session=SessionContext(), audit=audit,
                            on_permission_request=on_card, claims=self.claims, style=self.style,
                            relevance=rel)

        def status() -> dict[str, Any]:
            REC.note("halt", halt="status", what="her live health")
            raise RoutingHalt("status", "her live health (uptime, CPU, memory, spend)")

        router = Router(health_sample=status)
        router.route = self._timed("route", router.route, lambda a, k, out: routed_info(out))
        executor.run = self._timed("execute", executor.run, lambda a, k, out: {"call": call_info(a[0]) if a else None,
                                                                             "said": out})
        executor.answer_confirmation = self._timed("confirm", executor.answer_confirmation,
                                                   lambda a, k, out: {"text": a[0] if a else None, "said": out})
        executor.start_plan = self._timed("plan", executor.start_plan,
                                          lambda a, k, out: {"steps": [call_info(getattr(s, "call", None)) for s in (a[0] if a else [])],
                                                             "said": out[0] if out else None,
                                                             "ran": list(out[1]) if out else None})
        stub = _Stub(self, router, executor, conversation, audit)
        return {"conversation": conversation, "executor": executor, "router": router, "audit": audit, "stub": stub}

    def run_item(self, item: dict[str, Any]) -> dict[str, Any]:
        if not self.built:
            raise RuntimeError("build() first")
        self.current_item = str(item.get("id"))
        seed = int(hashlib.sha256(self.current_item.encode()).hexdigest()[:8], 16)
        random.seed(seed)
        REC.take()
        calls_before = len(self.model_calls)
        t0 = time.perf_counter()
        parts = self.new_turn_env(item)
        turns: list[dict[str, Any]] = []
        stop: dict[str, Any] | None = None
        seen_cards: set[str] = set()
        for text in item.get("turns") or []:
            turn = self._run_turn(parts, str(text), seen_cards)
            turns.append(turn)
            if turn.get("stop"):
                stop = turn["stop"]
                break
        rec = {"id": item.get("id"), "seed": seed, "turns": turns, "stop": stop,
               "ms": round((time.perf_counter() - t0) * 1000, 1),
               "model_calls": plain(self.model_calls[calls_before:]),
               "conversation_after": len(parts["conversation"].turns) if hasattr(parts["conversation"], "turns") else None}
        self.current_item = None
        return rec

    def _run_turn(self, parts: dict[str, Any], text: str, seen_cards: set[str]) -> dict[str, Any]:
        server_mod = self.server
        stub = parts["stub"]
        executor = parts["executor"]
        job = server_mod._TypedJob(server_mod.ulid(), text, time.perf_counter())
        REC.take()
        t0 = time.perf_counter()
        halt = None
        stop = None
        error = None
        try:
            self.loop.run_until_complete(server_mod.TessaDaemon._typed_turn(stub, job))
        except RoutingHalt as h:
            halt = {"kind": h.kind, "what": h.what, **h.detail}
        except (BudgetStop, NeedsModel, QuotaStop, NetworkStop, IsolationBreach) as h:
            stop = {"kind": h.kind, "class": type(h).__name__, "what": h.what, **h.detail}
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        total = (time.perf_counter() - t0) * 1000
        try:
            self.loop.run_until_complete(asyncio.sleep(0))
        except Exception:
            pass
        events = REC.take()
        held = executor.ledger.pending
        hold = None
        if held is not None:
            hold = {"tool": held.tool, "args": plain(held.args), "detail": held.detail,
                    "origin": getattr(held, "origin", None)}
        cards = []
        for req in executor.approvals.pending.values():
            if req.request_id in seen_cards:
                continue
            seen_cards.add(req.request_id)
            cards.append({"tool": req.tool, "tier": req.tier, "args": plain(req.args), "provenance": req.provenance,
                          "detail": req.detail, "frozen": list(req.frozen),
                          "external_at_request": bool(getattr(req, "external_at_request", False))})
        plan = executor.ledger.plan
        return build_turn(text, events, hold, cards, halt, stop, error, total,
                          {"steps": len(plan.steps), "tools": [s.tool for s in plan.steps]} if plan is not None else None)


class _Stub:
    voice = None

    def __init__(self, env: Rehearsal, router: Any, executor: Any, conversation: Any, audit: Any) -> None:
        self._env = env
        self.brain = env.brain
        self.conversation = conversation
        self.audit = audit
        self.clients = {"rehearsal-orb": {"surface": "orb", "authed": True}}
        self.convo = types.SimpleNamespace(note_turn=lambda: None, open=False)
        self._pair = (router, executor)

    def _text_agent(self) -> tuple[Any, Any]:
        return self._pair

    async def broadcast(self, msg_type: str, payload: dict[str, Any]) -> None:
        REC.note("surface", type=msg_type, payload=plain(payload))

    async def _say(self, line: dict[str, Any], spoken: str, job: Any = None) -> bool:
        REC.note("speech", shown=str(line.get("text") or ""), spoken=" ".join(str(spoken or "").split()))
        return True

    async def _rest(self, job: Any = None) -> None:
        REC.note("rest")

    async def _typed_cancelled(self, job: Any, turn: Any) -> None:
        REC.note("cancelled")


def call_info(call: Any) -> Any:
    if call is None:
        return None
    return {"name": getattr(call, "name", None), "args": plain(getattr(call, "args", None)),
            "origin": getattr(call, "origin", None), "confirmed": bool(getattr(call, "confirmed", False))}


def routed_info(routed: Any) -> Any:
    if routed is None:
        return None
    return {"intent": getattr(getattr(routed, "intent", None), "value", str(getattr(routed, "intent", None))),
            "score": getattr(routed, "score", None), "doubt": getattr(routed, "doubt", ""),
            "speech": getattr(routed, "speech", ""),
            "calls": [call_info(c) for c in getattr(routed, "calls", []) or []],
            "plan": [{"call": call_info(getattr(s, "call", None)), "clause": getattr(s, "clause", None),
                      "count": getattr(s, "count", None)} for s in getattr(routed, "plan", []) or []],
            "ends_session": bool(getattr(routed, "ends_session", False)),
            "sleeps_wake": bool(getattr(routed, "sleeps_wake", False)),
            "halts_speech": bool(getattr(routed, "halts_speech", False))}


def build_turn(text: str, events: list[dict[str, Any]], hold: Any, cards: list[dict[str, Any]], halt: Any,
               stop: Any, error: Any, total_ms: float, plan: Any) -> dict[str, Any]:
    stages: dict[str, float] = {}
    routed = None
    resolutions = []
    seconds = []
    chats = []
    humanised = []
    dispatched = []
    plans = []
    confirm = None
    turn = None
    for e in events:
        if e["kind"] != "stage":
            continue
        stages[e["stage"]] = round(stages.get(e["stage"], 0.0) + float(e["ms"]), 3)
        info = e.get("info")
        if e["stage"] == "route" and routed is None:
            routed = info
        elif e["stage"] == "intent":
            resolutions.append({**(info or {}), "stage_ms": e["ms"], "error_raised": e.get("error")})
        elif e["stage"] == "second_opinion":
            seconds.append(info)
        elif e["stage"] == "chat":
            chats.append({**(info or {}), "stage_ms": e["ms"], "error_raised": e.get("error")})
        elif e["stage"] == "humanise":
            humanised.append(info)
        elif e["stage"] == "execute":
            dispatched.append({**(info or {}), "stage_ms": e["ms"], "error_raised": e.get("error")})
        elif e["stage"] == "plan":
            plans.append(info)
        elif e["stage"] == "confirm":
            confirm = info
        elif e["stage"] == "turn":
            turn = info
    speech = [e for e in events if e["kind"] == "speech"]
    surface = [e for e in events if e["kind"] == "surface"]
    shown = speech[-1]["shown"] if speech else ""
    spoken = speech[-1]["spoken"] if speech else ""
    if not speech:
        lines = [e["payload"]["message"]["text"] for e in surface
                 if e.get("type") == "evt.transcript.message"
                 and isinstance(e.get("payload"), dict)
                 and (e["payload"].get("message") or {}).get("role") == "assistant"]
        shown = lines[-1] if lines else ""
        spoken = ""
    audit = [e for e in events if e["kind"] == "audit"]
    verbs = []
    for a in audit:
        if a.get("tool") == "agent.message":
            continue
        verb = str(a.get("summary") or "").split(" ", 1)[0]
        verbs.append({"verb": verb, "tool": a.get("tool"), "tier": a.get("tier"), "actor": a.get("actor"),
                      "summary": str(a.get("summary") or "")[:400]})
    turn_row = next((a for a in audit if a.get("tool") == "agent.message"), None)
    traps = [{"tool": e["tool"], "part": e["part"], "args": e.get("args"), "kwargs": e.get("kwargs")}
             for e in events if e["kind"] == "trap"]
    model = [{k: v for k, v in e.items() if k not in ("kind", "at")} for e in events if e["kind"] == "model"]
    wait_i = sum(float(m.get("wait_ms") or 0) for m in model if m.get("purpose") == "intent")
    wait_c = sum(float(m.get("wait_ms") or 0) for m in model if m.get("purpose") == "chat")
    raw_stages = dict(stages)
    for key, w in (("intent", wait_i), ("second_opinion", wait_i), ("chat", wait_c), ("turn", wait_i + wait_c)):
        if key in stages and w:
            stages[key] = round(max(0.0, stages[key] - w), 3)
    return {
        "text": text, "total_ms": round(max(0.0, total_ms - wait_i - wait_c), 1), "total_ms_raw": round(total_ms, 1),
        "pacing_wait_ms": round(wait_i + wait_c, 1), "stages_raw": raw_stages, "stages": stages, "routed": routed,
        "second_opinion": seconds, "intent_calls": resolutions, "chat": chats, "humanised": humanised,
        "dispatched": dispatched, "plans": plans, "plan_state": plan, "confirm": confirm, "turn": turn,
        "verbs": verbs, "turn_row": turn_row, "traps": traps, "cards": cards, "hold": hold,
        "states": [e.get("state") for e in events if e["kind"] == "state"],
        "surface": [e.get("type") for e in surface], "shown": shown, "spoken": spoken,
        "model": model, "halt": halt, "stop": stop, "error": error,
        "breaches": [{k: v for k, v in e.items() if k not in ("kind", "at")} for e in events if e["kind"] == "breach"],
        "stores": [e.get("store") for e in events if e["kind"] == "store"],
        "log": [e.get("line") for e in events if e["kind"] == "log"][:60],
    }


def current_traps() -> list[Trap]:
    from core.brain import appindex, executor as ex_mod, memory
    from core.tools import REGISTRY, x_tools
    found: list[Trap] = []

    def add(obj: Any) -> None:
        if isinstance(obj, Trap) and not any(obj is f for f in found):
            found.append(obj)

    for spec in REGISTRY.values():
        cap = getattr(spec.handler, "__wrapped_capability__", None)
        if cap is not None:
            add(cap.run)
            add(cap.describe)
        else:
            add(spec.handler)
        add(spec.resolve)
        add(spec.describe)
    for attr in ("open_path", "open_url", "open_in_vscode", "listening_on_port", "tool_version"):
        add(getattr(ex_mod, attr, None))
    add(getattr(ex_mod.shutil, "which", None))
    add(getattr(ex_mod.subprocess, "Popen", None))
    for attr in ("record", "he_opened_it_himself", "she_has_opened"):
        add(getattr(memory, attr, None))
    add(appindex.launch)
    add(x_tools.plan_targets)
    return found


def matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name or "", p) for p in patterns or [])
