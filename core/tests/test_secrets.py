from __future__ import annotations

import ctypes
import os
import secrets as pysecrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from core.security import secrets as S

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def os_block_has(name: str) -> bool:
    buf = ctypes.create_unicode_buffer(4)
    n = ctypes.windll.kernel32.GetEnvironmentVariableW(name, buf, 4)
    return n != 0


def secret_in(env: Any) -> list[str]:
    configured = S.configured_names()
    return sorted(n for n in env if S.is_secret_name(n, configured))


TMP = Path(tempfile.mkdtemp(prefix="tessa-secrets-test-"))
PLANT_KEY = "R1_PLANTED_API_KEY"
PLANT_TOKEN = "R1_PLANTED_TOKEN"
PLAIN = "R1_PLANTED_PLAIN"
VALUE_KEY = pysecrets.token_hex(20)
VALUE_TOKEN = pysecrets.token_hex(16)

print("\n1. which names count as secrets")
marked = ["GEMINI_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "NPM_TOKEN", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD",
          "MY_PASSPHRASE", "X_CREDENTIALS", "SOME_KEY", "KEY_FILE", "APIKEY", "SSH_PRIVATE_KEY", "ANTHROPIC_API_KEY"]
plain = ["PATH", "SESSIONNAME", "USERNAME", "USERPROFILE", "SystemRoot", "SSH_AUTH_SOCK", "PSModulePath", "TEMP",
         "LOCALAPPDATA", "KEYBOARD_LAYOUT", "NUMBER_OF_PROCESSORS", "COMPUTERNAME", "TESSA_RUNTIME_DIR"]
check("every name that marks a secret is caught", all(S.is_secret_name(n, set()) for n in marked),
      str([n for n in marked if not S.is_secret_name(n, set())]))
check("ordinary names are left alone", not any(S.is_secret_name(n, set()) for n in plain),
      str([n for n in plain if S.is_secret_name(n, set())]))
check("the configured brain key names are always caught",
      {"GEMINI_API_KEY", "ANTHROPIC_API_KEY"} <= S.configured_names(), str(sorted(S.configured_names())))

print("\n2. hold and scrub on a fake environment")
fake = {"PATH": "x", "SESSIONNAME": "Console", PLANT_KEY: VALUE_KEY, "OTHER_TOKEN": "t" * 12, PLAIN: "keep"}
names = S.hold_and_scrub(fake)
check("it removes exactly the secret names and reports them by name", names == ["OTHER_TOKEN", PLANT_KEY]
      and set(fake) == {"PATH", "SESSIONNAME", PLAIN}, str(names))
check("the daemon can still read a held value", S.get(PLANT_KEY) == VALUE_KEY)
check("and says where it came from", S.source_of(PLANT_KEY).startswith("held in memory"), S.source_of(PLANT_KEY))
check("describe() gives names and lengths only", S.describe()["lengths"].get(PLANT_KEY) == len(VALUE_KEY)
      and VALUE_KEY not in repr(S.describe()))

print("\n3. the daemon's own environment block, which every child inherits")
os.environ[PLANT_KEY] = VALUE_KEY
os.environ[PLANT_TOKEN] = VALUE_TOKEN
os.environ[PLAIN] = "keep"
check("planted values are in the process block before", os_block_has(PLANT_KEY) and os_block_has(PLANT_TOKEN))
held = S.hold_and_scrub()
check("after the scrub they are gone from os.environ", PLANT_KEY not in os.environ and PLANT_TOKEN not in os.environ,
      str(held))
check("...and from the Windows process environment block that CreateProcess and ShellExecute pass on",
      not os_block_has(PLANT_KEY) and not os_block_has(PLANT_TOKEN))
check("a plain variable stays in both", os.environ.get(PLAIN) == "keep" and os_block_has(PLAIN))
check("no secret-named variable is left in this process at all", secret_in(os.environ) == [], str(secret_in(os.environ)))
check("the held values are still readable inside the daemon", S.get(PLANT_TOKEN) == VALUE_TOKEN)

print("\n4. each spawning tool hands its child a clean environment (fakes, nothing started)")
seen: dict[str, Any] = {}


class Rec:
    def __init__(self, label: str) -> None:
        self.label = label

    def __call__(self, *a: Any, **k: Any) -> Any:
        env = k.get("env")
        seen[self.label] = dict(env) if env is not None else dict(os.environ)
        return type("P", (), {"returncode": 0, "stdout": b"", "stderr": b""})()


from types import SimpleNamespace

from core.security import gate
from core.tools import coding, gittools, langs, runner, shell
from core.tools.base import ToolRedirect
from core.system.abilities import code as code_ability

real = (shell.subprocess.run, gittools.subprocess.run, code_ability.subprocess.Popen, code_ability._launcher,
        runner.run_process, langs.find, coding.DATA, coding.code_folder)
try:
    shell.subprocess.run = lambda *a, **k: Rec("shell.execute")(*a, **k) and type(
        "P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    shell.execute("echo hi", provenance="human", confirmed=True)
    gittools.subprocess.run = Rec("git")
    gittools._run_git(["git", "status"], str(TMP), 5.0)
    launcher = TMP / "code.cmd"
    launcher.write_text("placeholder launcher\n", encoding="utf-8")
    code_ability._launcher = lambda: (str(launcher), "test")
    code_ability.subprocess.Popen = Rec("system.code.open")
    gate.use_store(gate.Store(TMP / "gate.db", TMP / "written.jsonl"))
    try:
        code_ability.open_in_code(str(TMP))
        asked = False
    except ToolRedirect:
        asked = True
    check("system.code.open: a VS Code launcher outside the program folders asks for its card first",
          asked and "system.code.open" not in seen)
    verdict = gate.app_verdict(SimpleNamespace(kind="exe", name="Visual Studio Code", launch=str(launcher)))
    gate.store().approve(gate.app_set_sha(verdict), gate.APP, str(launcher),
                         [{"rel": r["rel"], "sha256": r["sha256"]} for r in verdict["files"]],
                         [str(verdict["target"]), str(verdict["arguments"]), str(verdict["workdir"])], "test")
    code_ability.open_in_code(str(TMP))
    langs.find = lambda key: langs.Found("python", "Python", True, exe=sys.executable, where="test", version="test")
    coding.DATA = TMP / "data-code"
    coding.code_folder = lambda: TMP / "Tessa Code"

    def fake_run(argv: list[str], *, cwd: str, env: dict[str, str], timeout_s: float, out_file: Path,
                 label: str) -> Any:
        seen["code.run"] = dict(env)
        Path(out_file).write_text("ok\n", encoding="utf-8")
        return runner.ProcResult(argv=list(argv), code=0, ms=1.0, timed_out=False, stopped=False, stop_reason="",
                                 output=b"ok\n", total_bytes=3, pids_seen=[], left_after=[], started_at=time.time())

    runner.run_process = fake_run
    src = TMP / "hello.py"
    src.write_text("print('ok')\n", encoding="utf-8")
    facts = coding.run_source(src, langs.spec_for_ext(".py"), [], 5)
finally:
    (shell.subprocess.run, gittools.subprocess.run, code_ability.subprocess.Popen, code_ability._launcher,
     runner.run_process, langs.find, coding.DATA, coding.code_folder) = real
for label in ("shell.execute", "git", "system.code.open", "code.run"):
    env = seen.get(label)
    check(f"{label}: no secret-named variable reaches the child", env is not None and secret_in(env) == []
          and PLANT_KEY not in env, str(secret_in(env or {})))
check("code runs still report the held names as removed (names only)",
      PLANT_KEY in facts.get("removed_env", []) and PLANT_TOKEN in facts.get("removed_env", []))
env, removed = langs.child_env({})
check("langs.child_env: a clean child environment and the held names listed as removed",
      secret_in(env) == [] and PLANT_KEY in removed)

print("\n5. the brain still reads its key")
from core.brain.llm.gemini import GeminiLLM

g = GeminiLLM({"api_key_env": PLANT_KEY})
check("the Gemini adapter reads the held key, not the environment", g._key == VALUE_KEY and PLANT_KEY not in os.environ)
missing = GeminiLLM({"api_key_env": "R1_NOT_SET_ANYWHERE_API_KEY"})
check("an unset key still reads as unset", missing._key == "")

print("\n6. a program's error output fed back to the model has the held key redacted")


class FakeLLM:
    name = "gemini:fake"
    last_finish = "STOP"

    def __init__(self) -> None:
        self.users: list[str] = []

    def stream(self, system: str, messages: Any, **kw: Any) -> Any:
        self.users.append(messages[-1].content)
        yield "```python\nprint(1)\n```"


fake_llm = FakeLLM()
coding.generate("python", "prints one", ("print(1/0)\n", f"Traceback: bad header {VALUE_KEY} and {VALUE_TOKEN}"),
                brain=fake_llm)
sent = fake_llm.users[-1] if fake_llm.users else ""
check("neither held value reaches the model", bool(sent) and VALUE_KEY not in sent and VALUE_TOKEN not in sent)
check("...they are replaced by a marker", sent.count("<REDACTED>") >= 2)

print("\n7. a held value never reaches the audit log, whatever its shape")
from core.security.audit import AuditLog, redact

check("redact() replaces a held value that matches no key pattern", VALUE_KEY not in redact(f"saw {VALUE_KEY} here")
      and "<REDACTED>" in redact(f"saw {VALUE_KEY} here"))
log = AuditLog(TMP / "audit.log")
log.append(actor="system", tool="t", tier="green", summary=f"summary {VALUE_TOKEN}",
           detail={"nested": [f"x{VALUE_KEY}y"]})
raw = (TMP / "audit.log").read_text(encoding="utf-8")
check("an appended row holds neither value, in the summary or the detail",
      VALUE_KEY not in raw and VALUE_TOKEN not in raw and raw.count("<REDACTED>") == 2)
check("...and the row still verifies", log.verify()[0])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
