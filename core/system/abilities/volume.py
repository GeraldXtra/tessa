"""
core/system/abilities/volume.py — master volume: exact get, set, mute.

GREEN under `system.control` ("volume, media keys, brightness, lock, sleep.
Reversible without loss"). The existing `sys.volume` tool taps the media
keys, which is fine for "louder" and useless for "set it to forty" or "how
loud is it" — it cannot read the level at all. This reads and writes the
endpoint scalar and READS IT BACK, so what she says is what the mixer shows.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param
from core.system.winapi import audio


def _unavailable(exc: Exception) -> Exception:
    from core.tools.base import ToolError
    return ToolError(f"I cannot reach the playback device ({exc})",
                     "Check a speaker or headset is selected in Windows.")


def get() -> dict[str, Any]:
    try:
        level, muted = audio.get_volume()
    except audio.AudioUnavailable as exc:
        raise _unavailable(exc) from None
    return {"level": level, "muted": muted,
            "state": "muted" if muted else "unmuted",
            "muted_note": " It is muted." if muted else ""}


def set_level(level: int) -> dict[str, Any]:
    try:
        after, before = audio.set_volume(level)
    except audio.AudioUnavailable as exc:
        raise _unavailable(exc) from None
    if abs(after - level) > 1:
        from core.tools.base import ToolError
        raise ToolError(f"Windows did not take it — the mixer reads {after}",
                        "Try once more, or use the keys.")
    return {"level": after, "was": before, "asked": level}


def mute(muted: bool = True) -> dict[str, Any]:
    try:
        now = audio.set_mute(muted)
    except audio.AudioUnavailable as exc:
        raise _unavailable(exc) from None
    if now != bool(muted):
        from core.tools.base import ToolError
        raise ToolError("Windows did not change the mute state", "Try the mute key.")
    return {"muted": now, "state": "Muted" if now else "Unmuted"}


CAPABILITIES = [
    Capability(
        name="system.volume.get", capability="system.control", tier="green",
        run=get,
        phrasings=("what's the volume", "how loud is it", "volume level"),
        success="Volume is {level} percent, Emperor.{muted_note}",
        audit="volume get",
        note="IAudioEndpointVolume over stdlib ctypes. Exact, ~10 ms, no comtypes/pycaw.",
    ),
    Capability(
        name="system.volume.set", capability="system.control", tier="green",
        run=set_level,
        params=(Param("level", int, lo=0, hi=100, doc="Zero to a hundred."),),
        phrasings=("set volume to 40", "volume forty percent", "put the volume at 20"),
        success="Volume {level} percent, Emperor. It was {was}.",
        audit="volume set {level}",
        note="Sets the scalar and reads it back; refuses to claim a level the mixer does not show.",
    ),
    Capability(
        name="system.volume.mute", capability="system.control", tier="green",
        run=mute,
        params=(Param("muted", bool, default=True, doc="On to mute, off to unmute."),),
        phrasings=("mute", "unmute", "sound off", "sound back on"),
        success="{state}, Emperor.",
        audit="volume mute {muted}",
    ),
]
