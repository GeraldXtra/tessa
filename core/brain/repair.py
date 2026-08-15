"""
core/brain/repair.py — repair what Whisper hands back, before anything matches.

FROM HIS REAL TRANSCRIPTS, NOT FROM IMAGINATION:

    "documents"                            -> "Taluts"
    "open google dot com"                  -> "OpenGoogle.com"
    "tweet that I'm building an AI assistant" -> "Tweets, Data Mbudinon AI Assist"
    "Zoey"                                 -> "Zoi"

Priming (core/voice/stt.py) attacks these at the source and is the better fix
where it works. This module handles what survives it, and it handles CLASSES
rather than instances — a lookup table of three domains is a table that fails on
the fourth.

WHY THIS RUNS BEFORE THE ROUTER AND NOT INSIDE IT

Every matcher downstream — the phrasing table, the folder resolver, the app
index — assumes word boundaries. `OpenGoogle.com` has none. Repairing once, at
the front, means none of them need to know that speech arrives concatenated.
"""

from __future__ import annotations

import re

# ── HER OWN NAME, IN ANY SPELLING ────────────────────────────────────────────
#
# "Zoi" for "Zoey" is her own name being missed, and a command must never fail
# because of it. Whisper's plausible renderings of /ˈzoʊi/ are a small, closed
# set: the vowel is stable, the spelling is not.
#
# Matched ONLY as a leading address — "Zoey, open my downloads" — never mid
# sentence, so "tell me about Zoe Saldana" keeps her name.
_NAME_FORMS = r"zoey|zoi|zoe|joey|zoë|zooey|zowie|soy|zoya"
_LEAD_NAME = re.compile(rf"^\s*(?:hey\s+|hi\s+|ok(?:ay)?\s+|hello\s+)?(?:{_NAME_FORMS})\b[\s,.!:;-]*",
                        re.I)


def strip_wake_name(text: str) -> tuple[str, bool]:
    """('open my downloads', True) — and True means she was addressed by name."""
    t = (text or "").strip()
    out = _LEAD_NAME.sub("", t, count=1)
    return out.strip(" ,.!:;-"), out != t


# ── CONCATENATION ────────────────────────────────────────────────────────────
#
# "OpenGoogle.com" is one token where he said three words. The rule is a VERB
# PREFIX split: if a token begins with a command verb and continues with more
# capitalised or alphanumeric content, insert the boundary the microphone lost.
#
# Verb-anchored rather than general camel-case splitting, because a general rule
# mangles the things it should leave alone — "LedgerWatch", "ZOEY_OS",
# "WhatsApp", "iPhone" are all real words he says and all camel-cased.
_VERBS = ("open", "close", "show", "find", "search", "tweet", "post", "repost",
          "reply", "read", "play", "kill", "delete", "move", "copy", "rename",
          "minimise", "minimize", "maximise", "maximize", "click", "type")
# INLINE `(?i:...)` ON THE VERB ONLY — NOT `re.I` ON THE WHOLE PATTERN.
#
# A global `re.I` makes the lookahead `[A-Z0-9]` case-INSENSITIVE too, so it
# matched lowercase and split "Tweets" into "Tweet s" — mangling the exact
# transcript this module exists to repair. The boundary signal IS the capital
# letter, so the lookahead must stay case-sensitive while the verb does not.
_CONCAT_RE = re.compile(rf"\b((?i:{'|'.join(_VERBS)}))(?=[A-Z0-9])")


def split_concatenated(text: str) -> str:
    """"OpenGoogle.com" -> "Open Google.com". Idempotent."""
    return _CONCAT_RE.sub(lambda m: m.group(1) + " ", text or "")


# ── DOMAINS AS A CLASS ───────────────────────────────────────────────────────
#
# "google.com", "web.whatsapp.com", "x.com" all come back mangled, and a list of
# three sites is a list that fails on the fourth. Two general rules instead:
#
#   1. SPOKEN PUNCTUATION. He says "google dot com"; Whisper writes it out in
#      words as often as it writes the character. Any "<word> dot <tld>" becomes
#      a domain.
#   2. GLUED CASING inside a hostname. "Google.com" is already a domain and only
#      needs the verb split above; the lowercasing happens at match time.
#
# Deliberately bounded to REAL top-level domains. A general "word dot word" rule
# would rewrite "the dot product" and "dot matrix" into hostnames.
_TLDS = ("com", "org", "net", "io", "dev", "co", "ai", "app", "uk", "ng",
         "gov", "edu", "me", "tv", "info", "biz", "xyz")
_SPOKEN_DOT = re.compile(
    rf"\b([\w-]+(?:\s+dot\s+[\w-]+)*)\s+dot\s+({'|'.join(_TLDS)})\b", re.I)


def _collapse_dots(m: re.Match[str]) -> str:
    head = re.sub(r"\s+dot\s+", ".", m.group(1), flags=re.I)
    return f"{head}.{m.group(2)}".lower()


def recover_domains(text: str) -> str:
    """
    "google dot com" -> "google.com"; "web dot whatsapp dot com" ->
    "web.whatsapp.com". Handles arbitrary depth, so a subdomain he has never
    said before works the first time.
    """
    return _SPOKEN_DOT.sub(_collapse_dots, text or "")


# ── the one entry point ──────────────────────────────────────────────────────

def repair(text: str) -> tuple[str, bool]:
    """
    Returns (repaired_text, was_addressed_by_name).

    ORDER MATTERS. The name comes off first so "Zoi, OpenGoogle.com" does not
    have its verb split while the name is still attached; concatenation is split
    before domains so "OpenGoogle dot com" reaches the domain rule as
    "Open Google dot com".
    """
    t, addressed = strip_wake_name(text or "")
    t = split_concatenated(t)
    t = recover_domains(t)
    return " ".join(t.split()).strip(" ,.!:;-"), addressed
