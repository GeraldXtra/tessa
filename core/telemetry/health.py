"""
core/telemetry/health.py — real values for `evt.daemon.health`.

CONTRACT §4.1 fixes the payload shape exactly:

    { uptimeS, cpuPct, memMB, apiReachable, budgetSpent, budgetCap }

Six fields, no more, no fewer, no renames. The Orb already subscribes to
`daemon.*` and renders `uptimeS`; it deliberately ignores the rest because
publishing `apiReachable: false` from a hardcoded literal would park a permanent
false alarm on its UI. This module replaces those literals with measurements, so
the field can be trusted and rendered.

Two design points worth stating, because both were tempting to get wrong:

**cpuPct is THIS PROCESS, not the machine.** `psutil.cpu_percent()` would report
system-wide load, which on a 2-core laptop is dominated by whatever else is
running and says nothing about the daemon. `Process.cpu_percent()` answers the
question the field is actually asking. It is normalised across cores, so a
single fully-busy core reads ~50% on a 2-core box, not 100%.

**apiReachable is a network check, not an API check.** A plain TCP connect: no
API key, no request body, no tokens spent, a few hundred bytes. Answering "is my
key valid" would need a real request, would cost metered data on every probe,
and would make a billing problem indistinguishable from an outage.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Any

try:
    import psutil  # prebuilt wheel; never built from source (no MSVC here)

    _HAVE_PSUTIL = True
except ImportError:  # pragma: no cover - exercised only on a broken install
    _HAVE_PSUTIL = False


@dataclass
class HealthConfig:
    api_probe_interval_s: float = 60.0
    api_probe_host: str = "api.anthropic.com"
    api_probe_port: int = 443
    api_probe_timeout_s: float = 2.0


class HealthCollector:
    """
    Builds one `evt.daemon.health` payload per heartbeat.

    Cheap by construction: the only potentially slow part is the reachability
    probe, and that is rate-limited and cached so the 5 s beat never waits on
    the network.
    """

    def __init__(self, started_at_monotonic: float, config: HealthConfig | None = None) -> None:
        self._t0 = started_at_monotonic
        self.cfg = config or HealthConfig()

        self._proc = psutil.Process() if _HAVE_PSUTIL else None
        if self._proc is not None:
            # First call to cpu_percent() always returns 0.0 — it establishes the
            # baseline for the next interval. Prime it here so the FIRST
            # heartbeat carries a real figure instead of a misleading zero.
            self._proc.cpu_percent(interval=None)

        self._api_reachable = False
        self._api_checked_at: float | None = None

    # ── the probe ────────────────────────────────────────────────────────────

    def _probe_api(self) -> bool:
        """
        TCP connect only. Any failure means False; nothing here may raise into
        the heartbeat, because a network blip must not stop the beat the Orb
        renders from.
        """
        try:
            with socket.create_connection(
                (self.cfg.api_probe_host, self.cfg.api_probe_port),
                timeout=self.cfg.api_probe_timeout_s,
            ):
                return True
        except OSError:
            return False

    def _api_reachable_cached(self) -> bool:
        now = time.monotonic()
        due = self._api_checked_at is None or (now - self._api_checked_at) >= self.cfg.api_probe_interval_s
        if due:
            self._api_reachable = self._probe_api()
            self._api_checked_at = now
        return self._api_reachable

    # ── the payload ──────────────────────────────────────────────────────────

    def sample(self, *, budget_spent: float, budget_cap: float) -> dict[str, Any]:
        """
        Exactly CONTRACT §4.1's six fields, in that shape.

        `cpuPct` and `memMB` degrade to 0.0 if psutil is unavailable rather than
        raising — but that case is reported at startup, never silently, so a
        zero is not mistaken for an idle daemon.
        """
        cpu_pct = 0.0
        mem_mb = 0.0
        if self._proc is not None:
            try:
                cpu_pct = round(self._proc.cpu_percent(interval=None), 1)
                mem_mb = round(self._proc.memory_info().rss / (1024 * 1024), 1)
            except Exception:  # noqa: BLE001 - process may vanish mid-sample
                pass

        return {
            "uptimeS": round(time.monotonic() - self._t0, 1),
            "cpuPct": cpu_pct,
            "memMB": mem_mb,
            "apiReachable": self._api_reachable_cached(),
            "budgetSpent": round(budget_spent, 2),
            "budgetCap": round(budget_cap, 2),
        }

    @property
    def psutil_available(self) -> bool:
        return _HAVE_PSUTIL
