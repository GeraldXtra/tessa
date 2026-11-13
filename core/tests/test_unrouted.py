"""
core/tests/test_unrouted.py — the router reaching the brain, and the four
things that must NOT reach it.

WHAT THIS GUARDS

Gerald asked eight things and one worked. Seven of the failures were this file's
subject matter:

  1. UNROUTED never consulted the model. The engine was built, wired, reported
     at boot and imported by nothing in the voice path.
  2. "The" became a spoken turn.
  3. A request to open a mangled folder name reached the model, which replied "I am opening Taluts for
     you now" — an action it had not taken and cannot take.
  4. A weather question got "not yet" while she was holding a browser.

Run: python core/tests/test_unrouted.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.brain.repair import repair, split_concatenated, recover_domains  # noqa: E402
from core.brain.router import Intent, Router  # noqa: E402
from core.brain.unrouted import (Disposition, classify, is_fragment,  # noqa: E402
                                 unresolved_refusal)

passed = 0
failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {extra}")


print("\nunrouted: the router reaching the brain\n")

EIGHT = [
    ("Tessa, what's the temperature in Abuja right now?",            Disposition.LIVE_DATA),
    ("Tessa, how does a hash table work?", Disposition.QUESTION),
    ("The",                                   Disposition.FRAGMENT),
    ("Tessa, Open My Zorblats",                  Disposition.UNRESOLVED),
    ("OpenWikipedia.org",                   None),   # routes — never classified
    ("Tessa, OpenWikipedia.org",                  None),
    ("TESSA, open my pictures",               None),
    ("Tessa, tweets, the new build is green", None),
]
for text, want in EIGHT:
    r = Router()
    out = r.route(text)
    routed_locally = not (out.intent is Intent.UNROUTED and out.score == 0.0)
    if want is None:
        check(f"{text!r} routes locally", routed_locally,
              f"got {out.intent.value}")
    else:
        check(f"{text!r} -> {want.value}",
              not routed_locally and classify(text) is want,
              f"got {'routed' if routed_locally else classify(text).value}")

# ── 2. THE MODEL IS THE DEFAULT, not a special intent ───────────────────────
for q in ["what is a hash table", "explain recursion to me",
          "what is a linked list", "teach me about pointers",
          "how does a hash map work", "what does idempotent mean"]:
    check(f"{q!r} goes to the brain", classify(q) is Disposition.QUESTION,
          classify(q).value)

# ── 3. FOUR THINGS THAT MUST NOT REACH THE MODEL ────────────────────────────
for a in ["order me a pizza", "book me a flight", "call my mother",
          "remind me at six", "email John about the invoice"]:
    check(f"{a!r} is refused, not answered", classify(a) is Disposition.ACTION,
          classify(a).value)

for u in ["open my zorblats", "show me the thingummy", "close the wotsit"]:
    check(f"{u!r} names the word she could not place",
          classify(u) is Disposition.UNRESOLVED, classify(u).value)
check("the refusal names the object, not the verb",
      "Zorblats" in unresolved_refusal("Tessa, Open My Zorblats"),
      unresolved_refusal("Tessa, Open My Zorblats"))
check("...and never claims to have done it",
      not any(w in unresolved_refusal("Tessa, Open My Zorblats").lower()
              for w in ("opening", "on it", "i have opened", "done")))

for live in ["what's the temperature in Abuja right now", "what is the naira rate",
             "what is in the news today", "what is the bitcoin price"]:
    check(f"{live!r} searches rather than guessing",
          classify(live) is Disposition.LIVE_DATA, classify(live).value)

# ── 4. THE FRAGMENT RULE — and the one-word commands it must not eat ────────
for frag in ["The", "the", "um", "so", "uh", "and", "ok then", ""]:
    check(f"{frag!r} is a fragment", is_fragment(frag))
for real in ["downloads", "stop", "battery", "uptime", "documents", "mute"]:
    r = Router()
    out = r.route(real)
    resolved = not (out.intent is Intent.UNROUTED and out.score == 0.0)
    check(f"{real!r} still resolves and is never reachable by the fragment rule",
          resolved, f"got {out.intent.value}")
check("'weather' alone survives as live data",
      classify("weather") is Disposition.LIVE_DATA)

# ── 5. THE GREETING IS FIRST CONTACT ONLY ───────────────────────────────────
r = Router()
first = r.route("are you there").speech
for _ in range(4):
    r.route("the time on the clock")
later = r.route("are you there").speech
check("turn 1 greets", any(w in first for w in ("Good morning", "Good afternoon",
                                                "Good evening", "It is")), first)
check("turn 6 does NOT greet", later not in (first,) and "Good " not in later, later)
check("no UNROUTED line opens with an acknowledgement",
      not any(s.lower().startswith(("i heard", "heard,", "i caught"))
              for s in __import__("core.brain.router", fromlist=["_UNROUTED"])._UNROUTED))

# ── 6. TRANSCRIPT REPAIR ────────────────────────────────────────────────────
# THE PHONETIC SET IS HERS NOW, AND IT IS MEASURED.
#
# These used to assert Zoi / Joey / Zoe — Whisper's renderings of "Zoey". After
# the rename they kept passing for a while, which is the trap: a test that still
# asserts the old name is not testing what it claims. They now assert the set
# measured for "Tessa" over twenty synthesised utterances with no initial_prompt:
# Tesser (5), Tessa (3), Chester (4), Tessor, Tessir, Tester, Pessar.
for raw, want in [
    ("Tesser, OpenWikipedia.org", "Open Wikipedia.org"),
    ("Tessa, open my pictures", "open my pictures"),
    ("Chester, open my documents", "open my documents"),
    ("Tester, open my pictures", "open my pictures"),
    # The question mark SURVIVES on purpose: it is a signal to the
    # classifier and to the model, and Piper needs the punctuation.
    ("Tessor, what's the temperature in Abuja right now?", "what's the temperature in Abuja right now?"),
]:
    got = repair(raw)[0]
    check(f"repair {raw!r} -> {want!r}", got == want, f"got {got!r}")
check("her name is only stripped as an ADDRESS",
      repair("tell me about Chester Bennington")[0]
      == "tell me about Chester Bennington")
# "professor" was returned once by the decoder and DELIBERATELY excluded from
# the list: it is a real English word, and stripping it would eat a real
# sentence rather than an address.
check("a common word that merely sounds close is NOT stripped",
      repair("professor, how do closures work?")[0] == "professor, how do closures work?")
check("concatenation splits on a capital, not on a plural",
      split_concatenated("Tweets") == "Tweets" and
      split_concatenated("OpenGoogle") == "Open Google")
check("camel-cased real words survive",
      split_concatenated("LedgerWatch") == "LedgerWatch")
for spoken, want in [("google dot com", "google.com"),
                     ("web dot whatsapp dot com", "web.whatsapp.com"),
                     ("x dot com", "x.com")]:
    check(f"domain {spoken!r} -> {want!r}", recover_domains(spoken) == want,
          recover_domains(spoken))
check("'the dot product' is not a domain",
      recover_domains("the dot product") == "the dot product")

# ── 7. THE TWEET REACHES THE GATE ───────────────────────────────────────────
out = Router().route("Tessa, tweets, the new build is green")
check("his tweet transcript routes to x.post",
      bool(out.calls) and out.calls[0].name == "x.post",
      str([c.name for c in out.calls]))
check("...carrying the text he dictated",
      bool(out.calls) and "green" in str(out.calls[0].args.get("text", "")))
check("'post office opening times' does NOT become a tweet",
      not any(c.name == "x.post" for c in Router().route("post office opening times").calls))

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
