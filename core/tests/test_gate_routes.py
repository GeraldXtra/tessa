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

TMP = Path(tempfile.mkdtemp(prefix="tessa-gate-routes-"))
HOME = TMP / "home"
ENV = {"USERPROFILE": HOME, "APPDATA": HOME / "AppData" / "Roaming", "LOCALAPPDATA": HOME / "AppData" / "Local",
       "ProgramData": TMP / "ProgramData", "PUBLIC": TMP / "Public"}
ORIG = {k: os.environ.get(k) for k in ENV}
for _k, _v in ENV.items():
    _v.mkdir(parents=True, exist_ok=True)
    os.environ[_k] = str(_v)
(HOME / "Documents").mkdir(parents=True, exist_ok=True)

from core.brain.approvals import ApprovalError
from core.brain.executor import Executor
from core.brain.provenance import SessionContext
from core.brain.tools_local import ToolCall
from core.security import gate
from core.system.abilities import create
from core.tools import coding, files, langs
from core.tools.base import ToolError, ToolRedirect

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


CODE = TMP / "Tessa Code"
CODE.mkdir()
WORK = TMP / "work"
WORK.mkdir()
coding.DATA = TMP / "data-code"
coding.code_folder = lambda: CODE
coding.documents = lambda: HOME / "Documents"
gate.use_store(gate.Store(TMP / "gate.db", TMP / "written.jsonl"))
PY = sys.executable
FOUND_PY = langs.Found("python", "Python", True, exe=PY, where=f"PATH: {PY}", version="Python test")


def fake_find(key: str):
    if key == "python":
        return FOUND_PY
    return langs.Found(key, langs.display(key), False, note="not installed")


langs.find = fake_find
coding.langs.find = fake_find
RAN: list[dict] = []


def fake_run_source(p, spec, argv, timeout_s, *, mine=False):
    RAN.append({"file": str(p), "argv": list(argv), "mine": mine, "held": gate.current() is not None})
    return {"spoken": "It ran.", "said": "It ran.", "file": str(p)}


coding.run_source = fake_run_source
SPAWNED: list[list[str]] = []


def fake_spawn(argv, mode, opts):
    SPAWNED.append([str(a) for a in argv])
    return SimpleNamespace(returncode=0, stdout="", stderr="", pid=0)


gate._spawn = fake_spawn
STARTED: list[str] = []
os.startfile = lambda target, *a, **k: STARTED.append(str(target))


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def executor() -> Executor:
    return Executor(session=SessionContext(), audit=None)


def say(ex: Executor, name: str, args: dict, origin: str = "human") -> str:
    if ex.session.external_content_in_context:
        ex.session.clear_external()
        ex.last_injection = None
    return ex.run(ToolCall(name, args, origin=origin)) or ""


def cards(ex: Executor) -> list:
    return list(ex.approvals.pending.values())


print("\n(A) the approved list and the ledger: one store, forward-only\n")
a = gate.Store(TMP / "empty.db", TMP / "none.jsonl")
tables = set(a.tables())
check("an empty database gains the approved list, the ledger and the meta table",
      {"gate_meta", "gate_approved", "gate_ledger"} <= tables, str(tables))
ver = sqlite3.connect(str(TMP / "empty.db")).execute("PRAGMA user_version").fetchone()[0]
check("and is marked schema 1", ver == 1, str(ver))

old = TMP / "older.db"
db = sqlite3.connect(str(old))
db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
db.executemany("INSERT INTO notes (body) VALUES (?)", [("first",), ("second",), ("third",)])
db.commit()
db.close()
b = gate.Store(old, TMP / "none.jsonl")
b.tables()
db = sqlite3.connect(str(old))
rows = db.execute("SELECT body FROM notes ORDER BY id").fetchall()
names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
db.close()
check("an existing database keeps its own tables and every row, and gains the new tables",
      rows == [("first",), ("second",), ("third",)] and {"notes", "gate_approved", "gate_ledger"} <= names,
      f"{rows} {names}")

made = gate.Store(TMP / "today.db", TMP / "none.jsonl")
made.approve("a" * 64, gate.CODE, "x.py", [{"rel": "x.py", "sha256": "b" * 64}], [], "req1")
made.record(write(WORK / "ledger-one.txt", "placeholder"), "write", "test")
made._db.close()
copy = TMP / "today-copy.db"
shutil.copyfile(TMP / "today.db", copy)
c = gate.Store(copy, TMP / "none.jsonl")
check("a copy of a schema-1 store reopens with its approval and its ledger row",
      c.is_approved("a" * 64) and len(c.ledger_rows()) == 1, str(c.ledger_rows()))
c._db.close()
c2 = gate.Store(copy, TMP / "none.jsonl")
check("reopening it again duplicates nothing", len(c2.ledger_rows()) == 1 and c2.is_approved("a" * 64))
c2._db.close()

live = ROOT / "data" / "gate.db"
if live.exists():
    lc = TMP / "live-copy.db"
    shutil.copyfile(live, lc)
    before_n = sqlite3.connect(str(lc)).execute("SELECT count(*) FROM gate_ledger").fetchone()[0]
    lcs = gate.Store(lc, TMP / "none.jsonl")
    check("a copy of this machine's own gate store opens and keeps every ledger row",
          len(lcs.ledger_rows()) == before_n, f"{len(lcs.ledger_rows())} vs {before_n}")
else:
    print("  note  data\\gate.db does not exist on this machine yet, so there is no live store to copy")

newer = TMP / "newer.db"
db = sqlite3.connect(str(newer))
db.execute("CREATE TABLE gate_approved (set_sha TEXT PRIMARY KEY, kind TEXT NOT NULL, entry TEXT NOT NULL, "
           "files TEXT NOT NULL, argv TEXT NOT NULL, request_id TEXT NOT NULL, at TEXT NOT NULL)")
db.execute("INSERT INTO gate_approved VALUES (?, 'code', 'x', '[]', '[]', 'r', 't')", ("c" * 64,))
db.execute("PRAGMA user_version = 2")
db.commit()
db.close()
n = gate.Store(newer, TMP / "none.jsonl")
check("a store from a newer schema approves nothing, even a set it lists", not n.is_approved("c" * 64))
n.approve("d" * 64, gate.CODE, "y", [], [], "r")
db = sqlite3.connect(str(newer))
count = db.execute("SELECT count(*) FROM gate_approved").fetchone()[0]
ver = db.execute("PRAGMA user_version").fetchone()[0]
db.close()
check("and writes nothing into it, and leaves its version alone", count == 1 and ver == 2, f"{count} {ver}")

wj = TMP / "written-old.jsonl"
wj.write_text('{"path": "C:/placeholder/old-one.py", "sha256": "' + "e" * 64 + '", "at": "2026-10-01T10:00:00"}\n'
              "not json\n", encoding="utf-8")
w = gate.Store(TMP / "imported.db", wj)
first = w.ledger_rows()
w._db.close()
w2 = gate.Store(TMP / "imported.db", wj)
check("programs she wrote before the ledger are imported once as her writes",
      len(first) == 1 and first[0][1] == "e" * 64 and first[0][2] == "write" and len(w2.ledger_rows()) == 1,
      str(first))
w2._db.close()
check("everything in her code folder counts as hers, ledger or not", gate.store().hers(CODE / "any" / "old.py"))
check("a file nobody recorded, outside her folder, is not hers", not gate.store().hers(WORK / "nobody.py"))

print("\n(B) routes 1-3, 15, 17: code runs only from a card or the approved list\n")
ex = executor()
loose = write(WORK / "loose" / "sum_two.py", "placeholder one\n")
said = say(ex, "code.run_file", {"path": str(loose)})
pend = cards(ex)
check("route 1: a source file nobody approved goes to a card and does not run",
      len(pend) == 1 and pend[0].tool == "code.run_mine" and not RAN, said[:160])
card = pend[0].args
check("the card shows the code itself and its sha256",
      card.get("code") == "placeholder one\n" and card.get("sha256") == gate.sha256_file(loose))
check("the card names whose it is: not hers", card.get("whose") == "yours" and card["files"][0]["hers"] is False)
ex.approvals.pending.clear()

prog = CODE / "two-files"
entry = write(prog / "two-files.py", "placeholder entry\n")
helper = write(prog / "helper.py", "placeholder helper\n")
write(prog / "notes.txt", "placeholder notes\n")
write(prog / "node_modules" / "dep" / "index.js", "placeholder dependency\n")
say(ex, "code.run_mine", {"path": str(entry)})
pend = cards(ex)
card = pend[0].args if pend else {}
listed = sorted(f["file"] for f in card.get("files", []))
check("route 2: the card lists every file of the program that can run, not only the entry",
      listed == ["helper.py", "two-files.py"], str(listed))
check("each file on the card has its sha256 and its text to read",
      all(f["sha256"] == gate.sha256_file(f["path"]) for f in card["files"])
      and card["views"] == {"helper.py": "placeholder helper\n", "two-files.py": "placeholder entry\n"})
check("a dependency folder is named as not checked, with its file count",
      card["unchecked"] == [{"folder": "node_modules", "why": "a dependency or build folder", "files": 1}],
      str(card["unchecked"]))
check("the card's file list and set hash cannot be edited",
      {"files", "views", "set_sha256", "code", "sha256"} <= set(pend[0].frozen))
rid = pend[0].request_id
try:
    ex.execute_approved(rid, edited={**card, "files": card["files"][:1]})
    check("an edited file list is refused", False)
except ApprovalError:
    check("an edited file list is refused", not RAN)
ex.approvals.pending.clear()

say(ex, "code.run_mine", {"path": str(entry)})
rid = cards(ex)[0].request_id
write(helper, "placeholder helper!\n")
try:
    ex.execute_approved(rid)
    check("a file of the program changed after the card is refused", False)
except ApprovalError as err:
    check("a file of the program changed after the card is refused", not RAN and "changed" in str(err), str(err))
ex.approvals.pending.clear()

say(ex, "code.run_mine", {"path": str(entry)})
rid = cards(ex)[0].request_id
write(prog / "late.py", "placeholder late\n")
try:
    ex.execute_approved(rid)
    check("a file that can run, added after the card, is refused", False)
except ApprovalError as err:
    check("a file that can run, added after the card, is refused", not RAN and "new since the card" in str(err),
          str(err))
(prog / "late.py").unlink()
ex.approvals.pending.clear()

say(ex, "code.run_mine", {"path": str(entry)})
pend = cards(ex)
res = ex.execute_approved(pend[0].request_id)
check("approved on the card, it runs once, with every file held and re-checked",
      len(RAN) == 1 and RAN[0]["held"] and gate.store().is_approved(pend[0].args["set_sha256"]), str(RAN))
said = say(ex, "code.run_mine", {"path": str(entry)})
check("asked again unchanged, it runs with no card", len(RAN) == 2 and not cards(ex), said[:160])
said = say(ex, "code.run_file", {"path": str(entry)})
check("the same through code.run_file: no card", len(RAN) == 3 and not cards(ex), said[:160])
write(helper, "placeholder helper.\n")
say(ex, "code.run_mine", {"path": str(entry)})
check("one byte changed in an approved file means a new card and no run", len(RAN) == 3 and len(cards(ex)) == 1)
ex.approvals.pending.clear()
say(ex, "code.run_mine", {"path": str(entry), "argv": ["4"]})
check("the same files with other arguments are another set: a card", len(RAN) == 3 and len(cards(ex)) == 1)
ex.approvals.pending.clear()

made_py = WORK / "made" / "made.py"
made_py.parent.mkdir()
create.create_file(str(made_py), "placeholder made\n")
say(ex, "code.run_file", {"path": str(made_py)})
pend = cards(ex)
check("route 3: a file made by create_file then run goes to a card, labelled hers",
      len(pend) == 1 and pend[0].args["files"][0]["hers"] is True and len(RAN) == 3)
ex.approvals.pending.clear()

txt = write(WORK / "renamed" / "plain.txt", "placeholder renamed\n")
files.rename(str(txt), "plain.py")
moved = write(WORK / "moved-from" / "m.py", "placeholder moved\n")
files.move(str(moved), str(WORK / "renamed"))
copied = write(WORK / "copy-from" / "c.py", "placeholder copied\n")
files.copy(str(copied), str(WORK / "renamed"))
say(ex, "code.run_file", {"path": str(WORK / "renamed" / "plain.py")})
pend = cards(ex)
hers_by_file = {f["file"]: f["hers"] for f in pend[0].args["files"]} if pend else {}
check("route 15: renamed, moved and copied files are in the ledger and labelled hers on the card",
      hers_by_file == {"c.py": True, "m.py": True, "plain.py": True}, str(hers_by_file))
ops = {r[2] for r in gate.store().ledger_rows()}
check("the ledger names each operation", {"rename", "move", "copy", "create"} <= ops, str(ops))
ex.approvals.pending.clear()

unseen = write(WORK / "unseen" / "u.py", "placeholder unseen\n")
say(ex, "code.run_file", {"path": str(unseen)})
pend = cards(ex)
check("route 17: a file some program wrote, recorded nowhere, still needs a card and is not labelled hers",
      len(pend) == 1 and pend[0].args["files"][0]["hers"] is False and len(RAN) == 3)
ex.approvals.pending.clear()

n0 = len(SPAWNED)
try:
    gate.start(gate.Start(kind=gate.CODE, why="test", argv=[PY, str(unseen)], mode="popen"))
    check("the gate refuses to start code with no card or approval behind it", False)
except ToolError:
    check("the gate refuses to start code with no card or approval behind it", len(SPAWNED) == n0)

print("\n(C) routes 5-8, 14: opening a file, launching by name, VS Code\n")
from core.brain import appindex, memory
from core.system.abilities import code as vscode

memory.record = lambda *a, **k: None
memory.he_opened_it_himself = lambda *a, **k: False
doc = write(WORK / "docs" / "letter.txt", "placeholder letter\n")
files.open_path(str(doc))
check("route 5: a document still opens, through the gate", STARTED[-1:] == [str(doc)])
refused = []
n0 = len(STARTED)
for name in ("a.bat", "a.cmd", "a.exe", "a.msi", "a.lnk", "a.url", "a.reg", "a.hta", "a.docm", "a.vbs", "a.ps1x"):
    p = write(WORK / "openers" / name, "placeholder\n")
    try:
        files.open_path(str(p))
    except (ToolError, ToolRedirect):
        refused.append(name)
check("route 5: every file Windows would run when opened is refused, and nothing opens",
      len(refused) == 11 and len(STARTED) == n0, str(refused))
src = write(WORK / "openers" / "s.py", "placeholder source\n")
ran0 = len(RAN)
say(ex, "fs.open", {"path": str(src)})
check("route 5: a source file asked to open goes to the code tools, so to a card", len(cards(ex)) == 1
      and cards(ex)[0].tool == "code.run_mine" and len(RAN) == ran0)
ex.approvals.pending.clear()
said = say(ex, "app.open_folder", {"path": str(WORK / "openers" / "a.exe")})
check("route 6: the older open-a-path branch refuses a program", len(STARTED) == n0 and not cards(ex), said[:120])
say(ex, "app.open_folder", {"path": str(src)})
check("route 6: and sends a source file to a card", len(cards(ex)) == 1 and len(RAN) == ran0)
ex.approvals.pending.clear()
say(ex, "app.open_folder", {"path": str(WORK / "docs")})
check("route 6: a folder still opens", STARTED[-1:] == [str(WORK / "docs")])
dl = write(HOME / "Downloads" / "fetched.exe", "placeholder\n")
try:
    files.open_path(str(dl))
    check("route 14: a program in Downloads does not open", False)
except ToolError:
    check("route 14: a program in Downloads does not open", len(STARTED) == n0 + 1)

import ctypes

check("the gate's administrator check agrees with Windows",
      gate._elevated_now() == bool(ctypes.windll.shell32.IsUserAnAdmin()), str(gate._elevated_now()))
SC: dict = {}
gate.read_shortcut = lambda path: SC[gate.norm(path)]
SYS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
NOTEPAD = SYS / "notepad.exe"


def entry(kind: str, name: str, launch) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, name=name, launch=str(launch))


def lnk(path: Path, target, arguments: str = "") -> Path:
    write(path, "placeholder shortcut " + path.name)
    SC[gate.norm(path)] = gate.Shortcut(target=str(target), arguments=arguments, workdir="", advertised=False)
    return path


um = gate.user_start_menu() / "Programs"
mm = gate.machine_start_menu() / "Programs"
cases = [
    ("an installed program in the program folders", entry("exe", "Notepad", NOTEPAD), True),
    ("a Store app", entry("uwp", "Calculator", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"), True),
    ("one of Windows' own tools", entry("shell", "Settings", "ms-settings:"), True),
    ("his Start menu shortcut to an installed program", entry("lnk", "Pad", lnk(um / "Pad.lnk", NOTEPAD)), True),
    ("an all-users shortcut that adds arguments", entry("lnk", "PadA", lnk(mm / "PadA.lnk", NOTEPAD, "/A")), True),
    ("a shortcut in his own folder that adds arguments",
     entry("lnk", "PadB", lnk(um / "PadB.lnk", NOTEPAD, "/A")), False),
    ("a shortcut handing a command host a line", entry("lnk", "Host", lnk(mm / "Host.lnk", SYS / "cmd.exe",
                                                                          "placeholder-argument")), False),
    ("a shortcut whose program is a script", entry("lnk", "Scr", lnk(um / "Scr.lnk",
                                                                     write(WORK / "s1" / "go.bat", "placeholder"))),
     False),
    ("a program in a folder programs running as him can change",
     entry("exe", "Tool", write(HOME / "AppData" / "Local" / "Programs" / "Tool" / "tool.exe", "placeholder tool")),
     False),
]
for what, e, opens in cases:
    v = gate.app_needs_card(e)
    check(f"route 7: {what} {'opens by name with no card' if opens else 'needs a card'}",
          (v is None) == opens, "" if v is None else v["why"])
copied_lnk = lnk(WORK / "lnk-src" / "Mine.lnk", NOTEPAD)
(HOME / "Desktop").mkdir(exist_ok=True)
files.copy(str(copied_lnk), str(HOME / "Desktop"))
SC[gate.norm(HOME / "Desktop" / "Mine.lnk")] = SC[gate.norm(copied_lnk)]
v = gate.app_needs_card(entry("lnk", "Mine", HOME / "Desktop" / "Mine.lnk"))
check("route 7: a shortcut her file tools put on the Desktop needs a card, labelled hers",
      v is not None and v["why"] == "I wrote or changed one of its files" and any(r["hers"] for r in v["files"]),
      str(v and v["why"]))
n0 = len(STARTED)
ok, _detail = appindex.launch(cases[0][1])
check("everyday start: one of his installed apps opens by name with no card", ok and STARTED[-1:] == [str(NOTEPAD)])


class FakeIndex:
    def __init__(self, entries):
        self._e = list(entries)

    def entries(self):
        return list(self._e)

    def resolve(self, name):
        hits = [x for x in self._e if x.name.lower() == str(name).lower()]
        return hits, "exact"


tool = cases[-1][1]
appindex.get_index = lambda: FakeIndex([tool, cases[0][1]])
n0 = len(STARTED)
say(ex, "app.open", {"app": "Tool"})
pend = cards(ex)
check("route 7: opening that program by name raises a card that shows the program and its sha256",
      len(pend) == 1 and pend[0].tool == "app.launch_checked" and pend[0].args["program"] == tool.launch
      and pend[0].args["files"][0]["sha256"] == gate.sha256_file(tool.launch) and len(STARTED) == n0)
ex.execute_approved(pend[0].request_id)
check("approved, it opens once", STARTED[-1:] == [tool.launch] and len(STARTED) == n0 + 1)
say(ex, "app.open", {"app": "Tool"})
check("asked again unchanged, it opens with no card", not cards(ex) and len(STARTED) == n0 + 2)
write(Path(tool.launch), "placeholder tool!")
say(ex, "app.open", {"app": "Tool"})
check("one byte changed in it means a new card", len(cards(ex)) == 1 and len(STARTED) == n0 + 2)
ex.approvals.pending.clear()

launcher = write(WORK / "vsc" / "code.cmd", "placeholder launcher\n")
vscode._launcher = lambda: (str(launcher), "PATH")
proj = WORK / "proj-tasks"
write(proj / ".vscode" / "tasks.json", '{"tasks": [{"label": "placeholder", "runOptions": {"runOn": "folderOpen"}}]}')
try:
    vscode.checked_argv(str(proj))
    check("route 8: a folder with a task set to run on open is refused before VS Code starts", False)
except ToolError:
    check("route 8: a folder with a task set to run on open is refused before VS Code starts", True)
plain = WORK / "proj-plain"
plain.mkdir()
n0 = len(SPAWNED)
say(ex, "system.code.open", {"path": str(plain)})
pend = cards(ex)
check("route 8: a VS Code launcher that is a script, not a program, opens only from a card",
      len(pend) == 1 and pend[0].tool == "app.launch_checked" and len(SPAWNED) == n0)
ex.approvals.pending.clear()

print("\n(D) routes 9-13, 16: his commands, repositories, installers, downloads, archives\n")
from core.tools import gittools, x_tools

shd = WORK / "sh"
helper_bat = write(shd / "helper.bat", "placeholder helper\n")
n0 = len(SPAWNED)
say(ex, "shell.execute", {"command": "helper.bat", "cwd": str(shd)})
pend = cards(ex)
check("route 9: his command's card lists the file it names, with its sha256 and its text",
      len(pend) == 1 and [f["file"] for f in pend[0].args["files"]] == ["helper.bat"]
      and pend[0].args["views"] == {"helper.bat": "placeholder helper\n"})
write(helper_bat, "placeholder helper!\n")
try:
    ex.execute_approved(pend[0].request_id)
    check("route 9: a named file changed after the card is refused", False)
except ApprovalError:
    check("route 9: a named file changed after the card is refused", len(SPAWNED) == n0)
say(ex, "shell.execute", {"command": "later.bat", "cwd": str(shd)})
pend = cards(ex)
write(shd / "later.bat", "placeholder later\n")
try:
    ex.execute_approved(pend[0].request_id)
    check("route 9: a file the command names that appeared after the card is refused", False)
except ApprovalError:
    check("route 9: a file the command names that appeared after the card is refused", len(SPAWNED) == n0)
say(ex, "shell.execute", {"command": "helper.bat", "cwd": str(shd)})
ex.execute_approved(cards(ex)[0].request_id)
check("route 9: unchanged, it runs from the card", len(SPAWNED) == n0 + 1 and SPAWNED[-1][1:] == ["/c", "helper.bat"]
      and Path(SPAWNED[-1][0]).name.lower() == "cmd.exe", str(SPAWNED[-1:]))
pkg = WORK / "pkg"
write(pkg / "package.json", '{"name": "placeholder", "scripts": {}}\n')
say(ex, "shell.execute", {"command": "npm run placeholder", "cwd": str(pkg)})
pend = cards(ex)
check("route 11: a package command's card shows the package file whose scripts it runs",
      len(pend) == 1 and [f["file"] for f in pend[0].args["files"]] == ["package.json"])
ex.approvals.pending.clear()

repo = WORK / "repo"
write(repo / ".git" / "config", "[core]\n\trepositoryformatversion = 0\n\tbare = false\n[user]\n\tname = placeholder\n")
write(repo / "f1.py", "placeholder one\n")
write(repo / "sub" / "f2.py", "placeholder two\n")
GITS: list[list[str]] = []
REPLY = {"rev-parse": (0, "a" * 40 + "\n"), "switch": (0, ""), "diff": (0, "f1.py\0sub/f2.py\0gone.py\0")}


def fake_run_git(argv, cwd, timeout):
    GITS.append(list(argv))
    for k, v in REPLY.items():
        if k in argv:
            return v
    return 0, ""


gittools._run_git = fake_run_git
gittools.git_exe = lambda: "git.exe"
gittools.NO_HOOKS = TMP / "no-hooks"
gittools.git_repo("switch", str(repo), name="feature", provenance="human")
argv = next(a for a in GITS if "switch" in a)
check("route 10: every git run points hooks at an empty folder and turns the file monitor off",
      f"core.hooksPath={TMP / 'no-hooks'}" in argv and "core.fsmonitor=false" in argv, str(argv[:8]))
led = {Path(r[0]).name for r in gate.store().ledger_rows() if r[2] == "switch"}
check("route 10: the files a branch switch changed are in the ledger", led == {"f1.py", "f2.py"}, str(led))
write(TMP / "no-hooks" / "placeholder", "x")
try:
    gittools.git(repo, "status")
    check("route 10: a hooks folder that is not empty stops git before it runs", False)
except ToolError:
    check("route 10: a hooks folder that is not empty stops git before it runs", "status" not in GITS[-1])
(TMP / "no-hooks" / "placeholder").unlink()
for key in ("fsmonitor = placeholder", "sshCommand = placeholder", "hooksPath = placeholder"):
    write(repo / ".git" / "config", "[core]\n\tbare = false\n\t" + key + "\n")
    n0 = len(GITS)
    try:
        gittools.git(repo, "status")
        check(f"route 10: a repository setting core.{key.split()[0]} stops git before it runs", False)
    except ToolError:
        check(f"route 10: a repository setting core.{key.split()[0]} stops git before it runs", len(GITS) == n0)
for section in ('[alias]\n\tst = placeholder\n', '[filter "lfs"]\n\tclean = placeholder\n',
                '[include]\n\tpath = placeholder\n', '[diff "x"]\n\ttextconv = placeholder\n'):
    write(repo / ".git" / "config", section)
    n0 = len(GITS)
    try:
        gittools.git(repo, "status")
        check(f"route 10: a repository section {section.split(chr(10))[0]} stops git", False)
    except ToolError:
        check(f"route 10: a repository section {section.split(chr(10))[0]} stops git", len(GITS) == n0)
write(repo / ".git" / "config", "[core]\n\tbare = false\n")

s0 = len(SPAWNED)
said = say(ex, "system.software.install", {"id": str(WORK / "openers" / "a.msi"), "name": "placeholder"})
check("route 12: an installer named by path never reaches a card", not cards(ex) and len(SPAWNED) == s0, said[:120])
saved = WORK / "saved" / "pic.png"
x_tools._write_new(saved, b"\x89PNG\r\n\x1a\n" + b"0" * 16)
check("route 13: a file she saves is in the ledger as hers", any(Path(r[0]) == saved and r[2] == "download"
                                                                 for r in gate.store().ledger_rows())
      and gate.store().hers(saved))
import ast

extractors = []
for dp, dns, fns in os.walk(ROOT / "core"):
    dns[:] = [d for d in dns if d not in ("__pycache__", "tests")]
    for fn in fns:
        if fn.endswith(".py"):
            tree = ast.parse((Path(dp) / fn).read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(a.name in ("zipfile", "tarfile") for a in node.names):
                    extractors.append(fn)
                elif isinstance(node, ast.ImportFrom) and node.module in ("zipfile", "tarfile"):
                    extractors.append(fn)
                elif isinstance(node, ast.Attribute) and node.attr in ("unpack_archive", "extractall", "extract"):
                    extractors.append(fn)
check("route 16: no tool in core extracts an archive", not extractors, str(sorted(set(extractors))))

print("\n(E) routes 18 and 25: her file tools never write into a protected location\n")
from core.tools import browser


def _no_page():
    raise RuntimeError("no browser in this test")


browser.SESSION.page = _no_page
docs = HOME / "Documents"
appdata = Path(os.environ["APPDATA"])
local = Path(os.environ["LOCALAPPDATA"])
progdata = Path(os.environ["ProgramData"])
places = [
    ("her own code under the repository", ROOT / "core" / "gate-placeholder"),
    ("the repository's settings", ROOT / "core" / "config" / "gate-placeholder"),
    ("the daemon's data folder: audit log, gate store, ledger and approved list", ROOT / "data" / "gate-placeholder"),
    ("the daemon's local folder: runtime.json, vault.json and the rest", local / "Tessa" / "gate-placeholder"),
    ("his Start menu", gate.user_start_menu() / "Programs" / "gate-placeholder"),
    ("the all-users Start menu", gate.machine_start_menu() / "Programs" / "gate-placeholder"),
    ("his Startup folder", appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "gate-placeholder"),
    ("the all-users Startup folder", progdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "StartUp"
     / "gate-placeholder"),
    ("Windows PowerShell's profile folder", docs / "WindowsPowerShell" / "gate-placeholder"),
    ("PowerShell's profile folder", docs / "PowerShell" / "gate-placeholder"),
    ("Python's per-user packages", appdata / "Python" / "Python312" / "site-packages" / "gate-placeholder"),
    ("git's settings file", HOME / ".gitconfig"),
    ("git's settings folder", HOME / ".config" / "git" / "gate-placeholder"),
    ("ssh's settings", HOME / ".ssh" / "gate-placeholder"),
    ("a shell start file", HOME / ".bashrc"),
    ("a login shell start file", HOME / ".bash_profile"),
    ("a profile start file", HOME / ".profile"),
    ("npm's settings", HOME / ".npmrc"),
    ("a repository's own git folder", WORK / "repo" / ".git" / "hooks" / "gate-placeholder"),
]
src_file = write(WORK / "protect-src" / "note.txt", "placeholder\n")
for what, target in places:
    tried = []
    for label, fn in (("create_file", lambda t=target: create.create_file(str(t), "placeholder")),
                      ("create_folder", lambda t=target: create.create_folder(str(t))),
                      ("fs.create", lambda t=target: files.make_folder(str(t))),
                      ("fs.copy", lambda t=target: files.copy(str(src_file), str(t))),
                      ("fs.move", lambda t=target: files.move(str(src_file), str(t))),
                      ("browser.screenshot", lambda t=target: browser.screenshot(str(t)))):
        try:
            fn()
            tried.append(label + " WROTE")
        except ToolError:
            tried.append(label)
        except Exception as exc:
            tried.append(f"{label} {type(exc).__name__}")
    check(f"{what}: refused by every file tool, nothing written",
          all(" " not in x for x in tried) and not target.exists() and src_file.exists(), str(tried))
inside = write(local / "Tessa" / "store-placeholder.json", "placeholder\n")
for label, fn in (("fs.rename", lambda: files.rename(str(inside), "store-placeholder.old")),
                  ("fs.delete", lambda: files.delete(str(inside), confirmed=True))):
    try:
        fn()
        check(f"the daemon's own files: {label} is refused", False)
    except ToolError:
        check(f"the daemon's own files: {label} is refused", inside.exists())
real = {k: v for k, v in ORIG.items() if v}
saved_env = {k: os.environ[k] for k in real}
os.environ.update(real)
try:
    real_local = Path(real.get("LOCALAPPDATA", ""))
    real_appdata = Path(real.get("APPDATA", ""))
    named = {"runtime.json": real_local / "Tessa" / "runtime.json", "vault.json": real_local / "Tessa" / "vault.json",
             "his Startup folder": real_appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
             / "gate-placeholder.lnk",
             "the audit log": ROOT / "data" / "audit.log", "the gate store": ROOT / "data" / "gate.db"}
    for what, path in named.items():
        check(f"on his real paths, {what} is a protected location", bool(gate.protected_reason(path)), str(path))
finally:
    os.environ.update(saved_env)

print("\n(F) routes 19, 20, 26-28 and the everyday starts that need no card\n")
from core.brain import tools_local
from core.system.abilities import registry_write
from core.tools import REGISTRY

BS = chr(92)
try:
    registry_write.judge({"key": BS.join(["HKCU", "Software", "Microsoft", "Windows", "CurrentVersion", "Run"]),
                          "name": "placeholder", "value": "placeholder", "kind": "string"})
    check("route 19: a registry value under a start-up key is refused", False)
except registry_write.Refused:
    check("route 19: a registry value under a start-up key is refused", True)
makers = [n for n in REGISTRY if any(w in n for w in ("task", "service", "schedule"))
          and any(w in n for w in ("create", "register", "install", "add", "new"))]
check("route 20: no tool creates a scheduled task or a service", not makers, str(makers))
check("route 27: no tool writes into a terminal", not [n for n in REGISTRY if n.startswith("pty.") or "pty" in
                                                        str(getattr(REGISTRY[n], "capability", ""))])
deep = []
for dp, dns, fns in os.walk(ROOT / "core"):
    dns[:] = [d for d in dns if d not in ("__pycache__", "tests")]
    for fn in fns:
        if fn.endswith(".py") and "tessa://" in (Path(dp) / fn).read_text(encoding="utf-8", errors="replace"):
            deep.append(fn)
check("route 28: core builds no tessa:// links; it only checks them as origins", sorted(set(deep)) == ["server.py",
                                                                                                         "test_auth.py"]
      or sorted(set(deep)) == ["server.py"], str(deep))
check("route 26: her browser keeps Chrome's sandbox on", "chromium_sandbox=True" in
      (ROOT / "core" / "tools" / "browser.py").read_text(encoding="utf-8"))


class FakeChromium:
    def __init__(self):
        self.calls = []

    def launch_persistent_context(self, **opts):
        self.calls.append(opts)
        return SimpleNamespace(pages=[])


fc = FakeChromium()
gate.start(gate.Start(kind=gate.OWN, why="her browser, by channel", mode="browser", launcher=fc,
                      options={"channel": "chrome", "user_data_dir": str(TMP / "profile")}))
check("everyday start: her X work's Chrome starts through the gate with no card",
      fc.calls == [{"channel": "chrome", "user_data_dir": str(TMP / "profile")}] and not cards(ex))
n0 = len(SPAWNED)
tools_local._pid_name(4)
tools_local.listening_on_port(1)
got = [Path(a[0]).name.lower() for a in SPAWNED[n0:]]
check("everyday start: her helpers run as before, each resolved to Windows' own copy",
      got == ["tasklist.exe", "netstat.exe"] and all(Path(a[0]).parent == SYS for a in SPAWNED[n0:]),
      str(SPAWNED[n0:]))
n0, m0 = len(SPAWNED), len(STARTED)
ok, _d = tools_local.open_url("https://example.org/placeholder")
check("everyday start: a web page opens in his browser with no card",
      ok and (STARTED[m0:] == ["https://example.org/placeholder"] or SPAWNED[n0:][-1:][0][-1:] ==
              ["https://example.org/placeholder"]) and not cards(ex))
try:
    gate.start(gate.Start(kind=gate.PAGE, why="test", url="file:///C:/placeholder.bat"))
    check("a page is only ever a web address", False)
except ToolError:
    check("a page is only ever a web address", True)

print(f"\n{passed} passed, {failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
