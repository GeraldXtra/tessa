"""
core/tests/test_redact.py — redaction: still correct, no longer quadratic.

Two things have to hold at once, and a fix that trades one for the other is not
a fix. So this measures the cost curve AND re-proves every secret shape, with
one credential deliberately placed past the truncation boundary — that last case
is the security argument for truncating, and it is measured rather than asserted.

    python core/tests/test_redact.py
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.security.audit import _MAX_REDACT_INPUT_CHARS, redact  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


# ── 1. the cost curve ────────────────────────────────────────────────────────
print("\n(1) cost on NON-MATCHING input — the shape that was quadratic\n")
print(f"  {'size':>8}  {'ms':>10}  {'ms per KB':>11}   baseline")

BASELINE = {1: 44.0, 2: 212.0, 4: 754.0, 16: 13604.0}
per_kb: dict[int, float] = {}
for kb in (1, 2, 4, 16):
    s = "x" * (kb * 1024)
    xs = []
    for _ in range(5):
        t0 = time.perf_counter()
        redact(s)
        xs.append((time.perf_counter() - t0) * 1000.0)
    ms = statistics.median(xs)
    per_kb[kb] = ms / kb
    print(f"  {kb:>6} KB  {ms:>8.3f} ms  {ms / kb:>9.3f}    was {BASELINE[kb]:>8.1f} ms "
          f"({BASELINE[kb] / kb:>8.1f} ms/KB)")

# Linearity is asserted on the UN-TRUNCATED range only.
#
# A first version compared max/min ms-per-KB across all four sizes and flapped
# between 2.34x and 3.06x on a 3.0 threshold. That instrument was wrong, not
# marginal: the 16 KB point is deliberately capped at 8 KB by truncation, so it
# is CHEAPER per KB by design, and folding it into a "flatness" ratio conflates
# "flat" with "identical" and then measures jitter. 1->4 KB is the range where
# the regex actually scans everything it is given, and that is where quadratic
# behaviour would show: quadratic means 4x the input costs ~16x, linear ~4x.
growth_1_to_4 = (per_kb[4] * 4) / (per_kb[1] * 1)
check("1 KB -> 4 KB grows ~linearly, not quadratically",
      growth_1_to_4 < 8.0,
      f"4x the input costs {growth_1_to_4:.1f}x the time (linear ~4x, quadratic ~16x; "
      f"was {BASELINE[4] / BASELINE[1]:.0f}x)")
check("truncation caps the 16 KB case below the 8 KB scan cost",
      per_kb[16] * 16 < per_kb[4] * 4 * 3,
      f"16 KB total {per_kb[16] * 16:.1f} ms vs 4 KB total {per_kb[4] * 4:.1f} ms")
check("16 KB is no longer seconds",
      per_kb[16] * 16 < 100.0, f"{per_kb[16] * 16:.1f} ms (was 13,604 ms)")

# ── 2. it still redacts every shape the suite covers ─────────────────────────
print("\n(2) every secret shape still redacted\n")

CASES = [
    ("anthropic key", "using sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA now", "sk-ant-api03"),
    ("openai-style key", "key sk-" + "A" * 40 + " end", "sk-" + "A" * 40),
    ("aws access key id", "id AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
    ("aws secret", "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCY", "wJalrXUtnFEMI"),
    ("github pat", "tok ghp_" + "b" * 36 + " x", "ghp_" + "b" * 36),
    ("slack token", "xoxb-1234567890-abcdefghij tail", "xoxb-1234567890-abcdefghij"),
    ("google api key", "AIza" + "C" * 35 + " tail", "AIza" + "C" * 35),
    ("jwt bearer", "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVP",
     "eyJhbGciOiJIUzI1NiJ9"),
    ("postgres url password", "postgres://zoey:hunter2SECRET@db.internal:5432/x", "hunter2SECRET"),
    ("mongodb+srv url password", "mongodb+srv://u:TopS3cretPw@cluster0.mongodb.net/db", "TopS3cretPw"),
    ("api_key=value", 'api_key=abcd1234efgh5678 trailing', "abcd1234efgh5678"),
    ("password: value", "password: SuperSecret99", "SuperSecret99"),
]

for name, text, secret in CASES:
    out = redact(text)
    check(f"{name:<24} secret removed", secret not in out, f"-> {out[:74]}")

# ── 3. the boundary case — the security argument for truncating ─────────────
print(f"\n(3) a credential placed PAST the {_MAX_REDACT_INPUT_CHARS:,}-char boundary\n")

SECRET = "sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
padded = ("y" * (_MAX_REDACT_INPUT_CHARS + 500)) + " " + SECRET
out = redact(padded)

check("the past-boundary credential is NOT in the output",
      SECRET not in out, f"output ends: ...{out[-60:]!r}")
check("it was DROPPED by truncation, not merely unscanned",
      "[truncated" in out and len(out) < len(padded),
      f"input {len(padded):,} chars -> output {len(out):,} chars")
check("the truncation is explicit in the record, not silent",
      "…[truncated" in out, out[-40:])

# A credential BEFORE the boundary must still be caught — truncation must not
# become an accidental way to smuggle one in by padding the front.
inside = SECRET + (" z" * 2000)
check("a credential BEFORE the boundary is still redacted",
      SECRET not in redact(inside), "")

# ── 4. non-strings are untouched, recursion still works ─────────────────────
print("\n(4) structure preserved\n")
nested = {"a": ["postgres://u:PW123456@h/db", 42], "b": {"c": "sk-ant-api03-" + "Q" * 28}}
r = redact(nested)
check("dict/list recursion intact", isinstance(r, dict) and isinstance(r["a"], list))
check("nested url password redacted", "PW123456" not in str(r))
check("nested anthropic key redacted", "Q" * 28 not in str(r))
check("non-string values pass through", r["a"][1] == 42)

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
