from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.brain import phrasings, reading
from core.brain.approvals import ApprovalError
from core.brain.executor import Executor
from core.brain.llm.base import LLMAdapter, RateLimited
from core.brain.provenance import SessionContext
from core.brain.tools_local import ToolCall
from core.tools import coding, gittools, langs, runner

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


TMP = Path(tempfile.mkdtemp(prefix="tessa-code-test-"))
CODE = TMP / "Tessa Code"
WORK = TMP / "work"
WORK.mkdir()
coding.DATA = TMP / "data-code"
coding.code_folder = lambda: CODE
PY = langs.which("python") or sys.executable
REAL_FIND = langs.find
FOUND_PY = langs.Found("python", "Python", True, exe=PY, where=f"PATH: {PY}", version="Python test")


def fake_find(key: str):
    if key == "python":
        return FOUND_PY
    if key == "batch":
        return langs.Found("batch", "batch", True, exe=os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"))
    return langs.Found(key, langs.display(key), False, note="not installed")


langs.find = fake_find
coding.langs.find = fake_find


def write(name: str, text: str, folder: Path = WORK) -> Path:
    p = folder / name
    p.write_text(text, encoding="utf-8")
    return p


def executor() -> Executor:
    return Executor(session=SessionContext(), audit=None)


def new_turn(ex: Executor) -> Executor:
    if ex.session.external_content_in_context:
        ex.session.clear_external()
        ex.last_injection = None
    return ex


def outlived(r) -> list[int]:
    import psutil

    out = []
    for pid in r.pids_seen:
        try:
            p = psutil.Process(pid)
            if p.create_time() <= r.started_at + 1.0:
                p.wait(timeout=0)
        except psutil.TimeoutExpired:
            out.append(pid)
        except psutil.Error:
            continue
    return out


class FakeLLM(LLMAdapter):
    def __init__(self, reply, name="gemini:fake"):
        self.reply = reply
        self._name = name
        self.calls = []

    @property
    def name(self):
        return self._name

    @property
    def available(self):
        return True, ""

    def stream(self, system, messages, **kw):
        self.calls.append((system, messages[-1].content, kw))
        r = self.reply(messages[-1].content)
        if isinstance(r, BaseException):
            raise r
        yield r


print("\n(1) 3a: what can run here, found by looking\n")
check("languages have names, extensions and aliases",
      langs.canonical("C++") == "cpp" and langs.canonical("c#") == "csharp" and langs.canonical("Python script") == "python"
      and langs.canonical("cobol") == "cobol" and langs.canonical("klingon") is None)
check("an extension maps to its language", langs.spec_for_ext(".rs").key == "rust" and langs.spec_for_ext(".exe") is None)
stub = TMP / "Microsoft" / "WindowsApps"
stub.mkdir(parents=True)
(stub / "python.exe").write_bytes(b"")
check("the Windows Store's stand-in for Python is not Python", langs.which("python", [str(stub)]) == "")
saved = {k: os.environ.get(k) for k in ("FAKE_API_KEY", "MY_SECRET_TOKEN", "TESSA_TEST", "FOR_DISABLE_CONSOLE_CTRL_HANDLER")}
os.environ.update({"FAKE_API_KEY": "k", "MY_SECRET_TOKEN": "t", "TESSA_TEST": "x", "FOR_DISABLE_CONSOLE_CTRL_HANDLER": "1"})
env, removed = langs.child_env({"EXTRA": "1"})
check("her secrets never reach a program: key, token, TESSA_ and her console flag removed",
      not {"FAKE_API_KEY", "MY_SECRET_TOKEN", "TESSA_TEST", "FOR_DISABLE_CONSOLE_CTRL_HANDLER"} & set(env)
      and {"FAKE_API_KEY", "MY_SECRET_TOKEN", "TESSA_TEST"} <= set(removed), str(removed))
check("...and the settings' API key names are always removed", "GEMINI_API_KEY" not in env)
check("his normal environment otherwise stays, with the offline switches on",
      env.get("PATH") == os.environ.get("PATH") and env.get("GOPROXY") == "off" and env.get("GOTOOLCHAIN") == "local"
      and env.get("EXTRA") == "1")
for k, v in saved.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v
checks = []
real_check = langs._check
langs._check = lambda exe, args, env_extra=None: (checks.append((exe, tuple(args))) or ("v1.0", 1.0))
found = langs.look()
langs._check = real_check
keys = [f.key for f in found]
check("the look covers every language the brief names", all(k in keys for k in (
    "python", "javascript", "java", "c", "cpp", "rust", "csharp", "go", "php", "ruby", "powershell", "batch")), str(keys))
check("at most one quick version check per program found, never more",
      len(checks) == len({exe for exe, _a in checks}) and len(checks) <= len([f for f in found if f.installed]),
      f"{len(checks)} checks")
check("Python is found on PATH, where it is", next(f for f in found if f.key == "python").where.startswith("PATH"))
spoken, shown = langs.summary([langs.Found("python", "Python", True, exe="x", where="PATH: x", version="3"),
                               langs.Found("ruby", "Ruby", False, note="not installed")], "ruby")
check("a language that is not installed is said plainly and never installed",
      "Ruby is not installed" in spoken and "never install" in spoken, spoken)
spoken, shown = langs.summary([langs.Found("python", "Python", True, exe="x", where="PATH: x", version="3"),
                               langs.Found("php", "PHP", False, note="not installed")])
check("the list says what runs and what is missing, the details go on screen only",
      spoken.startswith("I can run Python here") and "PHP is not installed" in spoken and "```" in shown
      and "```" not in spoken, spoken)
src = (ROOT / "core" / "tools" / "coding.py").read_text(encoding="utf-8")
check("the look happens in the tool, never at her start", "langs.languages(" in src and
      "languages(" not in (ROOT / "core" / "server.py").read_text(encoding="utf-8"))

print("\n(2) the runner: a job object, a time limit, the keyboard closed\n")
out_file = WORK / "out.txt"
env, _r = langs.child_env({"PYTHONIOENCODING": "utf-8"})
hello = write("hello.py", "print('hello from a test')\n")
r = runner.run_process([PY, "-u", str(hello)], cwd=str(WORK), env=env, timeout_s=30, out_file=out_file, label="t")
check("a program runs and its output comes back", r.code == 0 and b"hello from a test" in r.output, str(r.code))
waits = write("waits.py", "name = input('name? ')\nprint(name)\n")
r = runner.run_process([PY, "-u", str(waits)], cwd=str(WORK), env=env, timeout_s=30, out_file=out_file, label="t")
check("keyboard input is closed: a program that waits gets end of file", r.code != 0 and b"EOFError" in r.output)
forever = write("forever.py", "import subprocess, sys, time\n"
                              "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
                              "print('started a child', flush=True)\ntime.sleep(300)\n")
t0 = time.perf_counter()
r = runner.run_process([PY, "-u", str(forever)], cwd=str(WORK), env=env, timeout_s=3, out_file=out_file, label="t")
took = time.perf_counter() - t0
check("the time limit ends a program that never ends", r.timed_out and took < 15, f"{took:.1f} s")
check("...and its child too: the whole tree is gone", len(r.pids_seen) >= 2 and r.left_after == [] and not outlived(r),
      f"seen {r.pids_seen}, left {r.left_after}, outlived {outlived(r)}")
holder: dict = {}
th = threading.Thread(target=lambda: holder.setdefault("r", runner.run_process(
    [PY, "-u", str(forever)], cwd=str(WORK), env=env, timeout_s=60, out_file=out_file, label="stop-test")))
th.start()
for _ in range(100):
    if runner.running():
        break
    time.sleep(0.05)
time.sleep(1.0)
n = runner.stop_all("test stop")
th.join(30)
r = holder.get("r")
check("STOP ends a running program and everything it started",
      n == 1 and r is not None and r.stopped and r.left_after == [] and len(r.pids_seen) >= 2 and not outlived(r),
      f"stopped {n}, seen {r.pids_seen if r else None}, left {r.left_after if r else None}")
check("nothing is left registered as running", runner.running() == [])
check("programs start with no console window and suspended until they are in the job",
      runner.CREATE_NO_WINDOW & 0x08000000 and "CREATE_SUSPENDED | CREATE_NO_WINDOW" in
      (ROOT / "core" / "tools" / "runner.py").read_text(encoding="utf-8"))

print("\n(3) 3b: running a file he names\n")
ex = executor()
args_py = write("args.py", "import os, sys\nprint('args', sys.argv[1:])\nprint('key', os.environ.get('FAKE_API_KEY'))\n")
os.environ["FAKE_API_KEY"] = "do-not-pass"
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(args_py), "argv": ["3", "4"], "how": "named"}, origin="human"))
os.environ.pop("FAKE_API_KEY", None)
check("a file he named runs on his word, with its own interpreter", "It ran and finished" in said, said[:120])
check("his arguments arrive as separate arguments", "args ['3', '4']" in said)
check("her secret is not in the program's environment", "key None" in said)
check("what it printed is on screen in a block that is never spoken", "```text" in said and "--- what it printed ---" in said)
check("the program's output is loaded into the fence", ex.session.external_content_in_context >= 1
      and "program output" in ex.session.sources)
runs = list((CODE / "runs").iterdir())
check("its full output is saved in a runs folder inside her code folder, never beside his file",
      runs and (runs[-1] / "output.txt").is_file() and not (WORK / "output.txt").exists())
ex = executor()
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(hello), "how": "it"}, origin="human"))
check("a file that came from 'it' is named, with where it is, and asked first",
      said.endswith("yes or no?") and "hello.py" in said and "conversation" in said and ex.ledger.pending is not None, said)
said = ex.answer_confirmation("yes")
check("...and his yes runs it", said is not None and "It ran and finished" in said, str(said)[:120])
ex = executor()
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(hello), "how": "named"}, origin="agent"))
check("a file the model picked asks first", said.endswith("yes or no?"), said)
web = write("fromweb.py", "print('x')\n")
with open(str(web) + ":Zone.Identifier", "w", encoding="utf-8") as fh:
    fh.write("[ZoneTransfer]\nZoneId=3\n")
ex = executor()
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(web), "how": "named"}, origin="human"))
check("a file Windows marks as downloaded from the internet is named and asked about first",
      said.endswith("yes or no?") and "internet" in said, said)
ex = executor()
exe = write("tool.exe", "MZ")
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(exe), "how": "named"}, origin="human"))
check("an .exe never runs through this tool", "never programs or installers" in said, said)
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(WORK / "missing.py"), "how": "named"}, origin="human"))
check("a file that is not there is said plainly", "is not there" in said, said)
rb = write("hello.rb", "puts 1\n")
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(rb), "how": "named"}, origin="human"))
check("a language that is not installed is said plainly, never installed", "Ruby is not installed" in said, said)
bat = write("go.bat", "@echo off\necho hi\n")
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(bat), "argv": ["x"], "how": "named"}, origin="human"))
check("a batch file takes no arguments", "takes no arguments" in said, said)
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(forever), "how": "named", "timeout_s": 2}, origin="human"))
check("a never-ending file stops at his limit and she says so", "time limit of 2 seconds" in said, said[:140])
check("the longest limit is ten minutes", "ten minutes" in new_turn(ex).run(ToolCall(
    "code.run_file", {"path": str(hello), "how": "named", "timeout_s": 900}, origin="human")))
crash = write("dllcrash.py", "import ctypes\nctypes.windll.kernel32.ExitProcess(0xC0000139)\n")
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(crash), "how": "named"}, origin="human"))
check("a Windows crash code is said in words, its number kept on screen",
      "not the version it was built for" in said.split("```")[0] and "0xC0000139" in said, said[:200])
rd = TMP / "plan"
gnu = coding.plan_for(langs.Found("fortran", "Fortran", True, exe="gfortran.exe"), WORK / "a.f90", rd, [])
gcc = coding.plan_for(langs.Found("c", "C", True, exe="gcc.exe", tool="gcc"), WORK / "a.c", rd, [])
go = coding.plan_for(langs.Found("go", "Go", True, exe="go.exe"), WORK / "a.go", rd, [])
check("GNU builds are static, so a different runtime library earlier on PATH cannot break them",
      "-static" in gnu["compile"] and "-static" in gcc["compile"])
check("Go's compile keeps its own counters in the run folder, never his profile, and nothing goes online",
      go["compile_env"].get("APPDATA") == str(rd) and langs.OFFLINE["GOPROXY"] == "off"
      and langs.OFFLINE["GOTOOLCHAIN"] == "local")
fake_git = TMP / "Program Files" / "Git"
(fake_git / "mingw64" / "bin").mkdir(parents=True)
(fake_git / "bin").mkdir()
(fake_git / "cmd").mkdir()
(fake_git / "usr" / "bin").mkdir(parents=True)
for part in (("mingw64", "bin", "git.exe"), ("bin", "bash.exe"), ("cmd", "git.exe"), ("usr", "bin", "perl.exe")):
    fake_git.joinpath(*part).write_bytes(b"")
real_which, real_check, real_his = langs.which, langs._check, langs._his_path
langs.which = lambda name, path=None: str(fake_git / "mingw64" / "bin" / "git.exe") if name == "git" else ""
langs._check = lambda exe, args, env_extra=None: ("This is perl 5, version 42" if exe.endswith("perl.exe") else "", 1.0)
langs._his_path = lambda: []
found_bash = langs._git_bash()
looked = {f.key: f for f in langs.look()}
langs.which, langs._check, langs._his_path = real_which, real_check, real_his
check("Git's bash is found even when the git on PATH is the one under mingw64",
      bool(found_bash) and Path(found_bash).resolve() == (fake_git / "bin" / "bash.exe").resolve(), found_bash)
perl = looked.get("perl")
check("with no Perl on PATH, the Perl inside Git is found the way Git's bash is, and said to be Git's copy",
      perl is not None and perl.installed and Path(perl.exe).resolve() == (fake_git / "usr" / "bin" / "perl.exe").resolve()
      and perl.note == langs.GIT_PERL, str(perl))
spoken, _shown = langs.summary(list(looked.values()), "perl")
check("asked about Perl, she says she can run it with the copy inside Git, never that it is not installed",
      spoken == "Yes, Emperor. I can run Perl here, with the copy that comes inside Git for Windows." and
      "not installed" not in spoken, spoken)
err = write("broken.py", "print(undefined_name)\n")
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(err), "how": "named"}, origin="human"))
check("an error is reported with its exit code, read from the output, no model call",
      "stopped with an error" in said and "exit code 1" in said, said[:160])

print("\n(4) 3c: writing a program with her chat model only\n")
PROGRAM = "```python\nfor n in range(3):\n    print('line', n)\n```"
fake = FakeLLM(lambda text: PROGRAM)
coding.bind_brain(fake)
ex = executor()
said = new_turn(ex).run(ToolCall("code.write", {"language": "python", "task": "prints three lines"}, origin="human"))
rows = coding.written()
check("she writes one file in her code folder and keeps its path and sha256",
      len(rows) == 1 and Path(rows[0]["path"]).is_file() and Path(rows[0]["path"]).parent.parent == CODE
      and rows[0]["sha256"] == coding.sha256_of(Path(rows[0]["path"])), str(rows))
mine = Path(rows[0]["path"])
check("the file name is plain and has the language's own extension", mine.suffix == ".py" and mine.stem == mine.parent.name)
check("she shows the code and says in one line what and where",
      "```python\nfor n in range(3):" in said and said.startswith("I wrote a Python program called")
      and "your Tessa Code folder" in said.split("\n")[0], said[:140])
check("the chat model was asked once, critically", len(fake.calls) == 1 and fake.calls[0][2].get("quality") == "critical")
spoken_line = said.split("\n```")[0]
check("what she says names no path and no code", "```" not in spoken_line and ":\\" not in spoken_line, spoken_line)
ex = executor()
said2 = new_turn(ex).run(ToolCall("code.write", {"language": "python", "task": "prints three lines"}, origin="human"))
rows = coding.written()
check("never over an existing file: a second program gets a new folder", len(rows) == 2 and rows[0]["path"] != rows[1]["path"])
coding.bind_brain(FakeLLM(lambda text: PROGRAM, name="local:qwen"))
said = executor().run(ToolCall("code.write", {"language": "python", "task": "x"}, origin="human"))
check("she never writes code with the small local model", "local model" in said and len(coding.written()) == 2, said)
coding.bind_brain(FakeLLM(lambda text: RateLimited("429", "spent", transient=False)))
said = executor().run(ToolCall("code.write", {"language": "python", "task": "x"}, origin="human"))
check("a spent allowance is said, and nothing is written", "allowance" in said and len(coding.written()) == 2, said)
count = len(coding.written())
coding.bind_brain(FakeLLM(lambda text: "```python\ndef is_prime(n):\n    if n"))
said = executor().run(ToolCall("code.write", {"language": "python", "task": "x"}, origin="human"))
check("a reply cut off inside its code block is never saved as a program",
      "cut off" in said and len(coding.written()) == count, said)
stopped = FakeLLM(lambda text: PROGRAM)
stopped.last_finish = "RECITATION"
coding.bind_brain(stopped)
said = executor().run(ToolCall("code.write", {"language": "python", "task": "x"}, origin="human"))
check("a reply the model did not finish normally is refused, with the model's reason",
      "recitation" in said and len(coding.written()) == count, said)
coding.bind_brain(fake)
said = executor().run(ToolCall("code.write", {"language": "cobol", "task": "prints hello"}, origin="human"))
check("a language that is not installed may be written, and she says she cannot run it",
      "cannot run COBOL" in said and coding.written()[-1]["path"].endswith(".cob"), said[:140])
real_synced = coding.synced
coding.synced = lambda p: "OneDrive (test)"
said = executor().run(ToolCall("code.write", {"language": "python", "task": "x"}, origin="human"))
coding.synced = real_synced
check("a code folder inside a synced folder is refused before anything is written", "metered" in said, said)

print("\n(5) 3d: running a program she wrote: the card, every time\n")
ex = executor()
said = new_turn(ex).run(ToolCall("code.run_file", {"path": str(mine), "how": "named"}, origin="human"))
pending = list(ex.approvals.pending.values())
check("a file she wrote is hers even when he names it: it goes to the card",
      len(pending) == 1 and pending[0].tool == "code.run_mine", said[:120])
req = pending[0]
check("the card says agent, the code's author", req.provenance == "agent")
check("the card carries the language, the file, the exact code, its sha256, where, the limit and that it runs as him",
      all(k in req.args for k in ("language", "file", "code", "sha256", "runs_in", "time_limit", "runs_as"))
      and req.args["code"] == mine.read_text(encoding="utf-8") and "not a sandbox" in req.args["runs_as"])
check("everything on the card is frozen", set(req.args) <= set(req.frozen), str(sorted(set(req.args) - set(req.frozen))))
check("what she says names it in plain words with its line count, never a tool name",
      "lines of code" in said and "code.run_mine" not in said and ":\\" not in said.split("```")[0], said)
record = ex.execute_approved(req.request_id)
check("his yes on the card runs it once", "It ran and finished" in record["result"]["said"])
said = new_turn(ex).run(ToolCall("code.run_mine", {"which": "last"}, origin="human"))
check("'the program you wrote' is the last one, and one she cannot run is said before any card",
      "cannot run COBOL" in said and not ex.approvals.pending, said)
said = new_turn(ex).run(ToolCall("code.run_mine", {"path": str(mine)}, origin="human"))
req = list(ex.approvals.pending.values())[-1]
target = Path(req.args["path"])
target.write_text(target.read_text(encoding="utf-8") + "print('changed')\n", encoding="utf-8")
try:
    ex.execute_approved(req.request_id)
    refused = False
except ApprovalError as exc:
    refused = "changed after the card" in exc.message
check("if the file changed after the card, nothing runs", refused)
new_turn(ex).run(ToolCall("code.run_mine", {"name": "three-lines-2"}, origin="human"))
waiting = len(ex.approvals.pending)
check("a yes typed or spoken is never an approval of her program: the card stays up, nothing runs",
      ex.answer_confirmation("yes") is None and len(ex.approvals.pending) == waiting >= 1)
check("her own program is never offered to the intent model", "code.run_mine" in
      __import__("core.brain.intent_model", fromlist=["NEVER_OFFERED"]).NEVER_OFFERED)
FAILING = "```python\nprint(1/0)\n```"
coding.bind_brain(FakeLLM(lambda text: FAILING))
ex = executor()
new_turn(ex).run(ToolCall("code.write", {"language": "python", "task": "divides one by zero"}, origin="human"))
bad = Path(coding.written()[-1]["path"])
new_turn(ex).run(ToolCall("code.run_mine", {"path": str(bad)}, origin="human"))
req = list(ex.approvals.pending.values())[-1]
record = ex.execute_approved(req.request_id)
check("when her program fails she shows the error and offers a fixed version",
      "ZeroDivisionError" in record["result"]["said"] and "Say fix the program" in record["result"]["said"])
SHAPED = "AIza" + "B" * 35
os.environ["FAKE_API_KEY"] = "q7Zx9-plain-value-of-hers-31"
coding.remember_run({"file": str(bad), "code": 1, "error_tail": "Traceback\nZeroDivisionError: division by zero\n"
                     f"saw q7Zx9-plain-value-of-hers-31 and {SHAPED}\n", "at": "now"})
fixer = FakeLLM(lambda text: "```python\nprint(1)\n```")
coding.bind_brain(fixer)
ex = executor()
said = new_turn(ex).run(ToolCall("code.write", {"fix": "last", "run": True}, origin="human"))
os.environ.pop("FAKE_API_KEY", None)
prompt = fixer.calls[-1][1] if fixer.calls else ""
check("only the error goes to the model, fenced as data, with secrets taken out",
      "UNTRUSTED EXTERNAL CONTENT" in prompt and "ZeroDivisionError" in prompt
      and "q7Zx9-plain-value-of-hers-31" not in prompt and SHAPED not in prompt, prompt[-200:])
check("the fix is saved as a new file", coding.written()[-1]["path"] != str(bad) and coding.written()[-1]["fix_of"] == str(bad))
check("the fence wins: the fix's card is held back while the error sits in her context",
      not ex.approvals.pending and "the fence wins" in said, said[-200:])
coding.bind_brain(fake)
ex = executor()
said = new_turn(ex).run(ToolCall("code.write", {"language": "python", "task": "prints three lines", "run": True}, origin="human"))
check("write it and run it: written, then the card in the same turn",
      len(ex.approvals.pending) == 1 and list(ex.approvals.pending.values())[0].tool == "code.run_mine", said[-200:])

print("\n(6) 3e: git, as a fixed list of actions, through a fake that records the command\n")
calls: list[list[str]] = []
answers: dict[str, tuple[int, str]] = {}


def fake_git(argv, cwd, timeout):
    calls.append(list(argv))
    key = " ".join(argv[argv.index("core.quotepath=false") + 1:])
    for k, v in answers.items():
        if key.startswith(k):
            return v
    return 0, ""


gittools._run_git = fake_git
repo = TMP / "repo"
(repo / ".git").mkdir(parents=True)
(repo / "app.py").write_text("x\n", encoding="utf-8")
answers.update({"status": (0, "## main...origin/main [ahead 1]\n M app.py\n?? new.txt\n"),
                "log": (0, "abc123 2026-10-24 Gerald First commit\n"),
                "diff --cached --name-only": (0, "app.py\n.env\n"),
                "rev-parse --abbrev-ref HEAD": (0, "main\n"),
                "rev-parse --abbrev-ref --symbolic-full-name": (0, "origin/main\n"),
                "rev-list --count": (0, "2\n"), "remote get-url": (0, "https://user:secret@github.com/me/repo.git\n")})
ex = executor()
said = new_turn(ex).run(ToolCall("git.repo", {"action": "status", "folder": str(repo)}, origin="human"))
check("git status runs on his word, as a fixed argument list, never a shell line",
      calls and calls[-1][-3:] == ["status", "--porcelain=v1", "--branch"] and calls[-1][1] == "-C", str(calls[-1:]))
check("...and she says the state in plain words", "On branch main" in said and "1 file changed" in said, said[:120])
new_turn(ex).run(ToolCall("git.repo", {"action": "log", "folder": str(repo), "n": 5}, origin="human"))
check("git log takes his count", calls[-1][-4:-1] == ["log", "-n5", "--date=short"], str(calls[-1][-4:]))
said = new_turn(ex).run(ToolCall("git.repo", {"action": "commit", "folder": str(repo), "message": "tidy up"}, origin="human"))
check("a commit asks first, names its files and any that may hold a secret",
      said.endswith("yes or no?") and "app.py" in said and ".env" in said and "may hold a secret" in said
      and not any("commit" in c and "-m" in c for c in calls), said)
said = ex.answer_confirmation("yes")
check("...and runs only after his yes, with his message", calls[-1][-3:] == ["commit", "-m", "tidy up"], str(calls[-1][-3:]))
said = new_turn(ex).run(ToolCall("git.repo", {"action": "commit", "folder": str(repo)}, origin="human"))
check("with no message she proposes one and asks", "a message I propose" in said and "Update" in said, said)
ex.ledger.clear()
said = new_turn(ex).run(ToolCall("git.repo", {"action": "branch", "name": "feature/x", "folder": str(ROOT)}, origin="human"))
check("a change to a protected repository asks first", said.endswith("yes or no?") and "protected" in said, said)
said = new_turn(ex).run(ToolCall("git.repo", {"action": "branch", "name": "--force", "folder": str(repo)}, origin="human"))
check("a branch name that is an option is refused", "not a branch name" in said, said)
said = new_turn(ex).run(ToolCall("git.repo", {"action": "reset", "folder": str(repo)}, origin="human"))
check("anything outside the list is refused", "do not do git reset" in said, said)
ex = executor()
said = new_turn(ex).run(ToolCall("git.push", {"folder": str(repo)}, origin="human"))
req = list(ex.approvals.pending.values())[-1]
check("a push is the card, and says it goes online before he answers",
      req.tool == "git.push" and "goes online" in said and "on the internet" in req.args["goes_online"], said)
check("the card never shows a credential from the remote's address", "secret" not in json.dumps(req.args), req.args["url"])
before = len(calls)
ex.execute_approved(req.request_id)
pushed = calls[-1]
check("the push is exactly remote and branch, never forced", pushed[-3:] == ["push", "origin", "main"]
      and not any(a in ("--force", "-f", "--force-with-lease", "--mirror") for a in pushed), str(pushed[-4:]))
try:
    phrasings.match("force push the tessa folder")
    said = ""
except reading.Ask as ask:
    said = ask.line
check("force push, reset --hard and clean are refused in plain words", "cannot do that in this step" in said, said)

print("\n(7) routing: run reaches the run tool, never the shell; old routes stay\n")


def routed(text, refs=None):
    with reading.context(refs or []):
        try:
            c = phrasings.match(text)
        except reading.Ask as ask:
            return ("ask", ask.line)
    return (c.name, c.args) if c else (None, None)


name, args = routed(r"run C:\Users\x\Documents\hello.py")
check("'run hello.py' with its place reaches code.run_file, never shell.execute", name == "code.run_file", str(name))
split = __import__("core.brain.intents", fromlist=["split_clauses"]).split_clauses
spaced = r"run C:\Users\x\Documents\Tessa Code\tessa-test\hello.py with 3 and 4"
name, args = routed(split(spaced)[0])
check("a full path with a space in it is one file, and his arguments stay with it",
      len(split(spaced)) == 1 and name == "code.run_file" and args.get("argv") == ["3", "4"]
      and args.get("path", "").endswith(r"Tessa Code\tessa-test\hello.py") and args.get("how") == "named", str(args))
check("a sentence with two jobs still splits", len(split("run chrome and open notes.txt with notepad")) == 2
      and len(split("run hello.py and then open my notes with notepad")) == 2)
placed = "run sum.js on my desktop with 3 and 4"
name, args = routed(split(placed)[0])
check("words between the file and its arguments keep them together",
      len(split(placed)) == 1 and name == "code.run_file" and args.get("argv") == ["3", "4"], str(args))
check("run git status in a folder is still the shell, as the exam pins it", routed("run git status in the tessa folder")[0] == "shell.execute")
check("'execute dir' is still the shell", routed("execute dir inside my videos folder")[0] == "shell.execute")
check("an .exe is never the run tool", routed("run setup.exe")[0] == "shell.execute")
check("'make vs code full screen' is not a program request", routed("make vs code full screen")[0] != "code.write")
check("'write a python program that ...' is code.write", routed("write a python program that prints hello")[0] == "code.write")
check("a task with 'and' in it stays one request",
      __import__("core.brain.intents", fromlist=["split_clauses"]).split_clauses(
          "write a c program that adds 2 and 3 and prints the sum") == ["write a c program that adds 2 and 3 and prints the sum"])
check("'run it' with nothing named asks", routed("run it")[0] == "ask")
now = time.time()
coding.remember_written(mine, coding.sha256_of(mine), "python", "test")
name, args = routed("run it", [reading.Referent("file", str(mine), now)])
check("'run it' after she wrote a program is her card", name == "code.run_mine" and args.get("path") == str(mine), str(name))
check("forever is never a time limit", routed("keep the program you wrote running forever")[0] == "ask")
check("over ten minutes asks", routed("run the program you wrote for twenty minutes")[0] == "ask")

print("\n(8) what she shows is not what she says; STOP reaches a running program\n")
from core import server as S
from core.brain.humanness import _FENCED

check("a code block is shown, never spoken", S._speakable("It ran.\n```text\nC:\\x\\y\n```") == "It ran.")
check("...the same rule on the typed path and in the voice loop",
      "_FENCED.sub(' ', spoken)" in ast.unparse(ast.parse((ROOT / "core" / "brain" / "typed_turn.py").read_text(
          encoding="utf-8"))) and "_FENCED.sub(' ', routed.speech)" in ast.unparse(ast.parse(
              (ROOT / "core" / "voice" / "loop.py").read_text(encoding="utf-8")))
      and _FENCED.sub(" ", "a\n```python\nprint(1)\n```\nb").split() == ["a", "b"])
check("a typed stop reaches a running program", bool(S._STOP_WORDS.match("stop")) and bool(S._STOP_WORDS.match("Tessa, stop it")))
tree = ast.parse((ROOT / "core" / "server.py").read_text(encoding="utf-8"))
cancel = ast.unparse(next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_h_agent_cancel"))
message = ast.unparse(next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_h_agent_message"))
respond = ast.unparse(next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_h_permission_respond"))
check("cmd.agent.cancel stops running programs", "_runner.stop_all('STOP')" in cancel)
check("a typed stop stops them before the turn is queued", "_runner.stop_all('he typed stop')" in message)
check("a card for her program or a push is never remembered, whatever the card says",
      respond.count("not in _NEVER_REMEMBERED") == 2 and S._NEVER_REMEMBERED == {"code.run_mine", "git.push"})
check("after his yes the result is shown in full and spoken without the code", "spoken=_speakable(said)" in respond)
check("the voice loop's stop stops programs too", "_runner.stop_all('he said stop')" in ast.unparse(
    ast.parse((ROOT / "core" / "voice" / "loop.py").read_text(encoding="utf-8"))))

langs.find = REAL_FIND
coding.langs.find = REAL_FIND
print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
