from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import socket
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = Path(tempfile.mkdtemp(prefix="tessa-turns-test-"))
LIVE = [ROOT / "data" / "audit.log", ROOT / "data" / "memory" / "conversation.json",
        ROOT / "data" / "tessa-actions.jsonl"]


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "absent"


LIVE_BEFORE = {str(p): digest(p) for p in LIVE}
NET = {"attempts": 0}
_real_connect = socket.socket.connect


def _fenced_connect(self: socket.socket, address: Any, *a: Any, **k: Any) -> Any:
    host = address[0] if isinstance(address, tuple) and address else address
    if str(host) in ("127.0.0.1", "::1", "localhost"):
        return _real_connect(self, address, *a, **k)
    NET["attempts"] += 1
    raise OSError("network fenced in test_turns")


def _fenced_create(*a: Any, **k: Any) -> Any:
    NET["attempts"] += 1
    raise OSError("network fenced in test_turns")


socket.socket.connect = _fenced_connect
socket.create_connection = _fenced_create

from core.brain import memory

memory.ACTION_LOG = TMP / "actions.jsonl"

import yaml

from core.brain import conversation, phrasings, reading
from core.brain.executor import Executor
from core.brain.llm.base import LLMAdapter
from core.brain.provenance import ExternalContent, SessionContext
from core.brain.router import Intent, normalise, score_intents
from core.brain.tools_local import ToolCall
from core.exam import items as I
from core.tools import REGISTRY, coding, langs, runner

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


CODE = TMP / "Tessa Code"
coding.DATA = TMP / "data-code"
coding.code_folder = lambda: CODE
FOUND_PY = langs.Found("python", "Python", True, exe=sys.executable, where="test", version="Python test")
REAL_FIND = langs.find


def fake_find(key: str):
    if key == "python":
        return FOUND_PY
    return langs.Found(key, langs.display(key), False, note="not installed")


langs.find = fake_find
RAN: list[list[str]] = []


def fake_runner(output: str, code: int = 0):
    def run_process(argv, *, cwd, env, timeout_s, out_file, label):
        Path(out_file).write_text(output, encoding="utf-8")
        RAN.append(list(argv))
        return runner.ProcResult(argv=list(argv), code=code, ms=120.0, timed_out=False, stopped=False,
                                 stop_reason="", output=output.encode("utf-8"), total_bytes=len(output),
                                 pids_seen=[], left_after=[], started_at=time.time())
    return run_process


runner.run_process = fake_runner("ok\n")


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


def executor() -> Executor:
    return Executor(session=SessionContext(), audit=None)


def routed(text, refs=None):
    with reading.context(refs or []):
        try:
            c = phrasings.match(text)
        except reading.Ask as ask:
            return ("ask", ask.line)
    return (c.name, c.args) if c else (None, None)


def literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def top_intent(text: str):
    ranked = score_intents(normalise(text))
    return ranked[0][0] if ranked else None


print("\n(1a) her work still runs on invented text\n")
check("an 'it going well then' greeting still reaches her status line",
      top_intent("it going well then") is Intent.STATUS and top_intent("it going alright tessa") is Intent.STATUS)
check("...and a weather question with 'is it going to' never does",
      top_intent("is it going to rain today") is not Intent.STATUS and top_intent("how is the weather") is not Intent.STATUS)
check("the other status words are unchanged",
      top_intent("how are you doing today") is Intent.STATUS and top_intent("status report") is Intent.STATUS)
ho = next(i for i in I.HELDOUT if i["id"] == "HO3g2")
ho_text = (ho.get("turns") or [ho.get("text")])[0]
check("held-out HO3g2 still expects her own standing down, never the laptop's sleep",
      ho["kinds"] == ["sleep"] and ho["never"] == ["sys.sleep", "system.power.*"] and ho.get("why") == "3g: her own standing down")
check("...and its invented text routes to her standing down", top_intent(ho_text) is Intent.SLEEP
      and routed(ho_text)[0] not in ("sys.sleep",), str(top_intent(ho_text)))
persona = (ROOT / "core" / "config" / "personalities" / "tessa.md").read_text(encoding="utf-8")
check("her persona's example still pairs an action request with her short confirmation",
      "> **He:** Tessa, open the budget spreadsheet.\n> **She:** Open, Emperor." in persona)
check("fs.open's first phrasing is an invented request", REGISTRY["fs.open"].phrasings[0] == "open the budget spreadsheet")
prompts = literal(ROOT / "core" / "voice" / "speaker.py", "ENROL_PROMPTS")
check("the enrolment prompts are still sentences to read",
      isinstance(prompts, list) and len(prompts) >= 5
      and all(p.rstrip()[-1:] in ".?!" and len(p.split()) >= 6 for p in prompts), str(len(prompts or [])))
prime = literal(ROOT / "core" / "voice" / "stt.py", "VOCABULARY_PRIME")
check("Whisper's prime still carries her name, the folders, the X verbs and the control phrases",
      isinstance(prime, str) and prime.count("Tessa") >= 6 and "open my documents" in prime
      and "Post this to X" in prime and "Stop the wake word" in prime and "That will be all" in prime)

print("\n(1b) code.write is amber, as creating a file is\n")
perms = yaml.safe_load((ROOT / "core" / "config" / "permissions.yaml").read_text(encoding="utf-8"))["tiers"]
check("code.write is amber in the registry and in permissions.yaml",
      REGISTRY["code.write"].tier == "amber" and "code.write" in perms["amber"] and "code.write" not in perms["green"])
PROGRAM = "```python\nfor n in range(3):\n    print('line', n)\n```"
fake = FakeLLM(lambda text: PROGRAM)
coding.bind_brain(fake)
ex = executor()
said = ex.run(ToolCall("code.write", {"language": "python", "task": "prints three lines"}, origin="human"))
check("when he asks her himself nothing changes: written at once, one model call, no question",
      said.startswith("I wrote a Python program") and len(fake.calls) == 1 and ex.ledger.pending is None
      and len(coding.written()) == 1, said[:100])
fake.calls.clear()
ex = executor()
said = ex.run(ToolCall("code.write", {"language": "python", "task": "prints three lines"}, origin="agent"))
check("when the model built the request she asks first, and nothing is written or asked of the model",
      said.endswith("yes or no?") and not fake.calls and len(coding.written()) == 1 and ex.ledger.pending is not None,
      said)
said = ex.answer_confirmation("yes")
check("...and his yes writes it", said is not None and said.startswith("I wrote a Python program")
      and len(fake.calls) == 1 and len(coding.written()) == 2, str(said)[:100])
fake.calls.clear()
ex = executor()
ex.session.load_external(ExternalContent(source="a web page", text="hello"))
said = ex.run(ToolCall("code.write", {"language": "python", "task": "prints three lines"}, origin="human"))
check("with a web page in front of her, writing a file waits like any amber act",
      "forget the page" in said and not fake.calls and len(coding.written()) == 2, said[:120])

print("\n(1c) she writes what he asks for\n")
system = coding.SYSTEM.format(lang="Python")
check("the writing rule no longer forbids windows, the network or his files",
      "must not open windows" not in system and "must not use the network" not in system
      and "outside its own folder" not in system and "read or change the owner's files, open a window or use the internet"
      in system, system)
check("...it asks for inputs as command-line arguments, never the keyboard, and the standard library unless named",
      "command-line arguments" in system and "never waits for typing" in system
      and "Use only what Python itself provides, unless the request names a package" in system)
check("what a program uses is read from its code",
      coding.program_traits("python", "import sys\nprint(sys.argv[1])") == {"args": True, "window": False, "online": False}
      and coding.program_traits("python", "import tkinter\ntkinter.Tk().mainloop()")["window"]
      and coding.program_traits("python", "import urllib.request\nurllib.request.urlopen('x')")["online"]
      and coding.program_traits("javascript", "fetch(process.argv[2])") == {"args": True, "window": False, "online": True}
      and coding.program_traits("powershell", "Invoke-WebRequest $args[0]")["online"]
      and coding.program_traits("python", "print(1)") == {"args": False, "window": False, "online": False})
ONLINE_PROGRAM = "```python\nimport sys\nimport urllib.request\nprint(urllib.request.urlopen(sys.argv[1]).status)\n```"
coding.bind_brain(FakeLLM(lambda text: ONLINE_PROGRAM))
ex = executor()
said = ex.run(ToolCall("code.write", {"language": "python", "task": "fetches a page"}, origin="human"))
spoken = said.split("\n```")[0]
check("a program that takes arguments and goes online is written, and she says both in plain words",
      "It takes its inputs as arguments: say run it with, then the values." in spoken
      and "It goes online when it runs." in spoken and "Say run it, and I will put it on the card." in spoken, spoken)
net_path = Path(coding.written()[-1]["path"])
WIN = "```python\nimport tkinter\ntkinter.Tk().mainloop()\n```"
coding.bind_brain(FakeLLM(lambda text: WIN))
said = executor().run(ToolCall("code.write", {"language": "python", "task": "opens a window"}, origin="human"))
check("a program that opens a window: she says the window closes at the time limit",
      "It opens a window, and the window closes at the time limit of a minute." in said.split("\n```")[0], said[:200])
ex = executor()
said = ex.run(ToolCall("code.run_mine", {"path": str(net_path), "argv": ["https://example.org"]}, origin="human"))
req = list(ex.approvals.pending.values())[-1]
check("the card says in plain words that it goes online, and her card line says so too",
      req.args.get("goes_online") == "Yes. This program uses the internet when it runs." and "it goes online" in said, said)
check("the card still carries the exact code, and his arguments are shown on it",
      req.args["code"] == net_path.read_text(encoding="utf-8") and req.args.get("argv") == ["https://example.org"]
      and req.args.get("arguments") == "https://example.org" and "with 1 argument" in said)
check("everything on the card is frozen", set(req.args) <= set(req.frozen), str(sorted(set(req.args) - set(req.frozen))))
RAN.clear()
record = ex.execute_approved(req.request_id)
check("his yes runs it once with exactly those arguments", len(RAN) == 1 and RAN[0][-1] == "https://example.org"
      and "It ran and finished" in record["result"]["said"], str(RAN))
coding.bind_brain(FakeLLM(lambda text: PROGRAM))
executor().run(ToolCall("code.write", {"language": "python", "task": "prints three lines"}, origin="human"))
ex = executor()
ex.run(ToolCall("code.run_mine", {"which": "last"}, origin="human"))
req = list(ex.approvals.pending.values())[-1]
check("otherwise the card is unchanged", set(req.args) == {"path", "language", "file", "code", "sha256", "runs_in",
                                                         "time_limit", "timeout_s", "lines", "runs_as"}, str(sorted(req.args)))
check("'run the program you wrote with 3 and 4' carries his arguments to her card",
      routed("run the program you wrote with 3 and 4") == ("code.run_mine", {"which": "last", "argv": ["3", "4"]}),
      str(routed("run the program you wrote with 3 and 4")))
mine = Path(coding.written()[-1]["path"])
now = time.time()
name, args = routed("run it with 5 and 6", [reading.Referent("file", str(mine), now)])
check("'run it with 5 and 6' after she wrote one is her card with those arguments",
      name == "code.run_mine" and args.get("argv") == ["5", "6"], str((name, args)))
check("...and a time limit still comes off before the arguments",
      routed("run the program you wrote with 7 for two minutes") == ("code.run_mine", {"which": "last", "argv": ["7"],
                                                                                      "timeout_s": 120}))

print("\n(1d) her words\n")
src = (ROOT / "core" / "tools" / "langs.py").read_text(encoding="utf-8")
check("her languages table says your, not his", "his execution policy" not in src and "on his PATH" not in src
      and "your execution policy decides" in src)
real_which = langs.which
langs.which = lambda name, path=None: "C:\\his\\php.exe" if path == ["HIS"] else ""
found = langs._simple("php", "php", ["--version"], ["HER"], ["HIS"])
langs.which = real_which
check("a language only on his own PATH is 'on your PATH'", found.note.startswith("on your PATH"), found.note)
check("a last line is spoken only when it is plain words and numbers",
      coding._speakable("hello from python 3.12.7") and coding._speakable("The sum of 7 and 5 is: 12")
      and not coding._speakable("args ['3', '4']") and not coding._speakable("2 + 2 = 4")
      and not coding._speakable("C:\\Users\\x\\out.txt"))
runner.run_process = fake_runner("hello\nargs ['3', '4']\n")
out = coding.run_source(mine, langs.spec_for_ext(".py"), ["3", "4"], 60)
runner.run_process = fake_runner("ok\n")
check("...otherwise she says the output is on screen, and shows it", out["spoken"].endswith("The output is on screen.")
      and "args" not in out["spoken"] and "args ['3', '4']" in out["said"], out["spoken"])
ex = executor()
ex.session.load_external(ExternalContent(source="program output", text="Traceback"))
said = ex.run(ToolCall("code.write", {"language": "python", "task": "x"}, origin="human"))
check("program output holding back an act is said in plain words, with 'forget that output'",
      said == "No, Emperor. What the program printed is still in front of me, and that was not a read-only action. "
              "Say forget that output and ask me again.", said)
coding.remember_run({"file": str(mine), "code": 1, "error_tail": "Traceback\nValueError: x\n", "at": "now"})
coding.bind_brain(FakeLLM(lambda text: "```python\nprint(1)\n```"))
ex = executor()
said = ex.run(ToolCall("code.write", {"fix": "last", "run": True}, origin="human"))
check("program output holding back the card is said in plain words",
      "I have not put it on the card, Emperor: what the program printed is still in front of me, and I never raise a "
      "card while it is. Say forget that output, then say run the program you wrote." in said
      and "fence wins" not in said and not ex.approvals.pending, said[-220:])
check("'forget that output' is not a request to forget the conversation",
      not conversation.is_clear_request("forget that output") and not conversation.is_clear_request("Tessa, forget that output")
      and conversation.is_clear_request("forget that") and conversation.is_clear_request("forget it"))
check("'forget that output' clears what the program printed, and 'forget the page' still does",
      routed("forget that output")[0] == "context.forget" and routed("forget the program output")[0] == "context.forget"
      and routed("forget the page")[0] == "context.forget")
said = ex.run(ToolCall("context.forget", {}, origin="human"))
said = ex.run(ToolCall("code.run_mine", {"which": "last"}, origin="human"))
check("...and once it is forgotten the card goes up", len(ex.approvals.pending) == 1, said[:120])

langs.find = REAL_FIND

import core.bus as BUS
import core.server as SRV
from core.brain import intent_model
from core.brain.claims import ClaimStore
from core.brain.conversation import Conversation
from core.brain.style import StyleStore
from core.capabilities.vault import Vault
from core.security.audit import AuditLog
from core.telemetry.cost import CostLedger
from core.voice import loop as VL
from core.voice.audio_io import ArmedMicrophone
from core.voice.tts.base import Synthesis

LOG: list[str] = []
_audits = {"n": 0}


def _audit(path: Any) -> AuditLog:
    _audits["n"] += 1
    return AuditLog(TMP / f"audit-{_audits['n']}.log")


SRV.AuditLog = _audit
SRV._Vault = lambda **kw: Vault(path=TMP / "vault.json", acl=False, profile_root=TMP / "profiles", **kw)
SRV.Conversation = lambda: Conversation(path=TMP / "conversation.json")
SRV.ClaimStore = lambda: ClaimStore(path=TMP / "claims.json")
SRV.StyleStore = lambda **kw: StyleStore(path=TMP / "style.json", **kw)
SRV.CostLedger = lambda path: CostLedger(TMP / "cost.jsonl")
SRV.log = lambda msg: LOG.append(msg)
intent_model._engine_for = lambda brain: brain


class CallbackStop(Exception):
    pass


class Dev:
    block = 576
    lead = 0.05
    streams: list[Any] = []


class FakeStream:
    def __init__(self, samplerate: int, channels: int, dtype: str, callback: Any,
                 finished_callback: Any = None, **kw: Any) -> None:
        self.rate = int(samplerate)
        self.cb = callback
        self.fin = finished_callback
        self.halt = threading.Event()
        self.thread: threading.Thread | None = None
        self.aborted_at: float | None = None
        self.done = False
        Dev.streams.append(self)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        period = Dev.block / self.rate
        while not self.halt.is_set():
            out = np.zeros((Dev.block, 1), dtype=np.int16)
            now = time.perf_counter()
            try:
                self.cb(out, Dev.block, types.SimpleNamespace(currentTime=now, outputBufferDacTime=now + Dev.lead), None)
            except CallbackStop:
                break
            self.halt.wait(period)
        if not self.done:
            self.done = True
            if self.fin is not None:
                self.fin()

    def abort(self, ignore_errors: bool = True) -> None:
        self.aborted_at = time.perf_counter()
        self.halt.set()
        if self.thread is not None and threading.current_thread() is not self.thread:
            self.thread.join(2.0)

    def close(self, ignore_errors: bool = True) -> None:
        return None


BUS.sd = types.SimpleNamespace(OutputStream=FakeStream, CallbackStop=CallbackStop)


class FakeTTS:
    rate = 22050

    def __init__(self) -> None:
        self.said: list[str] = []

    def synthesise(self, text: str) -> Synthesis:
        self.said.append(text)
        n = int(self.rate * 0.03 * max(1, len(text)))
        t = np.arange(n) / self.rate
        samples = (6000.0 * np.sin(2 * np.pi * 220.0 * t)).astype(np.int16)
        return Synthesis(samples=samples, sample_rate=self.rate, first_sample_ms=1.0, total_s=0.001, chunks=1)


class FakeSTT:
    def __init__(self) -> None:
        self.next = ""
        self.heard: list[np.ndarray] = []

    def transcribe(self, audio: Any, sr: int) -> Any:
        self.heard.append(np.asarray(audio))
        return types.SimpleNamespace(text=self.next, rms=3000.0, too_quiet=False)


class FakeBrain:
    name = "fake-brain"

    def __init__(self) -> None:
        self.reply = "Air scatters blue light more than red, Emperor."
        self.delay = 0.0
        self.calls = 0
        self.intent_calls = 0

    def complete(self, system: str, messages: Any, **kw: Any) -> Any:
        self.intent_calls += 1
        return types.SimpleNamespace(text='{"kind": "chat"}')

    def stream(self, system: str, messages: Any, max_tokens: int = 0, quality: Any = None) -> Any:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        yield self.reply


class FakeWs:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.frames.append(json.loads(raw))

    async def close(self, *a: Any) -> None:
        return None

    def states(self, start: int = 0) -> list[str]:
        return [f["payload"]["state"] for f in self.frames[start:] if f["type"] == "evt.agent.state"]

    def lines(self, role: str, start: int = 0) -> list[str]:
        return [f["payload"]["message"]["text"] for f in self.frames[start:]
                if f["type"] == "evt.transcript.message" and f["payload"]["message"]["role"] == role]


VAD = {"vad_silence_ms": 300, "vad_floor_rms": 300, "vad_hard_cap_s": 6, "vad_start_timeout_s": 0.6}
SR = 16000


async def build() -> Any:
    d = SRV.TessaDaemon(dev=False)
    rig = types.SimpleNamespace(d=d, brain=FakeBrain(), mic=ArmedMicrophone(pre_roll_s=1.0), stt=FakeSTT(),
                                tts=FakeTTS(), orb=FakeWs())
    d.brain = rig.brain
    d.attach_voice(mic=rig.mic, stt=rig.stt, tts=rig.tts)
    d.voice.vad_config = dict(VAD)
    d.clients[rig.orb] = {"authed": True, "surface": "orb", "subs": {"agent.*", "transcript.*", "voice.*",
                                                                    "permission.*"}, "sessionId": SRV.ulid()}
    return rig


async def send(rig: Any, mtype: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = SRV.envelope(mtype, payload)
    cid = json.loads(raw)["id"]
    await rig.d._dispatch(rig.orb, rig.d.clients[rig.orb], raw)
    for f in reversed(rig.orb.frames):
        if f.get("corr") == cid:
            return f
    return None


async def until(cond: Any, timeout: float = 8.0, step: float = 0.01) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if cond():
            return True
        await asyncio.sleep(step)
    return bool(cond())


async def quiet(rig: Any, timeout: float = 10.0) -> bool:
    d = rig.d

    def calm() -> bool:
        return (not d._typed_jobs and not d._typed_tasks and not d._speech_tasks and not d._voice_turns
                and not d.voice.bus.is_speaking)

    await asyncio.sleep(0.15)
    ok = await until(calm, timeout)
    await asyncio.sleep(0.15)
    ok = ok and await until(calm, timeout)
    await asyncio.sleep(0.1)
    return ok and calm()


def plain(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in frames if f["type"] != "evt.voice.amplitude"]


def tone(freq: float, seconds: float, amp: float = 8000.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def feed(mic: ArmedMicrophone, samples: np.ndarray, block: int = 800) -> None:
    for i in range(0, len(samples), block):
        part = samples[i:i + block]
        mic._callback(part.reshape(-1, 1), len(part), None, None)


def hush(rig: Any) -> None:
    feed(rig.mic, np.zeros(SR, dtype=np.int16))


async def press(rig: Any) -> dict[str, Any] | None:
    reply = await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await asyncio.sleep(0.05)
    return reply


async def feed_live(mic: ArmedMicrophone, samples: np.ndarray, block: int = 800) -> None:
    for i in range(0, len(samples), block):
        part = samples[i:i + block]
        mic._callback(part.reshape(-1, 1), len(part), None, None)
        await asyncio.sleep(block / SR)


def share_at(samples: np.ndarray, freq: float) -> float:
    x = np.asarray(samples, dtype=np.float64)
    if x.size < 64:
        return 0.0
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(x.size, 1.0 / SR)
    band = (freqs > freq - 20) & (freqs < freq + 20)
    return float(spec[band].sum() / (spec.sum() + 1e-9))


async def item2() -> None:
    print("\n(2a) one question per press\n")
    rig = await build()
    d = rig.d
    rig.stt.next = "the time on the clock"
    reply = await press(rig)
    check("a press opens one segment and she listens", reply["payload"]["active"] is True and d.voice.armed
          and rig.orb.states()[-1:] == ["listening"], str(rig.orb.states()))
    await feed_live(rig.mic, tone(300, 0.6))
    mark = len(rig.orb.frames)
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    said = rig.orb.lines("assistant", mark)
    check("a second press ends his question and she answers it", len(rig.stt.heard) == 1 and len(said) == 1
          and said[0] in rig.tts.said, str(said))
    check("...then she stops listening: no segment, no session, no listening state after her answer",
          not d.voice.armed and not d.ptt_active and not d.convo.open
          and "listening" not in rig.orb.states(mark), str(rig.orb.states(mark)))
    mark = len(rig.orb.frames)
    await feed_live(rig.mic, tone(300, 0.6))
    await asyncio.sleep(0.4)
    check("nothing in the room reaches her between presses: no transcription, no turn, no state",
          len(rig.stt.heard) == 1 and plain(rig.orb.frames[mark:]) == [], str(plain(rig.orb.frames[mark:]))[:120])
    hush(rig)
    LOG.clear()
    mark = len(rig.orb.frames)
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await until(lambda: not d.voice.armed, 3.0)
    await asyncio.sleep(0.2)
    check("a press with nothing said ends by itself after the silence limit, with no turn and no model call",
          not d.voice.armed and len(rig.stt.heard) == 1 and rig.brain.calls == 0 and rig.brain.intent_calls == 0
          and any("nothing said in 1 s after the press" in x for x in LOG)
          and rig.orb.states(mark)[-1:] == ["idle"], str(rig.orb.states(mark)))
    check("...the silence limit is 8 seconds unless settings say otherwise", VL.NO_SPEECH_S == 8.0)
    LOG.clear()
    mark = len(rig.orb.frames)
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    rows = [r for r in d.audit if str(r.get("tool", "")).startswith("voice.turn")]
    check("a press and a second press with nothing said: no transcription, no turn, no audit turn row",
          len(rig.stt.heard) == 1 and any("TURN empty" in x for x in LOG) and len(rows) == 1, str(len(rows)))
    LOG.clear()
    mark = len(rig.orb.frames)
    reply = await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await asyncio.sleep(0.2)
    check("a stop with nothing recording is answered and runs no turn",
          reply["payload"]["active"] is False and reply["payload"]["changed"] is False
          and rig.orb.states(mark) == [] and any("PTT stop with nothing recording" in x for x in LOG),
          str(rig.orb.states(mark)))
    rig.stt.next = "the time on the clock"
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.3))
    again = await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.3))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    check("a repeated start while she listens changes nothing and keeps what he said so far",
          again["payload"]["changed"] is False and len(rig.stt.heard) == 2 and rig.stt.heard[-1].size >= int(SR * 0.6),
          str(rig.stt.heard[-1].size))
    rig.stt.next = "write me a program"
    mark = len(rig.orb.frames)
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.3))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    first = rig.orb.lines("assistant", mark)
    check("when she asks him something she stops listening, and the first time says so in plain words",
          len(first) == 1 and first[0].endswith(VL.ANSWER_HINT) and not d.voice.armed, str(first))
    mark = len(rig.orb.frames)
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.3))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    second = rig.orb.lines("assistant", mark)
    check("...and only the first time in a run", len(second) == 1 and VL.ANSWER_HINT not in second[0], str(second))
    rig.stt.next = "we are finished for now"
    mark = len(rig.orb.frames)
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.3))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    check("closing words with nothing open: she answers, and nothing opens or re-arms",
          len(rig.orb.lines("assistant", mark)) == 1 and not d.convo.open and not d.voice.armed)
    LOG.clear()
    rig.stt.next = "the time on the clock"
    mark = len(rig.orb.frames)
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.4))
    ok = await until(lambda: any("VAD closed the segment (silence)" in x for x in LOG), 3.0)
    await quiet(rig)
    vad = next((x for x in LOG if "VAD closed the segment (silence)" in x), "")
    check("his question also ends when he stops talking, and the log says how long after his press",
          ok and "ms after his press reached her" in vad and len(rig.orb.lines("assistant", mark)) == 1, vad)

    print("\n(2b) never her own voice in his question\n")
    rig = await build()
    d = rig.d
    feed(rig.mic, tone(300, 1.0))
    rig.stt.next = "the time on the clock"
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.4))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    kept = rig.stt.heard[-1]
    check("when she has been silent, the second before his press is kept (his first syllable)",
          kept.size >= int(SR * 1.3) and share_at(kept, 300) > 0.5, str(kept.size))
    LOG.clear()
    long_line = rig.tts.synthesise("x" * 120)
    d.voice.bus.speak(long_line.samples, long_line.sample_rate)
    await asyncio.sleep(0.3)
    feed(rig.mic, tone(700, 1.0))
    check("she is speaking and the ring now holds a second of her voice", d.voice.bus.is_speaking
          and rig.mic._filled == SR, str(rig.mic._filled))
    mark = len(rig.orb.frames)
    rig.stt.next = "the time on the clock"
    await press(rig)
    check("his press cuts her off and she listens", not d.voice.bus.is_speaking and d.voice.armed
          and rig.orb.states(mark) == ["listening"], str(rig.orb.states(mark)))
    feed(rig.mic, tone(700, 0.2))
    await feed_live(rig.mic, tone(300, 0.6))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    q = rig.stt.heard[-1]
    check("his question holds none of her voice: nothing from before the press, nothing she said",
          share_at(q, 700) < 0.01 and share_at(q, 300) > 0.5, f"her {share_at(q, 700):.4f}, his {share_at(q, 300):.3f}")
    check("...and what reached transcription is only what he said after the press",
          abs(q.size - int(SR * 0.6)) <= 800, f"{q.size} samples")
    barge = next((x for x in LOG if "stopped her for push-to-talk in" in x), "")
    clean = next((x for x in LOG if "kept out of his question" in x), "")
    check("the cut is logged with its milliseconds from his press reaching her", "ms" in barge, barge)
    check("...and so is what was kept out", "1.00 s of pre-roll" in clean and "first 200 ms" in clean, clean)
    rig2 = await build()
    short = rig2.tts.synthesise("y" * 10)
    rig2.d.voice.bus.speak(short.samples, short.sample_rate)
    await until(lambda: not rig2.d.voice.bus.is_speaking, 5.0)
    await asyncio.sleep(0.3)
    feed(rig2.mic, tone(700, 0.8))
    rig2.stt.next = "the time on the clock"
    await press(rig2)
    await feed_live(rig2.mic, tone(300, 0.5))
    await send(rig2, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig2)
    q2 = rig2.stt.heard[-1]
    check("a press just after she finished speaking also keeps her last words out",
          share_at(q2, 700) < 0.01 and rig2.d.voice.last_clean is not None, f"her {share_at(q2, 700):.4f}")

    print("\n(2c) no late answers\n")
    rig = await build()
    d = rig.d
    rig.brain.delay = 1.2
    rig.stt.next = "do snails have teeth"
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.3))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await until(lambda: rig.brain.calls == 1, 5.0)
    LOG.clear()
    mark = len(rig.orb.frames)
    turns_before = len(d.conversation.messages())
    await press(rig)
    check("a press while she is thinking: she listens to the new question at once",
          d.voice.armed and rig.orb.states(mark) == ["listening"], str(rig.orb.states(mark)))
    await asyncio.sleep(1.8)
    check("...and the old answer, when the model returns, is never spoken or shown",
          rig.orb.lines("assistant", mark) == [] and rig.brain.reply not in rig.tts.said
          and len(d.conversation.messages()) == turns_before, str(rig.orb.lines("assistant", mark)))
    check("...the log says it was dropped, and the old turn never moved her state again",
          any("turn.dropped" in x and "a new press replaced it" in x for x in LOG)
          and "thinking" not in rig.orb.states(mark) and "speaking" not in rig.orb.states(mark),
          str(rig.orb.states(mark)))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    rig = await build()
    d = rig.d
    rig.brain.delay = 1.2
    rig.stt.next = "do snails have teeth"
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.3))
    LOG.clear()
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await until(lambda: rig.brain.calls == 1, 5.0)
    mark = len(rig.orb.frames)
    reply = await send(rig, "cmd.agent.cancel", {"companionId": "tessa"})
    await asyncio.sleep(1.6)
    await quiet(rig)
    check("a cancel while she thinks on a spoken question: answered, and the answer never comes",
          reply["payload"].get("cancelled") is True and rig.orb.lines("assistant", mark) == []
          and rig.brain.reply not in rig.tts.said and any("turn.dropped" in x and "cancelled" in x for x in LOG),
          str(rig.orb.lines("assistant", mark)))
    check("...her state leaves thinking at the cancel", rig.orb.states(mark)[:1] == ["idle"], str(rig.orb.states(mark)))
    rig = await build()
    d = rig.d
    rig.brain.delay = 1.2
    rig.stt.next = "do snails have teeth"
    saved = SRV.VOICE_TURN_STALL_S
    SRV.VOICE_TURN_STALL_S = 0.4
    try:
        await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
        await feed_live(rig.mic, tone(300, 0.3))
        LOG.clear()
        mark = len(rig.orb.frames)
        await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
        await asyncio.sleep(2.0)
        await quiet(rig)
    finally:
        SRV.VOICE_TURN_STALL_S = saved
    check("a turn that stalls is never spoken later",
          any("STALLED" in x for x in LOG) and rig.orb.lines("assistant", mark) == []
          and rig.brain.reply not in rig.tts.said and any("turn.dropped" in x and "stalled" in x for x in LOG),
          str([x for x in LOG if "dropped" in x or "STALLED" in x])[:200])
    v = d.voice
    v.abandon("she is shutting down")
    syn = rig.tts.synthesise("late")
    check("an answer from before a close cannot reach the speaker: the check and the playback are one step",
          v._speak(v._gen - 1, syn) is False and not v.bus.is_speaking)
    src = Path(SRV.__file__).read_text(encoding="utf-8")
    check("...and shutting down drops any voice turn still in flight", 'daemon.voice.abandon("she is shutting down")' in src)

    print("\n(2d) the smaller ones\n")
    rig = await build()
    d = rig.d
    rig.stt.next = "the time on the clock"
    mark = len(rig.orb.frames)
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await feed_live(rig.mic, tone(300, 0.3))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)
    kinds = [(f["type"], (f["payload"].get("message") or {}).get("role"), f["payload"].get("state"),
              f["payload"].get("segmentOpen")) for f in rig.orb.frames[mark:]]
    user_at = [i for i, k in enumerate(kinds) if k[0] == "evt.transcript.message" and k[1] == "user"]
    speak_at = [i for i, k in enumerate(kinds) if k[0] == "evt.agent.state" and k[2] == "speaking"]
    check("his words go out as soon as they are transcribed, before she speaks, and once",
          len(user_at) == 1 and speak_at and user_at[0] < speak_at[0], str(kinds)[:240])
    line = next(f["payload"]["message"] for f in rig.orb.frames[mark:]
                if f["type"] == "evt.transcript.message" and f["payload"]["message"]["role"] == "user")
    check("...marked via voice", line.get("via") == "voice" and line["text"] == "the time on the clock")
    opens = [k[3] for k in kinds if k[0] == "evt.voice.state"]
    check("the segment's state follows the microphone: open at the press, closed when his question ends",
          opens[:2] == [True, False], str(opens))
    states = [k[2] for k in kinds if k[0] == "evt.agent.state"]
    check("listening is shown only while she is listening", states[0] == "listening" and "listening" not in states[1:],
          str(states))
    late = FakeWs()
    d.clients[late] = {"authed": True, "surface": "orb", "subs": set(), "sessionId": SRV.ulid()}
    await rig.d._dispatch(late, d.clients[late], SRV.envelope("cmd.subscribe", {"topics": ["voice.*"]}))
    snap = [f["payload"] for f in late.frames if f["type"] == "evt.voice.state"]
    check("a surface subscribing to voice.* gets the segment state at once",
          snap == [{"companionId": "tessa", "muted": False, "segmentOpen": False}], str(snap))
    check("nothing his voice level or partial words: no frame outside the contract was sent",
          not any(f["type"] in ("evt.voice.vad", "evt.voice.partialTranscript") for f in rig.orb.frames))


asyncio.run(item2())

print("\n(fences)\n")
check("no network connection was attempted", NET["attempts"] == 0, str(NET["attempts"]))
check("her live audit log, thread and action log are untouched", {str(p): digest(p) for p in LIVE} == LIVE_BEFORE)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
