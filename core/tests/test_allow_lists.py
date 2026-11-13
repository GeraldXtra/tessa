from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = Path(tempfile.mkdtemp(prefix="tessa-allow-lists-"))
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

coding.DATA = TMP / "data-code"
gate.use_store(gate.Store(TMP / "gate.db", TMP / "written.jsonl"))
CODE = TMP / "Tessa Code"
CODE.mkdir()
coding.code_folder = lambda: CODE
coding.documents = lambda: HOME / "Documents"
PY = sys.executable
FOUND_PY = langs.Found("python", "Python", True, exe=PY, where=f"PATH: {PY}", version="Python test")
FOUND_JS = langs.Found("javascript", "JavaScript", True, exe=PY, where="PATH: placeholder", version="placeholder")
FOUND_TS = langs.Found("typescript", "TypeScript", True, exe=PY, where="PATH: placeholder", version="placeholder")
langs.find = lambda key: {"python": FOUND_PY, "javascript": FOUND_JS, "typescript": FOUND_TS}.get(key) or langs.Found(
    key, langs.display(key), False, note="absent")
coding.langs.find = langs.find
RAN: list[dict] = []


def fake_run_source(p, spec, argv, timeout_s, *, mine=False):
    RAN.append({"file": str(p), "held": gate.current() is not None})
    return {"spoken": "It ran.", "said": "It ran.", "file": str(p)}


coding.run_source = fake_run_source
SPAWNED: list[list[str]] = []


def fake_spawn(argv, mode, opts):
    SPAWNED.append([str(a) for a in argv])
    return SimpleNamespace(returncode=0, stdout="", stderr="", pid=0)


gate._spawn = fake_spawn
STARTED: list[str] = []
os.startfile = lambda target, *a, **k: STARTED.append(str(target))
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


def executor() -> Executor:
    return Executor(session=SessionContext(), audit=None)


def say(ex: Executor, name: str, args: dict, origin: str = "human") -> str:
    return ex.run(ToolCall(name, args, origin=origin)) or ""


def cards(ex: Executor) -> list:
    return list(ex.approvals.pending.values())


def listed(card: dict) -> list[str]:
    return sorted(Path(f["file"]).as_posix() for f in card.get("files", []))


print("\n(C) the run card for a lone script: the script and what it imports, not the whole folder\n")
DOCS = HOME / "Documents"
lone = DOCS / "placeholder-lone"
main = write(lone / "main.py", "import placeholder_helper\nfrom placeholder_pkg import part\nprint(part)\n")
helper = write(lone / "placeholder_helper.py", "import placeholder_deep\n")
deep = write(lone / "placeholder_deep.py", "VALUE = 1\n")
write(lone / "placeholder_pkg" / "__init__.py", "from . import inner\n")
write(lone / "placeholder_pkg" / "part.py", "X = 2\n")
write(lone / "placeholder_pkg" / "inner.py", "Y = 3\n")
for i in range(200):
    write(lone / f"placeholder_other_{i}.py", f"placeholder other {i}\n")
write(lone / "placeholder_notes.txt", "placeholder\n")
want = ["main.py", "placeholder_deep.py", "placeholder_helper.py", "placeholder_pkg/__init__.py",
        "placeholder_pkg/inner.py", "placeholder_pkg/part.py"]
ex = executor()
said = say(ex, "code.run_file", {"path": str(main)})
pend = cards(ex)
card = pend[0].args if pend else {}
check("a lone script in a folder of 200 other scripts gets a card, and nothing runs",
      len(pend) == 1 and pend[0].tool == "code.run_mine" and not RAN, said[:200])
check("the card lists only the script and the files it imports, followed through each import",
      listed(card) == want, str(listed(card)))
check("the card says its scope is the script, and that the rest of the folder was not walked",
      card.get("scope") == gate.SCRIPT and card.get("unchecked", [{}])[0].get("folder") == "the rest of its folder",
      str(card.get("scope")) + " " + str(card.get("unchecked")))
check("she says the other files are what it imports or what sits at the top of its folder",
      "5 more files it imports or that sit at the top of its folder" in said, said[:240])
check("the scope is frozen on the card", "scope" in pend[0].frozen if pend else False)
ex.execute_approved(pend[0].request_id)
check("his approval runs it once, with its files held", len(RAN) == 1 and RAN[0]["held"])
write(lone / "placeholder_other_5.py", "placeholder other 5 changed\n")
write(lone / "sub" / "placeholder_new.py", "placeholder new\n")
said = say(ex, "code.run_file", {"path": str(main)})
check("a change to a file it does not import, and a new file in a subfolder, need no new card",
      len(RAN) == 2 and not cards(ex), said[:200])
write(deep, "VALUE = 2\n")
said = say(ex, "code.run_file", {"path": str(main)})
check("one byte changed in a file it imports two steps down brings the card back, and nothing runs",
      len(RAN) == 2 and len(cards(ex)) == 1, said[:200])
pend = cards(ex)
ex.execute_approved(pend[0].request_id)
write(lone / "json.py", "placeholder shadow\n")
said = say(ex, "code.run_file", {"path": str(main)})
pend = cards(ex)
check("a file named like a standard module, added at the top of its folder, brings a card that lists it",
      len(RAN) == 3 and len(pend) == 1 and "json.py" in listed(pend[0].args), str(listed(pend[0].args) if pend else said))
ex.approvals.pending.clear()
(lone / "json.py").unlink()
write(lone / "placeholder-tool.exe", "placeholder program bytes\n")
say(ex, "code.run_file", {"path": str(main)})
pend = cards(ex)
check("a program added at the top of its folder is on the card, because Windows looks there first",
      len(pend) == 1 and "placeholder-tool.exe" in listed(pend[0].args), str(listed(pend[0].args) if pend else ""))
write(helper, "import placeholder_deep\nimport subprocess\n")
try:
    ex.execute_approved(pend[0].request_id)
    check("a file it imports that starts loading code in a way she cannot follow, after the card, is refused", False)
except ApprovalError as err:
    check("a file it imports that starts loading code in a way she cannot follow, after the card, is refused",
          len(RAN) == 3 and "cannot follow" in str(err), str(err))
ex.approvals.pending.clear()

dyn = DOCS / "placeholder-dyn"
dyn_main = write(dyn / "main.py", "import importlib\nm = importlib.import_module('placeholder_part')\n")
write(dyn / "sub" / "placeholder_part.py", "Z = 1\n")
write(dyn / "placeholder_unused.py", "W = 1\n")
say(ex, "code.run_file", {"path": str(dyn_main)})
pend = cards(ex)
check("a script that loads files by a computed name keeps the folder rule: every runnable file of its folder",
      len(pend) == 1 and pend[0].args.get("scope") == gate.FOLDER
      and listed(pend[0].args) == ["main.py", "placeholder_unused.py", "sub/placeholder_part.py"],
      str(listed(pend[0].args) if pend else ""))
ex.approvals.pending.clear()
for label, text in (("exec of a file's text", "exec(open('placeholder.txt').read())\n"),
                    ("__import__ by name", "m = __import__('placeholder_part')\n"),
                    ("sys.path changed", "import sys\nsys.path.insert(0, 'sub')\nimport placeholder_part\n"),
                    ("a getattr with a computed name", "import os\nf = getattr(os, 'sy' + 'stem')\n"),
                    ("a library loaded with ctypes", "import ctypes\n"),
                    ("text that is not Python", "placeholder text\n")):
    write(dyn / "main.py", text)
    say(ex, "code.run_file", {"path": str(dyn_main)})
    pend = cards(ex)
    check(f"...and so does {label}", len(pend) == 1 and pend[0].args.get("scope") == gate.FOLDER,
          str(pend[0].args.get("scope") if pend else "no card"))
    ex.approvals.pending.clear()

proj = DOCS / "placeholder-project"
proj_main = write(proj / "main.py", "print(1)\n")
write(proj / "requirements.txt", "placeholder\n")
write(proj / "lib" / "util.py", "U = 1\n")
say(ex, "code.run_file", {"path": str(proj_main)})
pend = cards(ex)
check("a project folder (requirements.txt) still lists its project files",
      len(pend) == 1 and pend[0].args.get("scope") == gate.FOLDER and listed(pend[0].args) == ["lib/util.py", "main.py"],
      str(listed(pend[0].args) if pend else ""))
ex.approvals.pending.clear()
nested = write(proj / "tools" / "run.py", "print(2)\n")
write(proj / "tools" / "other.py", "O = 1\n")
say(ex, "code.run_file", {"path": str(nested)})
pend = cards(ex)
check("a script inside a project's subfolder keeps today's folder card",
      len(pend) == 1 and pend[0].args.get("scope") == gate.FOLDER and listed(pend[0].args) == ["other.py", "run.py"],
      str(listed(pend[0].args) if pend else ""))
ex.approvals.pending.clear()
for marker in ("package.json", "pyproject.toml", ".git", "placeholder.csproj"):
    m = DOCS / f"placeholder-marker-{marker.strip('.')}"
    write(m / "main.py", "print(3)\n")
    write(m / "sub" / "x.py", "X = 1\n")
    if marker == ".git":
        (m / ".git").mkdir()
    else:
        write(m / marker, "{}\n")
    check(f"a folder holding {marker} is a project, so its card covers the folder",
          gate.program_set(m / "main.py").scope == gate.FOLDER and "sub/x.py" in
          [Path(p.rel).as_posix() for p in gate.program_set(m / "main.py").pieces])

js = DOCS / "placeholder-js"
app = write(js / "app.js", "const h = require('./placeholder-helper');\nimport('./placeholder-later.mjs');\n"
                           "module.exports = h;\n")
write(js / "placeholder-helper.js", "module.exports = require('./lib/placeholder-deep');\n")
write(js / "lib" / "placeholder-deep.js", "module.exports = 1;\n")
write(js / "placeholder-later.mjs", "export const v = 1;\n")
for i in range(20):
    write(js / f"placeholder-other-{i}.js", "module.exports = 0;\n")
fs = gate.program_set(app)
check("a lone JavaScript file lists itself and what it requires and imports, followed through each one",
      fs.scope == gate.SCRIPT and sorted(Path(p.rel).as_posix() for p in fs.pieces)
      == ["app.js", "lib/placeholder-deep.js", "placeholder-helper.js", "placeholder-later.mjs"],
      str([p.rel for p in fs.pieces]))
ts = write(js / "main.ts", "import { v } from './placeholder-part.js';\nexport { v };\n")
write(js / "placeholder-part.ts", "export const v: number = 1;\n")
fs = gate.program_set(ts)
check("a TypeScript import of ./x.js finds x.ts, as Node's type stripping does",
      fs.scope == gate.SCRIPT and sorted(Path(p.rel).as_posix() for p in fs.pieces) == ["main.ts", "placeholder-part.ts"],
      str([p.rel for p in fs.pieces]))
for label, text in (("a require by a computed name", "const n = 'x';\nrequire(n);\n"),
                    ("a child process", "const cp = require('child_process');\n"),
                    ("eval", "eval('1');\n"),
                    ("an escaped name", "const r = requ\\u0069re;\n")):
    write(js / "dyn.js", text)
    check(f"JavaScript with {label} keeps the folder rule", gate.program_set(js / "dyn.js").scope == gate.FOLDER)

down = DOCS / "placeholder-down"
down_main = write(down / "main.py", "print(4)\n")
write(down / "sub" / "x.py", "X = 1\n")
with open(str(down_main) + ":Zone.Identifier", "w", encoding="utf-8") as fh:
    fh.write("[ZoneTransfer]\nZoneId=3\n")
check("a script Windows marks as downloaded keeps the folder rule", gate.program_set(down_main).scope == gate.FOLDER)
ps = write(DOCS / "placeholder-ps" / "go.ps1", "Write-Output 1\n")
write(DOCS / "placeholder-ps" / "sub" / "x.ps1", "Write-Output 2\n")
check("a language she does not follow (PowerShell) keeps the folder rule", gate.program_set(ps).scope == gate.FOLDER)
old_body = json.dumps({"kind": gate.CODE, "entry": "a.py", "files": [["a.py", "0" * 64]], "argv": []},
                      sort_keys=True, ensure_ascii=False)
check("a folder card's set hash is computed exactly as before, so his earlier approvals still count",
      gate.set_sha(gate.CODE, "a.py", [("a.py", "0" * 64)], []) == hashlib.sha256(old_body.encode("utf-8")).hexdigest())
check("a script card's set hash differs from a folder card of the same files",
      gate.set_sha(gate.CODE, "a.py", [("a.py", "0" * 64)], [], gate.SCRIPT)
      != gate.set_sha(gate.CODE, "a.py", [("a.py", "0" * 64)], []))

print("\n(D) apps in his own folders: a signature from a publisher he approved once opens them with no card\n")
CALLS: list[dict] = []
CODES: list[int] = []


def fake_wvt(data):
    CALLS.append({"ui": int(data.dwUIChoice), "rev": int(data.fdwRevocationChecks), "flags": int(data.dwProvFlags),
                  "action": int(data.dwStateAction)})
    if int(data.dwStateAction) == gate._WTD_STATEACTION_CLOSE:
        return 0
    data.hWVTStateData = 1
    return CODES.pop(0) if CODES else 0


gate._wvt = fake_wvt
gate._signer_names = lambda state: {"publisher": "Placeholder Publisher", "issuer": "Placeholder CA",
                                    "subject": "CN=Placeholder Publisher, O=Placeholder Publisher"}
CODES[:] = [0, 0]
sig = gate.signature_of(TMP / "placeholder.exe")
verifies = [c for c in CALLS if c["action"] == gate._WTD_STATEACTION_VERIFY]
check("the check asks Windows for cached revocation data only, on every call, and never shows a window",
      len(verifies) == 2 and all(c["flags"] & gate._WTD_CACHE_ONLY_URL_RETRIEVAL and c["ui"] == gate._WTD_UI_NONE
                                 for c in CALLS), str(CALLS))
check("the signature is checked first with revocation off, then revocation from the cache for the whole chain",
      verifies[0]["rev"] == gate._WTD_REVOKE_NONE and verifies[0]["flags"] & gate._WTD_REVOCATION_CHECK_NONE
      and verifies[1]["rev"] == gate._WTD_REVOKE_WHOLECHAIN
      and verifies[1]["flags"] & gate._WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT, str(verifies))
check("every state Windows opened is closed again", sum(c["action"] == gate._WTD_STATEACTION_CLOSE for c in CALLS) == 2)
check("a good signature names its publisher and says it was not checked online",
      sig["status"] == gate.SIGNED_OK and sig["publisher"] == "Placeholder Publisher"
      and "nothing was checked online" in sig["revocation"], str(sig))
CODES[:] = [0, 0x80092013]
sig = gate.signature_of(TMP / "placeholder.exe")
check("no cached revocation answer: still a good signature, and the card says revocation is not known",
      sig["status"] == gate.SIGNED_OK and sig["revocation"].startswith("not known"), str(sig))
CODES[:] = [0, 0x800B010C]
check("revoked in the cached lists: a broken signature", gate.signature_of(TMP / "x.exe")["status"] == gate.SIGNED_BROKEN)
CODES[:] = [0x80096010]
sig = gate.signature_of(TMP / "x.exe")
check("contents that do not match the signature: broken, and said so", sig["status"] == gate.SIGNED_BROKEN
      and "do not match" in sig["why"], str(sig))
CODES[:] = [0x800B0100]
check("no signature at all: unsigned", gate.signature_of(TMP / "x.exe")["status"] == gate.UNSIGNED)

SIGS: dict[str, dict] = {}
gate.signature_of = lambda path: dict(SIGS.get(gate.norm(path)) or {"status": gate.UNSIGNED, "why": "it is not signed"})
from core.brain import appindex, memory

memory.record = lambda *a, **k: None
memory.he_opened_it_himself = lambda *a, **k: False
PROGRAMS = HOME / "AppData" / "Local" / "Programs"
signed_exe = write(PROGRAMS / "PlaceholderApp" / "placeholder-app.exe", "placeholder program one\n")
unsigned_exe = write(PROGRAMS / "PlaceholderPlain" / "placeholder-plain.exe", "placeholder program two\n")
broken_exe = write(PROGRAMS / "PlaceholderBroken" / "placeholder-broken.exe", "placeholder program three\n")
interp = write(PROGRAMS / "PlaceholderPython" / "python.exe", "placeholder interpreter\n")
GOOD = {"status": gate.SIGNED_OK, "publisher": "Placeholder Publisher", "issuer": "Placeholder CA",
        "subject": "CN=Placeholder Publisher, O=Placeholder Publisher", "revocation": "not known", "why": "signed"}
SIGS[gate.norm(signed_exe)] = dict(GOOD)
SIGS[gate.norm(interp)] = dict(GOOD)
SIGS[gate.norm(broken_exe)] = {"status": gate.SIGNED_BROKEN, "publisher": "Placeholder Publisher",
                               "subject": "CN=Placeholder Publisher, O=Placeholder Publisher",
                               "why": "its contents do not match its signature"}


class FakeIndex:
    def __init__(self, entries):
        self._e = list(entries)

    def entries(self):
        return list(self._e)

    def resolve(self, name):
        return [x for x in self._e if x.name.lower() == str(name).lower()], "exact"


apps = {"Placeholder App": signed_exe, "Placeholder Plain": unsigned_exe, "Placeholder Broken": broken_exe,
        "Placeholder Python": interp}
appindex.get_index = lambda: FakeIndex([SimpleNamespace(kind="exe", name=n, launch=str(p)) for n, p in apps.items()])
ex = executor()
s0 = len(STARTED)
said = say(ex, "app.open", {"app": "Placeholder App"})
pend = cards(ex)
check("the first time, a signed app from a publisher he has not approved brings a card, and does not open",
      len(pend) == 1 and pend[0].tool == "app.launch_checked" and len(STARTED) == s0, said[:200])
card = pend[0].args if pend else {}
check("the card names the publisher, and says approving trusts that publisher's apps in his folders",
      card.get("publisher") == "Placeholder Publisher" and "Placeholder Publisher" in str(card.get("approving"))
      and "signed by Placeholder Publisher" in str(pend[0].detail), str(card.get("approving")) + " " + str(pend[0].detail))
check("the publisher and the signature are frozen on the card",
      {"publisher", "signature", "approving"} <= set(pend[0].frozen) if pend else False)
ex.execute_approved(pend[0].request_id)
check("his approval opens it, and approves the publisher once",
      STARTED[s0:] == [str(signed_exe)] and gate.publisher_approved(GOOD))
said = say(ex, "app.open", {"app": "Placeholder App"})
check("the second time it opens with no card", STARTED[-1] == str(signed_exe) and len(STARTED) == s0 + 2
      and not cards(ex), said[:200])
write(signed_exe, "placeholder program one, updated\n")
write(signed_exe.parent / "placeholder-part.dll", "placeholder library\n")
said = say(ex, "app.open", {"app": "Placeholder App"})
check("after an update changes its files, a good signature from the same publisher still needs no card",
      len(STARTED) == s0 + 3 and not cards(ex), said[:200])
SIGS[gate.norm(signed_exe)] = {**GOOD, "publisher": "Placeholder Other", "subject": "CN=Placeholder Other"}
said = say(ex, "app.open", {"app": "Placeholder App"})
pend = cards(ex)
check("signed by a different publisher after an update: a card naming the new publisher",
      len(pend) == 1 and pend[0].args.get("publisher") == "Placeholder Other" and len(STARTED) == s0 + 3, said[:200])
ex.approvals.pending.clear()
SIGS[gate.norm(signed_exe)] = dict(GOOD)
said = say(ex, "app.open", {"app": "Placeholder Plain"})
pend = cards(ex)
check("an unsigned app keeps the content rule: a card listing its program with its sha256",
      len(pend) == 1 and "not signed" in pend[0].args.get("why", "") and not pend[0].args.get("publisher")
      and pend[0].args["files"][0]["sha256"] == gate.sha256_file(unsigned_exe), str(pend[0].args.get("why") if pend else said))
ex.execute_approved(pend[0].request_id)
said = say(ex, "app.open", {"app": "Placeholder Plain"})
check("...approved once, the same content opens with no card", not cards(ex) and STARTED[-1] == str(unsigned_exe))
write(unsigned_exe, "placeholder program two, updated\n")
said = say(ex, "app.open", {"app": "Placeholder Plain"})
check("...and after its files change, the unsigned app asks again", len(cards(ex)) == 1, said[:200])
ex.approvals.pending.clear()
said = say(ex, "app.open", {"app": "Placeholder Broken"})
pend = cards(ex)
check("a broken signature brings a card that says why, even from a publisher he approved",
      len(pend) == 1 and "do not match its signature" in pend[0].args.get("why", "") and not pend[0].args.get("publisher"),
      str(pend[0].args.get("why") if pend else said))
ex.approvals.pending.clear()
n0 = len(STARTED)
said = say(ex, "app.open", {"app": "Placeholder Python"})
check("a signed interpreter from an approved publisher opens by itself with no card",
      len(STARTED) == n0 + 1 and not cards(ex), said[:200])
lnk = write(HOME / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "placeholder.lnk",
            "placeholder shortcut\n")
real_read = gate.read_shortcut
gate.read_shortcut = lambda path: gate.Shortcut(target=str(interp), arguments="placeholder.py", workdir="", advertised=False)
apps["Placeholder Script"] = lnk
appindex.get_index = lambda: FakeIndex([SimpleNamespace(kind="lnk" if n == "Placeholder Script" else "exe", name=n,
                                                        launch=str(p)) for n, p in apps.items()])
said = say(ex, "app.open", {"app": "Placeholder Script"})
pend = cards(ex)
check("a shortcut that hands that signed interpreter a script still brings a card",
      len(pend) == 1 and "command line" in pend[0].args.get("why", ""), str(pend[0].args.get("why") if pend else said))
ex.approvals.pending.clear()
gate.read_shortcut = real_read
FOUND_PY.exe = str(interp)
script = write(DOCS / "placeholder-interp" / "run.py", "print(5)\n")
r0 = len(RAN)
said = say(ex, "code.run_file", {"path": str(script)})
pend = cards(ex)
check("asking that signed interpreter to run a script still brings the run card, and nothing runs",
      len(pend) == 1 and pend[0].tool == "code.run_mine" and len(RAN) == r0, said[:200])
ex.approvals.pending.clear()
FOUND_PY.exe = PY

print("\n(A) her file tools write freely only in his usual folders, resolved from Windows\n")
from core.security import allow
from core.tools import files

ONE = HOME / "OneDrive"
PLACES = {"Documents": ONE / "Documents", "Desktop": ONE / "Desktop", "Pictures": ONE / "Pictures",
          "Downloads": HOME / "Downloads", "Videos": HOME / "Videos", "Music": HOME / "Music"}
for _p in PLACES.values():
    _p.mkdir(parents=True, exist_ok=True)
TEMP = TMP / "temp"
TEMP.mkdir()
GUIDS = {allow.KNOWN[n]: p for n, p in PLACES.items()}
allow._known = lambda guid: GUIDS.get(guid)
allow._temp = lambda: TEMP
coding.documents = lambda: PLACES["Documents"]


class FakeGuard:
    def is_protected(self, target):
        full = Path(target).resolve()
        return gate.under(full, ONE.resolve()) or gate.under(full, (TMP / "protected").resolve())


allow._GUARD["g"] = FakeGuard()
check("his folders come from Windows' own record of where they are, OneDrive included",
      allow.folders() == {**PLACES, "temp": TEMP}, str(allow.folders()))
ex = executor()
for name, where in [*PLACES.items(), ("temp", TEMP), ("Tessa Code", PLACES["Documents"] / "Tessa Code")]:
    where.mkdir(parents=True, exist_ok=True)
    target = where / "placeholder-note.txt"
    said = say(ex, "system.files.create_file", {"path": str(target), "text": "placeholder note"}, origin="agent")
    check(f"a note in {name} is written with no card and no hold, even when the model built the call",
          target.read_text(encoding="utf-8") == "placeholder note" and not cards(ex) and ex.ledger.pending is None,
          said[:160])
said = say(ex, "system.files.create_file", {"path": "placeholder-relative.txt", "text": "placeholder"}, origin="agent")
check("a bare file name lands in his real Documents, wherever Windows keeps it",
      (PLACES["Documents"] / "placeholder-relative.txt").is_file(), said[:160])
other = TMP / "elsewhere"
other.mkdir()
said = say(ex, "system.files.create_file", {"path": str(other / "placeholder.txt"), "text": "placeholder card text"},
           origin="agent")
pend = cards(ex)
check("a note anywhere else gets a red card and is not written",
      len(pend) == 1 and pend[0].tool == "fs.write_checked" and not (other / "placeholder.txt").exists(), said[:160])
check("she says nothing on that card can be changed", "Nothing on that card can be changed" in said, said[-160:])
card = pend[0].args if pend else {}
check("the card shows the path and the whole text", card.get("path") == str(other / "placeholder.txt")
      and card.get("text") == "placeholder card text" and card.get("op") == "create_file", str(card))
check("every field of the write card is frozen", set(card) <= set(pend[0].frozen) if pend else False,
      str(set(card) - set(pend[0].frozen)) if pend else "")
said_missing = say(ex, "fs.write_checked", {"op": "create_file", "path": str(TMP / "no-such" / "x.txt"),
                                             "text": "placeholder"}, origin="agent")
check("a write into a folder that does not exist is refused before any card",
      len(cards(ex)) == 1 and "does not exist" in said_missing, said_missing[:160])
ex.execute_approved(pend[0].request_id)
check("approved, it is written exactly as shown", (other / "placeholder.txt").read_text(encoding="utf-8")
      == "placeholder card text")
say(ex, "system.files.create_file", {"path": str(other / "placeholder-2.txt"), "text": "placeholder"}, origin="human")
check("a folder he named himself in his own words is written with no card",
      (other / "placeholder-2.txt").is_file() and not cards(ex))
(TMP / "protected").mkdir()
say(ex, "system.files.create_file", {"path": str(TMP / "protected" / "x.txt"), "text": "placeholder"}, origin="human")
check("...unless that folder is a protected one: then the card", len(cards(ex)) == 1
      and not (TMP / "protected" / "x.txt").exists())
ex.approvals.pending.clear()
(ONE / "Projects").mkdir()
say(ex, "system.files.create_file", {"path": str(ONE / "Projects" / "x.txt"), "text": "placeholder"}, origin="agent")
check("OneDrive outside his known folders still gets the card", len(cards(ex)) == 1)
ex.approvals.pending.clear()
for label, name in (("a batch file", "run.bat"), ("a shortcut", "app.lnk"), ("a web shortcut", "site.url"),
                    ("a folder settings file", "desktop.ini")):
    say(ex, "system.files.create_file", {"path": str(PLACES["Desktop"] / name), "text": "placeholder"}, origin="agent")
    check(f"{label} on the Desktop still gets the card, because Windows runs or acts on it",
          len(cards(ex)) == 1 and not (PLACES["Desktop"] / name).exists())
    ex.approvals.pending.clear()
for label, target in (("her data folder", ROOT / "data" / "placeholder-r1d.txt"),
                      ("her own stores", HOME / "AppData" / "Local" / "Tessa" / "placeholder-r1d.txt"),
                      ("her own code and settings", ROOT / "core" / "placeholder-r1d.txt")):
    for origin in ("agent", "human"):
        said = say(ex, "system.files.create_file", {"path": str(target), "text": "placeholder"}, origin=origin)
        check(f"a write into {label} is refused before any card ({origin})",
              not cards(ex) and not target.exists() and "never write there" in said, said[:160])
    req = ex.approvals.request(tool="fs.write_checked", tier="red", provenance="human", detail="placeholder",
                               args={"op": "create_file", "path": str(target), "text": "placeholder",
                                     "sha256": hashlib.sha256(b"placeholder").hexdigest()})
    try:
        ex.execute_approved(req.request_id)
        check(f"...and refused even when a card for it is approved ({label})", False)
    except ApprovalError as err:
        check(f"...and refused even when a card for it is approved ({label})", not target.exists(), str(err))
say(ex, "system.files.create_file", {"path": str(PLACES["Desktop"] / "placeholder-sched.txt"), "text": "x"},
    origin="schedule")
check("an unattended request is never free, even in his folders", not (PLACES["Desktop"] / "placeholder-sched.txt").exists())
ex.approvals.pending.clear()
src = write(PLACES["Downloads"] / "placeholder-src.txt", "placeholder source text\n")
say(ex, "fs.copy", {"path": str(src), "to": str(PLACES["Documents"])}, origin="agent")
check("a copy from Downloads into Documents happens with no card and no hold",
      (PLACES["Documents"] / "placeholder-src.txt").is_file() and not cards(ex) and ex.ledger.pending is None)
said = say(ex, "fs.copy", {"path": str(src), "to": str(other)}, origin="agent")
pend = cards(ex)
card = pend[0].args if pend else {}
check("a copy to anywhere else gets the card, with the source, its sha256 and its text",
      len(pend) == 1 and card.get("source") == str(src) and card.get("sha256") == gate.sha256_file(src)
      and card.get("text") == "placeholder source text\n" and not (other / src.name).exists(), str(card) + said[:200])
write(src, "placeholder source text, changed\n")
try:
    ex.execute_approved(pend[0].request_id)
    check("a source that changed between the card and his approval is not copied", False)
except ApprovalError as err:
    check("a source that changed between the card and his approval is not copied", not (other / src.name).exists(),
          str(err))
outside = write(other / "placeholder-out.txt", "placeholder\n")
say(ex, "fs.move", {"path": str(outside), "to": str(PLACES["Documents"])}, origin="agent")
check("a move out of a folder that is not on the list gets the card, even into Documents",
      len(cards(ex)) == 1 and outside.exists())
ex.approvals.pending.clear()
inside = write(PLACES["Desktop"] / "placeholder-old.txt", "placeholder\n")
say(ex, "fs.rename", {"path": str(inside), "to": "placeholder-new.txt"}, origin="agent")
check("a rename inside the Desktop happens with no card", (PLACES["Desktop"] / "placeholder-new.txt").is_file()
      and not cards(ex))
say(ex, "fs.create", {"path": str(PLACES["Music"] / "placeholder-folder")}, origin="agent")
check("a new folder in Music needs no card", (PLACES["Music"] / "placeholder-folder").is_dir() and not cards(ex))
say(ex, "fs.create", {"path": str(other / "placeholder-folder")}, origin="agent")
check("a new folder elsewhere gets the card", len(cards(ex)) == 1 and not (other / "placeholder-folder").exists())
ex.approvals.pending.clear()
say(ex, "fs.move", {"path": str(PLACES["Downloads"]), "to": str(PLACES["Documents"])}, origin="agent")
check("moving his whole Downloads folder still holds for his yes first", ex.ledger.pending is not None
      and PLACES["Downloads"].is_dir())
ex.ledger.clear()

print("\n(B) her keyboard types freely only into a short list of targets\n")
from core.system.winapi import inputs

NOTE = {"hwnd": 101, "title": "placeholder - Notepad", "class": "Notepad", "pid": 11, "thread": 12,
        "process": "notepad.exe", "process_path": r"C:\Windows\System32\notepad.exe", "focus_hwnd": 102,
        "focus_class": "RichEditD2DPT", "refuse": ""}
WEB = {"hwnd": 201, "title": "placeholder page", "class": "Chrome_WidgetWin_1", "pid": 21, "thread": 22,
       "process": "placeholder.exe", "process_path": str(PROGRAMS / "PlaceholderApp" / "placeholder.exe"),
       "focus_hwnd": 202, "focus_class": "Chrome_RenderWidgetHostHWND", "refuse": ""}
ORB = {**WEB, "hwnd": 301, "title": "placeholder surface", "pid": 31, "focus_hwnd": 302}
NOW = {"t": dict(NOTE), "after": None, "front": True}
SENT: list[int] = []


def fake_target():
    if NOW["after"] is not None and len(SENT) >= 1:
        NOW["t"] = dict(NOW["after"])
    return dict(NOW["t"])


def fake_front(hwnd, pid=0):
    if NOW["front"] and int(hwnd) == int(NOTE["hwnd"]) or NOW["front"] and int(hwnd) == int(WEB["hwnd"]):
        NOW["t"] = dict(NOTE if int(hwnd) == NOTE["hwnd"] else WEB)
        return inputs.FRONT_DIRECT
    return ""


inputs.target_now = fake_target
inputs.bring_to_front = fake_front
inputs._send = lambda *seq: SENT.append(len(seq)) or len(seq)
inputs.TYPE_PAUSE_S = 0
said = say(ex, "system.input.type", {"text": "placeholder words"}, origin="agent")
check("typing into Notepad's text area goes ahead with no card and no hold",
      len(SENT) == 1 and not cards(ex) and ex.ledger.pending is None, said[:160])
SENT.clear()
said = say(ex, "system.input.key", {"keys": "ctrl+v"}, origin="human")
check("...and so does a paste there", len(SENT) == 1 and not cards(ex), said[:160])
SENT.clear()
NOW["t"] = dict(WEB)
said = say(ex, "system.input.type", {"text": "placeholder words"}, origin="agent")
pend = cards(ex)
card = pend[0].args if pend else {}
check("typing anywhere else gets a red card, and nothing is typed",
      len(pend) == 1 and pend[0].tool == "input.type_checked" and not SENT, said[:160])
check("the card shows the exact text and the target: program, window class and focused element",
      card.get("text") == "placeholder words" and card.get("process") == "placeholder.exe"
      and card.get("window_class") == "Chrome_WidgetWin_1" and card.get("focus") == "Chrome_RenderWidgetHostHWND"
      and card.get("hwnd") == 201, str(card))
check("every field of the typing card is frozen", set(card) <= set(pend[0].frozen) if pend else False)
ex.execute_approved(pend[0].request_id)
check("approved with the same target in front, it types", len(SENT) == 1)
SENT.clear()
NOW["t"] = dict(WEB)
say(ex, "system.input.type", {"text": "placeholder words"}, origin="human")
pend = cards(ex)
check("text he dictated himself goes on the same card", len(pend) == 1 and pend[0].args.get("text") == "placeholder words"
      and not SENT)
NOW["t"] = dict(ORB)
ex.execute_approved(pend[0].request_id)
check("when his click on the card took the focus, she brings the card's window back and types into it",
      len(SENT) == 1)
SENT.clear()
NOW["t"] = dict(WEB)
say(ex, "system.input.key", {"keys": "ctrl+v"}, origin="agent")
pend = cards(ex)
check("a paste anywhere else gets the card, and says it pastes the clipboard",
      len(pend) == 1 and pend[0].args.get("keys") == "ctrl+v" and "clipboard" in pend[0].args.get("pastes", ""))
NOW["t"] = {**WEB, "focus_hwnd": 203, "focus_class": "Edit"}
try:
    ex.execute_approved(pend[0].request_id)
    check("a target that changed between approval and typing asks again", False)
except ApprovalError as err:
    again = cards(ex)
    check("a target that changed between approval and typing asks again, with a new card, and types nothing",
          err.code == "permission.pending" and len(again) == 1 and again[0].args.get("focus") == "Edit" and not SENT,
          str(err))
ex.approvals.pending.clear()
NOW["t"] = dict(WEB)
say(ex, "system.input.type", {"text": "placeholder words"}, origin="agent")
pend = cards(ex)
NOW["t"] = dict(ORB)
NOW["front"] = False
try:
    ex.execute_approved(pend[0].request_id)
    check("when she cannot bring the card's window back, she types nothing", False)
except ApprovalError as err:
    check("when she cannot bring the card's window back, she types nothing", not SENT and "could not bring" in str(err),
          str(err))
NOW["front"] = True
NOW["t"] = dict(NOTE)
NOW["after"] = dict(WEB)
said = say(ex, "system.input.type", {"text": "x" * 60}, origin="agent")
check("the target is checked again before every part of the text: when it moves, typing stops",
      len(SENT) == 1 and "keyboard moved" in said, said[:160])
NOW["after"] = None
SENT.clear()
NOW["t"] = {**NOTE, "process_path": str(HOME / "Downloads" / "notepad.exe")}
say(ex, "system.input.type", {"text": "placeholder"}, origin="agent")
check("a program that only calls itself Notepad, outside the program folders, gets the card",
      len(cards(ex)) == 1 and not SENT)
ex.approvals.pending.clear()
NOW["t"] = dict(NOTE)
say(ex, "system.input.type", {"text": "placeholder"}, origin="schedule")
check("an unattended request never types freely", not SENT)
ex.approvals.pending.clear()
src_x = (ROOT / "core" / "tools" / "x_tools.py").read_text(encoding="utf-8")
src_b = (ROOT / "core" / "tools" / "browser.py").read_text(encoding="utf-8")
check("her X work never reaches the keyboard tool: x_tools and browser use no part of it",
      not any(w in src_x + src_b for w in ("winapi", "send_text(", "send_keys(", "SendInput", "system.input")))
from core.tools import browser


class FakeLoc:
    def __init__(self):
        self.filled = []
        self.first = self

    def count(self):
        return 1

    def fill(self, text):
        self.filled.append(text)


class FakePage:
    url = "https://x.com/placeholder"

    def __init__(self):
        self.loc = FakeLoc()

    def get_by_label(self, name):
        return self.loc


page = FakePage()
browser.SESSION.page = lambda: page
inputs.target_now = lambda: (_ for _ in ()).throw(AssertionError("the keyboard tool was reached"))
inputs._send = lambda *seq: (_ for _ in ()).throw(AssertionError("SendInput was reached"))
got = browser.type_text("placeholder field", "placeholder words")
check("typing into a page goes through the browser, with the keyboard tool switched off",
      page.loc.filled == ["placeholder words"] and got["chars"] == 17)

print(f"\n{passed} passed, {failed} failed")
