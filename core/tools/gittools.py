from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from core.security import gate

from . import langs, runner
from .base import ToolError, ToolHold

GIT_TIMEOUT_S = 60.0
PUSH_TIMEOUT_S = 180.0
SHOW_CHARS = 4000
ACTIONS = ("status", "log", "diff", "branches", "branch", "switch", "add", "commit")
MUTATING = ("branch", "switch", "add", "commit")
ALIASES = {"st": "status", "history": "log", "commits": "log", "changes": "diff", "list": "branches",
           "create": "branch", "new": "branch", "checkout": "switch", "stage": "add"}
SECRET_FILE = re.compile(r"(?:^|[\\/])(?:\.env(?:\..*)?|.*\.(?:pem|key|p12|pfx|keystore|jks|ppk|kdbx)|"
                         r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|.*(?:secret|token|credential|password|apikey|"
                         r"api_key|api-key).*)$", re.I)
BRANCH = re.compile(r"^(?!-)(?!.*\.\.)(?!.*@\{)(?!.*[\\~^:?*\[\s])(?!.*\.lock$)(?!.*/$)(?!.*\.$)[A-Za-z0-9._/-]{1,100}$")
PERMISSIONS = Path(__file__).resolve().parents[1] / "config" / "permissions.yaml"
NO_HOOKS: Path | None = None
SAFE_CONFIG = re.compile(
    r"^(?:core\.(?:repositoryformatversion|filemode|bare|logallrefupdates|symlinks|ignorecase|autocrlf|eol|safecrlf|"
    r"longpaths|precomposeunicode|quotepath|compression|loosecompression|whitespace|checkstat|trustctime|"
    r"untrackedcache|splitindex|preloadindex|fscache|abbrev|warnambiguousrefs|sharedrepository|protectntfs|"
    r"protecthfs|commitgraph|multipackindex)"
    r"|remote\..+\.(?:url|pushurl|fetch|push|tagopt|prune|prunetags|mirror|skipdefaultupdate|skipfetchall|"
    r"promisor|partialclonefilter)"
    r"|branch\..+\.(?:remote|merge|rebase|pushremote|description)"
    r"|user\.(?:name|email)|pull\.(?:rebase|ff)|push\.(?:default|autosetupremote|followtags)"
    r"|fetch\.(?:prune|prunetags|writecommitgraph)|init\.defaultbranch"
    r"|extensions\.(?:worktreeconfig|objectformat|refstorage|preciousobjects|partialclone)"
    r"|gc\.(?:auto|autopacklimit|autodetach|reflogexpire|reflogexpireunreachable|pruneexpire)"
    r"|index\.(?:version|threads)|status\.(?:showuntrackedfiles|branch|short)|color\.[^.]+(?:\.[^.]+)?"
    r"|log\.(?:date|decorate|follow|abbrevcommit|mailmap)"
    r"|diff\.(?:renames|algorithm|mnemonicprefix|noprefix|context|statgraphwidth|indentheuristic|colormoved)"
    r"|merge\.(?:ff|conflictstyle|log|renamelimit|verbosity|autostash)"
    r"|rebase\.(?:autostash|autosquash|updaterefs|stat)"
    r"|submodule\..+\.(?:url|active|branch|ignore|shallow)"
    r"|lfs\.repositoryformatversion|i18n\.(?:commitencoding|logoutputencoding)|gui\.[^.]+)$")


def git_exe() -> str:
    found = shutil.which("git")
    if found:
        return found
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        v = os.environ.get(var)
        if v and (Path(v) / "Git" / "cmd" / "git.exe").is_file():
            return str(Path(v) / "Git" / "cmd" / "git.exe")
    raise ToolError("git is not installed on this laptop", "I never install it myself.")


def _run_git(argv: list[str], cwd: str, timeout: float) -> tuple[int, str]:
    env, _removed = langs.child_env({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_OPTIONAL_LOCKS": "0",
                                     "GIT_PAGER": "cat", "PAGER": "cat", "LC_ALL": "C"})
    try:
        proc = gate.run(argv, why="git", cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=timeout, creationflags=runner.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ToolError(f"git took longer than {int(timeout)} seconds, so I stopped waiting", "Try it in your terminal.") from None
    except OSError as exc:
        raise ToolError(f"git would not start ({exc.strerror or exc})", "Check that git is installed.") from None
    return proc.returncode, runner.decode(proc.stdout)


def _continues(line: str) -> bool:
    tail = len(line) - len(line.rstrip("\\"))
    return tail % 2 == 1


def config_keys(text: str) -> list[str]:
    keys: list[str] = []
    section: str | None = None
    cont = False
    for raw in text.splitlines():
        line = raw.strip()
        if cont:
            cont = _continues(line)
            continue
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            m = re.match(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\](.*)$', line)
            if not m:
                raise ValueError(f"a section line I cannot read: {line[:60]!r}")
            name, sub, rest = m.groups()
            section = name.lower() + ("." + sub.lower() if sub is not None else "")
            line = rest.strip()
            if not line or line[0] in "#;":
                continue
        if section is None:
            raise ValueError("a setting outside any section")
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*)\s*(?:=|$|[#;])", line)
        if not m:
            raise ValueError(f"a setting line I cannot read: {line[:60]!r}")
        keys.append(section + "." + m.group(1).lower())
        cont = _continues(line)
    return keys


def config_files(root: Path) -> list[Path]:
    dot = root / ".git"
    if dot.is_dir():
        gitdir = dot
    else:
        try:
            text = dot.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            raise ToolError(f"I could not read {dot}", "Run git there yourself.") from None
        if not text.lower().startswith("gitdir:"):
            raise ToolError(f"{dot} is not a git link I can read", "Run git there yourself.")
        gitdir = Path(text[7:].strip())
        if not gitdir.is_absolute():
            gitdir = (root / gitdir).resolve()
    base = gitdir
    common = gitdir / "commondir"
    if common.is_file():
        base = (gitdir / common.read_text(encoding="utf-8", errors="replace").strip()).resolve()
    out: list[Path] = []
    for p in (base / "config", base / "config.worktree", gitdir / "config", gitdir / "config.worktree"):
        if p.is_file() and not any(gate.norm(p) == gate.norm(q) for q in out):
            out.append(p)
    return out


def check_config(root: Path) -> None:
    for f in config_files(root):
        try:
            keys = config_keys(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError) as exc:
            raise ToolError(f"I could not read this repository's settings in {f} ({exc}), so I did not run git",
                            "Run git there yourself.") from None
        odd = sorted({k for k in keys if not SAFE_CONFIG.match(k)})
        if odd:
            raise ToolError(f"this repository's own settings ({f}) include {', '.join(odd[:6])}, which I do not run "
                            f"git with, because settings like these can make git start a program",
                            "Run git there yourself, or take that setting out of the repository.")


def no_hooks_dir() -> Path:
    hooks = NO_HOOKS or gate.store_paths()[0].parent / "git-no-hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    if any(hooks.iterdir()):
        raise ToolError(f"{hooks} should be empty and is not, so I did not run git",
                        "Empty that folder, then ask me again.")
    return hooks


def git(root: Path, *args: str, timeout: float = GIT_TIMEOUT_S) -> tuple[int, str]:
    check_config(root)
    hooks = no_hooks_dir()
    argv = [git_exe(), "-C", str(root), "--no-pager", "-c", f"core.hooksPath={hooks}", "-c", "core.fsmonitor=false",
            "-c", "color.ui=false", "-c", "core.quotepath=false", *args]
    return _run_git(argv, str(root), timeout)


def record_switched(root: Path, old: str) -> None:
    if not re.match(r"^[0-9a-f]{40,64}$", old):
        gate.record(root, "switch", "git.repo")
        return
    code, out = git(root, "diff", "--name-only", "-z", old, "HEAD")
    if code != 0:
        gate.record(root, "switch", "git.repo")
        return
    for rel in out.split("\0"):
        if rel.strip() and (root / rel).is_file():
            gate.record(root / rel, "switch", "git.repo")


def repo_root(folder: Any) -> Path:
    raw = str(folder or "").strip().strip('"')
    if not raw:
        raise ToolError("no folder came through", "Tell me the folder the repository is in.")
    p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute() or not p.exists():
        raise ToolError(f"{p} is not there", "Tell me the folder the repository is in.")
    here = p if p.is_dir() else p.parent
    for d in (here, *here.parents):
        if (d / ".git").exists():
            return d
    raise ToolError(f"{here.name or here} is not a git repository", "Name a folder that is one.")


def _protected(root: Path) -> bool:
    from core.security.guard import Guard

    return Guard(PERMISSIONS).is_protected(str(root))


def _fenced(spoken: str, out: str) -> str:
    body = out.strip() or "(git printed nothing)"
    if len(body) > SHOW_CHARS:
        body = body[:SHOW_CHARS] + f"\n... {len(out) - SHOW_CHARS} more characters"
    return f"{spoken}\n```text\n{body.replace('```', chr(39) * 3)}\n```"


def _listed(names: list[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _clean_words(text: str) -> str:
    return text if re.match(r"^[\w .,'!?:;()-]{0,80}$", text) and "_" not in text else ""


def _status_words(out: str) -> str:
    lines = out.splitlines()
    head = lines[0] if lines and lines[0].startswith("##") else ""
    m = re.match(r"^## ([^.\s]+)", head)
    branch = m.group(1) if m else "an unnamed branch"
    files = [ln for ln in lines if ln and not ln.startswith("##")]
    staged = sum(1 for ln in files if ln[0] not in " ?")
    changed = sum(1 for ln in files if len(ln) > 1 and ln[1] not in " ?")
    new = sum(1 for ln in files if ln.startswith("??"))
    ahead = re.search(r"ahead (\d+)", head)
    behind = re.search(r"behind (\d+)", head)
    parts = []
    if staged:
        parts.append(f"{_plural(staged, 'file')} staged")
    if changed:
        parts.append(f"{_plural(changed, 'file')} changed")
    if new:
        parts.append(f"{_plural(new, 'new file')}")
    words = f"On branch {branch}, Emperor: " + (_listed(parts) if parts else "nothing to commit") + "."
    if ahead:
        words += f" {_plural(int(ahead.group(1)), 'commit')} not pushed yet."
    if behind:
        words += f" {_plural(int(behind.group(1)), 'commit')} behind."
    return words


def _propose(files: list[str]) -> str:
    names = [Path(f).name for f in files]
    if len(names) <= 2:
        return "Update " + " and ".join(names)
    return f"Update {names[0]}, {names[1]} and {len(names) - 2} other file{'s' if len(names) > 3 else ''}"


def _secret_note(files: list[str]) -> str:
    risky = [Path(f).name for f in files if SECRET_FILE.search(f.replace("/", os.sep))]
    if not risky:
        return ""
    return f", and {_listed(risky)} may hold a secret"


def git_repo(action: str, folder: str, name: str = "", files: Any = (), message: str = "", n: int = 10, *,
             provenance: str = "schedule", confirmed: bool = False) -> dict[str, Any]:
    act = ALIASES.get(str(action or "").strip().lower(), str(action or "").strip().lower())
    if act not in ACTIONS:
        raise ToolError(f"I do not do git {action or 'that'} in this step",
                        "I can show the status, the log, a diff and the branches, make or switch a branch, "
                        "stage files and commit.")
    root = repo_root(folder)
    repo = root.name or str(root)
    try:
        count = int(n)
    except (TypeError, ValueError):
        count = 10
    count = max(1, min(50, count))
    names = [str(f) for f in files] if isinstance(files, (list, tuple)) else [f for f in str(files or "").split() if f]
    reasons = []
    if provenance != "human":
        reasons.append("the model picked it from your words")
    if act in MUTATING and _protected(root):
        reasons.append(f"{repo} is a protected folder")
    args_now = {"action": act, "folder": str(folder), "name": name, "files": names, "message": message, "n": count}
    if act == "status":
        if reasons and not confirmed:
            raise ToolHold(f"That reads the git status of {repo}: {', and '.join(reasons)}")
        code, out = git(root, "status", "--porcelain=v1", "--branch")
        if code != 0:
            raise ToolError(f"git status failed: {out.strip()[:160]}", "Check the folder in your terminal.")
        spoken = _status_words(out)
    elif act == "log":
        if reasons and not confirmed:
            raise ToolHold(f"That reads the git log of {repo}: {', and '.join(reasons)}")
        code, out = git(root, "log", f"-n{count}", "--date=short", "--pretty=format:%h %ad %an %s")
        if code != 0:
            raise ToolError(f"git log failed: {out.strip()[:160]}", "The repository may have no commits yet.")
        rows = [ln for ln in out.splitlines() if ln.strip()]
        newest = rows[0].split(" ", 3)[3] if rows and len(rows[0].split(" ", 3)) == 4 else ""
        spoken = f"The last {_plural(len(rows), 'commit')} in {repo}, Emperor." + (
            f" The newest is: {newest.rstrip('.')}." if _clean_words(newest) else "")
    elif act == "diff":
        if reasons and not confirmed:
            raise ToolHold(f"That reads the changes in {repo}: {', and '.join(reasons)}")
        code, stat = git(root, "diff", "--stat")
        code2, full = git(root, "diff")
        if code != 0 or code2 != 0:
            raise ToolError(f"git diff failed: {(stat or full).strip()[:160]}", "Check the folder in your terminal.")
        last = stat.strip().splitlines()[-1].strip() if stat.strip() else ""
        spoken = (f"Unstaged changes in {repo}, Emperor: {last}." if last else f"No unstaged changes in {repo}, Emperor.")
        out = stat + ("\n" + full if full.strip() else "")
    elif act == "branches":
        if reasons and not confirmed:
            raise ToolHold(f"That lists the branches of {repo}: {', and '.join(reasons)}")
        code, out = git(root, "branch", "--list")
        if code != 0:
            raise ToolError(f"git branch failed: {out.strip()[:160]}", "Check the folder in your terminal.")
        rows = [ln for ln in out.splitlines() if ln.strip()]
        cur = next((ln[2:].strip() for ln in rows if ln.startswith("*")), "")
        spoken = f"{_plural(len(rows), 'branch')} in {repo}, Emperor" + (f"; you are on {cur}." if cur else ".")
    elif act in ("branch", "switch"):
        if not BRANCH.match(str(name or "")):
            raise ToolError(f"{name or 'that'} is not a branch name git accepts", "Use letters, digits, dots, dashes and slashes.")
        if reasons and not confirmed:
            verb = "makes the branch" if act == "branch" else "switches to the branch"
            raise ToolHold(f"That {verb} {name} in {repo}: {', and '.join(reasons)}")
        old = git(root, "rev-parse", "HEAD")[1].strip() if act == "switch" else ""
        code, out = git(root, "branch", name) if act == "branch" else git(root, "switch", name)
        if code != 0:
            raise ToolError(f"git {act} failed: {out.strip()[:160]}", "Check the branch name and try again.")
        if act == "switch":
            record_switched(root, old)
        spoken = (f"Made the branch {name} in {repo}, Emperor." if act == "branch"
                  else f"Switched {repo} to {name}, Emperor.")
    elif act == "add":
        everything = not names or any(x.lower() in ("all", "everything", ".", "-a", "--all") for x in names)
        if everything:
            code, st = git(root, "status", "--porcelain=v1")
            picked = [ln[3:].strip() for ln in st.splitlines() if len(ln) > 3]
        else:
            picked = []
            for f in names:
                fp = (root / f) if not Path(f).is_absolute() else Path(f)
                try:
                    rel = fp.resolve().relative_to(root.resolve())
                except ValueError:
                    raise ToolError(f"{f} is not inside {repo}", "Name files inside the repository.") from None
                if str(rel).startswith("-"):
                    raise ToolError(f"{f} looks like an option, not a file", "Name the file plainly.")
                picked.append(rel.as_posix())
        note = _secret_note(picked)
        if (reasons or note) and not confirmed:
            why = ", and ".join(reasons + ([note[2:]] if note else []))
            raise ToolHold(f"That stages {_plural(len(picked), 'file')} in {repo}: {why}")
        code, out = git(root, "add", "--all") if everything else git(root, "add", "--", *picked)
        if code != 0:
            raise ToolError(f"git add failed: {out.strip()[:160]}", "Check the file names.")
        spoken = f"Staged {_plural(len(picked), 'file')} in {repo}, Emperor."
        out = out or "\n".join(picked)
    else:
        code, staged_out = git(root, "diff", "--cached", "--name-only")
        staged = [ln.strip() for ln in staged_out.splitlines() if ln.strip()]
        if code != 0:
            raise ToolError(f"git could not list the staged files: {staged_out.strip()[:160]}", "Check the folder.")
        if not staged:
            raise ToolError(f"nothing is staged to commit in {repo}", f"Say stage the changes in {repo} first.")
        words = str(message or "").strip() or _propose(staged)
        if not confirmed:
            shown = _listed([Path(f).name for f in staged[:6]]) + (f" and {len(staged) - 6} more" if len(staged) > 6 else "")
            ask = "" if str(message or "").strip() else ", a message I propose"
            raise ToolHold(f"That commits {_plural(len(staged), 'file')} in {repo}: {shown}{_secret_note(staged)}, with "
                           f"the message \"{words}\"{ask}", resolved={**args_now, "message": words})
        code, out = git(root, "commit", "-m", words)
        if code != 0:
            raise ToolError(f"git commit failed: {out.strip()[:160]}", "Check the repository in your terminal.")
        spoken = f"Committed {_plural(len(staged), 'file')} in {repo}, Emperor."
    return {"spoken": spoken, "said": _fenced(spoken, out), "repo": str(root), "action": act,
            "external_text": out[:8000], "external_source": "git output"}


def _display_url(url: str) -> str:
    u = re.sub(r"(?<=://)[^@/]+@", "", url.strip())
    return u


def resolve_push(args: dict[str, Any]) -> dict[str, Any]:
    root = repo_root(args.get("folder"))
    code, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()
    if code != 0 or not branch or branch == "HEAD" or not BRANCH.match(branch):
        raise ToolError(f"{root.name} is not on a branch I can push", "Switch to a branch first.")
    remote = str(args.get("remote") or "").strip()
    ahead = "unknown"
    code_u, upstream = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if code_u == 0 and "/" in upstream.strip():
        remote = remote or upstream.strip().split("/", 1)[0]
        code_a, count = git(root, "rev-list", "--count", "@{u}..HEAD")
        if code_a == 0 and count.strip().isdigit():
            ahead = count.strip()
    remote = remote or "origin"
    if not re.match(r"^[A-Za-z0-9._-]{1,60}$", remote):
        raise ToolError(f"{remote} is not a remote name I accept", "Name the remote plainly.")
    code_r, url = git(root, "remote", "get-url", remote)
    if code_r != 0 or not url.strip():
        raise ToolError(f"{root.name} has no remote called {remote}", "Add the remote in your terminal first.")
    shown = _display_url(url)
    return {"folder": str(root), "remote": remote, "url": shown, "branch": branch, "ahead": ahead,
            "goes_online": f"This sends your commits to {shown}, on the internet. It cannot be taken back by me."}


def describe_push(args: dict[str, Any]) -> str:
    host = re.sub(r"^[a-z+]+://", "", str(args.get("url") or "")).split("/")[0].split(":")[0] or "the remote"
    ahead = str(args.get("ahead") or "unknown")
    what = "your commits" if not ahead.isdigit() else f"{ahead} commit{'' if ahead == '1' else 's'}"
    return (f"push {what} from {args.get('branch')} in {Path(str(args.get('folder') or '')).name} to {host}, "
            f"which goes online")


def git_push(folder: str = "", remote: str = "", url: str = "", branch: str = "", ahead: str = "", goes_online: str = "",
             *, _approved_by_surface: bool = False) -> dict[str, Any]:
    if not _approved_by_surface:
        raise ToolError("a push goes online and runs only from the approval card", "Approve it on the card.")
    root = repo_root(folder)
    if not BRANCH.match(str(branch or "")) or not re.match(r"^[A-Za-z0-9._-]{1,60}$", str(remote or "")):
        raise ToolError("the branch or the remote on the card is not one I accept", "Ask me to push again.")
    code, out = git(root, "push", remote, branch, timeout=PUSH_TIMEOUT_S)
    if code != 0:
        raise ToolError(f"git push failed: {out.strip()[:200]}", "Run it in your terminal once to sign in, then ask me again.")
    spoken = f"Pushed {branch} of {root.name} to {remote}, Emperor."
    return {"spoken": spoken, "said": _fenced(spoken, out), "external_text": out[:8000],
            "external_source": "git output"}
