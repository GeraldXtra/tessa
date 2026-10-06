"""
core/system/winapi/display.py — the monitors, their real modes, and the scale.

Pure stdlib `ctypes`. Measured at 2 ms on this machine.

THE PROCESS DPI AWARENESS IS DELIBERATELY NOT TOUCHED. `SetProcessDpiAwareness`
is a process-wide, effectively one-way switch, and this module is imported by a
long-running daemon that also runs the microphone and the tool surface.
Flipping a global just to read a number is not a trade this module gets to
make on the daemon's behalf.

So the resolution comes from `EnumDisplaySettingsW`, which reports the
adapter's ACTUAL current mode in physical pixels and is unaffected by whether
this process is DPI-aware, and the scale comes from `GetDpiForMonitor`, which
is per-monitor and needs no process-wide setting either. The two together are
the honest answer on a scaled display; `GetSystemMetrics` alone would have
reported a virtualised size.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any


class DisplayUnavailable(RuntimeError):
    pass


user32 = ctypes.windll.user32

_MONITORINFOF_PRIMARY = 0x00000001
_ENUM_CURRENT_SETTINGS = -1
_MDT_EFFECTIVE_DPI = 0


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


class _DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
                ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(_RECT), wintypes.LPARAM)


def _mode_of(device: str) -> tuple[int, int, int]:
    """(width, height, refresh_hz) from the adapter's current mode."""
    dm = _DEVMODEW()
    dm.dmSize = ctypes.sizeof(_DEVMODEW)
    if not user32.EnumDisplaySettingsW(device, _ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return 0, 0, 0
    return int(dm.dmPelsWidth), int(dm.dmPelsHeight), int(dm.dmDisplayFrequency)


def _friendly_of(device: str) -> str:
    """The monitor's own name, when the driver exposes one."""
    dd = _DISPLAY_DEVICEW()
    dd.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
    if user32.EnumDisplayDevicesW(device, 0, ctypes.byref(dd), 0):
        return (dd.DeviceString or "").strip()
    return ""


def _dpi_of(hmonitor: Any) -> int:
    dx = wintypes.UINT()
    dy = wintypes.UINT()
    try:
        if ctypes.windll.shcore.GetDpiForMonitor(
                hmonitor, _MDT_EFFECTIVE_DPI, ctypes.byref(dx), ctypes.byref(dy)) == 0:
            return int(dx.value) or 96
    except (OSError, AttributeError):
        pass
    return 96


def monitors() -> list[dict[str, Any]]:
    """Every attached monitor, primary first."""
    found: list[dict[str, Any]] = []

    def _cb(hmon: Any, hdc: Any, lprc: Any, lparam: Any) -> int:
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return 1
        device = info.szDevice or ""
        w, h, hz = _mode_of(device)
        dpi = _dpi_of(hmon)
        found.append({
            "device": device,
            "name": _friendly_of(device) or device.replace("\\\\.\\", ""),
            "primary": bool(info.dwFlags & _MONITORINFOF_PRIMARY),
            "width": w or (info.rcMonitor.right - info.rcMonitor.left),
            "height": h or (info.rcMonitor.bottom - info.rcMonitor.top),
            "refresh_hz": hz,
            "dpi": dpi,
            "scale_pct": int(round(dpi / 96.0 * 100)),
            "x": int(info.rcMonitor.left), "y": int(info.rcMonitor.top),
        })
        return 1

    if not user32.EnumDisplayMonitors(0, 0, _MONITORENUMPROC(_cb), 0):
        raise DisplayUnavailable("Windows would not enumerate the monitors")
    if not found:
        raise DisplayUnavailable("no attached monitor was reported")
    found.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    return found
