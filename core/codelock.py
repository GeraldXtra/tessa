from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

CORE = Path(__file__).resolve().parent
SKIP_DIRS = frozenset({"__pycache__", "tests", "exam"})
SKIP_MODULES = frozenset({"server", "test_auth"})
LATER = ("voice", "bus")


class MixedCode(ImportError):
    pass


def _key(path: Any) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _stat(path: Any) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def snapshot(core: Path = CORE) -> dict[str, tuple[int, int] | None]:
    out: dict[str, tuple[int, int] | None] = {}
    for dirpath, dirnames, filenames in os.walk(core):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in filenames:
            if f.endswith(".py"):
                p = os.path.join(dirpath, f)
                out[_key(p)] = _stat(p)
    return out


def module_names(core: Path = CORE) -> list[str]:
    names: list[str] = []
    root = core.parent
    for dirpath, dirnames, filenames in os.walk(core):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        here = Path(dirpath)
        if not (here / "__init__.py").exists():
            dirnames[:] = []
            continue
        pkg = ".".join(here.relative_to(root).parts)
        names.append(pkg)
        for f in sorted(filenames):
            if f.endswith(".py") and f not in ("__init__.py", "__main__.py"):
                names.append(f"{pkg}.{f[:-3]}")
    skip = {f"{core.name}.{m}" for m in SKIP_MODULES}
    return [n for n in names if n not in skip]


@dataclass
class Lock:
    stats: dict[str, tuple[int, int] | None]
    core: Path = CORE
    taken_at: float = field(default_factory=time.time)
    loaded: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    later: list[str] = field(default_factory=list)
    ms: float = 0.0

    def changed(self, path: Any) -> bool:
        key = _key(path)
        if key not in self.stats:
            return True
        return _stat(key) != self.stats[key]

    @property
    def package(self) -> str:
        return self.core.name

    def ours(self, name: str) -> bool:
        return name == self.package or name.startswith(self.package + ".")

    def under_core(self, path: Any) -> bool:
        key = _key(path)
        base = _key(self.core)
        return key == base or key.startswith(base.rstrip("\\/") + os.sep)

    def describe(self) -> str:
        bits = [f"{len(self.loaded)} modules loaded now", f"{len(self.already)} already loaded",
                f"{len(self.later)} left for start-up ({', '.join(f'{self.package}.{p}' for p in LATER)})"]
        if self.failed:
            bits.append(f"{len(self.failed)} could not load: " + ", ".join(n for n, _ in self.failed[:6]))
        return f"code: one version until a restart - {'; '.join(bits)}; {self.ms:.0f} ms"


class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, lock: Lock) -> None:
        self.lock = lock

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if not self.lock.ours(fullname):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        origin = getattr(spec, "origin", None) if spec is not None else None
        if not origin or not str(origin).endswith(".py") or not self.lock.under_core(origin):
            return None
        if self.lock.changed(origin):
            self.lock.refused.append(fullname)
            raise MixedCode(f"{fullname} ({origin}) changed on disk after she started; she runs the code she "
                            f"started with until she is restarted")
        return None


def lock(stats: dict[str, tuple[int, int] | None], *, core: Path = CORE,
         log: Callable[[str], None] | None = None) -> Lock:
    t0 = time.perf_counter()
    held = Lock(stats=dict(stats), core=core)
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if not f or not held.ours(name):
            continue
        if not str(f).endswith(".py") or not held.under_core(f):
            continue
        held.already.append(name)
        if held.changed(f):
            raise MixedCode(f"{name} ({f}) changed on disk while she was starting; start her again")
    sys.meta_path[:] = [m for m in sys.meta_path if not (isinstance(m, _Finder) and m.lock.package == held.package)]
    sys.meta_path.insert(0, _Finder(held))
    later = tuple(f"{held.package}.{p}" for p in LATER)
    for name in module_names(core):
        if name in sys.modules:
            continue
        if any(name == p or name.startswith(p + ".") for p in later):
            held.later.append(name)
            continue
        try:
            importlib.import_module(name)
            held.loaded.append(name)
        except MixedCode:
            raise
        except Exception as exc:
            held.failed.append((name, f"{type(exc).__name__}: {exc}"[:200]))
    held.ms = (time.perf_counter() - t0) * 1000.0
    if log is not None:
        log(held.describe())
        for name, why in held.failed:
            log(f"!! code: {name} did not load at start ({why})")
    return held


def current() -> Lock | None:
    for m in sys.meta_path:
        if isinstance(m, _Finder):
            return m.lock
    return None
