"""
core/telemetry/cost.py — the spend ledger behind `evt.daemon.health.budgetSpent`.

CONTRACT §4.1 ships `budgetSpent` and `budgetCap` on every heartbeat, and
TESSA_CORE-spec §6 makes the nightly cap a **hard stop, not a warning**. A hard stop
can only be enforced against a real running total, so this is a persisted,
date-keyed ledger rather than a number.

Today's honest value is 0.00 — nothing has spent anything yet. The distinction
that matters: it is 0.00 **because the ledger says so**, not because a literal
zero was typed into the heartbeat. The voice pipeline lands next and will start
appending real entries against it.

**Unit: NGN (Nigerian naira).** CONTRACT §4.1 does not state a currency for
`budgetSpent`/`budgetCap`, which is a genuine ambiguity between two surfaces —
a §4.1 clarification is proposed to the owner rather than assumed away here.

Keyed by LOCAL date, not UTC. A "nightly budget" is a human, wall-clock notion:
work at 01:00 in Lagos belongs to that night, and UTC keying would roll the
budget over at 01:00 local and hand back a fresh allowance mid-session.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

# The unit for every amount in this module and on the wire.
CURRENCY = "NGN"


@dataclass(frozen=True)
class SpendEntry:
    ts: str
    category: str
    amount: float
    note: str


class CostLedger:
    """
    Append-only spend ledger, one JSON object per line, grouped by local date.

    Same shape as the audit log deliberately: append-only JSONL survives a power
    cut at line granularity, which matters on a machine with unreliable mains.
    It is NOT hash-chained — this is an accounting aid, not a tamper-evidence
    record, and pretending otherwise would overstate it.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # date-string -> running total, rebuilt from disk at startup so a daemon
        # restart mid-evening does not reset the night's spend to zero.
        self._totals: dict[str, float] = {}
        self._load()

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    # Torn final write from a power cut. Skip it rather than
                    # refusing to start — a lost fraction of a naira must never
                    # prevent the daemon from booting.
                    continue
                day = str(row.get("day", ""))
                amount = row.get("amount")
                if day and isinstance(amount, (int, float)):
                    self._totals[day] = round(self._totals.get(day, 0.0) + float(amount), 4)

    def record(self, *, category: str, amount: float, note: str = "") -> float:
        """Append a spend and return the new running total for today."""
        if amount < 0:
            raise ValueError("spend cannot be negative")
        with self._lock:
            day = self._today()
            row: dict[str, Any] = {
                "day": day,
                "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "category": category,
                "amount": round(float(amount), 4),
                "currency": CURRENCY,
                "note": note,
            }
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._totals[day] = round(self._totals.get(day, 0.0) + row["amount"], 4)
            return self._totals[day]

    def spent_today(self) -> float:
        """Today's running total. 0.0 when the ledger has no entries for today."""
        return round(self._totals.get(self._today(), 0.0), 2)

    def entries_today(self) -> int:
        if not self.path.exists():
            return 0
        day = self._today()
        n = 0
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    if json.loads(line).get("day") == day:
                        n += 1
                except json.JSONDecodeError:
                    continue
        return n
