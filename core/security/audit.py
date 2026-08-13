"""
core/security/audit.py — tamper-evident, append-only audit log.

CONTRACT §6.2 / plan.md §5.1 non-negotiable #3 and #5.

Two properties this file exists to guarantee:

  1. TAMPER EVIDENCE. Each entry commits to the hash of the previous one. You
     cannot quietly rewrite or delete history without breaking the chain, and
     `verify()` will point at the exact entry where it broke.

  2. REDACTION BEFORE WRITE. Secrets are scrubbed on the way IN, never on the
     way out. A secret written unredacted once is leaked permanently — rotating
     it is then the only remedy, and you would not know to.

This starts at commit one deliberately. Provenance cannot be reconstructed
later: by the time you want to know whether the agent or the owner ran
something, the information is gone.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

# Audit actors are exactly CONTRACT §6.2 Provenance — human | program | agent |
# schedule | external | system. Kept as a Literal here rather than imported so
# audit.py has no import-path dependency; core/tests asserts the two match.
Actor = Literal["human", "program", "agent", "schedule", "external", "system"]
Tier = Literal["green", "amber", "red", "none"]

GENESIS = "0" * 64

# How much of the log's tail to read when re-deriving the chain head.
#
# Only the last line is needed. 64 KB is ~150 entries at the observed ~430 bytes
# each — enormous headroom for one line, while making the read O(1) in log
# length instead of O(entries). _recover() widens this automatically if a single
# line ever exceeds it, so the constant is a performance choice, not a limit.
_TAIL_WINDOW_BYTES = 64 * 1024

# How long to wait for another process to finish its append before giving up.
_LOCK_TIMEOUT_S = 5.0
_LOCK_RETRY_S = 0.01


@contextlib.contextmanager
def _exclusive(path: Path):
    """
    Cross-PROCESS exclusive lock, held for the duration of one append.

    A threading.Lock is not enough. It guards threads inside ONE interpreter,
    and the failure this prevents was caused by two interpreters: during Phase 1
    two daemons overlapped for ~2 s (the port walk landing on 47601 is the
    tell), each `AuditLog` recovered the same head, and both wrote seq 69
    chained to the same predecessor. The chain forked.

    That matters more than a cosmetic duplicate. Hash-chaining exists so a fork
    means tampering; a fork that concurrency can also produce makes the signal
    ambiguous, and an ambiguous tamper-evidence record is not tamper evidence.

    A sidecar lock file with O_EXCL is used rather than msvcrt/fcntl byte-range
    locking: it behaves identically on both platforms, and it cannot leave the
    audit file itself in a locked state if the holder dies mid-write.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            # Windows raises PermissionError (EACCES), not FileExistsError, when
            # O_EXCL hits an existing file — and again transiently while another
            # process is unlinking it. Catching only FileExistsError crashed a
            # worker under a 4-process concurrency test; the lock itself held.
            if time.monotonic() >= deadline:
                # Assume a crashed holder rather than blocking the daemon
                # forever. Losing the lock is bad; refusing to audit is worse.
                with contextlib.suppress(OSError):
                    os.unlink(str(lock_path))
                continue
            time.sleep(_LOCK_RETRY_S)
    try:
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(str(lock_path))


# ─────────────────────────────────────────────────────────── redaction ──────

# Ordered most-specific first. Each pattern keeps enough context for the entry
# to stay readable while removing the secret itself.
# Longest string handed to the regex set. Anything past this is DROPPED before
# scanning — see the note in redact() for why that is the safe direction.
_MAX_REDACT_INPUT_CHARS = 8 * 1024

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Anthropic
    (re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"), "sk-ant-<REDACTED>"),
    # OpenAI and lookalikes
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"), "sk-<REDACTED>"),
    # AWS access key id + secret
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AKIA<REDACTED>"),
    (
        re.compile(r"(?i)(aws_secret_access_key\s*[=:]\s*)\S+"),
        r"\1<REDACTED>",
    ),
    # GitHub
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{36,}\b"), "gh<REDACTED>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"), "github_pat_<REDACTED>"),
    # Slack
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "xox<REDACTED>"),
    # Google API
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), "AIza<REDACTED>"),
    # JWT — three base64url segments
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "<REDACTED_JWT>",
    ),
    # PEM private keys
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----",
            re.DOTALL,
        ),
        "<REDACTED_PRIVATE_KEY>",
    ),
    # Authorization headers, incl. `curl -H "Authorization: Bearer ..."`
    (re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic|token)\s+)\S+"), r"\1<REDACTED>"),
    # Password inside a connection string / URL userinfo.
    #
    # The scheme repetition is BOUNDED at 15. Unbounded, `[a-z][a-z0-9+.\-]*`
    # matched a long unbroken alphanumeric run at every start position, failed on
    # `://`, and backtracked through every length — O(n^2). Measured on
    # non-matching input: 44 ms at 1 KB, 754 ms at 4 KB, 13.6 s at 16 KB. 309x
    # the time for 16x the input, on the write path of every audit entry, INSIDE
    # the cross-process lock, so one slow redaction stalls every append in every
    # process. A bounded repetition cannot backtrack across an unbounded run.
    #
    # 15 is generous for a URI scheme: `https` is 5, `postgresql` 10,
    # `mongodb+srv` 11. Nothing real is excluded, and a credential in a longer
    # pseudo-scheme is still caught by the key=value pattern below.
    (re.compile(r"(?i)([a-z][a-z0-9+.\-]{0,15}://[^:/\s]+:)[^@/\s]+(@)"), r"\1<REDACTED>\2"),
    # key=value / key: value for obviously-sensitive names
    (
        re.compile(
            r"(?i)\b((?:api[_-]?key|apikey|secret|token|passwd|password|pwd|private[_-]?key|access[_-]?key|client[_-]?secret)\s*[=:]\s*)"
            r"(\"[^\"]{4,}\"|'[^']{4,}'|[^\s,;)}\]]{4,})"
        ),
        r"\1<REDACTED>",
    ),
]


def redact(value: Any) -> Any:
    """
    Recursively scrub secrets from a value before it is persisted.

    Applied to every audit payload on the way in. Conservative by design: it is
    far better to over-redact an audit summary than to write one live key.

    ─────────────────────────────────────────────────────────────────────────────
    STRINGS ARE TRUNCATED BEFORE REDACTION, AND THAT IS THE SAFE DIRECTION.

    Every pattern below is linear on bounded input, but the input itself is not
    bounded: CLAUDE.md invariant 3 says terminal, tool, file, web and email
    output is untrusted DATA, and once the voice pipeline lands a tool RESULT —
    a directory listing, a file read — reaches this function. Scanning megabytes
    inside the audit lock is a self-inflicted denial of service on the one
    component that has to survive everything else.

    Truncation FAILS SAFE. The discarded tail is never written to the log at
    all, so a secret beyond the budget is DROPPED, not exposed — the opposite of
    the usual truncation hazard where a scanner stops early and the unscanned
    remainder is still persisted. An audit summary is a summary; if 8 KB is not
    enough to describe an action, the entry is malformed, not truncated.
    ─────────────────────────────────────────────────────────────────────────────
    """
    if isinstance(value, str):
        out = value
        if len(out) > _MAX_REDACT_INPUT_CHARS:
            dropped = len(out) - _MAX_REDACT_INPUT_CHARS
            out = out[:_MAX_REDACT_INPUT_CHARS] + f"…[truncated {dropped} bytes]"
        for pattern, replacement in _SECRET_PATTERNS:
            out = pattern.sub(replacement, out)
        return out
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


# ────────────────────────────────────────────────────────────── the log ─────


def _canonical(entry: dict[str, Any]) -> bytes:
    """Deterministic serialisation — the bytes the chain hash is taken over."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AuditLog:
    """
    Append-only hash-chained log, one JSON object per line.

    Thread-safe. Survives power loss at line granularity: a torn final line is
    detected by `verify()` rather than silently trusted, which matters on a
    machine with unreliable mains power.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq, self._head = self._recover()

    def _recover(self) -> tuple[int, str]:
        """
        Re-derive (next_seq, head_hash) from disk. Returns (0, GENESIS) if empty.

        ─────────────────────────────────────────────────────────────────────────
        WHY THIS READS THE TAIL AND NOT THE FILE

        This runs INSIDE the lock on EVERY append, and it has to: the in-memory
        _seq/_head are a cache, another process may have appended since, and
        trusting that cache is exactly what forked the chain at seq 69. The
        re-derivation is correct and stays.

        Reading the whole file to do it was not. Measured: the walk cost ~31 us
        per entry, flat, i.e. linear in log length — 36.5 ms of a 45.7 ms grant
        at 288 entries, 327 ms at 10,000, 773 ms at 25,000, on a log that is
        append-only and never shrinks. Every audited action paid it, and the
        voice pipeline pays it on every spoken confirmation against a 2 s budget.

        Only the LAST line is needed, so only the last few KB are read. The
        multi-process property is untouched: still re-derived from disk, still
        inside the lock, just without reading 25,000 lines to find one.

        FOUR CASES THIS MUST SURVIVE, each with a test in tests/test_audit_tail.py:
          a. Torn final line (power cut mid-append) — walk BACKWARDS to the last
             line that parses. Do not give up, and do not resume from before it.
          b. UTF-8 sequence split across the window boundary — read BYTES, drop
             the leading partial line, and split on b"\\n", which cannot occur
             inside a multi-byte UTF-8 sequence.
          c. No trailing newline on the final line.
          d. A log shorter than the window, and an empty log.

        A DELIBERATE BEHAVIOUR CHANGE, not an accident of the rewrite: the old
        forward scan stopped at the FIRST unparseable line, so a torn line in the
        MIDDLE made it resume from before it — and the next append would then
        re-use seq numbers that already existed further down, forking the chain
        exactly the way seq 69 forked. Walking backwards from the end resumes
        from the true head instead. verify() still reports the mid-file tear; it
        is deliberately left O(n) because walking the whole chain is its job, and
        it runs at startup, not on the write path.
        ─────────────────────────────────────────────────────────────────────────
        """
        if not self.path.exists():
            return 0, GENESIS
        size = self.path.stat().st_size
        if size == 0:
            return 0, GENESIS

        window = _TAIL_WINDOW_BYTES
        while True:
            start = max(0, size - window)
            with self.path.open("rb") as fh:
                fh.seek(start)
                chunk = fh.read(size - start)

            if start > 0:
                # The window almost certainly begins mid-line. Drop that partial
                # line: it is incomplete anyway, and dropping it is also what
                # makes the UTF-8 boundary safe — we never decode a truncated
                # multi-byte sequence, because we never decode the first line.
                nl = chunk.find(b"\n")
                if nl == -1:
                    # One line longer than the whole window. Widen and retry.
                    if window >= size:
                        return 0, GENESIS
                    window *= 4
                    continue
                chunk = chunk[nl + 1:]

            for raw in reversed([ln for ln in chunk.split(b"\n") if ln.strip()]):
                try:
                    entry = json.loads(raw.decode("utf-8"))
                    return int(entry["seq"]) + 1, str(entry["hash"])
                except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
                    # Case (a): this line is torn or not an entry. Keep walking
                    # backwards rather than treating it as the end of the log.
                    continue

            # Nothing in the window parsed. If we have already read the whole
            # file there is nothing to resume from; otherwise widen and retry.
            if start == 0:
                return 0, GENESIS
            window *= 4

    def append(
        self,
        *,
        actor: Actor,
        tool: str,
        summary: str,
        tier: Tier = "none",
        detail: dict[str, Any] | None = None,
        provenance: str | None = None,
    ) -> dict[str, Any]:
        """
        Write one entry. Returns the persisted entry (already redacted).

        `actor` is REQUIRED and has no default: every entry must say whether the
        owner, the model, or a scheduled trigger caused it. That attribution is
        the whole point of the log.
        """
        with self._lock, _exclusive(self.path):
            # Re-derive from the file, INSIDE the lock. The in-memory _seq/_head
            # are only a cache; another process may have appended since this
            # instance last looked, and trusting the cache is exactly what forked
            # the chain at seq 69.
            self._seq, self._head = self._recover()

            entry: dict[str, Any] = {
                "seq": self._seq,
                "ts": _now(),
                "actor": actor,
                "tool": tool,
                "tier": tier,
                "summary": redact(summary),
                "detail": redact(detail or {}),
                "provenance": provenance,
                "prev": self._head,
            }
            entry["hash"] = hashlib.sha256(_canonical(entry)).hexdigest()

            # Write + fsync so an entry survives a power cut. The owner is in
            # Lagos; this is not a hypothetical.
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())

            self._seq = entry["seq"] + 1
            self._head = entry["hash"]
            return entry

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return iter(())
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def verify(self) -> tuple[bool, str | None]:
        """
        Walk the chain.

        Returns (True, None) if intact, else (False, reason) naming the first
        entry where it broke.
        """
        prev = GENESIS
        expected_seq = 0
        n = 0

        if not self.path.exists():
            return True, None

        with self.path.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    return False, f"line {lineno}: malformed JSON (torn write?)"

                if entry.get("seq") != expected_seq:
                    return False, f"line {lineno}: seq {entry.get('seq')} != expected {expected_seq}"
                if entry.get("prev") != prev:
                    return False, f"seq {entry['seq']}: prev hash does not match previous entry"

                stated = entry.pop("hash", None)
                recomputed = hashlib.sha256(_canonical(entry)).hexdigest()
                if stated != recomputed:
                    return False, f"seq {entry['seq']}: content altered (hash mismatch)"

                prev = recomputed
                expected_seq += 1
                n += 1

        return True, None

    @property
    def head(self) -> str:
        return self._head

    @property
    def count(self) -> int:
        return self._seq
