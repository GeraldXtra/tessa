"""
core/system/abilities/registry_write.py — WRITE ONE REGISTRY VALUE.
RED, an ALLOWLIST of writable keys, an ABSOLUTE NEVER-LIST, DEFAULT-DENY,
and the key + value name + value FROZEN on the card.

    system.registry.set     red    registry.write     winreg.SetValueEx of ONE value under an
                                                      ALLOWLISTED HKCU preference key (no create)

────────────────────────────────────────────────────────────────────────────────
⚠⚠ WHY THIS IS THE DEEPEST KEY OF THE MACHINE-CONTROL BATCHES

The registry is where Windows keeps its own configuration. A wrong write does
not raise an error — Windows silently does what the value says: runs a program
at login (the Run keys), runs a program INSTEAD of the shell (Winlogon), turns
the antivirus off (Windows Defender), swaps the debugger for every .exe (Image
File Execution Options). It is where malware PERSISTS and where security is
DISABLED. A poisoned tweet reaching a registry write is "add me to Run and turn
Defender off". So the guard here is the tightest in the build, and it is
STRUCTURAL, not hopeful:

  1. DEFAULT-DENY. Only a key under one of the `ALLOWLIST` roots — his own
     appearance and input preferences under HKCU (Control Panel\\Desktop,
     Explorer\\Advanced, Themes\\Personalize, DWM, Console ...) — can be written
     at all. An unknown key, a key in another hive, a key nobody classified:
     refused. The allowlist is the whole set of what is writable; nothing else
     is, whatever the card says.
  2. THE NEVER-LIST IS ABSOLUTE, checked BEFORE the allowlist, BEFORE the card
     (`describe`) and AGAIN after approval (`run`). It names the persistence
     keys (Run*, Winlogon, IFEO, AppInit, Shell Folders, App Paths, Classes,
     Environment, Command Processor ...), the security keys (Windows Defender,
     every Policies key, Windows Update, certificates, the firewall ...), the
     system hives (SYSTEM, SAM, SECURITY, BCD, HARDWARE, HKCR, HKCC), and
     Tessa's OWN keys (anything named Tessa, the Python runtime she runs on, her
     Chrome profiles, OneDrive). No approval reaches past it.
  3. ONE VALUE, TWO TYPES, NO KEY CREATION. A REG_SZ (or an existing REG_EXPAND_SZ,
     preserved) or a REG_DWORD. Never binary, never multi-string, never a new
     key. The value NAME cannot be a loader slot (SCRNSAVE.EXE, Shell, Userinit,
     Load, Debugger, AppInit_DLLs ...) and the VALUE cannot name an executable,
     a script, a URL or a shell — so even an allowlisted key cannot be turned
     into a launch point.
  4. FROZEN. `key`, `name`, `value` and `kind` are all frozen on the card
     (`frozen=(...)`): an approved "set Advanced\\HideFileExt to 0" cannot come
     back from the surface as a Run key or a malware path. `resolve_edit`
     refuses the edit and keeps the request. There is no wording to edit.
  5. RED, CARD-ONLY, TWICE. The framework will not call `run` without the real
     `_approved_by_surface` from `Executor.execute_approved`; `run` refuses again
     without it. A model's args carrying the flag are stripped; a fenced context
     (a tweet in the fence) refuses the red action before a card is raised.
  6. ONE SEAM. `_set_value` is the only place `winreg.SetValueEx` is called, it
     re-asserts HKCU and the two types, and a proof replaces it with a recorder.

THE SHOW-WHAT'S-THERE STEP IS A READ. `describe` opens the key read-only and
names the current value on the card ("it is 1 now" / "it is not set now"), so he
approves against what the value IS, not only what he said. Reading needs no
tier: it is the same stdlib `winreg` the green observers use.

winreg IS STDLIB. No new dependency, no subprocess, no string is ever executed.
"""

from __future__ import annotations

import re
import winreg
from dataclasses import dataclass
from typing import Any, Callable

from core.system.capability import Capability, Param

BS = chr(92)

#: Spoken hive prefixes -> (short name, winreg handle). Every hive is PARSED so
#: a never-listed HKLM key is refused as a never-list hit ("that is a
#: persistence key") rather than as a spelling error — the audit line should
#: say what was attempted.
_HIVES: dict[str, tuple[str, int]] = {
    "HKCU": ("HKCU", winreg.HKEY_CURRENT_USER), "HKEY_CURRENT_USER": ("HKCU", winreg.HKEY_CURRENT_USER),
    "HKLM": ("HKLM", winreg.HKEY_LOCAL_MACHINE), "HKEY_LOCAL_MACHINE": ("HKLM", winreg.HKEY_LOCAL_MACHINE),
    "HKU": ("HKU", winreg.HKEY_USERS), "HKEY_USERS": ("HKU", winreg.HKEY_USERS),
    "HKCR": ("HKCR", winreg.HKEY_CLASSES_ROOT), "HKEY_CLASSES_ROOT": ("HKCR", winreg.HKEY_CLASSES_ROOT),
    "HKCC": ("HKCC", winreg.HKEY_CURRENT_CONFIG), "HKEY_CURRENT_CONFIG": ("HKCC", winreg.HKEY_CURRENT_CONFIG),
}

#: Characters a key segment or value name may not carry: control characters,
#: the wildcard/redirect set, and the forward slash (a key is spelled with
#: backslashes; a slash is a path being smuggled in).
_BAD_CHARS = re.compile(r'[\x00-\x1f/*?"<>|]')

MAX_KEY_CHARS = 512
MAX_SEGMENTS = 32
MAX_NAME_CHARS = 255
MAX_VALUE_CHARS = 1024

_TYPE_WORDS = {winreg.REG_SZ: "string", winreg.REG_EXPAND_SZ: "expandable string",
               winreg.REG_DWORD: "dword", winreg.REG_QWORD: "qword", winreg.REG_BINARY: "binary",
               winreg.REG_MULTI_SZ: "multi-string", winreg.REG_LINK: "link", winreg.REG_NONE: "none"}

_DWORD_RE = re.compile(r"^(?:0x[0-9a-fA-F]{1,8}|\d{1,10})$")


class Refused(Exception):
    """Internal: a target or value this module will not write. Converted to ToolError."""


# ─────────────────────────────────────────────────────────────────────────────
# THE ALLOWLIST — the whole set of what may be written. Subtrees, HKCU only.
# ─────────────────────────────────────────────────────────────────────────────

#: Each entry is the segment path under HKCU, lowercase; a key matches when its
#: segments START WITH one of these (the root itself or anything beneath it).
#: These are his appearance and input preferences: reversible, per-user, and
#: none of them is a place Windows loads code from. NOTHING ELSE IS WRITABLE.
ALLOWLIST: tuple[tuple[str, ...], ...] = (
    ("control panel", "desktop"),              # wallpaper, MenuShowDelay, screensaver timeout
    ("control panel", "mouse"),                # speed, swap buttons, double-click time
    ("control panel", "keyboard"),             # repeat delay and rate
    ("control panel", "colors"),               # classic colour scheme
    ("control panel", "sound"),                # beep, extended sounds
    ("console",),                              # console font, colours, buffer
    ("software", "microsoft", "windows", "currentversion", "explorer", "advanced"),   # hidden files, extensions, taskbar
    ("software", "microsoft", "windows", "currentversion", "themes", "personalize"),  # dark mode, transparency
    ("software", "microsoft", "windows", "currentversion", "search"),                 # Bing / Cortana toggles
    ("software", "microsoft", "windows", "dwm"),                                      # accent colour
    ("software", "microsoft", "windows", "currentversion", "contentdeliverymanager"), # suggestions and tips
)

#: The allowlist, spelled for the report and the refusal line.
ALLOWLIST_SPOKEN: tuple[str, ...] = tuple(
    "HKCU" + BS + BS.join(seg for seg in root) for root in ALLOWLIST)


# ─────────────────────────────────────────────────────────────────────────────
# THE NEVER-LIST — absolute. Checked before the allowlist, before the card,
# and again after approval. Hive-agnostic: a fragment matches in ANY hive.
# ─────────────────────────────────────────────────────────────────────────────

_PERSIST = "a persistence key — where auto-start entries and malware live"
_LOGON = "a login-hijack key — Windows runs whatever is written there at logon"
_SECURITY = "a security key — Windows Defender, policies, updates, certificates and the firewall"
_SYSTEM = "a system hive — boot, services, drivers and accounts"
_OWN = "part of what I run on — my own keys, my Python runtime, my Chrome profiles, OneDrive"

#: ("run", segments) matches those segments as a CONTIGUOUS run anywhere in
#: the key path; ("seg", prefix) matches ANY single segment that starts with
#: the prefix. All lowercase. WOW6432Node segments are removed before matching
#: so the 32-bit view of a key is the same key.
NEVER: tuple[tuple[str, tuple[str, ...] | str, str], ...] = (
    # persistence — the Run family and every other "load this at start" slot
    ("run", ("currentversion", "run"), _PERSIST),
    ("run", ("currentversion", "runonce"), _PERSIST),
    ("run", ("currentversion", "runonceex"), _PERSIST),
    ("run", ("currentversion", "runservices"), _PERSIST),
    ("run", ("currentversion", "runservicesonce"), _PERSIST),
    ("run", ("currentversion", "explorer", "shell folders"), _PERSIST),
    ("run", ("currentversion", "explorer", "user shell folders"), _PERSIST),
    ("run", ("currentversion", "explorer", "shellexecutehooks"), _PERSIST),
    ("run", ("currentversion", "explorer", "shellserviceobjectdelayload"), _PERSIST),
    ("run", ("currentversion", "explorer", "browser helper objects"), _PERSIST),
    ("run", ("currentversion", "explorer", "shelliconoverlayidentifiers"), _PERSIST),
    ("run", ("currentversion", "explorer", "fileexts"), _PERSIST),
    ("run", ("currentversion", "explorer", "desktop", "namespace"), _PERSIST),
    ("run", ("currentversion", "explorer", "mycomputer", "namespace"), _PERSIST),
    ("run", ("currentversion", "shellserviceobjectdelayload"), _PERSIST),
    ("run", ("currentversion", "app paths"), _PERSIST),
    ("run", ("currentversion", "uninstall"), _PERSIST),
    ("run", ("currentversion", "group policy"), _PERSIST),
    ("run", ("windows nt", "currentversion", "windows"), _PERSIST),            # AppInit_DLLs, Load, Run
    ("run", ("windows nt", "currentversion", "image file execution options"), _PERSIST),
    ("run", ("windows nt", "currentversion", "silentprocessexit"), _PERSIST),
    ("run", ("windows nt", "currentversion", "schedule"), _PERSIST),
    ("run", ("windows nt", "currentversion", "terminal server"), _PERSIST),
    ("run", ("windows nt", "currentversion", "accessibility"), _PERSIST),      # assistive-tech launch
    ("run", ("windows nt", "currentversion", "drivers32"), _PERSIST),
    ("run", ("windows nt", "currentversion", "aedebug"), _PERSIST),
    ("run", ("microsoft", "command processor"), _PERSIST),                      # cmd.exe AutoRun
    ("run", ("microsoft", "windows script host"), _PERSIST),
    ("run", ("microsoft", "active setup"), _PERSIST),
    ("run", ("microsoft", "ctf"), _PERSIST),
    ("seg", "shell extensions", _PERSIST),
    ("seg", "classes", _PERSIST),                # HKCR mirror: file-type and COM hijacks
    ("seg", "environment", _PERSIST),            # PATH hijack: the next python.exe is not mine
    ("seg", "services", _SYSTEM),                # service configuration, in any hive
    # login hijack
    ("run", ("windows nt", "currentversion", "winlogon"), _LOGON),
    ("run", ("currentversion", "authentication"), _LOGON),
    # security
    ("seg", "windows defender", _SECURITY),      # Windows Defender, ... Security Center, ... Exploit Guard
    ("seg", "windows advanced threat protection", _SECURITY),
    ("seg", "windows security", _SECURITY),
    ("seg", "security center", _SECURITY),
    ("seg", "smartscreen", _SECURITY),
    ("seg", "policies", _SECURITY),              # every Policies key: Defender, UAC, Windows Update, Chrome
    ("run", ("currentversion", "windowsupdate"), _SECURITY),
    ("run", ("microsoft", "windowsupdate"), _SECURITY),
    ("run", ("microsoft", "systemcertificates"), _SECURITY),
    ("run", ("microsoft", "cryptography"), _SECURITY),
    ("run", ("currentversion", "internet settings"), _SECURITY),               # proxy hijack
    ("run", ("currentversion", "apphost"), _SECURITY),                          # SmartScreen for apps
    ("run", ("microsoft", "powershell"), _SECURITY),                            # execution policy
    ("seg", "sharedaccess", _SECURITY),                                         # the firewall
    # her own
    ("seg", "tessa", _OWN),
    ("seg", "python", _OWN),                     # Python, PythonCore
    ("run", ("google", "chrome"), _OWN),
    ("run", ("microsoft", "onedrive"), _OWN),
)

#: Whole hives, or the first segment under HKLM, that are never written.
_NEVER_HIVES = {"HKCR": _PERSIST, "HKCC": _SYSTEM}
_NEVER_HKLM_ROOTS = {"system", "sam", "security", "bcd00000000", "hardware"}

#: Value NAMES that are loader slots wherever they appear. A value called
#: `Shell` under an allowlisted key does nothing today; refusing the name
#: costs nothing and removes the argument.
NEVER_VALUE_NAMES: frozenset[str] = frozenset({
    "scrnsave.exe", "shell", "userinit", "load", "run", "runonce", "appinit_dlls", "loadappinit_dlls",
    "debugger", "startexe", "autorun", "notify", "taskman", "startup", "logon", "monitorprocess",
    "verifierdlls", "globalflag", "uihost", "ginadll", "vmapplet", "silentprocessexit",
})

#: File extensions a VALUE may not name. A wallpaper is a .jpg; a Run entry
#: is an .exe. Even inside an allowlisted key, a value that names one of
#: these is a launch point waiting for a loader.
EXECUTABLE_EXTS: frozenset[str] = frozenset({
    "exe", "dll", "scr", "bat", "cmd", "ps1", "psm1", "psd1", "vbs", "vbe", "js", "jse", "wsf", "wsh",
    "msi", "msp", "msu", "hta", "com", "pif", "lnk", "cpl", "sys", "drv", "ocx", "jar", "py", "pyw",
    "reg", "inf", "url", "iso", "vhd", "vhdx", "appx", "msix", "appxbundle", "msixbundle",
})
_LAUNCHER_WORDS = re.compile(
    r"\b(?:rundll32|regsvr32|mshta|wscript|cscript|powershell|pwsh|cmd|bitsadmin|certutil|"
    r"msiexec|schtasks|reg|sc|net|forfiles|explorer)(?:\.exe)?\b", re.I)


# ─────────────────────────────────────────────────────────────────────────────
# parsing and judging a request — the same code before the card and after
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ParsedKey:
    hive: str                       # HKCU | HKLM | HKU | HKCR | HKCC
    handle: int
    segments: tuple[str, ...]       # original spelling, no hive
    path: str                       # segments joined with backslashes

    @property
    def canonical(self) -> str:
        return f"{self.hive}{BS}{self.path}"

    @property
    def lowered(self) -> tuple[str, ...]:
        return tuple(s.lower() for s in self.segments)


def parse_key(raw: Any) -> ParsedKey:
    """
    The ONE parser every key passes. Accepts `HKCU\\...`, `HKEY_CURRENT_USER\\...`
    and regedit's `Computer\\HKCU\\...`; refuses everything that is not a
    plain backslash-separated key with a known hive. Raises `Refused`.
    """
    s = str(raw if raw is not None else "").strip().strip('"' + "'").strip()
    if not s:
        raise Refused("no registry key was given",
                      "Say the full key, hive first — HKCU" + BS + "Control Panel" + BS + "Desktop.")
    if s.lower().startswith("computer" + BS):
        s = s[len("computer" + BS):]
    if "/" in s:
        raise Refused(f"{s[:60]!r} is spelled with forward slashes",
                      "A registry key is spelled with backslashes, hive first.")
    if len(s) > MAX_KEY_CHARS:
        raise Refused("that key path is too long", f"{MAX_KEY_CHARS} characters at most.")
    parts = s.split(BS)
    hive_raw = parts[0].strip().upper()
    if hive_raw not in _HIVES:
        raise Refused(f"{parts[0][:40]!r} is not a registry hive I know",
                      "Start with HKCU or HKEY_CURRENT_USER — that is the only hive I write.")
    hive, handle = _HIVES[hive_raw]
    segs = parts[1:]
    if not segs:
        raise Refused(f"{hive} on its own is a hive, not a key", "Name the key under it.")
    if len(segs) > MAX_SEGMENTS:
        raise Refused("that key path has too many parts", f"{MAX_SEGMENTS} at most.")
    for seg in segs:
        if not seg or seg != seg.strip() or seg in (".", ".."):
            raise Refused(f"{s[:60]!r} has an empty or malformed part", "One backslash between each part.")
        if _BAD_CHARS.search(seg) or len(seg) > 255:
            raise Refused(f"{seg[:40]!r} is not a registry key part I can use",
                          "Letters, digits, spaces, dots and dashes.")
    return ParsedKey(hive=hive, handle=handle, segments=tuple(segs), path=BS.join(segs))


def never_reason(key: ParsedKey) -> tuple[str, str] | None:
    """
    (reason, alternative) when `key` is on the never-list, else None. Matched
    with WOW6432Node removed so the 32-bit view cannot slip past. This runs
    BEFORE the allowlist so the refusal names what the key IS.
    """
    if key.hive in _NEVER_HIVES:
        return (f"{key.canonical} is {_NEVER_HIVES[key.hive]}", "I never write there, approved or not.")
    low = [s for s in key.lowered if s != "wow6432node"]
    if key.hive == "HKLM" and low and low[0] in _NEVER_HKLM_ROOTS:
        return (f"{key.canonical} is {_SYSTEM}", "I never write there, approved or not.")
    for kind, frag, why in NEVER:
        if kind == "seg":
            if any(seg.startswith(str(frag)) for seg in low):
                return (f"{key.canonical} is {why}", "I never write there, approved or not.")
        else:
            run = tuple(frag)
            n = len(run)
            for i in range(0, len(low) - n + 1):
                if tuple(low[i:i + n]) == run:
                    return (f"{key.canonical} is {why}", "I never write there, approved or not.")
    return None


def allowlisted_root(key: ParsedKey) -> str | None:
    """The spoken allowlist root `key` falls under, or None (= refused)."""
    if key.hive != "HKCU":
        return None
    low = key.lowered
    if "wow6432node" in low:
        return None
    for root in ALLOWLIST:
        if low[:len(root)] == root:
            return "HKCU" + BS + BS.join(root)
    return None


def validate_name(raw: Any) -> str:
    s = str(raw if raw is not None else "").strip()
    if not s:
        raise Refused("no value name was given — I do not write a key's (Default) value",
                      "Name the value: HideFileExt, MenuShowDelay, Wallpaper.")
    if len(s) > MAX_NAME_CHARS or _BAD_CHARS.search(s) or BS in s:
        raise Refused(f"{s[:40]!r} is not a value name I can use", "Letters, digits, dots, dashes and underscores.")
    low = s.lower()
    if low in NEVER_VALUE_NAMES or low.rsplit(".", 1)[-1] in EXECUTABLE_EXTS:
        raise Refused(f"{s} is a loader slot, not a preference",
                      "Windows runs whatever is written under that name. I never set it.")
    return s


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"""[\s;,"'|<>()]+""", s) if t]


def validate_value(raw: Any, kind: str) -> str | int:
    """The typed value for `kind` (`string` or `dword`), or `Refused`."""
    s = str(raw if raw is not None else "").strip()
    if len(s) > MAX_VALUE_CHARS:
        raise Refused("that value is too long", f"{MAX_VALUE_CHARS} characters at most.")
    if re.search(r"[\x00-\x1f]", s):
        raise Refused("that value carries control characters", "Plain text or a number.")
    if kind == "dword":
        if not _DWORD_RE.match(s):
            raise Refused(f"{s[:40]!r} is not a whole number", "A dword is 0 to 4294967295, or 0x hex.")
        n = int(s, 16) if s.lower().startswith("0x") else int(s)
        if n > 0xFFFFFFFF:
            raise Refused(f"{s} is more than a dword holds", "0 to 4294967295.")
        return n
    if "://" in s:
        raise Refused("that value is a web address", "I do not write links into the registry.")
    for tok in _tokens(s):
        ext = tok.rstrip(".").rsplit(".", 1)[-1].lower() if "." in tok else ""
        if ext in EXECUTABLE_EXTS:
            raise Refused(f"that value names a program or script ({tok[:40]})",
                          "A preference is text or a number, never something Windows could run.")
    if _LAUNCHER_WORDS.search(s):
        raise Refused("that value names a shell or a loader", "A preference is text or a number.")
    return s


@dataclass(frozen=True)
class Judged:
    key: ParsedKey
    root: str                       # the allowlist root it fell under
    name: str
    kind: str                       # string | dword (resolved)
    regtype: int                    # REG_SZ | REG_EXPAND_SZ | REG_DWORD
    data: str | int
    was: Any                        # current data, or None
    was_type: int | None

    @property
    def shown(self) -> str:
        return str(self.data) if self.kind == "dword" else f"{str(self.data)[:120]!r}"

    @property
    def was_spoken(self) -> str:
        if self.was_type is None:
            return "it is not set now"
        w = str(self.was)
        return f"it is {w[:80]!r} now" if self.was_type != winreg.REG_DWORD else f"it is {w} now"


def _read_current(key: ParsedKey, name: str) -> tuple[Any, int | None]:
    """(data, type) of the value now, read-only; (None, None) when unset. Raises Refused when the KEY is absent."""
    try:
        h = winreg.OpenKey(key.handle, key.path, 0, winreg.KEY_READ)
    except FileNotFoundError:
        raise Refused(f"{key.canonical} does not exist, and I do not create keys",
                      "Check the spelling, or set a value that already has a key.") from None
    except PermissionError:
        raise Refused(f"Windows would not let me read {key.canonical}", "I only write what I can read first.") from None
    except OSError as exc:
        raise Refused(f"{key.canonical} could not be opened: {exc}", "Check the key and say it again.") from None
    try:
        try:
            data, typ = winreg.QueryValueEx(h, name)
        except FileNotFoundError:
            return None, None
        except OSError as exc:
            raise Refused(f"{name} under {key.canonical} could not be read: {exc}",
                          "Check the value and say it again.") from None
    finally:
        winreg.CloseKey(h)
    return data, int(typ)


def judge(args: dict[str, Any]) -> Judged:
    """
    THE ONE GATE. Order: key shape -> never-list -> allowlist -> value name ->
    the current value (the key must exist) -> the value for its type. Every
    refusal is `Refused`; the card path turns it into a refusal BEFORE the
    card, the run path into APPROVED-BUT-FAILED after it.
    """
    key = parse_key(args.get("key"))
    hit = never_reason(key)
    if hit is not None:
        raise Refused(*hit)
    root = allowlisted_root(key)
    if root is None:
        raise Refused(f"{key.canonical} is not on my allowlist",
                      "I write only his own preference keys under HKCU: "
                      + ", ".join(r.split(BS, 1)[1] for r in ALLOWLIST_SPOKEN[:5]) + " and a few more. Nothing else.")
    name = validate_name(args.get("name"))
    kind = str(args.get("kind") or "auto").strip().lower()
    was, was_type = _read_current(key, name)
    if was_type is not None and was_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ, winreg.REG_DWORD):
        raise Refused(f"{name} under {key.canonical} is a {_TYPE_WORDS.get(was_type, 'special')} value",
                      "I write strings and dwords only.")
    raw_value = str(args.get("value") if args.get("value") is not None else "").strip()
    if kind == "auto":
        if was_type == winreg.REG_DWORD:
            kind = "dword"
        elif was_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            kind = "string"
        else:
            kind = "dword" if _DWORD_RE.match(raw_value) else "string"
    if kind not in ("string", "dword"):
        raise Refused(f"{kind!r} is not a value type I write", "string or dword.")
    if was_type is not None:
        want = winreg.REG_DWORD if kind == "dword" else winreg.REG_SZ
        same = (was_type == want) or (kind == "string" and was_type == winreg.REG_EXPAND_SZ)
        if not same:
            raise Refused(f"{name} is a {_TYPE_WORDS.get(was_type)} value now and you asked for a {kind}",
                          "I keep a value's type. Say it as it is, or leave the type to me.")
    data = validate_value(raw_value, kind)
    regtype = winreg.REG_DWORD if kind == "dword" else (
        winreg.REG_EXPAND_SZ if was_type == winreg.REG_EXPAND_SZ else winreg.REG_SZ)
    return Judged(key=key, root=root, name=name, kind=kind, regtype=regtype, data=data,
                  was=was, was_type=was_type)


# ─────────────────────────────────────────────────────────────────────────────
# THE ONE SEAM — the only SetValueEx in this module
# ─────────────────────────────────────────────────────────────────────────────

_WRITABLE_TYPES = (winreg.REG_SZ, winreg.REG_EXPAND_SZ, winreg.REG_DWORD)


def _set_value(hive: int, path: str, name: str, data: str | int, regtype: int) -> None:
    """
    THE ONE SEAM. Opens an EXISTING key for KEY_SET_VALUE (never CreateKey) and
    writes one value. Re-asserts HKCU and the writable types so a later edit
    above cannot widen what reaches here. A proof replaces this function with
    a recorder and nothing below it ever runs.
    """
    if hive != winreg.HKEY_CURRENT_USER:
        raise Refused("only HKCU is ever written", "That is the whole allowlist.")
    if regtype not in _WRITABLE_TYPES:
        raise Refused("only string and dword values are ever written", "That is the whole design.")
    h = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(h, name, 0, regtype, data)
    finally:
        winreg.CloseKey(h)


# ─────────────────────────────────────────────────────────────────────────────
# RED: the write
# ─────────────────────────────────────────────────────────────────────────────

#: `Executor._log`, bound at construction like power's and software's. Writes
#: RUNNING / RAN / RAN-FAILED with the exact key, name, value and what it was.
_REPORT: Callable[..., None] | None = None


def bind_reporter(report: Callable[..., None] | None) -> None:
    global _REPORT
    _REPORT = report


def _report(verb: str, tool: str, summary: str, actor: str) -> None:
    if _REPORT is None:
        return
    try:
        _REPORT(verb, tool, summary, "red", actor=actor)
    except Exception:  # noqa: BLE001
        pass


TOOL = "system.registry.set"


def set_value(key: str, name: str, value: str, kind: str = "auto", provenance: str = "schedule",
              _approved_by_surface: bool = False, _request_id: str = "") -> dict[str, Any]:
    """RED. ONE value under an allowlisted HKCU key, judged again here, only from the card."""
    from core.tools.base import ToolError

    try:
        j = judge({"key": key, "name": name, "value": value, "kind": kind})
        if not _approved_by_surface:
            raise Refused(f"{j.key.canonical}{BS}{j.name} cannot be written without your approval on the card",
                          "Approve it there and I will write it.")
    except Refused as r:
        raise ToolError(*r.args) from None

    target = f"{j.key.canonical}{BS}{j.name}"
    _report("RUNNING", TOOL, f"requestId={_request_id} set {target} = {j.shown} ({j.kind}); {j.was_spoken}", provenance)
    try:
        _set_value(j.key.handle, j.key.path, j.name, j.data, j.regtype)
    except Refused as r:
        _report("RAN-FAILED", TOOL, f"requestId={_request_id} {target}: {r.args[0]}", provenance)
        raise ToolError(*r.args) from None
    except PermissionError:
        _report("RAN-FAILED", TOOL, f"requestId={_request_id} {target}: access denied", provenance)
        raise ToolError(f"Windows refused the write to {target} (access denied)",
                        "Nothing was changed.") from None
    except OSError as exc:
        _report("RAN-FAILED", TOOL, f"requestId={_request_id} {target}: {type(exc).__name__}: {exc}"[:200], provenance)
        raise ToolError(f"the write to {target} failed: {exc}", "Nothing was changed.") from None

    now, now_type = _read_current(j.key, j.name)
    ok = (now == j.data) and (now_type == j.regtype)
    _report("RAN" if ok else "RAN-FAILED", TOOL,
            f"requestId={_request_id} {target} = {j.shown} ({j.kind}); was {j.was!r}; read back {now!r}", provenance)
    if not ok:
        raise ToolError(f"{target} reads back as {now!r}, not {j.shown}", "Windows did not keep it.")
    was_words = "it was not set" if j.was_type is None else f"it was {j.was!r}"
    return {
        "key": j.key.canonical, "name": j.name, "value": str(j.data), "kind": j.kind,
        "root": j.root, "was": j.was, "was_set": j.was_type is not None, "ok": True,
        "verdict": f"Set {j.name} to {j.shown} under {j.key.canonical}, Emperor — {was_words}.",
    }


def describe(args: dict[str, Any]) -> str:
    """
    What the card SAYS, and what it refuses before it is raised. Read-only:
    the never-list, the allowlist and the current value are decided here,
    and a ToolError from here is a refusal BEFORE the card.
    """
    from core.tools.base import ToolError

    try:
        j = judge(args)
    except Refused as r:
        raise ToolError(*r.args) from None
    return f"set {j.key.canonical}{BS}{j.name} to {j.shown} ({j.kind}) — {j.was_spoken}"


CAPABILITIES = [
    Capability(
        name=TOOL, capability="registry.write", tier="red",
        run=set_value,
        params=(Param("key", str, doc="The full key, hive first: HKCU" + BS + "Control Panel" + BS + "Desktop."),
                Param("name", str, doc="The value name under it: MenuShowDelay."),
                Param("value", str, doc="The new value — a number or plain text."),
                Param("kind", str, default="auto", choices=("auto", "string", "dword"),
                      doc="string or dword; left to me, I keep the value's current type.")),
        # ⚠⚠ EVERYTHING IS FROZEN. A registry write has no wording, only a target.
        frozen=("key", "name", "value", "kind"),
        describe=describe,
        phrasings=("set registry HKCU" + BS + "Software" + BS + "Microsoft" + BS + "Windows" + BS
                   + "CurrentVersion" + BS + "Explorer" + BS + "Advanced" + BS + "HideFileExt to 0",
                   "set the registry key HKCU" + BS + "Control Panel" + BS + "Desktop" + BS + "MenuShowDelay to 0",
                   "set registry value HideFileExt under HKCU" + BS + "Software" + BS + "Microsoft" + BS + "Windows"
                   + BS + "CurrentVersion" + BS + "Explorer" + BS + "Advanced to 0"),
        success="{verdict}",
        failure="I did not write it, sir. {reason} {alternative}",
        audit="REGISTRY SET {key}" + BS + "{name} = {value} ({kind})",
        hold="set {key}" + BS + "{name} to {value}",
        note="RED, card-only. winreg.SetValueEx of ONE string/dword value under an ALLOWLISTED HKCU "
             "preference subtree (Control Panel Desktop/Mouse/Keyboard/Colors/Sound, Console, Explorer "
             "Advanced, Themes Personalize, Search, DWM, ContentDeliveryManager) — DEFAULT-DENY for every "
             "other key. NEVER-LIST refused before the card (describe) and after approval (run): Run*, "
             "Winlogon, IFEO, AppInit, Shell Folders, App Paths, Classes, Environment, Command Processor, "
             "Windows Defender, every Policies key, Windows Update, certificates, the firewall, the "
             "SYSTEM/SAM/SECURITY/BCD/HARDWARE hives, HKCR, HKCC, and Tessa's own keys (Tessa, Python, "
             "Google Chrome, OneDrive). No key creation, no binary/multi-string, no executable-shaped "
             "value, no loader-slot value name. key+name+value+kind frozen on the card.",
    ),
]
