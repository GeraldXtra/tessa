"""
core/system/winapi/inventory.py — what is INSTALLED, and what STARTS at login.

Both read the registry through stdlib `winreg`. Measured on this machine:
installed software 25 ms for 99 programs, startup items 3 ms for 21 entries.

`Win32_Product` IS DELIBERATELY NOT USED, and this is the important decision
in this file. It is the obvious WMI class for "installed software" and it is a
trap: querying it makes the Windows Installer VALIDATE and reconfigure every
MSI-installed package on the machine, which takes minutes, writes MSI events
into the Application log, and has been known to trigger repair installs.
Microsoft's own guidance is not to use it. The Uninstall registry keys are
what Add/Remove Programs itself reads, they are 200 times faster, and reading
them cannot change anything.

BOTH 64- AND 32-BIT VIEWS ARE READ. A 32-bit application on 64-bit Windows
registers under `WOW6432Node`, and a scan that misses that view reports half
the machine. `KEY_WOW64_64KEY` / `KEY_WOW64_32KEY` ask for each view
explicitly rather than depending on what bitness Python happens to be.

EVERYTHING RETURNED IS UNTRUSTED. A `DisplayName` is a string the installer
chose. See core/system/untrusted.py.
"""

from __future__ import annotations

import os
import winreg
from pathlib import Path
from typing import Any

_UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"

#: (hive, path, wow64 flag, label). Both views of HKLM, plus per-user.
_UNINSTALL_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL, winreg.KEY_WOW64_64KEY, "machine"),
    (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL, winreg.KEY_WOW64_32KEY, "machine (32-bit)"),
    (winreg.HKEY_CURRENT_USER, _UNINSTALL, 0, "user"),
]

_RUN_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
     winreg.KEY_WOW64_64KEY, "all users (registry)"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
     winreg.KEY_WOW64_32KEY, "all users (registry, 32-bit)"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
     0, "you (registry)"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
     0, "you (registry, once)"),
]


def _value(key: Any, name: str) -> Any:
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def installed() -> list[dict[str, Any]]:
    """
    Every program Add/Remove Programs would show, deduplicated by display name.

    Entries flagged `SystemComponent`, and those with no `DisplayName`, are
    skipped: they are patches, redistributables and driver packages, which is
    noise when he asks what he has installed.
    """
    found: dict[str, dict[str, Any]] = {}
    for hive, path, flag, scope in _UNINSTALL_ROOTS:
        try:
            root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(root)[0]
        except OSError:
            continue
        for i in range(count):
            try:
                sub = winreg.EnumKey(root, i)
                with winreg.OpenKey(root, sub) as key:
                    name = _value(key, "DisplayName")
                    if not name or _value(key, "SystemComponent") == 1:
                        continue
                    name = str(name).strip()
                    if name in found:
                        continue
                    found[name] = {
                        "name": name,
                        "version": str(_value(key, "DisplayVersion") or "") or None,
                        "publisher": str(_value(key, "Publisher") or "") or None,
                        "installed_on": str(_value(key, "InstallDate") or "") or None,
                        "scope": scope,
                    }
            except OSError:
                continue
    return sorted(found.values(), key=lambda e: e["name"].lower())


def startup_items() -> list[dict[str, Any]]:
    """
    What runs at login: the four Run keys plus both Startup folders.

    THIS IS THE STARTUP AUDIT. It is read-only and it is the whole point of
    having it — the owner should be able to ask what launches itself on his
    machine without opening five places in Windows to find out.
    """
    items: list[dict[str, Any]] = []
    for hive, path, flag, where in _RUN_ROOTS:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        try:
            values = winreg.QueryInfoKey(key)[1]
        except OSError:
            continue
        for i in range(values):
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            items.append({"name": str(name), "command": str(value), "where": where})

    for folder, where in ((Path(os.environ.get("APPDATA", "")) /
                           "Microsoft/Windows/Start Menu/Programs/Startup", "you (Startup folder)"),
                          (Path(os.environ.get("ProgramData", "")) /
                           "Microsoft/Windows/Start Menu/Programs/Startup", "all users (Startup folder)")):
        try:
            if not folder.is_dir():
                continue
            for entry in sorted(folder.iterdir()):
                if entry.suffix.lower() in (".lnk", ".exe", ".cmd", ".bat", ".vbs"):
                    items.append({"name": entry.stem, "command": str(entry), "where": where})
        except OSError:
            continue
    return items
