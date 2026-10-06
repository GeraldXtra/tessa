"""
core/system/winapi/audio.py — the default playback endpoint's master volume.

IAudioEndpointVolume through stdlib `ctypes`, no `comtypes`, no `pycaw`.
The existing `sys.volume` tool presses the media keys, which is relative and
unreadable; this reads and sets the exact scalar and reads it back, which is
what "set volume to forty" and "what's the volume" need.

The three interfaces are called by vtable slot. Slot numbers are the
published interface layouts (mmdeviceapi.h, endpointvolume.h); they are
constants of the ABI, not of any Windows version.

COM IS INITIALISED PER CALL. The executor runs handlers under
`asyncio.to_thread`, so there is no single thread to initialise once. Every
entry point opens the endpoint, does its work, releases, and uninitialises
only if it was the one that initialised — `RPC_E_CHANGED_MODE` means the
thread already had COM in another apartment model, which is fine for this
API and must NOT be balanced with a CoUninitialize.
"""

from __future__ import annotations

import ctypes
from ctypes import (HRESULT, POINTER, WINFUNCTYPE, byref, c_float, c_int,
                    c_void_p, cast, wintypes)
from typing import Any

# ── constants ────────────────────────────────────────────────────────────────

_CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

_CLSCTX_ALL = 23
_eRender = 0
_eMultimedia = 1

_S_OK = 0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = -2147417850          # 0x80010106

# vtable slots (after the three IUnknown slots)
_ENUM_GetDefaultAudioEndpoint = 4
_DEV_Activate = 3
_VOL_SetMasterVolumeLevelScalar = 7
_VOL_GetMasterVolumeLevelScalar = 9
_VOL_SetMute = 14
_VOL_GetMute = 15
_IUnknown_Release = 2


class AudioUnavailable(RuntimeError):
    """No default playback endpoint, or Windows refused the interface."""


def _guid(s: str) -> Any:
    g = ctypes.create_string_buffer(16)
    ctypes.oledll.ole32.CLSIDFromString(s, g)
    return g


def _slot(ptr: c_void_p, idx: int, *argtypes: Any):
    vtbl = cast(cast(ptr, POINTER(c_void_p))[0], POINTER(c_void_p))
    fn = WINFUNCTYPE(HRESULT, c_void_p, *argtypes)(vtbl[idx])
    return lambda *a: fn(ptr, *a)


class _Endpoint:
    """Context manager: the default render endpoint's IAudioEndpointVolume."""

    def __init__(self) -> None:
        self._owns_com = False
        self._ptrs: list[c_void_p] = []
        self.vol: c_void_p | None = None

    def __enter__(self) -> "_Endpoint":
        hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
        if hr in (_S_OK, _S_FALSE):
            self._owns_com = True
        elif hr != _RPC_E_CHANGED_MODE:
            raise AudioUnavailable(f"CoInitializeEx failed with 0x{hr & 0xFFFFFFFF:08X}")
        try:
            enum = c_void_p()
            ctypes.oledll.ole32.CoCreateInstance(
                _guid(_CLSID_MMDeviceEnumerator), None, _CLSCTX_ALL,
                _guid(_IID_IMMDeviceEnumerator), byref(enum))
            self._ptrs.append(enum)
            dev = c_void_p()
            _slot(enum, _ENUM_GetDefaultAudioEndpoint, c_int, c_int, POINTER(c_void_p))(
                _eRender, _eMultimedia, byref(dev))
            self._ptrs.append(dev)
            vol = c_void_p()
            iid = _guid(_IID_IAudioEndpointVolume)
            _slot(dev, _DEV_Activate, c_void_p, wintypes.DWORD, c_void_p, POINTER(c_void_p))(
                ctypes.addressof(iid), _CLSCTX_ALL, None, byref(vol))
            self._ptrs.append(vol)
            self.vol = vol
        except OSError as exc:
            self.__exit__(None, None, None)
            raise AudioUnavailable(f"no default playback device ({exc})") from None
        return self

    def __exit__(self, *_: Any) -> None:
        for p in reversed(self._ptrs):
            try:
                _slot(p, _IUnknown_Release)()
            except OSError:
                pass
        self._ptrs.clear()
        if self._owns_com:
            ctypes.windll.ole32.CoUninitialize()
            self._owns_com = False

    # ── the four operations ──────────────────────────────────────────────────

    def get_level(self) -> float:
        level = c_float()
        _slot(self.vol, _VOL_GetMasterVolumeLevelScalar, POINTER(c_float))(byref(level))
        return float(level.value)

    def set_level(self, scalar: float) -> None:
        scalar = max(0.0, min(1.0, float(scalar)))
        _slot(self.vol, _VOL_SetMasterVolumeLevelScalar, c_float, c_void_p)(c_float(scalar), None)

    def get_mute(self) -> bool:
        mute = wintypes.BOOL()
        _slot(self.vol, _VOL_GetMute, POINTER(wintypes.BOOL))(byref(mute))
        return bool(mute.value)

    def set_mute(self, muted: bool) -> None:
        _slot(self.vol, _VOL_SetMute, wintypes.BOOL, c_void_p)(1 if muted else 0, None)


# ── public API: percentages in, percentages out ──────────────────────────────

def _pct(scalar: float) -> int:
    return int(round(scalar * 100.0))


def get_volume() -> tuple[int, bool]:
    """(percent 0..100, muted)."""
    with _Endpoint() as ep:
        return _pct(ep.get_level()), ep.get_mute()


def set_volume(percent: int) -> tuple[int, int]:
    """Set the master level. Returns (readback percent, previous percent)."""
    pct = max(0, min(100, int(percent)))
    with _Endpoint() as ep:
        before = _pct(ep.get_level())
        ep.set_level(pct / 100.0)
        return _pct(ep.get_level()), before


def set_mute(muted: bool) -> bool:
    """Mute or unmute. Returns the readback."""
    with _Endpoint() as ep:
        ep.set_mute(bool(muted))
        return ep.get_mute()
