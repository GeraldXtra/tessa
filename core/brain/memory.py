"""
core/brain/memory.py — what Zoey has actually done, so she can say so truthfully.

THIS EXISTS FOR ONE LINE:

    "Done, Emperor. You opened this one yourself yesterday, by the way.
     I noticed. Ask me next time."

zoey.md says she is possessive of her work and notices when he did something
himself that she could have done. It also says MEMORY IS REAL ONLY — she never
invents a recollection to seem attentive, because a fabricated small observation
teaches him not to trust the large ones.

So that line needs EVIDENCE, and this module is the evidence:

  * Her own action log — every open she performed, appended here.
  * Windows' Recent Items (`%APPDATA%\\Microsoft\\Windows\\Recent`) — shortcuts
    Explorer writes when HE opens something himself.

The claim "you opened this one yourself" is made only when the target appears in
Recent and does NOT appear in her log. That is checkable, and when there is no
evidence she simply says "Done, Emperor." rather than reaching for the character
line. The register is earned, not performed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTION_LOG = ROOT / "data" / "zoey-actions.jsonl"
RECENT_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"

#: How far back a Recent shortcut still counts as "he did this himself".
RECENT_WINDOW_S = 7 * 24 * 3600


@dataclass(frozen=True)
class Action:
    tool: str
    target: str
    ts: float


def record(tool: str, target: str) -> None:
    """Append one action Zoey performed. Never raises — memory must not break a turn."""
    try:
        ACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ACTION_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"tool": tool, "target": target, "ts": time.time()}) + "\n")
    except OSError:
        pass


def she_has_opened(target: str) -> bool:
    key = _key(target)
    try:
        with ACTION_LOG.open("r", encoding="utf-8") as fh:
            return any(_key(json.loads(l).get("target", "")) == key
                       for l in fh if l.strip())
    except (OSError, json.JSONDecodeError):
        return False


def he_opened_it_himself(target: str) -> bool:
    """
    True only with real evidence: a Recent shortcut he created, and no record of
    her having opened it. Both halves are required — either alone is a guess.
    """
    if she_has_opened(target):
        return False
    key = _key(target)
    if not key or not RECENT_DIR.exists():
        return False
    cutoff = time.time() - RECENT_WINDOW_S
    try:
        for lnk in RECENT_DIR.glob("*.lnk"):
            if lnk.stem.lower() != key:
                continue
            if lnk.stat().st_mtime >= cutoff:
                return True
    except OSError:
        return False
    return False


def _key(target: str) -> str:
    """Compare on the leaf name — a folder is the same folder however it is spelled."""
    if not target:
        return ""
    return Path(str(target).rstrip("\\/")).name.lower()
