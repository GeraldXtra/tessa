"""
core/tests/test_audit_tail.py — _recover()'s tail read, case by case.

_recover() stopped reading the whole log and started reading only its tail. That
is a change to the one function that decides where the hash chain resumes, so it
does not get to be verified by "the daemon still starts". Each case the rewrite
had to survive is constructed here as a real file on disk and asserted on.

    python core/tests/test_audit_tail.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.security.audit import GENESIS, AuditLog, _TAIL_WINDOW_BYTES  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def fresh(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"zoey-tail-{name}-")) / "audit.log"


def seeded(path: Path, n: int) -> AuditLog:
    """A real log with n real entries, built through the real append path."""
    log = AuditLog(path)
    for i in range(n):
        log.append(actor="system", tool="seed", summary=f"entry {i}")
    return log


def head_of(path: Path) -> tuple[int, str]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows[-1]["seq"] + 1, rows[-1]["hash"]


print("\n_recover() tail read\n")

# ── (d1) empty log ───────────────────────────────────────────────────────────
p = fresh("empty")
p.parent.mkdir(parents=True, exist_ok=True)
p.write_bytes(b"")
log = AuditLog(p)
check("(d1) EMPTY file -> (0, GENESIS)", log._recover() == (0, GENESIS), str(log._recover()))

# ── (d1b) absent file ────────────────────────────────────────────────────────
p2 = fresh("absent")
log2 = AuditLog(p2)          # __init__ creates the parent dir, not the file
check("(d1b) ABSENT file -> (0, GENESIS)", log2._recover() == (0, GENESIS))

# ── (d2) log SHORTER than the tail window ────────────────────────────────────
p3 = fresh("short")
log3 = seeded(p3, 3)
check("(d2) log shorter than the 64 KB window",
      log3._recover() == head_of(p3),
      f"{p3.stat().st_size} bytes < {_TAIL_WINDOW_BYTES}, recover={log3._recover()[0]}")

# ── (d3) log LONGER than the tail window — the whole point ───────────────────
p4 = fresh("long")
log4 = seeded(p4, 400)       # ~430 B each => comfortably over 64 KB
check("(d3) log LONGER than the window still finds the true head",
      log4._recover() == head_of(p4),
      f"{p4.stat().st_size} bytes > {_TAIL_WINDOW_BYTES}, recover={log4._recover()[0]}")

# ── (c) no trailing newline ──────────────────────────────────────────────────
p5 = fresh("nonewline")
log5 = seeded(p5, 5)
raw = p5.read_bytes()
assert raw.endswith(b"\n")
p5.write_bytes(raw[:-1])
check("(c) final line with NO trailing newline",
      log5._recover() == head_of(p5), str(log5._recover()))

# ── (a) torn final line ──────────────────────────────────────────────────────
p6 = fresh("torn")
log6 = seeded(p6, 6)
good_head = head_of(p6)
with p6.open("ab") as fh:
    fh.write(b'{"seq": 6, "ts": "2026-08-13T00:00:00.000Z", "actor": "sys')  # power cut
r6 = log6._recover()
check("(a) TORN final line -> resumes from the last GOOD entry",
      r6 == good_head, f"got {r6}, expected {good_head}")

# ── (a2) torn line in the MIDDLE — the behaviour change ──────────────────────
# The old forward scan stopped at the first bad line and resumed from BEFORE it,
# which would re-use seq numbers that already exist further down and fork the
# chain. Walking backwards resumes from the true head instead.
p7 = fresh("midtorn")
log7 = seeded(p7, 10)
lines = p7.read_text(encoding="utf-8").splitlines()
true_head = (json.loads(lines[-1])["seq"] + 1, json.loads(lines[-1])["hash"])
lines[4] = '{"seq": 4, "ts": "trunca'
p7.write_text("\n".join(lines) + "\n", encoding="utf-8")
r7 = log7._recover()
check("(a2) torn line in the MIDDLE -> still resumes from the TRUE head",
      r7 == true_head, f"got seq {r7[0]}, expected {true_head[0]} (old code would have said 5)")

# ── (b) multi-byte UTF-8 across the window boundary ──────────────────────────
# Entries padded with 3-byte characters so the 64 KB boundary lands inside one.
p8 = fresh("utf8")
log8 = AuditLog(p8)
for i in range(300):
    log8.append(actor="system", tool="seed", summary="→" * 120 + f" {i}")


def naive_tail_splits(path: Path) -> bool:
    """True if decoding the last window as UTF-8 blind would raise."""
    try:
        path.read_bytes()[-_TAIL_WINDOW_BYTES:].decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


# The boundary must be FORCED, not hoped for. A first attempt asserted the
# fixture was adversarial and it was not — the window happened to land on a
# clean character edge, so the test proved nothing about case (b) even though
# it passed. Append until the split is real, and say so if it never becomes so.
attempts = 0
while not naive_tail_splits(p8) and attempts < 30:
    log8.append(actor="system", tool="seed", summary="→" * 120 + f" pad{attempts}")
    attempts += 1

check("(b) setup: log exceeds the window", p8.stat().st_size > _TAIL_WINDOW_BYTES,
      f"{p8.stat().st_size} bytes")
check("(b) setup: the window boundary REALLY splits a multi-byte character",
      naive_tail_splits(p8),
      f"forced in {attempts} appends — a blind 64 KB decode raises UnicodeDecodeError")
r8 = log8._recover()
check("(b) _recover() decodes cleanly ACROSS that split boundary",
      r8 == head_of(p8), f"got {r8[0]}, expected {head_of(p8)[0]}")

# ── one line longer than the whole window ────────────────────────────────────
#
# The entry is written DIRECTLY rather than through append(), on purpose. The
# case under test is _recover()'s window widening, and routing a 128 KB summary
# through append() would instead exercise redact() — where pattern [11],
# `([a-z][a-z0-9+.\-]*://[^:/\s]+:)[^@/\s]+(@)`, is QUADRATIC in input length
# (measured: 44 ms at 1 KB, 13.6 s at 16 KB — 309x the time for 16x the input).
# That is a real defect, reported separately and not fixed here; a test for the
# tail read should not be gated on an unrelated bug in the redactor.
p9 = fresh("hugeline")
p9.parent.mkdir(parents=True, exist_ok=True)
huge = {"seq": 0, "ts": "2026-08-13T00:00:00.000Z", "actor": "system", "tool": "seed",
        "tier": "none", "summary": "x" * (_TAIL_WINDOW_BYTES * 2), "detail": {},
        "provenance": None, "prev": GENESIS, "hash": "f" * 64}
p9.write_text(json.dumps(huge) + "\n", encoding="utf-8")
log9 = AuditLog(p9)
r9 = log9._recover()
check("(extra) a single entry LARGER than the window widens and still resolves",
      r9 == (1, "f" * 64), f"{p9.stat().st_size} bytes in one line, got seq {r9[0]}")

# ── the chain still actually chains ──────────────────────────────────────────
p10 = fresh("chain")
log10 = seeded(p10, 50)
ok, why = log10.verify()
check("(extra) 50 entries appended via the tail-read path verify() intact", ok, str(why))

# ── against a COPY of the real production log ────────────────────────────────
real = ROOT / "data" / "audit.log"
if real.exists():
    p11 = fresh("realcopy")
    shutil.copy2(real, p11)
    log11 = AuditLog(p11)
    check("(extra) tail read agrees with a full scan of the REAL log",
          log11._recover() == head_of(p11),
          f"{p11.stat().st_size / 1024:.0f} KB, head seq {head_of(p11)[0] - 1}")

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
