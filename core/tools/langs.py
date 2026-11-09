from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess

from core.security import gate
import tempfile
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.security import secrets as secret_store

CREATE_NO_WINDOW = 0x08000000
CHECK_TIMEOUT_S = 10.0
SECRET_NAME = secret_store.SECRET_NAME
HER_ONLY = ("FOR_DISABLE_CONSOLE_CTRL_HANDLER",)
OFFLINE = {"GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off",
           "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "CARGO_NET_OFFLINE": "true", "RUSTUP_AUTO_INSTALL": "0",
           "PIP_NO_INDEX": "1", "npm_config_offline": "true"}
SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
GIT_PERL = "the only Perl here is the copy inside Git for Windows"


@dataclass(frozen=True)
class Spec:
    key: str
    name: str
    exts: tuple[str, ...]
    compiled: bool = False


LANGS: tuple[Spec, ...] = (
    Spec("python", "Python", (".py",)),
    Spec("javascript", "JavaScript", (".js", ".mjs", ".cjs")),
    Spec("typescript", "TypeScript", (".ts", ".mts")),
    Spec("java", "Java", (".java",)),
    Spec("c", "C", (".c",), True),
    Spec("cpp", "C++", (".cpp", ".cc", ".cxx"), True),
    Spec("csharp", "C#", (".cs",), True),
    Spec("fortran", "Fortran", (".f90", ".f95", ".f"), True),
    Spec("rust", "Rust", (".rs",), True),
    Spec("go", "Go", (".go",), True),
    Spec("php", "PHP", (".php",)),
    Spec("ruby", "Ruby", (".rb",)),
    Spec("powershell", "PowerShell", (".ps1",)),
    Spec("batch", "batch", (".bat", ".cmd")),
    Spec("bash", "Bash", (".sh",)),
    Spec("perl", "Perl", (".pl",)),
    Spec("lua", "Lua", (".lua",)),
    Spec("r", "R", (".r",)),
    Spec("julia", "Julia", (".jl",)),
)
BY_KEY = {s.key: s for s in LANGS}
WRITE_ONLY = {"cobol": ("COBOL", ".cob"), "haskell": ("Haskell", ".hs"), "kotlin": ("Kotlin", ".kt"),
              "swift": ("Swift", ".swift"), "scala": ("Scala", ".scala"), "dart": ("Dart", ".dart"),
              "elixir": ("Elixir", ".ex"), "sql": ("SQL", ".sql"), "pascal": ("Pascal", ".pas"),
              "zig": ("Zig", ".zig"), "nim": ("Nim", ".nim"), "ocaml": ("OCaml", ".ml"),
              "clojure": ("Clojure", ".clj"), "erlang": ("Erlang", ".erl"), "assembly": ("Assembly", ".asm"),
              "visualbasic": ("Visual Basic", ".vb")}
ALIASES = {"py": "python", "python3": "python", "js": "javascript", "node": "javascript", "nodejs": "javascript",
           "node.js": "javascript", "ts": "typescript", "c++": "cpp", "cplusplus": "cpp", "cpp": "cpp",
           "c#": "csharp", "csharp": "csharp", "c-sharp": "csharp", "golang": "go", "rs": "rust",
           "ps": "powershell", "ps1": "powershell", "pwsh": "powershell", "bat": "batch", "cmd": "batch",
           "shell": "bash", "sh": "bash", "f90": "fortran", "rb": "ruby", "pl": "perl", "jl": "julia",
           "vb": "visualbasic", "vb.net": "visualbasic", "asm": "assembly"}


def canonical(name: Any) -> str | None:
    t = re.sub(r"\s+", " ", str(name or "").strip().lower())
    t = re.sub(r"\s+(?:script|code|language|program)$", "", t)
    t = {"c sharp": "csharp", "c plus plus": "cpp", "visual basic": "visualbasic"}.get(t, t)
    t = ALIASES.get(t, t)
    if t in BY_KEY or t in WRITE_ONLY:
        return t
    return None


def display(key: str) -> str:
    if key in BY_KEY:
        return BY_KEY[key].name
    return WRITE_ONLY.get(key, (key, ""))[0]


def extension(key: str) -> str:
    if key in BY_KEY:
        return BY_KEY[key].exts[0]
    return WRITE_ONLY.get(key, ("", ""))[1]


def spec_for_ext(ext: str) -> Spec | None:
    e = str(ext or "").lower()
    return next((s for s in LANGS if e in s.exts), None)


@dataclass
class Found:
    key: str
    name: str
    installed: bool
    exe: str = ""
    where: str = ""
    version: str = ""
    version_from: str = ""
    check_ms: float | None = None
    tool: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    only_his_terminal: bool = False


def _her_path() -> list[str]:
    return [p for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()]


def _his_path() -> list[str]:
    import winreg

    out: list[str] = []
    for hive, key, name in ((winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "Path"),
                            (winreg.HKEY_CURRENT_USER, r"Environment", "PATH")):
        try:
            with winreg.OpenKey(hive, key) as k:
                value, _t = winreg.QueryValueEx(k, name)
        except OSError:
            continue
        out.extend(os.path.expandvars(p) for p in str(value).split(os.pathsep) if p.strip())
    return out


def _store_stub(path: str) -> bool:
    return "\\microsoft\\windowsapps\\" in os.path.normcase(path)


def which(name: str, path: list[str] | None = None) -> str:
    dirs = _her_path() if path is None else path
    for d in dirs:
        for ext in (".exe", ".cmd", ".bat", ""):
            p = os.path.join(d.strip().strip('"'), name + ext)
            if os.path.isfile(p) and not _store_stub(p):
                return p
    return ""


def file_version(path: str) -> str:
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buf):
            return ""
        ptr = ctypes.c_void_p()
        n = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)):
            return ""
        vs = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        return f"{vs[2] >> 16}.{vs[2] & 0xFFFF}.{vs[3] >> 16}.{vs[3] & 0xFFFF}"
    except Exception:
        return ""


def secret_names() -> set[str]:
    names = {n for n in os.environ if SECRET_NAME.search(n) or n.upper().startswith("TESSA_")}
    names.update(secret_store.held_names())
    names.update(n for n in HER_ONLY if n in os.environ)
    try:
        import yaml

        brain = (yaml.safe_load(SETTINGS.read_text(encoding="utf-8")) or {}).get("brain") or {}
        for v in brain.values():
            if isinstance(v, dict) and v.get("api_key_env"):
                names.add(str(v["api_key_env"]))
    except Exception:
        pass
    return {n for n in names if n in os.environ or n in secret_store.held_names()}


def child_env(extra: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    removed = sorted(secret_names(), key=str.upper)
    env = {k: v for k, v in os.environ.items() if k not in removed}
    env.update(OFFLINE)
    env.update(extra or {})
    return env, removed


def _check(exe: str, args: list[str], env_extra: dict[str, str] | None = None) -> tuple[str, float | None]:
    env, _removed = child_env(env_extra)
    t0 = time.perf_counter()
    try:
        proc = gate.run([exe, *args], why="language version check", stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=CHECK_TIMEOUT_S, creationflags=CREATE_NO_WINDOW,
                        env=env, cwd=tempfile.gettempdir())
    except (OSError, subprocess.SubprocessError):
        return "", None
    ms = (time.perf_counter() - t0) * 1000.0
    text = proc.stdout.decode("utf-8", errors="replace").strip().splitlines()
    return (text[0].strip() if text else ""), ms


def _program_files() -> list[Path]:
    out = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(var)
        if v and Path(v) not in out:
            out.append(Path(v))
    return out


def _vtuple(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:4])


def find_msvc() -> dict[str, Any] | None:
    best: tuple[tuple[int, ...], Path] | None = None
    for root in _program_files():
        base = root / "Microsoft Visual Studio"
        if not base.is_dir():
            continue
        for tools in base.glob("*/*/VC/Tools/MSVC/*"):
            cl = tools / "bin" / "Hostx64" / "x64" / "cl.exe"
            if cl.is_file():
                v = _vtuple(tools.name)
                if best is None or v > best[0]:
                    best = (v, tools)
    if best is None:
        return None
    tools = best[1]
    kits = None
    for root in _program_files():
        inc = root / "Windows Kits" / "10" / "Include"
        if inc.is_dir():
            vers = sorted((d for d in inc.iterdir() if (d / "um" / "windows.h").is_file()), key=lambda d: _vtuple(d.name))
            if vers:
                kits = (root / "Windows Kits" / "10", vers[-1].name)
                break
    if kits is None:
        return None
    kroot, kver = kits
    include = [tools / "include", kroot / "Include" / kver / "ucrt", kroot / "Include" / kver / "um",
               kroot / "Include" / kver / "shared"]
    lib = [tools / "lib" / "x64", kroot / "Lib" / kver / "ucrt" / "x64", kroot / "Lib" / kver / "um" / "x64"]
    cl = tools / "bin" / "Hostx64" / "x64" / "cl.exe"
    return {"cl": str(cl), "msvc": tools.name, "kits": kver,
            "env": {"INCLUDE": os.pathsep.join(str(p) for p in include),
                    "LIB": os.pathsep.join(str(p) for p in lib),
                    "PATH": str(cl.parent) + os.pathsep + os.environ.get("PATH", "")},
            "where": str(tools.parents[3])}


def _gcc_version(gcc: str) -> str:
    root = Path(gcc).resolve().parent.parent / "lib" / "gcc"
    vers = sorted((d.name for d in root.glob("*/*") if re.match(r"^\d+(\.\d+)+$", d.name)), key=_vtuple)
    return vers[-1] if vers else ""


def _rustc() -> tuple[str, str]:
    home = Path(os.environ.get("RUSTUP_HOME") or (Path.home() / ".rustup"))
    settings = home / "settings.toml"
    tc = ""
    try:
        m = re.search(r'^default_toolchain\s*=\s*"([^"]+)"', settings.read_text(encoding="utf-8"), re.M)
        tc = m.group(1) if m else ""
    except OSError:
        pass
    if tc:
        exe = home / "toolchains" / tc / "bin" / "rustc.exe"
        if exe.is_file():
            ver = ""
            manifest = home / "toolchains" / tc / "lib" / "rustlib" / "multirust-channel-manifest.toml"
            try:
                m = re.search(r'\[pkg\.rustc\]\s*version\s*=\s*"([^"]+)"', manifest.read_text(encoding="utf-8"))
                ver = m.group(1) if m else ""
            except OSError:
                pass
            return str(exe), ver
    return "", ""


def _in_git(*parts: str) -> str:
    git = which("git")
    if not git:
        for root in _program_files():
            if (root / "Git" / "cmd" / "git.exe").is_file():
                git = str(root / "Git" / "cmd" / "git.exe")
                break
    roots = list(Path(git).resolve().parents) if git else []
    roots += [root / "Git" for root in _program_files()]
    for root in roots:
        if not ((root / "git-bash.exe").is_file() or (root / "cmd" / "git.exe").is_file()):
            continue
        found = root.joinpath(*parts)
        if found.is_file():
            return str(found)
    return ""


def _git_bash() -> str:
    return _in_git("bin", "bash.exe")


def _git_perl() -> str:
    return _in_git("usr", "bin", "perl.exe")


def _simple(key: str, exe_name: str, args: list[str], her: list[str], his: list[str]) -> Found:
    spec = BY_KEY[key]
    exe = which(exe_name, her)
    other = which(exe_name, his)
    if not exe:
        f = Found(key, spec.name, False, note="not installed")
        if other:
            f.only_his_terminal = True
            f.note = f"on your PATH ({other}) but not on mine; restart me to see it"
        return f
    line, ms = _check(exe, args)
    return Found(key, spec.name, True, exe=exe, where=f"PATH: {exe}", version=line, version_from="one quick check",
                 check_ms=ms)


def look() -> list[Found]:
    her, his = _her_path(), _his_path()
    out: list[Found] = []
    py = which("python", her)
    if py:
        line, ms = _check(py, ["--version"])
        out.append(Found("python", "Python", True, exe=py, where=f"PATH: {py}", version=line or file_version(py),
                         version_from="one quick check" if line else "file", check_ms=ms))
    else:
        out.append(Found("python", "Python", False, note="not installed (the Windows Store stand-in does not count)"))
    node = which("node", her)
    if node:
        line, ms = _check(node, ["--version"])
        out.append(Found("javascript", "JavaScript", True, exe=node, where=f"PATH: {node}", tool="Node",
                         version=line or file_version(node), version_from="one quick check" if line else "file",
                         check_ms=ms))
        v = _vtuple(line or file_version(node))
        ok = v >= (22, 18)
        out.append(Found("typescript", "TypeScript", ok, exe=node if ok else "", where=f"PATH: {node}" if ok else "",
                         tool="Node", version=line, version_from="same check as JavaScript",
                         note=("Node's built-in type stripping: the types are not checked" if ok
                               else "needs Node 22.18 or newer")))
    else:
        out.append(Found("javascript", "JavaScript", False, note="not installed"))
        out.append(Found("typescript", "TypeScript", False, note="needs Node, which is not installed"))
    java = which("java", her)
    javac = str(Path(java).with_name("javac.exe")) if java else ""
    if java and Path(javac).is_file():
        release = Path(java).resolve().parent.parent / "release"
        ver = ""
        try:
            m = re.search(r'^JAVA_VERSION="([^"]+)"', release.read_text(encoding="utf-8"), re.M)
            ver = m.group(1) if m else ""
        except OSError:
            pass
        line, ms = _check(java, ["--version"])
        out.append(Found("java", "Java", True, exe=java, where=f"PATH: {java}", version=line or ver,
                         version_from="one quick check" if line else "release file", check_ms=ms,
                         note="source-file mode: nothing is compiled to disk"))
    else:
        out.append(Found("java", "Java", False, note=("a Java runtime without the compiler cannot run source files"
                                                      if java else "not installed")))
    msvc = find_msvc()
    gcc, gpp, gfortran = which("gcc", her), which("g++", her), which("gfortran", her)
    for key, gnu in (("c", gcc), ("cpp", gpp)):
        spec = BY_KEY[key]
        if msvc:
            out.append(Found(key, spec.name, True, exe=msvc["cl"], where=f"installed folder: {msvc['where']}",
                             tool="MSVC cl", version=f"cl {file_version(msvc['cl'])} (MSVC {msvc['msvc']}, Windows SDK "
                                                         f"{msvc['kits']})",
                             version_from="file", extra={"env": msvc["env"], "gnu": gnu},
                             note=(f"also {Path(gnu).stem} {_gcc_version(gnu)} on PATH" if gnu else "")))
        elif gnu:
            line, ms = _check(gnu, ["--version"])
            out.append(Found(key, spec.name, True, exe=gnu, where=f"PATH: {gnu}", tool=Path(gnu).stem,
                             version=line or _gcc_version(gnu), version_from="one quick check" if line else "folder",
                             check_ms=ms))
        else:
            out.append(Found(key, spec.name, False, note="no C or C++ compiler found"))
    csc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if csc.is_file():
        out.append(Found("csharp", "C#", True, exe=str(csc), where=f"installed folder: {csc.parent}",
                         tool=".NET Framework csc", version=f"csc {file_version(str(csc))} (C# 5)", version_from="file",
                         note="the dotnet SDK is left out: it restores packages from the internet"))
    else:
        out.append(Found("csharp", "C#", False, note="no offline C# compiler found"))
    if gfortran:
        line, ms = _check(gfortran, ["--version"])
        out.append(Found("fortran", "Fortran", True, exe=gfortran, where=f"PATH: {gfortran}", tool="gfortran",
                         version=line or _gcc_version(gfortran), version_from="one quick check" if line else "folder",
                         check_ms=ms))
    else:
        out.append(Found("fortran", "Fortran", False, note="not installed"))
    rustc, rver = _rustc()
    if rustc:
        line, ms = _check(rustc, ["--version"])
        out.append(Found("rust", "Rust", True, exe=rustc, where=f"installed folder: {Path(rustc).parent}",
                         version=line or rver, version_from="one quick check" if line else "channel manifest",
                         check_ms=ms, note="the toolchain's own rustc, not the rustup proxy: no toolchain download"))
    elif which("rustc", her):
        out.append(Found("rust", "Rust", False, note="only the rustup proxy was found; I do not let it download"))
    else:
        out.append(Found("rust", "Rust", False, note="not installed"))
    go = which("go", her)
    if not go:
        for root in _program_files():
            if (root / "Go" / "bin" / "go.exe").is_file():
                go = str(root / "Go" / "bin" / "go.exe")
                break
    if go:
        ver = ""
        try:
            ver = (Path(go).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError):
            pass
        line, ms = _check(go, ["version"])
        out.append(Found("go", "Go", True, exe=go, where=f"PATH: {go}" if which("go", her) else f"installed folder: {go}",
                         version=line or ver, version_from="one quick check" if line else "VERSION file", check_ms=ms,
                         note="GOTOOLCHAIN=local, GOPROXY=off, GOSUMDB=off for every run; its telemetry "
                              "counters go to the run's own folder, so nothing is uploaded"))
    else:
        out.append(Found("go", "Go", False, note="not installed"))
    out.append(_simple("php", "php", ["--version"], her, his))
    out.append(_simple("ruby", "ruby", ["--version"], her, his))
    ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    psver = ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\PowerShell\3\PowerShellEngine") as k:
            psver = str(winreg.QueryValueEx(k, "PowerShellVersion")[0])
    except OSError:
        pass
    out.append(Found("powershell", "PowerShell", ps.is_file(), exe=str(ps) if ps.is_file() else "",
                     where=f"installed folder: {ps.parent}" if ps.is_file() else "", version=psver,
                     version_from="registry", note="your execution policy decides; I never get round it"))
    cmd = os.environ.get("ComSpec") or str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe")
    out.append(Found("batch", "batch", Path(cmd).is_file(), exe=cmd, where=f"Windows: {cmd}", version="cmd.exe",
                     version_from="Windows", note="a batch file takes no arguments in this step"))
    bash = _git_bash()
    if bash:
        line, ms = _check(bash, ["--version"])
        out.append(Found("bash", "Bash", True, exe=bash, where=f"installed folder (Git for Windows): {bash}",
                         version=line, version_from="one quick check", check_ms=ms,
                         note="Git's bash, never the Windows Subsystem for Linux"))
    else:
        out.append(Found("bash", "Bash", False, note="Git for Windows' bash not found"))
    for key, name in (("perl", "perl"), ("lua", "lua"), ("r", "Rscript"), ("julia", "julia")):
        f = _simple(key, name, ["--version"], her, his)
        if key == "perl" and not f.installed:
            perl = _git_perl()
            if perl:
                line, ms = _check(perl, ["--version"])
                f = Found("perl", BY_KEY["perl"].name, True, exe=perl, where=f"installed folder (Git for Windows): {perl}",
                          version=line, version_from="one quick check", check_ms=ms,
                          note=GIT_PERL)
        if f.installed or f.only_his_terminal:
            out.append(f)
    return out


_STATE: dict[str, Any] = {"found": None, "at": 0.0, "ms": 0.0}
_LOCK = threading.Lock()


def languages(look_again: bool = False) -> list[Found]:
    with _LOCK:
        if _STATE["found"] is None or look_again:
            t0 = time.perf_counter()
            _STATE["found"] = look()
            _STATE["at"] = time.time()
            _STATE["ms"] = (time.perf_counter() - t0) * 1000.0
        return list(_STATE["found"])


def find(key: str) -> Found | None:
    return next((f for f in languages() if f.key == key), None)


def _listed(names: list[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def summary(found: list[Found], language: str = "") -> tuple[str, str]:
    if language:
        key = canonical(language)
        f = next((x for x in found if x.key == key), None) if key else None
        if key and f is None:
            name = display(key)
            spoken = f"I cannot run {name} here, Emperor: it is not one I can run on this laptop. I can still write it."
        elif f is None:
            spoken = f"I do not know a language called {language}, Emperor."
        elif f.installed:
            spoken = f"Yes, Emperor. I can run {f.name} here" + (
                ", with the copy that comes inside Git for Windows." if f.note == GIT_PERL else ".")
        elif f.only_his_terminal:
            spoken = f"{f.name} is on your own path but not on mine, Emperor. Restart me and I will see it."
        else:
            spoken = f"{f.name} is not installed on this laptop, Emperor. I never install it myself."
    else:
        yes = [f.name for f in found if f.installed]
        no = [f.name for f in found if not f.installed and f.key in ("php", "ruby", "python", "javascript", "java",
                                                                       "c", "cpp", "rust", "go", "csharp")]
        spoken = f"I can run {_listed(yes)} here, Emperor."
        if no:
            spoken += f" {_listed(no)} {'is' if len(no) == 1 else 'are'} not installed."
        his = [f.name for f in found if f.only_his_terminal]
        if his:
            spoken += f" {_listed(his)} {'is' if len(his) == 1 else 'are'} on your own path but not on mine yet."
    rows = []
    for f in found:
        if f.installed:
            rows.append(f"{f.name:<11} yes  {f.version}  [{f.where}; version from {f.version_from}"
                        + (f", check {f.check_ms:.0f} ms" if f.check_ms is not None else "") + "]"
                        + (f"  {f.note}" if f.note else ""))
        else:
            rows.append(f"{f.name:<11} no   {f.note}")
    shown = spoken + "\n```text\n" + "\n".join(rows) + "\n```"
    return spoken, shown
