from __future__ import annotations

import ast
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "core"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GATE = "core/security/gate.py"
MOD_STARTERS = {
    "subprocess": {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
    "os": {"startfile", "system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp",
           "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "posix_spawn",
           "posix_spawnp"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "webbrowser": {"open", "open_new", "open_new_tab", "get"},
    "multiprocessing": {"Process", "Pool", "get_context"},
    "pty": {"spawn"},
}
ATTR_STARTERS = {"launch_persistent_context", "launch_server", "subprocess_exec", "subprocess_shell", "startfile"}
NATIVE = {"ShellExecuteW", "ShellExecuteExW", "ShellExecuteA", "ShellExecuteExA", "CreateProcessW", "CreateProcessA",
          "CreateProcessAsUserW", "CreateProcessWithTokenW", "CreateProcessWithLogonW", "WinExec"}
EXEMPT = Counter({
    ("core/lifecycle.py", "quiet_console_children", "subprocess.Popen"): 2,
    ("core/capabilities/vault/store.py", "lock_acl", "subprocess.run"): 1,
    ("core/capabilities/vault/store.py", "acl_is_locked_down", "subprocess.run"): 1,
    ("core/exam/rehearsal.py", "<module>", "string.CreateProcessW"): 1,
    ("core/exam/rehearsal.py", "<module>", "string.ShellExecuteW"): 1,
    ("core/exam/rehearsal.py", "<module>", "string.ShellExecuteExW"): 1,
    ("core/exam/rehearsal.py", "Rehearsal._install_fences", "string.startfile"): 1,
    ("core/exam/rehearsal.py", "Rehearsal._install_fences", "subprocess.Popen"): 1,
    ("core/exam/selftest.py", "_probes", "subprocess.Popen"): 1,
    ("core/exam/selftest.py", "_probes", "subprocess.run"): 1,
    ("core/exam/selftest.py", "_probes", "os.system"): 1,
    ("core/exam/selftest.py", "_probes", "os.startfile"): 1,
    ("core/exam/selftest.py", "_probes", "webbrowser.open"): 1,
})

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"ok    {name}")
    else:
        failed += 1
        print(f"FAIL  {name}" + (f"\n      {detail}" if detail else ""))


def scan_source(src: str, rel: str) -> list[tuple[str, int, str, str]]:
    tree = ast.parse(src, filename=rel)
    aliases: dict[str, str] = {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in MOD_STARTERS:
                    aliases[a.asname or top] = top
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in MOD_STARTERS:
                for a in node.names:
                    if a.name in MOD_STARTERS[top] or a.name == "*":
                        names[a.asname or a.name] = f"{top}.{a.name}"
    parents = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node

    def owner(node: ast.AST) -> str:
        chain = []
        n = node
        while n in parents:
            n = parents[n]
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                chain.append(n.name)
        return ".".join(reversed(chain)) or "<module>"

    hits = []
    for node in ast.walk(tree):
        what = None
        if isinstance(node, ast.Attribute):
            base = node.value
            if isinstance(base, ast.Name) and base.id in aliases and node.attr in MOD_STARTERS[aliases[base.id]]:
                what = f"{aliases[base.id]}.{node.attr}"
            elif node.attr in ATTR_STARTERS:
                what = f"*.{node.attr}"
            elif node.attr == "launch" and isinstance(base, ast.Attribute) and base.attr in ("chromium", "firefox", "webkit"):
                what = f"*.{base.attr}.launch"
            elif node.attr in NATIVE:
                what = f"native.{node.attr}"
        elif isinstance(node, ast.Name) and node.id in names and isinstance(node.ctx, ast.Load):
            what = names[node.id]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            base, arg = node.args[0], node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                mod = aliases.get(base.id) if isinstance(base, ast.Name) else None
                if arg.value in ATTR_STARTERS or arg.value in NATIVE or (mod and arg.value in MOD_STARTERS[mod]):
                    what = f"dynamic.{arg.value}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and arg.value in MOD_STARTERS:
                what = f"dynamic.import.{arg.value}"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in NATIVE | {"startfile"}:
            what = f"string.{node.value}"
        if what:
            hits.append((rel, node.lineno, owner(node), what))
    return hits


def scan_tree(core: Path) -> list[tuple[str, int, str, str]]:
    out = []
    for dp, dns, fns in os.walk(core):
        dns[:] = sorted(d for d in dns if d not in ("__pycache__", "tests"))
        for fn in sorted(fns):
            if fn.endswith(".py"):
                p = Path(dp) / fn
                rel = p.relative_to(core.parent).as_posix()
                out.extend(scan_source(p.read_text(encoding="utf-8", errors="replace"), rel))
    return out


def verdict(hits: list[tuple[str, int, str, str]]) -> tuple[list[tuple[str, int, str, str]], list[tuple[str, str, str]]]:
    seen: Counter = Counter()
    outside = []
    for rel, line, func, what in hits:
        if rel == GATE:
            continue
        key = (rel, func, what)
        if EXEMPT.get(key, 0) > seen[key]:
            seen[key] += 1
            continue
        outside.append((rel, line, func, what))
    stale = [k for k, n in EXEMPT.items() if seen[k] != n]
    return outside, stale


print("1. the scanner sees every way to start a process or launch a file")
probe = '''
import subprocess, os, webbrowser, asyncio
import subprocess as sp
from subprocess import Popen as P
from os import startfile
import ctypes

def a():
    subprocess.run(["x"])
    sp.call(["x"])
    P(["x"])
    os.startfile("x")
    startfile("y")
    os.system("x")
    webbrowser.open("u")
    asyncio.create_subprocess_exec("x")
    ctypes.windll.shell32.ShellExecuteW(None, None, "x", None, None, 1)
    getattr(ctypes.windll.kernel32, "CreateProcessW")
    getattr(os, "startfile")("z")
    pw.chromium.launch_persistent_context("d")
    pw.chromium.launch()
'''
got = {h[3] for h in scan_source(probe, "probe.py")}
want = {"subprocess.run", "subprocess.call", "subprocess.Popen", "os.startfile", "os.system", "webbrowser.open",
        "asyncio.create_subprocess_exec", "native.ShellExecuteW", "dynamic.CreateProcessW", "dynamic.startfile",
        "*.launch_persistent_context", "*.chromium.launch"}
check("every planted start is found", want <= got, f"missing {sorted(want - got)}")
quiet = scan_source("import subprocess\nx = subprocess.TimeoutExpired\ny = subprocess.PIPE\n", "quiet.py")
check("names that start nothing are not counted", not quiet, str(quiet))

print("\n2. every start in core/ goes through core/security/gate.py")
hits = scan_tree(CORE)
outside, stale = verdict(hits)
check("no process start or file launch outside the gate", not outside,
      "\n      ".join(f"{r}:{ln} {w} in {f}" for r, ln, f, w in outside[:60]) + (
          f"\n      ... {len(outside) - 60} more" if len(outside) > 60 else ""))
check("every exemption is still exactly what is there (none stale, none grown)", not stale,
      "\n      ".join(f"{k} expected {EXEMPT[k]}" for k in stale))
in_gate = [h for h in hits if h[0] == GATE]
check("the gate itself is where the starts are", len(in_gate) >= 4, f"{len(in_gate)} in the gate")
print(f"\n   starts referenced: {len(hits)} in all, {len(in_gate)} in the gate, "
      f"{sum(EXEMPT.values())} exempt by name, {len(outside)} outside")

print("\n3. the exemptions are only what they claim")
src = (CORE / "lifecycle.py").read_text(encoding="utf-8")
fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "quiet_console_children")
calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
check("lifecycle.quiet_console_children only wraps Popen.__init__ and calls the original",
      "Popen.__init__" in ast.unparse(fn) and not any(
          isinstance(c.func, ast.Attribute) and c.func.attr in ("run", "call", "check_output") for c in calls))
daemon_imports = set()
for p in (CORE / "server.py", CORE / "lifecycle.py", CORE / "brain" / "executor.py", CORE / "tools" / "__init__.py"):
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if isinstance(n, ast.ImportFrom) and n.module:
            daemon_imports.add(n.module)
        elif isinstance(n, ast.Import):
            daemon_imports.update(a.name for a in n.names)
check("the daemon, the executor and the tool table never import the exam harness",
      not any(m.startswith("core.exam") for m in daemon_imports))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
