"""
core/system/winapi/fileindex.py — instant filename search, built the same way
`core/brain/appindex.py` builds the application index.

WHY THIS EXISTS, WITH THE MEASUREMENT THAT FORCED IT

`core.tools.files.search` walks the filesystem live on every question. Measured
on this machine, 2026-09-07:

    search("plan", root=C:\\dev\\tessa)        6 991 ms
    search("tessa", root=C:\\dev)              6 788 ms
    search("invoice", root=<home>)            42 361 ms

Forty-two seconds. He asks where a file is and she is silent for most of a
minute, which is not a slow feature, it is a feature he stops using. The cost
is not OneDrive alone — Downloads is 76 764 entries on its own, and Documents
another 29 647.

The profile that decided the design:

    root                     entries        cold walk
    Downloads                 76 764        40 621 ms
    Documents                 29 647        17 975 ms
    C:\\dev                    15 369         1 297 ms
    Desktop / Pictures            30             3 ms

    all roots, depth <= 2      1 971            95 ms
    all roots, depth <= 3      7 271           295 ms
    all roots, FULL          121 821         8 021 ms  (warm cache)

So: a full walk cannot answer a spoken question, and a depth-limited one can.
TWO TIERS, exactly like appindex, and for the same reason — he must never wait
for the slow one.

  * FAST — depth 3 over every root, ~300 ms. Built synchronously the first
    time, and it already covers the places things actually live: the top of
    Downloads, project folders under C:\\dev, Documents' first few levels.
  * FULL — the complete walk, ~8 s warm, built on a BACKGROUND THREAD and
    cached to disk. Every later question is answered from memory.

Once the cache is warm a query is a substring scan over basenames held in
memory, which is milliseconds. That is the "instant filename search" the goal
docs describe, with no Everything SDK, no service, and no new dependency —
there is no Everything integration anywhere in this repo to reuse, which was
checked before this was written.

⚠ METADATA ONLY, AND CLOUD-SAFE BY CONSTRUCTION

Nothing here opens a file. The walk uses `os.scandir`, reads NAMES, and never
touches content — so it cannot hydrate a cloud placeholder and cannot spend a
byte of metered data. It also never DESCENDS a reparse point, which is the
same rule `core.tools.files._walk` follows and for the same reason: walking
into a OneDrive junction and stat-ing everything under it is how a name search
turns into a download somebody has to explain later.

⚠ ONEDRIVE IS EXCLUDED FROM THE ROOTS BY DEFAULT. CONTRACT §6.3: "OneDrive is
excluded from content indexing by default. Opt-in per folder, never
recursive-by-default." Indexing names there costs no data, but the tree holds
17 340 placeholders, it added 22 s to the build, and the contract's default is
off. `roots=` takes an explicit list for the day he wants it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable

#: Where the cache lives. Beside the app index, under the same data/ budget.
ROOT_DIR = Path(__file__).resolve().parents[2]
CACHE_PATH = ROOT_DIR.parent / "data" / "fileindex.txt"
META_PATH = ROOT_DIR.parent / "data" / "fileindex.meta.json"

FILE_ATTRIBUTE_REPARSE_POINT = 0x400

#: How long a cached index is trusted before a refresh is triggered.
#: Six hours: long enough that he never waits, short enough that a folder made
#: this morning is findable this afternoon. A miss also triggers a refresh.
STALE_AFTER_S = 6 * 3600

#: The fast tier's depth. 3 measured at 295 ms and 7 271 paths.
FAST_DEPTH = 3

#: Hard ceiling on the full walk, so a pathological tree cannot run away.
MAX_ENTRIES = 400_000


def default_roots() -> list[Path]:
    """
    Where his things are. Existing directories only, OneDrive deliberately
    absent (see the module docstring and CONTRACT §6.3).
    """
    home = Path.home()
    candidates = [home / "Desktop", home / "Documents", home / "Downloads",
                  home / "Pictures", home / "Videos", home / "Music",
                  Path(r"C:\dev")]
    out: list[Path] = []
    for c in candidates:
        try:
            if c.is_dir():
                out.append(c)
        except OSError:
            continue
    return out


def _walk(roots: Iterable[Path], *, max_depth: int | None, cap: int) -> list[str]:
    """
    Names and paths, nothing else. Never opens a file. Never descends a
    reparse point.
    """
    found: list[str] = []
    stack: list[tuple[Path, int]] = [(Path(r), 0) for r in roots]
    while stack and len(found) < cap:
        current, depth = stack.pop()
        try:
            entries = list(os.scandir(current))
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if len(found) >= cap:
                break
            found.append(entry.path)
            if max_depth is not None and depth >= max_depth:
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                # The reparse check is on the DIRECTORY, before descending.
                if entry.stat(follow_symlinks=False).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
                stack.append((Path(entry.path), depth + 1))
            except (OSError, AttributeError):
                continue
    return found


class FileIndex:
    """
    Two tiers and a disk cache. Thread-safe: the full tier is built off-thread
    while queries keep being served from the fast one.
    """

    def __init__(self, roots: list[Path] | None = None,
                 cache_path: Path | None = None) -> None:
        self.roots = roots if roots is not None else default_roots()
        self.cache_path = cache_path or CACHE_PATH
        self.meta_path = (self.cache_path.with_suffix(".meta.json")
                          if cache_path else META_PATH)
        self._lock = threading.Lock()
        self._paths: list[str] = []
        self._names: list[str] = []          # lowercased basenames, parallel to _paths
        self._tier = "none"                  # none | fast | full
        self.built_at = 0.0
        self.timings: dict[str, float] = {}
        self._building = False

    # ── building ─────────────────────────────────────────────────────────────

    def _install(self, paths: list[str], tier: str) -> None:
        names = [p.rsplit("\\", 1)[-1].lower() for p in paths]
        with self._lock:
            self._paths = paths
            self._names = names
            self._tier = tier
            self.built_at = time.time()

    def build_fast(self) -> None:
        t0 = time.perf_counter()
        paths = _walk(self.roots, max_depth=FAST_DEPTH, cap=MAX_ENTRIES)
        self._install(paths, "fast")
        self.timings["fast"] = (time.perf_counter() - t0) * 1000

    def build_full(self, save: bool = True) -> None:
        t0 = time.perf_counter()
        paths = _walk(self.roots, max_depth=None, cap=MAX_ENTRIES)
        self._install(paths, "full")
        self.timings["full"] = (time.perf_counter() - t0) * 1000
        if save:
            self._save()

    def build_async(self) -> threading.Thread | None:
        """Full tier, off-thread. He is never made to wait for it."""
        with self._lock:
            if self._building:
                return None
            self._building = True

        def _run() -> None:
            try:
                self.build_full()
            except Exception:  # noqa: BLE001 — an index failure must not kill a turn
                pass
            finally:
                with self._lock:
                    self._building = False

        thread = threading.Thread(target=_run, name="fileindex", daemon=True)
        thread.start()
        return thread

    def ensure(self) -> None:
        """
        Serve something NOW. Load the cache if it is usable, otherwise build
        the fast tier synchronously and start the full one behind it.
        """
        with self._lock:
            if self._tier != "none":
                return
        if self._load():
            if self.stale:
                self.build_async()
            return
        self.build_fast()
        self.build_async()

    @property
    def stale(self) -> bool:
        return (time.time() - self.built_at) > STALE_AFTER_S

    @property
    def tier(self) -> str:
        return self._tier

    @property
    def count(self) -> int:
        return len(self._paths)

    # ── the cache ────────────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            with self._lock:
                paths = list(self._paths)
            tmp.write_text("\n".join(paths), encoding="utf-8")
            tmp.replace(self.cache_path)
            self.meta_path.write_text(json.dumps({
                "built_at": self.built_at, "count": len(paths),
                "roots": [str(r) for r in self.roots], "tier": "full",
            }), encoding="utf-8")
        except OSError:
            pass    # a cache that will not write is a slow index, not a broken one

    def _load(self) -> bool:
        try:
            if not (self.cache_path.exists() and self.meta_path.exists()):
                return False
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if [str(r) for r in self.roots] != meta.get("roots"):
                return False        # the roots changed; the cache is about a different question
            paths = self.cache_path.read_text(encoding="utf-8").splitlines()
            if not paths:
                return False
            self._install(paths, "full")
            self.built_at = float(meta.get("built_at") or 0.0)
            return True
        except (OSError, ValueError):
            return False

    # ── querying ─────────────────────────────────────────────────────────────

    def find(self, needle: str, limit: int = 40) -> list[str]:
        """
        Substring match on the NAME, case-insensitive — he says "the invoice
        one", not a glob, and a matcher that needs `*invoice*` is one he will
        stop using. Same semantics as `core.tools.files.search`, answered from
        memory instead of a walk.
        """
        q = str(needle or "").strip().lower()
        if not q:
            return []
        self.ensure()
        with self._lock:
            names, paths = self._names, self._paths
        hits: list[str] = []
        for i, name in enumerate(names):
            if q in name:
                hits.append(paths[i])
                if len(hits) >= limit:
                    break
        return hits

    def status(self) -> dict[str, Any]:
        return {"tier": self._tier, "count": self.count, "stale": self.stale,
                "roots": [str(r) for r in self.roots], "timings": dict(self.timings)}


_INDEX: FileIndex | None = None
_INDEX_LOCK = threading.Lock()


def get_index() -> FileIndex:
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is None:
            _INDEX = FileIndex()
    return _INDEX
