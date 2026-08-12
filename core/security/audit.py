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

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

# Audit actors are exactly CONTRACT §6.2 Provenance — human | program | agent |
# schedule | external | system. Kept as a Literal here rather than imported so
# audit.py has no import-path dependency; core/tests asserts the two match.
Actor = Literal["human", "program", "agent", "schedule", "external", "system"]
Tier = Literal["green", "amber", "red", "none"]

GENESIS = "0" * 64


# ─────────────────────────────────────────────────────────── redaction ──────

# Ordered most-specific first. Each pattern keeps enough context for the entry
# to stay readable while removing the secret itself.
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
    # Password inside a connection string / URL userinfo
    (re.compile(r"(?i)([a-z][a-z0-9+.\-]*://[^:/\s]+:)[^@/\s]+(@)"), r"\1<REDACTED>\2"),
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
    """
    if isinstance(value, str):
        out = value
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
        """Read the tail to resume the chain. Returns (next_seq, head_hash)."""
        if not self.path.exists():
            return 0, GENESIS
        last: dict[str, Any] | None = None
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    # Torn final write from a power cut. Stop here; verify()
                    # will report it. We do not silently discard it.
                    break
        if last is None:
            return 0, GENESIS
        return int(last["seq"]) + 1, str(last["hash"])

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
        with self._lock:
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
