"""
core/security/guard.py — the permission guard.

CONTRACT §6.4/§6.5: this is the ONLY authority on tiers. Surfaces render what
the guard decides; they never evaluate policy themselves. A Console that spawned
a PTY without a grant from here would be a contract violation.

The guard answers one question: may <actor> perform <capability> on <target>?
It returns one of ALLOW / CONFIRM / DENY, plus a reason the UI can show the
owner. It never executes anything.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, Literal

import yaml

_GEN = Path(__file__).resolve().parents[2] / "packages" / "protocol" / "gen" / "python"
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))

# Generated from packages/protocol/schema/enums.json
from tessa_protocol import Tier, TIERS  # noqa: E402,F401

# PTY spawn actors are a strict subset of Provenance: `program` and `external`
# can never request a shell, and every unattended trigger (fileWatch, email,
# webhook, systemEvent) maps to `schedule` for tier purposes — the most
# restrictive of the three, which is the safe default.
Actor = Literal["human", "agent", "schedule"]


class Verdict(str, Enum):
    ALLOW = "allow"       # proceed, log it
    CONFIRM = "confirm"   # ask the owner first
    DENY = "deny"         # refuse; no prompt can override


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    tier: Tier | None
    reason: str
    capability: str
    actor: Actor
    target: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


class Guard:
    def __init__(self, config_path: str | os.PathLike[str]) -> None:
        self.config_path = Path(config_path)
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))

        tiers: dict[str, list[str]] = raw.get("tiers", {})
        # capability -> tier, flattened for O(1) lookup
        self._tier_of: dict[str, Tier] = {}
        for tier_name in ("green", "amber", "red"):
            for cap in tiers.get(tier_name, []) or []:
                self._tier_of[cap] = tier_name  # type: ignore[assignment]

        self._protected: list[PurePath] = [
            PurePath(os.path.normcase(os.path.normpath(p)))
            for p in raw.get("protected_paths", []) or []
        ]
        self._never: set[str] = set(raw.get("never", []) or [])
        self._hydration: dict[str, int] = raw.get("hydration", {}) or {}

    # ── paths ────────────────────────────────────────────────────────────────

    def is_protected(self, target: str) -> bool:
        """
        True if `target` is at or under a protected root.

        Uses normcase + parts comparison rather than string prefixing, so
        `C:\\dev\\tessa-other` is NOT treated as inside `C:\\dev\\tessa`.
        """
        try:
            resolved = Path(target).resolve()
        except (OSError, ValueError):
            resolved = Path(os.path.normpath(target))

        candidate = PurePath(os.path.normcase(str(resolved)))
        for root in self._protected:
            if candidate == root or root.parts == candidate.parts[: len(root.parts)]:
                return True
        return False

    # ── core evaluation ──────────────────────────────────────────────────────

    def evaluate(
        self,
        capability: str,
        actor: Actor,
        target: str | None = None,
        *,
        mutating: bool = False,
    ) -> Decision:
        """
        Decide whether `actor` may perform `capability`, optionally on `target`.

        `mutating` marks write/delete/rename intent, which is what the
        protected-path rule keys on — reading and listing a protected folder is
        always fine, writing to one is not.
        """
        # 1. Absolutes. No approval path exists for these.
        if capability in self._never:
            return Decision(
                Verdict.DENY, None,
                f"'{capability}' is permanently disabled in permissions.yaml (never list)",
                capability, actor, target,
            )

        tier = self._tier_of.get(capability)
        if tier is None:
            # Unknown capability is not an implicit allow. Fail closed.
            return Decision(
                Verdict.CONFIRM, "red",
                f"'{capability}' is not listed in permissions.yaml — treating as red until classified",
                capability, actor, target,
            )

        # 2. Protected paths override tier for mutating operations.
        if mutating and target is not None and self.is_protected(target):
            return Decision(
                Verdict.CONFIRM, tier,
                f"{target} is a protected path — confirm regardless of tier or actor",
                capability, actor, target,
            )

        # 3. Tier policy.
        if tier == "red":
            return Decision(
                Verdict.CONFIRM, tier,
                f"'{capability}' is red-tier — always requires explicit approval",
                capability, actor, target,
            )

        if tier == "amber":
            if actor == "human":
                return Decision(
                    Verdict.ALLOW, tier,
                    f"'{capability}' is amber, but the owner initiated it directly",
                    capability, actor, target,
                )
            return Decision(
                Verdict.CONFIRM, tier,
                f"'{capability}' is amber and was initiated by {actor} — confirm",
                capability, actor, target,
            )

        return Decision(
            Verdict.ALLOW, tier, f"'{capability}' is green-tier", capability, actor, target
        )

    # ── console-specific helpers ─────────────────────────────────────────────

    def evaluate_pty_spawn(self, actor: Actor, cwd: str) -> Decision:
        """
        CONTRACT §6.5 — no PTY may exist without a grant.

        Note the deliberate asymmetry: a human opening a shell anywhere is
        green (it is their machine), but the agent asking for one is amber even
        in an ordinary directory, because a shell is a general-purpose
        capability and the model does not get one silently.
        """
        capability = f"pty.spawn.{actor}"
        decision = self.evaluate(capability, actor, cwd, mutating=False)

        # A shell rooted inside a protected path can mutate it, so treat
        # spawning there as mutating intent even though spawning itself is not.
        if decision.verdict is Verdict.ALLOW and self.is_protected(cwd):
            if actor != "human":
                return Decision(
                    Verdict.CONFIRM, decision.tier,
                    f"{actor} requested a shell inside protected path {cwd}",
                    capability, actor, cwd,
                )
        return decision

    def evaluate_hydration(self, actor: Actor, path: str, bytes_to_download: int) -> Decision:
        """
        CONTRACT §6.3 — recalling cloud placeholders costs the owner metered data.
        """
        warn = int(self._hydration.get("warn_bytes", 50 * 1024 * 1024))
        stop = int(self._hydration.get("require_approval_bytes", 200 * 1024 * 1024))
        ngn_per_gb = int(self._hydration.get("ngn_per_gb", 500))
        cost_ngn = round(bytes_to_download / (1024**3) * ngn_per_gb, 2)

        extra = {
            "bytesToDownload": bytes_to_download,
            "estimatedCostNGN": cost_ngn,
            "warnBytes": warn,
        }

        if bytes_to_download >= stop:
            return Decision(
                Verdict.CONFIRM, "amber",
                f"This would download {bytes_to_download / 1024**2:.1f} MB "
                f"(~₦{cost_ngn:,.2f}) from cloud storage",
                "fs.hydrate", actor, path, extra,
            )
        if bytes_to_download >= warn:
            return Decision(
                Verdict.ALLOW, "amber",
                f"Downloads {bytes_to_download / 1024**2:.1f} MB (~₦{cost_ngn:,.2f})",
                "fs.hydrate", actor, path, extra,
            )
        return Decision(Verdict.ALLOW, "amber", "Below hydration warning threshold",
                        "fs.hydrate", actor, path, extra)

    @property
    def protected_paths(self) -> list[str]:
        return [str(p) for p in self._protected]
