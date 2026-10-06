"""
core/tools/chrome_profiles.py — which Chrome profiles exist, and which are HERS.

This module RESOLVES and DESCRIBES profiles. It drives no browser and types
nothing; `core/tools/browser.py` owns the launching.

────────────────────────────────────────────────────────────────────────────────
⚠⚠ THE DISTINCTION THIS FILE EXISTS TO MAKE

There are two completely different kinds of Chrome profile on this machine and
treating them alike would be the security mistake of the whole feature.

  TESSA'S OWN, under %LOCALAPPDATA%\\Tessa\\browser-profiles\\<name>. Created by
  her, holding only what Gerald deliberately signed into there. Opening one is
  green: nothing is in it that he did not put there on purpose.

  HIS, under %LOCALAPPDATA%\\Google\\Chrome\\User Data\\<Profile N>. Measured on
  this machine on 2026-09-08: THIRTY of them, signed into real Google accounts
  — leogerald101@, gerald.dev2@, geraldaws1000@ and more. Driving a browser in
  one of those means acting as him in his mail, his drive, his accounts.

That is not a difference of degree. It is why opening his profile is AMBER and
lives behind a SEPARATE capability, so the green one is structurally incapable
of reaching it rather than merely declining to.

────────────────────────────────────────────────────────────────────────────────
HOW CHROME NAMES ITS OWN PROFILES

`User Data\\Local State` is JSON holding `profile.info_cache`: a map from the
directory name Chrome uses on disk (`Default`, `Profile 18`) to the display
name a human sees (`Person 1`, `Gerald`) and the signed-in account. He says
"the Gerald profile"; Chrome calls it `Profile 18`. This reads that map so the
friendly name he actually uses resolves.

READ ONLY. `Local State` is opened, parsed, and never written.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .base import ToolError
from .browser import PROFILE_ROOT

#: Where Chrome keeps HIS profiles.
CHROME_USER_DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
LOCAL_STATE = CHROME_USER_DATA / "Local State"

#: A friendly name that is safe as a directory. Deliberately narrow: this
#: string becomes a path, and a path built from a spoken word is exactly where
#: traversal gets in. No dots, no separators, no drive letters.
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,30}$")

#: The names that mean "the one she already uses" (round 1's profile).
DEFAULT_NAMES = ("default", "tessa", "tessa's", "your", "yours", "her", "hers")


@dataclass(frozen=True)
class ProfileRef:
    """One resolved profile, and — the important field — whose it is."""

    kind: str          # "tessa" | "personal"
    name: str          # the friendly name he said
    label: str         # what she calls it back
    path: Path         # the user_data_dir to launch against
    directory: str = ""    # Chrome's own subdir name, personal profiles only
    account: str = ""      # the signed-in account, personal profiles only

    @property
    def is_personal(self) -> bool:
        return self.kind == "personal"


def normalise(name: str) -> str:
    return " ".join(str(name or "").strip().lower().replace("_", " ").split())


def safe_profile_name(name: str) -> str:
    """
    A friendly name that may become a directory, or a refusal.

    Rejects separators and dots outright rather than sanitising them away: a
    name that needed cleaning is a name he did not mean, and silently
    rewriting it is how "open the ../../profile" becomes a surprise.
    """
    clean = normalise(name)
    if not clean or not _SAFE_NAME.match(clean):
        raise ToolError(f"{name!r} is not a name I can use for a profile",
                        "Letters, numbers, spaces and dashes only.")
    return clean


def tessa_profiles() -> list[ProfileRef]:
    """Every profile SHE owns."""
    out: list[ProfileRef] = []
    try:
        if PROFILE_ROOT.is_dir():
            for d in sorted(PROFILE_ROOT.iterdir()):
                if d.is_dir():
                    out.append(ProfileRef(kind="tessa", name=d.name,
                                          label=f"Tessa's {d.name} profile", path=d))
    except OSError:
        pass
    return out


def chrome_profiles() -> list[ProfileRef]:
    """
    Every profile HIS Chrome knows about, from Local State. Metadata only.

    Never raises on a malformed or absent Local State — an unreadable profile
    list means "I cannot see your profiles", not a broken tool.
    """
    out: list[ProfileRef] = []
    try:
        raw = json.loads(LOCAL_STATE.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return out
    cache = ((raw.get("profile") or {}).get("info_cache") or {})
    for directory, info in sorted(cache.items()):
        display = str((info or {}).get("name") or directory)
        out.append(ProfileRef(
            kind="personal", name=display, label=f"your {display} Chrome profile",
            # THE USER DATA DIR, not the profile subdirectory. Chrome selects
            # the profile with --profile-directory; pointing a persistent
            # context straight at `User Data\\Profile 18` makes Chrome treat it
            # as a whole new user data root and rebuild it.
            path=CHROME_USER_DATA,
            directory=directory,
            account=str((info or {}).get("user_name") or ""),
        ))
    return out


def resolve(name: str) -> ProfileRef:
    """
    A spoken name to ONE profile, HERS PREFERRED.

    Order matters and is a security choice: her own profiles are matched first,
    so "open your profile" can never land on one of his even if he happens to
    have a Chrome profile called "tessa". Ambiguity among HIS profiles is a
    refusal rather than a guess — thirty of them exist and picking the wrong
    one means driving the wrong signed-in account.
    """
    clean = normalise(name)
    # "open YOUR PROFILE" and "the tessa profile" carry the noun. Strip it, or
    # every natural phrasing misses the DEFAULT_NAMES table below by one word.
    clean = re.sub(r"\b(?:chrome\s+)?profile$", "", clean).strip()
    if not clean:
        raise ToolError("no profile name came through", "Say which profile.")

    mine = tessa_profiles()
    if clean in DEFAULT_NAMES:
        hit = next((p for p in mine if p.name == "default"), None)
        if hit:
            return hit
        return ProfileRef(kind="tessa", name="default",
                          label="Tessa's default profile", path=PROFILE_ROOT / "default")
    for p in mine:
        if p.name == clean:
            return p

    his = chrome_profiles()
    exact = [p for p in his if normalise(p.name) == clean or normalise(p.directory) == clean]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ToolError(f"{len(exact)} of your Chrome profiles are called {name!r}",
                        "Say the account it belongs to instead.")
    near = [p for p in his if clean in normalise(p.name) or clean in normalise(p.account)]
    if len(near) == 1:
        return near[0]
    if len(near) > 1:
        listed = "; ".join(f"{p.name} ({p.account or 'no account'})" for p in near[:3])
        raise ToolError(f"{len(near)} profiles match {name!r}", f"Which one — {listed}?")
    raise ToolError(f"I cannot find a profile called {name!r}",
                    "Say list my chrome profiles and I will read you the names.")


def ensure_tessa_profile(name: str = "default") -> tuple[ProfileRef, bool]:
    """
    (profile, created). Idempotent: an existing profile is returned untouched.

    Only ever creates under HER root. There is no argument that can make this
    create, alter or touch a directory under his Chrome user data.
    """
    clean = safe_profile_name(name)
    if clean in DEFAULT_NAMES:
        clean = "default"
    target = PROFILE_ROOT / clean
    existed = target.is_dir()
    if not existed:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolError(f"I could not make the profile folder ({exc.strerror or exc})",
                            "Check there is room on the disk.") from None
    return (ProfileRef(kind="tessa", name=clean, label=f"Tessa's {clean} profile",
                       path=target), not existed)
