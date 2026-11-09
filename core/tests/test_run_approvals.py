from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = Path(tempfile.mkdtemp(prefix="tessa-run-approvals-"))
HOME = TMP / "home"
ENV = {"USERPROFILE": HOME, "APPDATA": HOME / "AppData" / "Roaming", "LOCALAPPDATA": HOME / "AppData" / "Local",
       "ProgramData": TMP / "ProgramData", "PUBLIC": TMP / "Public"}
for _k, _v in ENV.items():
    _v.mkdir(parents=True, exist_ok=True)
    os.environ[_k] = str(_v)
(HOME / "Documents").mkdir(parents=True, exist_ok=True)

from core.brain.approvals import ApprovalError
from core.brain.executor import Executor
from core.brain.provenance import SessionContext
from core.brain.tools_local import ToolCall
from core.security import gate
from core.tools import coding, langs
from core.tools.base import ToolError

REAL_DATA = coding.DATA
coding.DATA = TMP / "first" / "code"
passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


print("\n(A) where the approved list lives, and that it keeps what it holds\n")
coding.DATA = REAL_DATA
real_db, real_written = gate.store_paths()
check("by default the approved list and ledger are data\\gate.db, and the written record is data\\code\\written.jsonl",
      real_db == ROOT / "data" / "gate.db" and real_written == ROOT / "data" / "code" / "written.jsonl",
      f"{real_db} {real_written}")
coding.DATA = TMP / "first" / "code"
first = gate.store()
check("the store follows the code tools' data folder, so a test that moves it gets its own store",
      first.path == TMP / "first" / "gate.db" and first.written == TMP / "first" / "code" / "written.jsonl", str(first.path))
coding.DATA = TMP / "second" / "code"
check("...and moves with it", gate.store().path == TMP / "second" / "gate.db", str(gate.store().path))
pinned = gate.Store(TMP / "pinned.db", TMP / "pinned.jsonl")
gate.use_store(pinned)
coding.DATA = TMP / "third" / "code"
check("a store set on purpose stays set", gate.store() is pinned)

zero = TMP / "zero.db"
zero.write_bytes(b"")
z = gate.Store(zero, TMP / "none.jsonl")
check("a zero-byte database file opens and gains the approved list", "gate_approved" in z.tables(), str(z.tables()))
z._db.close()

today = TMP / "today.db"
t1 = gate.Store(today, TMP / "none.jsonl")
t1.approve("1" * 64, gate.CODE, "placeholder.py", [{"rel": "placeholder.py", "sha256": "2" * 64}], ["4"], "req-a")
t1.approve("3" * 64, gate.APP, "placeholder.lnk", [{"rel": "placeholder.lnk", "sha256": "4" * 64}], [], "req-b")
t1.record(write(TMP / "ledger" / "one.txt", "placeholder"), "write", "placeholder")
t1._db.close()
copy = TMP / "today-copy.db"
shutil.copyfile(today, copy)
t2 = gate.Store(copy, TMP / "none.jsonl")
check("a copy of a store from this build reopens with every approval and ledger row",
      t2.is_approved("1" * 64) and t2.is_approved("3" * 64) and len(t2.ledger_rows()) == 1)
t2._db.close()
row = sqlite3.connect(str(copy)).execute("SELECT files, argv FROM gate_approved WHERE set_sha = ?", ("1" * 64,)).fetchone()
check("an approval keeps the set of file hashes it covers, and its arguments",
      row == ('[["placeholder.py", "' + "2" * 64 + '"]]', '["4"]'), str(row))

print("\n(B) her code: a card that shows every file, then no card while nothing changes\n")
gate.use_store(gate.Store(TMP / "gate.db", TMP / "written.jsonl"))
CODE = TMP / "Tessa Code"
CODE.mkdir()
coding.DATA = TMP / "data-code"
coding.code_folder = lambda: CODE
coding.documents = lambda: HOME / "Documents"
PY = sys.executable
FOUND_PY = langs.Found("python", "Python", True, exe=PY, where=f"PATH: {PY}", version="Python test")
FOUND_BAT = langs.Found("batch", "batch", True, exe=os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"))
langs.find = lambda key: {"python": FOUND_PY, "batch": FOUND_BAT}.get(key) or langs.Found(
    key, langs.display(key), False, note="absent")
coding.langs.find = langs.find
RAN: list[dict] = []


def fake_run_source(p, spec, argv, timeout_s, *, mine=False):
    RAN.append({"file": str(p), "argv": list(argv), "held": gate.current() is not None})
    return {"spoken": "It ran.", "said": "It ran.", "file": str(p)}


coding.run_source = fake_run_source
SPAWNED: list[list[str]] = []


def fake_spawn(argv, mode, opts):
    SPAWNED.append([str(a) for a in argv])
    return SimpleNamespace(returncode=0, stdout="", stderr="", pid=0)


gate._spawn = fake_spawn
STARTED: list[str] = []
os.startfile = lambda target, *a, **k: STARTED.append(str(target))


def executor() -> Executor:
    return Executor(session=SessionContext(), audit=None)


def say(ex: Executor, name: str, args: dict) -> str:
    return ex.run(ToolCall(name, args, origin="human")) or ""


def cards(ex: Executor) -> list:
    return list(ex.approvals.pending.values())


prog = CODE / "placeholder-sums"
entry = write(prog / "placeholder-sums.py", "placeholder entry text\n")
helper = write(prog / "helpers" / "numbers.py", "placeholder helper text\n")
init = write(prog / "helpers" / "__init__.py", "")
batch = write(prog / "tools" / "go.bat", "placeholder batch text\n")
write(prog / "notes.md", "placeholder notes\n")
write(prog / "data.json", "{}\n")
write(prog / ".venv" / "Lib" / "site.py", "placeholder dependency\n")
ex = executor()
said = say(ex, "code.run_mine", {"path": str(entry)})
pend = cards(ex)
card = pend[0].args if pend else {}
listed = sorted(Path(f["file"]).as_posix() for f in card.get("files", []))
check("new code gets a card and does not run", len(pend) == 1 and pend[0].tool == "code.run_mine" and not RAN, said[:160])
check("the card lists every file of the program that can run, in every subfolder",
      listed == ["helpers/__init__.py", "helpers/numbers.py", "placeholder-sums.py", "tools/go.bat"], str(listed))
check("the card shows the entry's code, and each file's text and sha256",
      card.get("code") == "placeholder entry text\n"
      and all(f["sha256"] == gate.sha256_file(f["path"]) for f in card["files"])
      and card["views"].get(str(Path("helpers") / "numbers.py")) == "placeholder helper text\n"
      and card["views"].get(str(Path("tools") / "go.bat")) == "placeholder batch text\n", str(sorted(card.get("views", {}))))
check("files that only hold data are not listed, and the dependency folder is named as not checked",
      card["unchecked"] == [{"folder": ".venv", "why": "a dependency or build folder", "files": 1}], str(card["unchecked"]))
check("she says how many more files it can load", "and 3 more files it can load, all on the card" in said, said[:220])
check("every file of hers is labelled hers", card["whose"] == "mine" and all(f["hers"] for f in card["files"]))
ex.execute_approved(pend[0].request_id)
check("his approval runs it once, with the files held", len(RAN) == 1 and RAN[0]["held"])
said = say(ex, "code.run_mine", {"path": str(entry)})
check("approved content runs again with no card", len(RAN) == 2 and not cards(ex), said[:160])
said = say(ex, "code.run_file", {"path": str(entry)})
check("...asked through code.run_file too", len(RAN) == 3 and not cards(ex), said[:160])
gate.use_store(gate.Store(TMP / "gate.db", TMP / "written.jsonl"))
said = say(executor(), "code.run_mine", {"path": str(entry)})
check("...and after a restart: a new store on the same file and a new executor still need no card", len(RAN) == 4,
      said[:160])
ex = executor()
write(batch, "placeholder batch text!\n")
said = say(ex, "code.run_mine", {"path": str(entry)})
check("one byte changed in any of its files asks again, and nothing runs", len(RAN) == 4 and len(cards(ex)) == 1,
      said[:160])
pend = cards(ex)
write(helper, "placeholder helper text?\n")
try:
    ex.execute_approved(pend[0].request_id)
    check("a file changed between the card and the run is refused", False)
except ApprovalError as err:
    check("a file changed between the card and the run is refused", len(RAN) == 4 and "changed" in str(err), str(err))
ex.approvals.pending.clear()
say(ex, "code.run_mine", {"path": str(entry)})
pend = cards(ex)
init.unlink()
try:
    ex.execute_approved(pend[0].request_id)
    check("a file taken away between the card and the run is refused", False)
except ApprovalError as err:
    check("a file taken away between the card and the run is refused", len(RAN) == 4 and "gone since the card" in str(err),
          str(err))
ex.approvals.pending.clear()

his = HOME / "Documents" / "placeholder-project"
his_entry = write(his / "main.py", "placeholder his entry\n")
write(his / "lib" / "part.py", "placeholder his part\n")
say(ex, "code.run_file", {"path": str(his_entry)})
pend = cards(ex)
check("his own program gets the same card, every file listed and labelled his",
      len(pend) == 1 and sorted(Path(f["file"]).as_posix() for f in pend[0].args["files"]) == ["lib/part.py", "main.py"]
      and pend[0].args["whose"] == "yours" and len(RAN) == 4)
ex.approvals.pending.clear()

print("\n(C) folders too big to show in full are refused, not cut short\n")
wide = CODE / "placeholder-wide"
wide_entry = write(wide / "placeholder-wide.py", "placeholder wide\n")
for i in range(5):
    write(wide / f"part{i}.py", f"placeholder part {i}\n")
saved_max = gate.MAX_FILES
gate.MAX_FILES = 4
said = say(ex, "code.run_mine", {"path": str(wide_entry)})
check("more files that can run than the card shows: refused, no card, nothing ran",
      not cards(ex) and len(RAN) == 4 and "too many" in said, said[:200])
gate.MAX_FILES = saved_max
saved_walk = gate.MAX_WALK_ENTRIES
gate.MAX_WALK_ENTRIES = 30
for i in range(40):
    write(wide / "pictures" / f"p{i}.png", "placeholder")
said = say(ex, "code.run_mine", {"path": str(wide_entry)})
check("a folder with more entries than she checks: refused, no card, nothing ran",
      not cards(ex) and len(RAN) == 4 and "too many to check" in said, said[:200])
gate.MAX_WALK_ENTRIES = saved_walk

print("\n(D) his dictated commands: the card lists every file of the project the command runs\n")
from core.tools import REGISTRY

check("the files, their text and what was not checked are all frozen on the card",
      {"files", "views", "unchecked"} <= set(REGISTRY["shell.execute"].frozen))
proj = TMP / "work" / "placeholder-site"
write(proj / "package.json", '{"name": "placeholder", "scripts": {"start": "node src/app.js"}}\n')
app_js = write(proj / "src" / "app.js", "placeholder app\n")
write(proj / "lib" / "util.js", "placeholder util\n")
write(proj / "README.md", "placeholder readme\n")
write(proj / "node_modules" / "dep" / "index.js", "placeholder dependency\n")
write(proj / "node_modules" / "dep" / "package.json", "{}\n")
say(ex, "shell.execute", {"command": "npm run start", "cwd": str(proj)})
pend = cards(ex)
files = sorted(Path(f["file"]).as_posix() for f in pend[0].args["files"]) if pend else []
check("a package command's card lists the package file and every project file that can run",
      files == ["lib/util.js", "package.json", "src/app.js"], str(files))
check("...and names the dependency folder it did not check, with its file count",
      pend[0].args["unchecked"] == [{"folder": "node_modules", "why": "a dependency or build folder", "files": 2}],
      str(pend[0].args.get("unchecked")))
n0 = len(SPAWNED)
write(app_js, "placeholder app!\n")
try:
    ex.execute_approved(pend[0].request_id)
    check("a project file changed after the card: refused, nothing ran", False)
except ApprovalError:
    check("a project file changed after the card: refused, nothing ran", len(SPAWNED) == n0)
say(ex, "shell.execute", {"command": "npm run start", "cwd": str(proj)})
pend = cards(ex)
write(proj / "lib" / "late.js", "placeholder late\n")
try:
    ex.execute_approved(pend[0].request_id)
    check("a project file added after the card: refused, nothing ran", False)
except ApprovalError:
    check("a project file added after the card: refused, nothing ran", len(SPAWNED) == n0)
say(ex, "shell.execute", {"command": "npm run start", "cwd": str(proj)})
ex.execute_approved(cards(ex)[0].request_id)
check("unchanged since the card, it runs once", len(SPAWNED) == n0 + 1 and SPAWNED[-1][1:] == ["/c", "npm run start"],
      str(SPAWNED[-1:]))
scr = TMP / "work" / "placeholder-script"
write(scr / "run_me.py", "placeholder script\n")
write(scr / "beside.py", "placeholder beside\n")
say(ex, "shell.execute", {"command": "python run_me.py", "cwd": str(scr)})
pend = cards(ex)
check("a script he names: its folder's other files are on the card too",
      sorted(Path(f["file"]).as_posix() for f in pend[0].args["files"]) == ["beside.py", "run_me.py"],
      str([f["file"] for f in pend[0].args["files"]]))
ex.approvals.pending.clear()
SYS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
where = SYS / "where.exe"
if where.is_file():
    empty = TMP / "work" / "empty"
    empty.mkdir()
    say(ex, "shell.execute", {"command": f'"{where}" placeholder', "cwd": str(empty)})
    pend = cards(ex)
    check("a program in Windows' own folder is listed by itself; its admin-only folder is not walked",
          [Path(f["path"]).name.lower() for f in pend[0].args["files"]] == ["where.exe"],
          str([f["file"] for f in pend[0].args["files"]]))
    ex.approvals.pending.clear()
repo = TMP / "work" / "placeholder-repo"
write(repo / ".git" / "config", "[core]\n\tbare = false\n")
write(repo / ".git" / "hooks" / "pre-commit", "placeholder hook\n")
write(repo / ".git" / "hooks" / "pre-commit.sample", "placeholder sample\n")
write(repo / "f.txt", "placeholder\n")
say(ex, "shell.execute", {"command": "git commit -m placeholder", "cwd": str(repo)})
pend = cards(ex)
hooks = [Path(f["file"]).as_posix() for f in pend[0].args["files"]] if pend else []
check("a git command he dictates: the card lists the repository's hooks, which git would run, and not the samples",
      hooks == [".git/hooks/pre-commit"], str(hooks))
check("...and says that git's own settings are not checked for his commands",
      any(u["folder"] == "git's own settings" for u in pend[0].args["unchecked"]), str(pend[0].args.get("unchecked")))
ex.approvals.pending.clear()

print("\n(E) what keeps working with no card\n")
from core.brain import appindex, memory, tools_local
from core.tools import browser, gittools

memory.record = lambda *a, **k: None
memory.he_opened_it_himself = lambda *a, **k: False
n0 = len(SPAWNED)
tools_local._pid_name(4)
tools_local.listening_on_port(1)
got = [Path(a[0]).name.lower() for a in SPAWNED[n0:]]
check("her helper programs and system queries start with no card, each Windows' own copy",
      got == ["tasklist.exe", "netstat.exe"] and all(Path(a[0]).parent == SYS for a in SPAWNED[n0:]) and not cards(ex),
      str(SPAWNED[n0:]))
NOTEPAD = SYS / "notepad.exe"


class FakeIndex:
    def __init__(self, entries):
        self._e = list(entries)

    def entries(self):
        return list(self._e)

    def resolve(self, name):
        return [x for x in self._e if x.name.lower() == str(name).lower()], "exact"


pad = SimpleNamespace(kind="exe", name="Notepad", launch=str(NOTEPAD))
appindex.get_index = lambda: FakeIndex([pad])
m0 = len(STARTED)
said = say(ex, "app.open", {"app": "Notepad"})
check("one of his installed apps, opened by name, opens with no card",
      STARTED[m0:] == [str(NOTEPAD)] and not cards(ex), said[:160])


class FakeContext:
    pages: list = []

    def on(self, *a, **k):
        return None

    def set_default_timeout(self, *a, **k):
        return None

    def add_init_script(self, *a, **k):
        return None

    def close(self):
        return None


class FakeChromium:
    def __init__(self):
        self.calls: list[dict] = []

    def launch_persistent_context(self, **opts):
        self.calls.append(opts)
        return FakeContext()


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()

    def stop(self):
        return None


fake_pw = FakePlaywright()
browser.SESSION._require_playwright = lambda: (lambda: SimpleNamespace(start=lambda: fake_pw))
browser.SESSION._record_pid = lambda: None
browser.SESSION._start_reaper = lambda: None
browser._reap_profile_chrome = lambda profile: ""
seen0 = len(gate.SEEN)
browser.SESSION.context()
launched = fake_pw.chromium.calls
check("Chrome for her X work starts through the gate with no card, on her own profile, by channel",
      len(launched) == 1 and launched[0].get("channel") == "chrome"
      and Path(launched[0]["user_data_dir"]) == browser.DEFAULT_PROFILE
      and any(s["kind"] == gate.OWN and s["what"] == "her browser" for s in gate.SEEN[seen0:]) and not cards(ex),
      str(launched))
browser.SESSION.close(reason="test")
GITS: list[list[str]] = []


def fake_run_git(argv, cwd, timeout):
    GITS.append(list(argv))
    return 0, "## main\n"


gittools._run_git = fake_run_git
gittools.git_exe = lambda: "git.exe"
gittools.NO_HOOKS = TMP / "no-hooks"
gittools.git_repo("status", str(repo), provenance="human")
argv = GITS[-1] if GITS else []
check("her git runs with the repository's hooks turned off: hooks point at an empty folder, the file monitor is off",
      f"core.hooksPath={TMP / 'no-hooks'}" in argv and "core.fsmonitor=false" in argv
      and not any((TMP / "no-hooks").iterdir()), str(argv[:9]))

print("\n(F) what she can say before any card, and what the card adds\n")
loose = TMP / "work" / "loose"
rb = write(loose / "placeholder.rb", "placeholder ruby\n")
ex = executor()
said = say(ex, "code.run_file", {"path": str(rb)})
check("a language that is not installed is named before any card, and nothing is put on one",
      "Ruby is not installed" in said and not cards(ex), said[:160])
bat = write(loose / "placeholder.bat", "placeholder batch\n")
said = say(ex, "code.run_file", {"path": str(bat), "argv": ["x"]})
check("a batch file given arguments is refused before any card", "takes no arguments" in said and not cards(ex),
      said[:160])
said = say(ex, "code.run_mine", {"path": str(bat), "argv": ["x"]})
check("...through code.run_mine too", "takes no arguments" in said and not cards(ex), said[:160])
fetched = write(loose / "fetched.py", "placeholder fetched\n")
with open(str(fetched) + ":Zone.Identifier", "w", encoding="utf-8") as fh:
    fh.write("[ZoneTransfer]\nZoneId=3\n")
said = say(ex, "code.run_file", {"path": str(fetched)})
pend = cards(ex)
check("a file Windows marks as downloaded: the card says so, frozen, and her line says so",
      len(pend) == 1 and "fetched.py" in pend[0].args.get("downloaded", "") and "downloaded" in pend[0].frozen
      and "downloaded from the internet" in said, said[:200])
ex.approvals.pending.clear()
heavy = CODE / "placeholder-heavy"
heavy_entry = write(heavy / "placeholder-heavy.py", "placeholder heavy entry\n")
for i in range(6):
    write(heavy / f"part{i}.py", chr(1) * 60000)
say(ex, "code.run_mine", {"path": str(heavy_entry)})
pend = cards(ex)
card = pend[0].args if pend else {}
whole = gate.json_bytes({k: v for k, v in card.items() if k != "views"}) + sum(
    gate.json_bytes([str(f["file"]), Path(f["path"]).read_text(encoding="utf-8")]) for f in card.get("files", []))
sent = gate.json_bytes(card)
print(f"  note  the heavy card: {sent} bytes sent, {whole} bytes if every file's text were on it")
check("files whose text would push the card past the 1 MiB frame limit are listed by sha256 only, and the card fits",
      len(pend) == 1 and whole > 1024 * 1024 and sent <= gate.MAX_CARD_BYTES and len(card["files"]) == 7
      and sum(1 for f in card["files"] if "too long to show" in f.get("view", "")) >= 1
      and all(f["sha256"] == gate.sha256_file(f["path"]) for f in card["files"]), f"{sent} {whole}")
ex.approvals.pending.clear()
gittools.NO_HOOKS = None
coding.DATA = TMP / "hooks-follow" / "code"
gate._STORE.clear()
check("git's empty hooks folder sits beside the store, so it follows the data folder too",
      gittools.no_hooks_dir() == TMP / "hooks-follow" / "git-no-hooks")

print(f"\n{passed} passed, {failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
