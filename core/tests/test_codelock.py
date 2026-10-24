from __future__ import annotations

import ast
import importlib
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core import codelock

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def refused(name: str) -> bool:
    try:
        importlib.import_module(name)
    except codelock.MixedCode:
        return True
    return False


print("\n(1) what she loads when she starts\n")
names = codelock.module_names()
check("typed_turn, the module the voice round caught half-loaded, is loaded at start", "core.brain.typed_turn" in names)
check("the router, executor, intent model and the tool modules are in the list",
      all(n in names for n in ("core.brain.router", "core.brain.executor", "core.brain.intent_model",
                               "core.tools.shell", "core.system.capability")))
check("no test, exam, server or __main__ module is loaded at start",
      not [n for n in names if n.startswith(("core.tests", "core.exam")) or n in ("core.server", "core.test_auth")
           or n.endswith(".__main__")])
check("the calendar's command line is never imported at start", "core.gcal.__main__" not in names)

print("\n(2) a fake package: loaded once, then its files change on disk\n")
TMP = Path(tempfile.mkdtemp(prefix="tessa-codelock-"))
pkg = TMP / "fakecore"
write(pkg / "__init__.py", "")
write(pkg / "server.py", "raise SystemExit('the server was imported')\n")
write(pkg / "brain" / "__init__.py", "")
write(pkg / "brain" / "router.py", "VALUE = 1\n")
write(pkg / "brain" / "turn.py", "from fakecore.brain.router import VALUE\nSAID = VALUE\n")
write(pkg / "brain" / "__main__.py", "raise SystemExit('a command line was imported')\n")
write(pkg / "voice" / "__init__.py", "")
write(pkg / "voice" / "late.py", "LATE = 1\n")
write(pkg / "voice" / "steady.py", "STEADY = 1\n")
sys.path.insert(0, str(TMP))
snap = codelock.snapshot(pkg)
importlib.import_module("fakecore.brain.router")
held = codelock.lock(snap, core=pkg)
check("the turn module is loaded at start", "fakecore.brain.turn" in held.loaded, str(held.loaded))
check("the router, loaded before the lock, is counted as already loaded", "fakecore.brain.router" in held.already)
check("server and __main__ modules are never imported",
      "fakecore.server" not in sys.modules and "fakecore.brain.__main__" not in sys.modules)
check("voice modules are left for start-up, not imported by the lock",
      "fakecore.voice.late" in held.later and "fakecore.voice.late" not in sys.modules)
time.sleep(0.05)
write(pkg / "brain" / "router.py", "VALUE = 2\nNEW = 'added after she started'\n")
write(pkg / "brain" / "turn.py", "from fakecore.brain.router import NEW\nSAID = NEW\n")
write(pkg / "voice" / "late.py", "LATE = 'changed after she started'\n")
write(pkg / "brain" / "added.py", "ADDED = 1\n")
turn = importlib.import_module("fakecore.brain.turn")
check("a module loaded at start keeps the code it started with", turn.SAID == 1, repr(turn.SAID))
check("a module changed after start is refused, never half-loaded", refused("fakecore.voice.late"))
check("a module created after start is refused", refused("fakecore.brain.added"))
check("an unchanged module that was never loaded still imports", importlib.import_module("fakecore.voice.steady").STEADY == 1)
check("the refusals are recorded on the lock", held.refused == ["fakecore.voice.late", "fakecore.brain.added"],
      str(held.refused))
check("the lock is the one installed for that package", codelock.current() is not None)
check("the finder leaves other packages alone", importlib.import_module("json") is not None)

print("\n(3) a file that changes while she is starting stops the start\n")
pkg2 = TMP / "fakecore2"
write(pkg2 / "__init__.py", "")
write(pkg2 / "brain" / "__init__.py", "")
write(pkg2 / "brain" / "router.py", "VALUE = 1\n")
snap2 = codelock.snapshot(pkg2)
importlib.import_module("fakecore2.brain.router")
time.sleep(0.05)
write(pkg2 / "brain" / "router.py", "VALUE = 'changed during start-up'\n")
try:
    codelock.lock(snap2, core=pkg2)
    stopped = False
except codelock.MixedCode as exc:
    stopped = "while she was starting" in str(exc)
check("a module that changed between the snapshot and the lock is refused at start", stopped)
for m in [m for m in sys.meta_path if isinstance(m, codelock._Finder) and m.lock.package in ("fakecore", "fakecore2")]:
    sys.meta_path.remove(m)
sys.path.remove(str(TMP))

print("\n(4) the daemon takes the snapshot early and the lock before main()\n")
src = (ROOT / "core" / "server.py").read_text(encoding="utf-8")
tree = ast.parse(src)
snap_line = next((n.lineno for n in tree.body if isinstance(n, ast.Assign)
                  and ast.unparse(n.value) == "codelock.snapshot()"), None)
proto_line = next((n.lineno for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "tessa_protocol"), None)
first_brain = min((n.lineno for n in tree.body if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("core.")
                   and n.module != "core"), default=None)
check("the snapshot is taken before any other core module is imported",
      snap_line is not None and proto_line is not None and first_brain is not None
      and snap_line < proto_line and snap_line < first_brain, f"{snap_line} < {proto_line}, {first_brain}")
main_block = next(n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test))
block = ast.unparse(main_block)
check("the main path locks the code after the guard and before main() runs",
      block.index("lifecycle.after_guard") < block.index("codelock.lock(_CODE_AT_START") < block.index("asyncio.run(main())"))
check("mixed code at start is a refusal that ends the process",
      "except codelock.MixedCode" in block and "lifecycle.finish" in block.split("except codelock.MixedCode", 1)[1].split("_code, _why", 1)[0])
typed = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_typed_turn")
check("the typed turn still looks typed_turn up when it runs (the exam patches it there)",
      "from core.brain.typed_turn import run_typed_turn" in ast.unparse(typed))

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
