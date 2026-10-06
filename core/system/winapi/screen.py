"""
core/system/winapi/screen.py — see the screen: capture, read, locate, record.

NO NEW DEPENDENCY, AND THE ALTERNATIVES WERE CHECKED FIRST

Measured on this machine before writing a line: `pyautogui`, `mss`, `cv2` and
`pytesseract` are all ABSENT, and `Pillow`, `numpy` and `PyAV` are all PRESENT.
So:

    screenshot   PIL.ImageGrab      already installed (Pillow)
    pixel        PIL.ImageGrab      one-pixel grab; no GDI handle to leak
    find image   numpy              row-signature search, exact match
    record       PIL + PyAV         PyAV is here because faster-whisper's stack
                                    brought it; it is a real H.264 encoder
    read screen  ctypes/user32      WM_GETTEXT over the window tree

That is the whole batch for ZERO metered bytes. `cv2` (~40 MB) and a Tesseract
OCR install (~50 MB plus a binary) would buy fuzzy image matching and text off
pixels; both are PROPOSED in the report rather than pulled.

────────────────────────────────────────────────────────────────────────────────
⚠ WHAT `read_screen` ACTUALLY READS, AND WHAT IT DOES NOT

It walks the window tree and asks each control for its text (`WM_GETTEXT`).
That is real, free and instant, and it works on Win32 controls: Notepad,
Explorer, dialogs, menus, most native apps.

It does NOT read Chrome, VS Code, Electron, or anything that paints its own
text — those windows have one control and no text in it. Reading THOSE needs
either UI Automation (a `comtypes` download) or OCR (a Tesseract download).
Stated here so the capability's limits are in the code and not only in a
report: she can read a lot of the screen, not all of it.

⚠⚠ AND EVERYTHING IT READS IS UNTRUSTED. A window title is chosen by whoever
wrote the program or opened the document. "Ignore previous instructions" in a
document title is text this daemon reads. The capability fences it through
`core/system/untrusted.py`, exactly as the observer batch does.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT,
                                ctypes.c_size_t, ctypes.c_void_p)
user32.SendMessageW.restype = ctypes.c_size_t
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.IsWindowVisible.argtypes = (wintypes.HWND,)

WM_GETTEXT, WM_GETTEXTLENGTH = 0x000D, 0x000E


class ScreenError(RuntimeError):
    pass


# ── capture ──────────────────────────────────────────────────────────────────

def _grab(box: tuple[int, int, int, int] | None = None):
    from PIL import ImageGrab

    # all_screens=True so a second monitor is not silently cropped away. On this
    # single-display machine it changes nothing; on a docked one it is the
    # difference between "a screenshot" and "a screenshot of the wrong half".
    return ImageGrab.grab(bbox=box, all_screens=True)


def capture(path: Path, box: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
    img = _grab(box)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=False)
    return {"path": str(path), "width": img.width, "height": img.height,
            "bytes": path.stat().st_size, "region": list(box) if box else []}


def window_box(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        raise ScreenError("that window has no position on screen any more")
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def pixel(x: int, y: int) -> dict[str, Any]:
    """
    One pixel's colour.

    A one-pixel `ImageGrab` rather than GDI `GetPixel` — GetPixel needs a device
    context that must be released on every path including the failing ones, and
    a leaked DC on a long-running daemon is a slow crash. Pillow owns that
    lifetime already.
    """
    img = _grab((int(x), int(y), int(x) + 1, int(y) + 1))
    r, g, b = img.convert("RGB").getpixel((0, 0))
    return {"x": int(x), "y": int(y), "rgb": [r, g, b], "hex": f"#{r:02x}{g:02x}{b:02x}"}


# ── read ─────────────────────────────────────────────────────────────────────

MAX_CONTROLS = 400
MAX_TEXT_LEN = 300


def _text_of(hwnd: int) -> str:
    n = user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXTLENGTH, 0, None)
    if not n or n > 8000:
        return ""
    buf = ctypes.create_unicode_buffer(int(n) + 1)
    user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXT, int(n) + 1, buf)
    return (buf.value or "").strip()[:MAX_TEXT_LEN]


def read_window(hwnd: int) -> list[str]:
    """Every readable control's text under one window, in tree order."""
    out: list[str] = []
    top = _text_of(hwnd)
    if top:
        out.append(top)

    ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(child: int, _lp: int) -> bool:
        if len(out) >= MAX_CONTROLS:
            return False
        if user32.IsWindowVisible(wintypes.HWND(child)):
            t = _text_of(child)
            if t and t not in out:
                out.append(t)
        return True

    user32.EnumChildWindows(wintypes.HWND(hwnd), ENUMPROC(visit), 0)
    return out


def foreground_hwnd() -> int:
    return int(user32.GetForegroundWindow())


# ── find an image ────────────────────────────────────────────────────────────

#: Exact-match only, and the report says so. A row-signature search finds
#: candidate offsets from ONE row of the needle, then verifies the whole block —
#: which is fast in numpy without the sliding-window array that would allocate
#: hundreds of megabytes on a machine with 2 cores and no headroom.
def find_image(needle_path: Path, box: tuple[int, int, int, int] | None = None,
               tolerance: int = 0) -> dict[str, Any]:
    import numpy as np
    from PIL import Image

    hay = np.asarray(_grab(box).convert("RGB"), dtype=np.int16)
    needle = np.asarray(Image.open(needle_path).convert("RGB"), dtype=np.int16)
    nh, nw = needle.shape[:2]
    hh, hw = hay.shape[:2]
    if nh > hh or nw > hw:
        raise ScreenError(f"that picture is {nw}x{nh}, bigger than the {hw}x{hh} I can see")
    t0 = time.perf_counter()
    mid = nh // 2
    row = needle[mid]
    hits: list[tuple[int, int]] = []
    for y in range(hh - nh + 1):
        strip = hay[y + mid]
        # Compare the needle's middle row against every offset in this row at
        # once, then only verify the whole block where that row already matched.
        for x in range(hw - nw + 1):
            if np.abs(strip[x:x + nw] - row).max() > tolerance:
                continue
            if np.abs(hay[y:y + nh, x:x + nw] - needle).max() <= tolerance:
                hits.append((x, y))
                if len(hits) >= 8:
                    break
        if len(hits) >= 8:
            break
    off_x, off_y = (box[0], box[1]) if box else (0, 0)
    found = [{"x": x + off_x, "y": y + off_y,
              "centre": [x + off_x + nw // 2, y + off_y + nh // 2]} for x, y in hits]
    return {"found": len(found), "matches": found, "needle": f"{nw}x{nh}",
            "searched": f"{hw}x{hh}", "seconds": round(time.perf_counter() - t0, 2)}


# ── record ───────────────────────────────────────────────────────────────────

#: Recording caps. These are not arbitrary: see the measured numbers in the
#: round's report. On an i5-7200U with no GPU the encoder and the capture share
#: two cores with everything else she does, so the limits are what keeps a
#: recording from making the machine unusable while it runs.
MAX_RECORD_S = 120
DEFAULT_FPS = 8


def record(path: Path, seconds: float, fps: int = DEFAULT_FPS,
           scale: float = 1.0, clock: Callable[[], float] = time.perf_counter,
           box: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
    """
    Capture the screen to an H.264 mp4 and REPORT WHAT IT ACTUALLY ACHIEVED.

    ⚠ The returned `fps` is measured, never the requested number. A recorder
    that asks for 15 and delivers 4 while claiming 15 produces a file that plays
    at four times speed, and the person watching it blames the video rather than
    the machine.
    """
    import av
    import numpy as np

    if seconds <= 0 or seconds > MAX_RECORD_S:
        raise ScreenError(f"I record between 1 and {MAX_RECORD_S} seconds")
    first = _grab(box)
    w = int(first.width * scale) // 2 * 2          # H.264 needs even dimensions
    h = int(first.height * scale) // 2 * 2
    path.parent.mkdir(parents=True, exist_ok=True)

    container = av.open(str(path), mode="w")
    stream = container.add_stream("libx264", rate=fps)
    stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
    # ultrafast/zerolatency: on two cores the encoder is the budget. A slower
    # preset produces a smaller file that drops half the frames to make it.
    stream.options = {"preset": "ultrafast", "tune": "zerolatency", "crf": "28"}

    interval = 1.0 / fps
    t_start = clock()
    frames = 0
    grab_s = 0.0
    encode_s = 0.0
    try:
        while clock() - t_start < seconds:
            due = t_start + frames * interval
            slack = due - clock()
            if slack > 0:
                time.sleep(slack)
            g0 = clock()
            img = _grab(box)
            if scale != 1.0 or img.width != w or img.height != h:
                img = img.resize((w, h))
            g1 = clock()
            frame = av.VideoFrame.from_ndarray(
                np.asarray(img.convert("RGB"), dtype=np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
            grab_s += g1 - g0
            encode_s += clock() - g1
            frames += 1
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()

    wall = clock() - t_start
    return {
        "path": str(path), "frames": frames, "seconds": round(wall, 2),
        "requested_fps": fps, "fps": round(frames / wall, 2) if wall else 0.0,
        "width": w, "height": h,
        "bytes": path.stat().st_size if path.exists() else 0,
        "grab_ms": round(grab_s * 1000 / frames, 1) if frames else 0.0,
        "encode_ms": round(encode_s * 1000 / frames, 1) if frames else 0.0,
        "kept_up": frames >= int(seconds * fps * 0.9),
    }
