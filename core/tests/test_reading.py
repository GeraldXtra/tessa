from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


TMP = Path(tempfile.mkdtemp(prefix="tessa-reading-test-"))

print("\n(1e) chrome.pid is removed only by the process that started that Chrome\n")
from core.tools import browser

LIVE_PID_FILE = browser.PID_FILE
browser.PID_FILE = TMP / "chrome.pid"
browser.PID_FILE.write_text(json.dumps({"pid": 4242, "profile": str(browser.DEFAULT_PROFILE)}), encoding="utf-8")
session = browser.BrowserSession()
session.close(reason="daemon shutdown")
check("close() in a process that started no Chrome leaves chrome.pid alone", browser.PID_FILE.exists())
session.close_within(reason="daemon shutdown", grace_s=0.5, polite_s=0.1)
check("close_within() in that process leaves it alone too", browser.PID_FILE.exists())
browser.SESSION.close(reason="daemon shutdown")
check("the import-time atexit session leaves it alone", browser.PID_FILE.exists())

import psutil

real_iter = psutil.process_iter
fake = types.SimpleNamespace(info={"pid": 5151, "name": "chrome.exe",
                                   "cmdline": ["chrome.exe", f"--user-data-dir={browser.DEFAULT_PROFILE}"]})
psutil.process_iter = lambda attrs=None: iter([fake])
try:
    mine = browser.BrowserSession()
    mine._pw = types.SimpleNamespace(chromium=types.SimpleNamespace())
    mine._record_pid()
finally:
    psutil.process_iter = real_iter
written = json.loads(browser.PID_FILE.read_text(encoding="utf-8"))
check("a session that started its Chrome records it", written.get("pid") == 5151 and mine.wrote_pid_file == browser.PID_FILE,
      str(written))
mine.close(reason="asked")
check("and removes that file when it closes", not browser.PID_FILE.exists())

browser.PID_FILE.write_text(json.dumps({"pid": 6262, "profile": str(browser.DEFAULT_PROFILE)}), encoding="utf-8")
again = browser.BrowserSession()
again.chrome_pid = 5151
again.wrote_pid_file = browser.PID_FILE
again.close(reason="asked")
check("a file that now names another Chrome is left for its owner", browser.PID_FILE.exists())
browser.PID_FILE = LIVE_PID_FILE
check("the live chrome.pid path was never touched by this suite", browser.PID_FILE == LIVE_PID_FILE)

import dataclasses
import os
from datetime import datetime, timedelta
from typing import Any

from core.brain import appindex, reading
from core.brain.conversation import Conversation
from core.brain.executor import Executor
from core.brain.provenance import SessionContext
from core.brain.router import Intent, Router, history_kwargs
from core.brain.tools_local import ToolCall
from core.brain.typed_turn import run_typed_turn
from core.security.guard import Guard, Verdict, whole_folder
from core.system.winapi import fileindex
from core.tools import REGISTRY
from core.tools.base import ToolError

HOME = Path.home()
DESKTOP, DOCS, DOWNLOADS = HOME / "Desktop", HOME / "Documents", HOME / "Downloads"
REPO = ROOT

fake_files = fileindex.FileIndex(cache_path=TMP / "fileindex.txt")
for attr in ("build_fast", "build_full", "build_async", "_save"):
    setattr(fake_files, attr, lambda *a, **k: None)
fake_files._install([str(TMP / "a" / "budget.xlsx"), str(TMP / "b" / "budget.xlsx"), str(TMP / "c" / "solo-report.pdf")],
                    "full")
saved_files, fileindex._INDEX = fileindex._INDEX, fake_files
fake_apps = appindex.AppIndex(cache_path=TMP / "appindex.json")
for attr in ("build_fast", "build_slow", "build_async", "_save", "refresh_if_stale"):
    setattr(fake_apps, attr, lambda *a, **k: None)
fake_apps._fast_built = True
saved_apps, appindex._INDEX = appindex._INDEX, fake_apps


class Audit:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append(self, **kw: Any) -> None:
        self.rows.append(kw)


def route(text: str, router: Router | None = None, history: list[str] | None = None):
    return (router or Router()).route(text, history=history)


def call_of(out) -> tuple[str, dict]:
    return (out.calls[0].name, dict(out.calls[0].args)) if out.calls else ("", {})


def asked(out) -> bool:
    return out.intent is Intent.TOOL and not out.calls and "?" in (out.speech or "")


print("\n(3a) a file he names is the target, in the folder he names\n")
for text, tool, want in [
    ("delete old-notes.txt from my desktop", "fs.delete", DESKTOP / "old-notes.txt"),
    ("read notes-2031.txt on my desktop", "fs.read", DESKTOP / "notes-2031.txt"),
    ("show me plan.md from the tessa folder in explorer", "fs.reveal", REPO / "plan.md"),
    ("copy plan.md from the tessa folder to my desktop", "fs.copy", REPO / "plan.md"),
    ("move notes.txt from my desktop into my documents folder", "fs.move", DESKTOP / "notes.txt"),
    ("rename notes.txt on my desktop to old-notes.txt", "fs.rename", DESKTOP / "notes.txt"),
    ("open agenda.docx from my documents", "app.open_folder", DOCS / "agenda.docx"),
]:
    name, args = call_of(route(text))
    check(f"{text!r} -> {tool} on the file, not the folder", name == tool and Path(args.get("path", "")) == want,
          f"{name} {args}")
name, args = call_of(route("move notes.txt from my desktop into my documents folder"))
check("the destination of a move is the folder he named", Path(args.get("to", "")) == DOCS, str(args))
out = route("read budget.xlsx")
check("two files with that name: she asks which, naming where they are",
      asked(out) and "budget.xlsx" in out.speech and " a " in f" {out.speech} " and " b" in out.speech, out.speech)
name, args = call_of(route("read solo-report.pdf"))
check("exactly one file with that name: that file", name == "fs.read" and args.get("path") == str(TMP / "c" / "solo-report.pdf"),
      str(args))
out = route("read nowhere-to-be-found.txt")
check("no file with that name: she says so and asks where", asked(out) and "cannot find" in out.speech, out.speech)
missing = TMP / "nothing-here" / "ghost.txt"
ex = Executor(session=SessionContext(), audit=Audit())
said = ex.run(ToolCall("fs.delete", {"path": str(missing)}, origin="human"))
check("a delete of a file that is not there is refused before the card", not ex.approvals.pending and "not there" in said, said)
moved: list[dict] = []
saved_move = REGISTRY["fs.move"]
REGISTRY["fs.move"] = dataclasses.replace(saved_move, handler=lambda path, to: (_ for _ in ()).throw(
    ToolError(f"{path} is not there", "Give me another path and I will open it.")) if not Path(path).exists() else moved.append(path))
said = ex.run(ToolCall("fs.move", {"path": str(missing), "to": str(TMP)}, origin="human"))
REGISTRY["fs.move"] = saved_move
check("a move of a file that is not there says so, and nothing moves", "not there" in said and not moved, said)

print("\n(3b) place and time words are never glued into an argument\n")
for text, tool, key, want in [
    ("create a text file called todo.txt on my desktop", "system.files.create_file", "path", str(DESKTOP / "todo.txt")),
    ("make a new folder named receipts 2031 in the downloads folder", "system.files.create_folder", "path",
     str(DOWNLOADS / "receipts 2031")),
    ("restore notepad from the taskbar", "system.window.restore", "name", "notepad"),
    ("bring calculator back up from the taskbar", "system.window.restore", "name", "calculator"),
    ("run git status in the tessa folder", "shell.execute", "command", "git status"),
    ("snap chrome to the left half of the screen", "system.window.snap", "name", "chrome"),
]:
    name, args = call_of(route(text))
    check(f"{text!r} -> {key} {want!r}", name == tool and args.get(key) == want, f"{name} {args}")
name, args = call_of(route("run git status in the tessa folder"))
check("the place a command runs in is its working folder", args.get("cwd") == str(REPO), str(args))
name, args = call_of(route("schedule a tweet for 9am tomorrow saying good morning lagos"))
nine = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
check("a scheduled tweet is scheduled: the words alone, the time in at",
      name == "system.x.post_tweet" and args.get("text") == "good morning lagos" and args.get("at") == str(int(nine.timestamp())),
      f"{name} {args}")
check("and never posted now", name != "x.post")
check("8pm is 20:00 today", reading.when("for 8pm") == "20:00")
check("in 30 minutes is +30m", reading.when("in 30 minutes") == "+30m")
out = route("schedule a tweet at six saying hello")
check("six with no morning or evening: she asks which", asked(out) and "morning" in out.speech, out.speech)

print("\n(3c) a word that names nothing is never a target\n")
for text in ("close it", "minimise it", "rename that to final.txt", "delete the file", "open the folder", "delete it"):
    out = route(text)
    check(f"{text!r}: she asks which, and nothing is called", asked(out), f"{out.intent} {out.calls} {out.speech!r}")
r = Router()
route("read report-q4.txt in my documents", r)
name, args = call_of(route("copy it to my desktop", r))
check("it takes its target from the conversation (an earlier turn)",
      name == "fs.copy" and args.get("path") == str(DOCS / "report-q4.txt"), f"{name} {args}")
r.forget()
check("after forget that, it names nothing again", asked(route("copy it to my desktop", r)))
name, args = call_of(route("copy it to my desktop", history=["read report-q3.txt in my documents"]))
check("it takes its target from the conversation history it is given",
      name == "fs.copy" and args.get("path") == str(DOCS / "report-q3.txt"), f"{name} {args}")
r = Router()
route("minimise chrome", r)
name, args = call_of(route("close it", r))
check("a window verb's it is the window named before", name == "win.close" and args.get("name") == "chrome", f"{name} {args}")
check("this window is the one in front, not a question", call_of(route("snap this window right"))[1].get("name") == "this window")
for text in ("Tessa, what's in my inbox?", "check my email", "do I have any new emails"):
    out = route(text)
    check(f"{text!r}: she says plainly she cannot read his email yet", not out.calls and out.speech == reading.EMAIL_LINE,
          out.speech)
out = route("draft a reply to the second one", history=["what's in my inbox?"])
check("a reply to an email she cannot read is the same plain answer, never an X card",
      not out.calls and out.speech == reading.EMAIL_REPLY_LINE, out.speech)
name, args = call_of(route("are you running"))
check("you is never a process to look for", name != "proc.find", f"{name} {args}")

print("\n(3d) her name is how he addresses her, never a target\n")
check("bare tessa is not a folder", reading.folder_of("tessa") is None)
check("the tessa folder is her repo folder", reading.folder_of("the tessa folder") == REPO)
for text, want in [("show me the dev folder tessa", REPO.parent), ("open the tessa folder", REPO),
                   ("take me to my documents tessa", DOCS), ("how big is the dev folder tessa", REPO.parent)]:
    name, args = call_of(route(text))
    check(f"{text!r} -> {want}", Path(args.get("path", "")) == want, f"{name} {args}")
for text in ("tessa, head to bed", "tessa, time to sleep"):
    out = route(text)
    check(f"{text!r} never opens her repo folder", out.intent is Intent.SLEEP and not out.calls, f"{out.intent} {out.calls}")

print("\n(3e) a question or a draft is never a publish\n")
publish = {"x.post", "x.reply", "x.quote", "x.thread", "x.send_dm", "system.x.post_tweet", "system.x.post_reply"}
for text, want in [("what tweets have I got scheduled", "system.x.schedule_status"),
                   ("have I got any tweets lined up for later", "system.x.schedule_status")]:
    check(f"{text!r} -> {want}", call_of(route(text))[0] == want, str(call_of(route(text))))
for text in ("how many tweets have I posted today", "draft a reply to post one for me", "write a reply in my voice",
             "tweet that", "post that"):
    out = route(text)
    check(f"{text!r} raises no publish card", not ({c.name for c in out.calls} & publish), str(out.calls))
name, args = call_of(route("post this on x: the orb is live"))
check("he asked to post: the card carries his words only", name == "x.post" and args.get("text") == "the orb is live",
      f"{name} {args}")

print("\n(3f) a keyword inside another question does not take it over\n")
check("today in a rate question is not a date question", route("what's the dollar to naira rate today").intent is not Intent.DATE)
check("today in a weather question is not a date question", route("is it going to rain today in lagos").intent is not Intent.DATE)
check("the date question still is one", route("what's the date today").intent is Intent.DATE)
check("heaviest processes sort by memory", call_of(route("which processes are the heaviest")) == ("proc.top", {"by": "memory"}))
check("most cpu sorts by cpu", call_of(route("show me the processes using the most cpu")) == ("proc.top", {"by": "cpu"}))
name, args = call_of(route("find the word invoice on my screen"))
check("finding something on the screen is a screen search", name == "system.screen.read", name)
name, args = call_of(route("find button.png from my pictures on the screen"))
check("finding a picture on the screen keeps the picture's folder",
      name == "system.screen.find" and args.get("path") == str(HOME / "Pictures" / "button.png"), f"{name} {args}")

print("\n(3g) sleep: hers is her standing down; the laptop's is red, the card every time\n")
for text in ("head back to bed", "time for you to sleep", "you can sleep now", "it's late, get some sleep", "so off you go, head to bed"):
    out = route(text)
    check(f"{text!r} is her sleep, nothing called", out.intent is Intent.SLEEP and not out.calls, f"{out.intent} {out.calls}")
spec = REGISTRY["sys.sleep"]
check("sys.sleep is red, holds, under system.shutdown", spec.tier == "red" and spec.holds and spec.capability == "system.shutdown")
check("put the laptop to sleep still means the laptop", call_of(route("put the laptop to sleep"))[0] == "sys.sleep")
slept: list[int] = []
REGISTRY["sys.sleep"] = dataclasses.replace(spec, handler=lambda confirmed=False: slept.append(1) or {})
ex = Executor(session=SessionContext(), audit=Audit())
said = ex.run(ToolCall("sys.sleep", {}, origin="human"))
check("the laptop's sleep raises the card and does not sleep", bool(ex.approvals.pending) and not slept, said)
said2 = ex.answer_confirmation("yes")
check("a spoken yes does not put it to sleep either", not slept, str(said2))
REGISTRY["sys.sleep"] = spec

print("\n(3h) a whole folder of his, or a repository root, always asks first\n")
check("his Desktop is a whole folder", whole_folder(str(DESKTOP)) == "your whole Desktop folder")
check("a drive root is a whole folder", whole_folder("C:\\").startswith("the whole C:"))
check("her repository root is a whole folder", whole_folder(str(REPO)) == f"the whole {REPO.name} repository")
check("a file inside a folder is not", whole_folder(str(DESKTOP / "notes.txt")) == "")
guard = Guard(ROOT / "core" / "config" / "permissions.yaml")
check("the guard confirms a mutation on his Downloads, even for him",
      guard.evaluate("fs.move", "human", str(DOWNLOADS), mutating=True).verdict is Verdict.CONFIRM)
check("...and still allows his move of a single file",
      guard.evaluate("fs.move", "human", str(TMP / "x.txt"), mutating=True).verdict is Verdict.ALLOW)
ran: list[dict] = []
saved_specs = {n: REGISTRY[n] for n in ("fs.move", "fs.copy", "fs.rename")}
for n in saved_specs:
    REGISTRY[n] = dataclasses.replace(saved_specs[n], handler=lambda _n=n, **k: ran.append({_n: k}) or
                                      {"name": "x", "to": "y", "path": "p", "was": "w"})
try:
    for tool, args in [("fs.move", {"path": str(DOWNLOADS), "to": str(DOCS)}),
                       ("fs.copy", {"path": str(DESKTOP), "to": str(DOCS)}),
                       ("fs.rename", {"path": str(HOME / "Music"), "to": "songs"}),
                       ("fs.move", {"path": str(REPO), "to": str(DOCS)})]:
        ex = Executor(session=SessionContext(), audit=Audit())
        ran.clear()
        said = ex.run(ToolCall(tool, args, origin="human"))
        check(f"{tool} of {Path(args['path']).name} from his own words holds first", not ran and said.endswith("yes or no?"),
              said)
        check("...and says it in words, with no path read out", ":\\" not in said and tool not in said, said)
        ex.answer_confirmation("yes")
        check("...and his yes runs it", len(ran) == 1, str(ran))
    ex = Executor(session=SessionContext(), audit=Audit())
    ran.clear()
    ex.run(ToolCall("fs.move", {"path": str(TMP / "one.txt"), "to": str(TMP / "b")}, origin="human"))
    check("his move of one file runs as before, without a question", len(ran) == 1, str(ran))
finally:
    REGISTRY.update(saved_specs)

print("\n(spoken = typed) both paths read through the same router call\n")
loop_src = (ROOT / "core" / "voice" / "loop.py").read_text(encoding="utf-8")
typed_src = (ROOT / "core" / "brain" / "typed_turn.py").read_text(encoding="utf-8")
check("the voice turn routes through router.route with the conversation",
      "self.router.route(heard, **history_kwargs(self.router, self.conversation))" in loop_src)
check("the typed turn routes through router.route with the conversation",
      "router.route(typed, **history_kwargs(router, conversation))" in typed_src)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []
        self.session = None
        self.approvals = type("A", (), {"pending": {}})()

    def answer_confirmation(self, text: str) -> None:
        return None

    def run(self, call: ToolCall) -> str:
        self.calls.append(call)
        return "Done."


rec = Recorder()
convo = Conversation(path=TMP / "conversation.json")
convo.save = lambda: None
turn = run_typed_turn("delete old-notes.txt from my desktop", router=Router(), executor=rec, brain=None, conversation=convo)
check("a typed request reaches the executor with the file, not the folder",
      rec.calls and rec.calls[0].name == "fs.delete" and rec.calls[0].args.get("path") == str(DESKTOP / "old-notes.txt"),
      str(rec.calls))
check("history_kwargs passes the conversation's own lines", history_kwargs(Router(), convo) == {"history": []})

fileindex._INDEX, appindex._INDEX = saved_files, saved_apps

import shutil

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
