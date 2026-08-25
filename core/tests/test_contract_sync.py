"""
core/tests/test_contract_sync.py — the anti-drift guard.

`packages/protocol/gen/python/` is generated from schema/enums.json and is the
authority. A few places in core/ still restate parts of the contract as plain
Python Literals, either because they are a deliberate SUBSET (guard.Actor) or
because the module must stay import-path-independent (audit.Actor).

Those are the exact places that drift silently. This asserts they cannot.

Run standalone (no pytest needed):
    python core/tests/test_contract_sync.py
"""

from __future__ import annotations

import sys
import typing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "protocol" / "gen" / "python"))

import tessa_protocol as proto  # noqa: E402
from core.security import audit, guard  # noqa: E402

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def literal_values(tp: object) -> set[str]:
    return set(typing.get_args(tp))


def main() -> int:
    print("\ncore <-> generated protocol sync\n")

    # ── audit.Actor must equal Provenance exactly ────────────────────────────
    actor_vals = literal_values(audit.Actor)
    check(
        "audit.Actor == CONTRACT Provenance",
        actor_vals == set(proto.PROVENANCE),
        f"audit={sorted(actor_vals)} contract={sorted(proto.PROVENANCE)}",
    )

    # ── guard.Actor is a deliberate SUBSET, not a copy ───────────────────────
    guard_vals = literal_values(guard.Actor)
    check(
        "guard.Actor is a subset of Provenance",
        guard_vals <= set(proto.PROVENANCE),
        f"guard has values not in the contract: {sorted(guard_vals - set(proto.PROVENANCE))}",
    )
    check(
        "guard.Actor excludes program/external/system (they cannot request a shell)",
        guard_vals == {"human", "agent", "schedule"},
        f"guard={sorted(guard_vals)}",
    )

    # ── tiers ────────────────────────────────────────────────────────────────
    check(
        "guard tiers == CONTRACT tiers",
        set(proto.TIERS) == {"green", "amber", "red"},
        f"{sorted(proto.TIERS)}",
    )
    audit_tiers = literal_values(audit.Tier)
    check(
        "audit.Tier is CONTRACT tiers plus 'none'",
        audit_tiers == set(proto.TIERS) | {"none"},
        f"audit={sorted(audit_tiers)}",
    )

    # ── permissions.yaml must classify every pty.spawn.<actor> ───────────────
    g = guard.Guard(ROOT / "core" / "config" / "permissions.yaml")
    for a in sorted(guard_vals):
        cap = f"pty.spawn.{a}"
        d = g.evaluate(cap, a, "C:\\dev")  # type: ignore[arg-type]
        check(
            f"permissions.yaml classifies {cap}",
            d.tier is not None and "not listed" not in d.reason,
            d.reason,
        )

    # ── every protected path is absolute and normalised ──────────────────────
    check(
        "protected paths are non-empty",
        len(g.protected_paths) > 0,
        "permissions.yaml lists no protected paths",
    )

    # ── the new enum values actually reached the generated Python ────────────
    for name, value in [
        ("AGENT_STATES", "blocked"),
        ("JOB_STATUSES", "needsReview"),
        ("CREATED_BY", "systemEvent"),
        ("CREATED_BY", "fileWatch"),
        ("PTY_REPORT_EVENTS", "startFailed"),
        ("PROVENANCE", "external"),
        ("CLOUD_STATES", "unknown"),
        ("SPAWN_MODES", "cdCurrent"),
        ("FS_CHANGE_KINDS", "hydrationChanged"),
        ("DECISIONS", "expired"),
    ]:
        check(f"{name} contains {value!r}", value in getattr(proto, name))

    check(
        "DECISIONS_SENDABLE excludes 'expired' (daemon-emitted only)",
        "expired" not in proto.DECISIONS_SENDABLE
        and proto.DECISIONS_SENDABLE == frozenset({"approve", "deny"}),
        f"{sorted(proto.DECISIONS_SENDABLE)}",
    )

    check(
        "SURFACES stays console|orb — mobile deliberately excluded",
        proto.SURFACES == frozenset({"console", "orb"}),
        f"{sorted(proto.SURFACES)}",
    )

    print(f"\n{passed} passed, {failed} failed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
