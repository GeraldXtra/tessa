"""
core/system/capability.py — what a capability IS, and how it becomes a tool.

THE ONE RULE

    A capability cannot exist without a tier, and it cannot pick its own.

Everything else in this file exists to make that rule impossible to break by
accident. Read this section and you know the whole design:

  * A capability is a `Capability`: a NAME, the permissions.yaml KEY that
    governs it, the tier its author EXPECTS, its PARAMETERS, and a `run`
    function that takes those parameters and returns a dict of facts.

  * `bind()` turns it into an ordinary `core.tools.base.ToolSpec` — the same
    shape every existing tool has, dispatched by the same `Executor`, held to
    the same `core/tools/_validate()`. There is no second registry and no
    second gate. `core/tools/__init__.py` merges the bound specs into
    `REGISTRY` and validates them with everything else.

  * THE TIER COMES FROM THE GUARD, NOT FROM THE MODULE. `bind()` asks
    `core.security.guard.Guard` — the daemon's existing permission guard,
    reading `core/config/permissions.yaml` — what tier the capability's key
    carries. The module's own `tier=` is a declaration the guard CHECKS:
    if the two disagree, `bind()` refuses and the daemon does not start.
    permissions.yaml is "THE SINGLE AUTHORITY on permission tiers"
    (CONTRACT §6.4); a module that could out-vote it would be a second one.

  * UNTIERED IS RED. A key the guard cannot find in permissions.yaml comes
    back from `Guard.evaluate` as red + CONFIRM ("not listed ... treating as
    red until classified"), and `bind()` binds it exactly that way: red,
    holding, only runnable from the approval card. `bound_specs()` — the
    daemon's path — then REFUSES to register it at all, with a message naming
    the missing line. So a forgotten permissions.yaml entry is loud at start
    and, if a spec is ever injected past that check, still red at run time.

  * THE HANDLER ENFORCES THE TIER ITSELF. The wrapper `bind()` builds around
    `run` will not call it unless the tier's condition is met: a red
    capability needs `_approved_by_surface=True`, which only
    `Executor.execute_approved` (the card) ever passes; an amber one raises
    `ToolHold` until the owner's "yes" arrives as `confirmed=True`, and
    refuses a non-human origin outright; a green one runs — unless the guard's
    protected-path rule says CONFIRM, in which case it holds too. The
    executor strips `provenance`, `confirmed` and `_approved_by_surface` out
    of a call's args before dispatch and re-supplies them by signature, so
    none of these can be forged from a model's args. An author cannot forget
    the hold, because the author never writes it.

  * `run` NEVER RECEIVES A COMMAND STRING. Parameters are typed and bounded
    by `Param`; anything the wrapper did not declare is refused before `run`
    sees it. CLAUDE.md invariant 4 holds by shape, as it does in core/tools.

ADDING A CAPABILITY

  One module under `core/system/abilities/`, exporting `CAPABILITIES`, and
  one line in `core/config/permissions.yaml`. Nothing else. See the package
  docstring in `core/system/__init__.py` for a worked example, including how
  a future RED capability (`files.delete`, `system.shutdown`) declares itself
  and is routed through the approval card by this same code with no changes.

WHY THIS DOES NOT IMPORT core.tools AT MODULE LEVEL

  `core/tools/__init__.py` imports THIS package to merge the bound specs, and
  this package needs `core.tools.base` for ToolSpec/ToolError/ToolHold. A
  top-level import in either direction is a cycle that breaks depending on
  which side is imported first. So the leaf types are imported inside the
  functions that use them — `core.tools.base` has no package dependencies of
  its own, and by the time any of these run, the partially-initialised
  `core.tools` package can already serve its submodule.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.security.guard import Decision, Guard, Verdict

#: The single authority. Same file `core/tools/_validate()` and the daemon's
#: own `Guard` read.
PERMISSIONS = Path(__file__).resolve().parents[1] / "config" / "permissions.yaml"

TIERS = ("green", "amber", "red")

#: CONTRACT §6.2 actors a call can carry. Anything else collapses to
#: `schedule`, the most restrictive — the same rule `Executor._actor_of`
#: applies, restated here so a handler called directly still fails safe.
_ACTORS = ("human", "agent", "schedule")

_REQUIRED = object()


class CapabilityError(RuntimeError):
    """A capability that cannot be bound. Raised at import; the daemon does not start."""


# ─────────────────────────────────────────────────────────────────────────────
# parameters
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Param:
    """
    One typed argument. `kind` is `str`, `int`, `float` or `bool`; `choices`
    is a closed set (case-insensitive, normalised to the declared spelling);
    `lo`/`hi` bound a number inclusively. Out of range is a refusal, not a
    clamp — "set volume to two hundred" should be answered, not rounded.
    """

    name: str
    kind: type = str
    default: Any = _REQUIRED
    choices: tuple[Any, ...] = ()
    lo: float | None = None
    hi: float | None = None
    doc: str = ""

    @property
    def required(self) -> bool:
        return self.default is _REQUIRED

    def coerce(self, value: Any, tool: str) -> Any:
        from core.tools.base import ToolError

        def bad(what: str) -> ToolError:
            return ToolError(f"{tool}: {self.name} {what}",
                             self.doc or "Say it again with a value I can use.")

        if self.kind is bool:
            if isinstance(value, bool):
                out: Any = value
            elif isinstance(value, str) and value.strip().lower() in _TRUE_WORDS:
                out = True
            elif isinstance(value, str) and value.strip().lower() in _FALSE_WORDS:
                out = False
            elif isinstance(value, int) and value in (0, 1):
                out = bool(value)
            else:
                raise bad(f"must be on or off, not {value!r}")
        elif self.kind in (int, float):
            if isinstance(value, bool):
                raise bad("must be a number")
            try:
                num = float(value) if isinstance(value, (int, float, str)) else None
            except ValueError:
                num = None
            if num is None or num != num:      # None, or NaN
                raise bad(f"must be a number, not {value!r}")
            if self.kind is int:
                if num != int(num):
                    raise bad("must be a whole number")
                out = int(num)
            else:
                out = num
            if self.lo is not None and out < self.lo:
                raise bad(f"must be at least {self.lo:g}")
            if self.hi is not None and out > self.hi:
                raise bad(f"must be at most {self.hi:g}")
        else:
            if value is None:
                raise bad("is missing")
            out = str(value).strip()
            if not out and self.required:
                raise bad("is empty")

        if self.choices:
            table = {str(c).lower(): c for c in self.choices}
            key = str(out).strip().lower()
            if key not in table:
                raise bad(f"must be one of {', '.join(str(c) for c in self.choices)}")
            out = table[key]
        return out


_TRUE_WORDS = ("true", "on", "yes", "1", "muted")
_FALSE_WORDS = ("false", "off", "no", "0", "unmuted")


# ─────────────────────────────────────────────────────────────────────────────
# the capability
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Capability:
    """
    One thing Tessa can do to the machine. See the module docstring.

    `tier` is what the AUTHOR believes; the guard decides, and a mismatch
    refuses to bind. It is required rather than optional so that reading a
    module tells you its tier without opening permissions.yaml — and so that
    a wrong belief is caught at import, not discovered on the audit chain.
    """

    name: str                                    # tool name, e.g. "system.volume.set"
    capability: str                              # permissions.yaml key, e.g. "system.control"
    tier: str                                    # green | amber | red — checked, never trusted
    run: Callable[..., dict[str, Any]]           # (**params) -> facts
    params: tuple[Param, ...] = ()
    #: What he might say. Documentation for the report; routing is intents.py.
    phrasings: tuple[str, ...] = ()
    #: Formatted with the dict `run` returns.
    success: str = "Done, Emperor."
    #: Formatted with {reason} and {alternative}.
    failure: str = "That failed, sir. {reason} {alternative}"
    #: One audit line, formatted with the ARGS (what was asked, even on failure).
    audit: str = "{name}"
    #: What she reads back before an amber act, formatted with the ARGS. The
    #: SPECIFIC target, never the category. Defaults to `audit`.
    hold: str = ""
    note: str = ""
    #: True for anything that writes, deletes, renames or otherwise changes
    #: the filesystem. The guard's protected-path rule keys on it, and a
    #: mutating capability holds so that rule's CONFIRM can be answered.
    mutating: bool = False
    #: The parameter that names the path/target the guard should judge.
    target: str = ""
    #: Parameter names the APPROVAL CARD may not edit. See `ToolSpec.frozen`:
    #: the card exists so he can correct what a red action SAYS, never what it
    #: is aimed at. Declared here so a capability's target is frozen by the
    #: same declaration that creates it.
    frozen: tuple[str, ...] = ()
    #: OPTIONAL: name the target for the hold, from the validated args.
    #:
    #: A template can only repeat what he SAID — "set 4242 to high" — and for a
    #: capability addressed by NUMBER that is not enough to confirm against:
    #: the whole point of the hold is that he hears WHAT 4242 is before he
    #: says yes. `describe(args)` is called in place of the `hold` template,
    #: BEFORE the hold is raised, and must be read-only. It may raise
    #: `ToolError` to REFUSE outright (a Windows core process, the daemon
    #: itself) so a catastrophic target is refused before he is ever asked;
    #: any other failure falls back to the template, because a hold must
    #: never fail to hold. Absent on every existing capability — nothing
    #: changes for them.
    describe: Callable[[dict[str, Any]], str] | None = None

    def __post_init__(self) -> None:
        if not self.name or "." not in self.name:
            raise CapabilityError(f"capability name {self.name!r} must be dotted (e.g. system.volume.set)")
        if not self.capability:
            raise CapabilityError(f"{self.name}: `capability` (the permissions.yaml key) is required")
        if not callable(self.run):
            raise CapabilityError(f"{self.name}: `run` must be callable")
        names = [p.name for p in self.params]
        if len(names) != len(set(names)):
            raise CapabilityError(f"{self.name}: duplicate parameter names {names}")
        for reserved in ("provenance", "confirmed", "_approved_by_surface", "origin"):
            if reserved in names:
                raise CapabilityError(
                    f"{self.name}: {reserved!r} is an executor-owned flag, not a parameter")
        if self.target and self.target not in names:
            raise CapabilityError(f"{self.name}: target {self.target!r} is not a declared parameter")
        # A frozen name that is not a real parameter would freeze nothing and
        # read, in the source, exactly like protection that is present.
        stray = [f for f in self.frozen if f not in names]
        if stray:
            raise CapabilityError(
                f"{self.name}: frozen {stray} are not declared parameters — "
                f"a frozen name that matches nothing protects nothing")


# ─────────────────────────────────────────────────────────────────────────────
# tier resolution — the guard decides
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TierRuling:
    tier: str
    #: True when permissions.yaml has no line for this key. The ruling is
    #: still `red` — that is the guard's fail-closed answer — but the daemon's
    #: registration path refuses to ship it.
    untiered: bool
    reason: str
    decision: Decision = field(repr=False)


def resolve_tier(cap: Capability, guard: Guard) -> TierRuling:
    """
    Ask the existing guard. `schedule` is the most restrictive actor and the
    tier itself is actor-independent, so the answer is the key's tier — or
    red with "not listed" when the key is unknown, or DENY for a key on the
    `never` list, which cannot be bound at all.
    """
    d = guard.evaluate(cap.capability, "schedule")
    if d.verdict is Verdict.DENY:
        raise CapabilityError(f"{cap.name}: {d.reason}")
    untiered = d.tier == "red" and "not listed" in d.reason
    tier = d.tier if d.tier in TIERS else "red"
    return TierRuling(tier=tier, untiered=untiered, reason=d.reason, decision=d)


# ─────────────────────────────────────────────────────────────────────────────
# binding — Capability -> ToolSpec, tier enforced inside the handler
# ─────────────────────────────────────────────────────────────────────────────

class _Missing(dict):
    def __missing__(self, key: str) -> str:  # noqa: D105
        return "-"


def _hold_line(cap: Capability, args: dict[str, Any]) -> str:
    """
    What she reads back before an amber act.

    ONE MAPPING, ARGS LAST — NOT `_Missing(name=cap.name, **args)`.

    That form is a TypeError the moment a capability has a parameter called
    `name`: "got multiple values for keyword argument 'name'". The bare
    `except` then swallowed it and returned the bare TOOL name, so the hold for
    `system.chrome.open_personal_profile` said

        "Hold on, sir. system.chrome.open_personal_profile."

    instead of naming WHICH of his thirty Chrome profiles was about to be
    opened. The hold sentence is the entire point of an amber tier — it is what
    he confirms against — and it was withholding the one fact that matters.

    This is the SAME bug, in the same shape, that `Executor._audit_line`
    carried until the batch-2 proof caught it. I fixed that copy and left this
    one, which is what happens when two functions do the same job in two files:
    found 2026-09-08 by the profile proof, for exactly the capability whose
    target had to be spoken aloud.
    """
    if cap.describe is not None:
        from core.tools.base import ToolError

        try:
            return str(cap.describe(dict(args)))
        except ToolError:
            # A REFUSAL, not a wording problem. The describer looked at the
            # target and will not have it held at all — it reaches him as the
            # tool's own failure line, before any "say it again".
            raise
        except Exception:  # noqa: BLE001
            pass    # the template below still holds; a hold must never fail to hold
    template = cap.hold or cap.audit
    try:
        return template.format_map(_Missing({"name": cap.name, **args}))
    except Exception:  # noqa: BLE001
        return cap.name


def _validate_args(cap: Capability, kw: dict[str, Any]) -> dict[str, Any]:
    from core.tools.base import ToolError

    declared = {p.name: p for p in cap.params}
    unknown = sorted(k for k in kw if k not in declared)
    if unknown:
        raise ToolError(f"{cap.name} does not take {', '.join(unknown)}",
                        f"It takes {', '.join(declared) or 'nothing'}.")
    out: dict[str, Any] = {}
    for p in cap.params:
        if p.name in kw and kw[p.name] is not None:
            out[p.name] = p.coerce(kw[p.name], cap.name)
        elif p.required:
            raise ToolError(f"{cap.name} needs {p.name}",
                            p.doc or f"Tell me the {p.name} and I will do it.")
        else:
            out[p.name] = p.default
    return out


def _actor_of(provenance: Any) -> str:
    return provenance if provenance in _ACTORS else "schedule"


def _make_handler(cap: Capability, tier: str, guard: Guard) -> Callable[..., dict[str, Any]]:
    """
    The handler the executor calls. Its SIGNATURE is what the executor reads
    to decide which flags to inject (`_with_provenance`, `execute_approved`),
    so it is set explicitly: the declared params, then `provenance`, then
    `confirmed` for anything that can hold, then `_approved_by_surface` for
    red only.
    """
    from core.tools.base import ToolError, ToolHold

    wants_provenance = "provenance" in inspect.signature(cap.run).parameters
    # A capability whose `run` declares `_approved_by_surface` is handed the
    # REAL, already-verified value — never a fabricated True.
    #
    # THIS EXISTS BECAUSE HARD-CODING IT LAUNDERS THE APPROVAL. `system.x.
    # post_reply` calls `x_tools.reply`, which does its own independent check
    # for the flag; if the capability passes a literal `True` down, that second
    # refusal can never fire and the "two independent gates" it claims are one
    # gate wearing two hats. Threading the real value keeps the tool's own
    # check honest, so a direct call that skipped the card is still refused one
    # layer further in. Signature-based, exactly like `provenance` above, so a
    # capability that does not ask for it is never handed an unexpected kwarg.
    wants_approval = "_approved_by_surface" in inspect.signature(cap.run).parameters
    # A capability that declares `_request_id` is told WHICH approval is running
    # it. Injected the same way, for the same reason: a scheduled post needs to
    # name the card that authorised it, and a value the handler cannot see is a
    # provenance claim it cannot make. Empty on a non-approval path.
    wants_request = "_request_id" in inspect.signature(cap.run).parameters
    can_hold = tier != "green" or cap.mutating

    def handler(**kw: Any) -> dict[str, Any]:
        # The three executor-owned flags. They are popped BEFORE validation so
        # a capability can never declare them as parameters by accident, and
        # they arrive only from the executor: a call's args are stripped of
        # these keys at dispatch, then re-supplied by signature.
        provenance = kw.pop("provenance", "schedule")
        confirmed = bool(kw.pop("confirmed", False))
        approved = bool(kw.pop("_approved_by_surface", False))
        request_id = str(kw.pop("_request_id", "") or "")
        args = _validate_args(cap, kw)
        actor = _actor_of(provenance)

        # THE EXISTING GUARD, AT CALL TIME. Tier was fixed at bind from this
        # same guard; asking again here adds the parts that depend on the
        # call — the actor, and the protected-path rule for a mutating
        # capability with a target — and the `never` list, which no flag
        # overrides.
        target = None
        if cap.target and args.get(cap.target) not in (None, ""):
            target = str(args[cap.target])
        decision = guard.evaluate(cap.capability, actor, target, mutating=cap.mutating)  # type: ignore[arg-type]
        if decision.verdict is Verdict.DENY:
            raise ToolError(f"{cap.name} is permanently disabled: {decision.reason}",
                            "No approval can enable it.")

        if tier == "red":
            # ONLY the card. `Executor._dispatch_registry` never calls a red
            # handler — it raises a permission request first — and the only
            # place `_approved_by_surface=True` is ever passed is
            # `execute_approved`, after the owner decided on a surface he can
            # see. A model's args cannot carry it: the key is stripped.
            if not approved:
                raise ToolError(f"{cap.name} is red-tier and runs only from the approval card",
                                "Approve it on the card and I will do it.")
        elif tier == "amber":
            # A STATE CHANGE, NOT A DESTRUCTION — but never unattended.
            # permissions.yaml: "unattended ONLY inside a job the owner
            # explicitly authorised", and no job system exists yet. The guard
            # says CONFIRM for a non-human actor, and the confirmation ledger
            # cannot prove an owner's "yes" for one (an agent repeating its
            # own call would match its own hold), so a non-human origin is
            # refused outright until a job grant can be checked here.
            if actor != "human":
                raise ToolError(f"{cap.name} is amber-tier and will not run unattended for {actor}",
                                "Ask me yourself and I will hold it for your yes.")
            if not confirmed:
                raise ToolHold(_hold_line(cap, args))
        else:
            # GREEN runs — unless the guard's protected-path rule intervened.
            if decision.verdict is Verdict.CONFIRM and not confirmed:
                raise ToolHold(f"{_hold_line(cap, args)} ({decision.reason})")

        if wants_provenance:
            args["provenance"] = actor
        if wants_approval:
            args["_approved_by_surface"] = approved
        if wants_request:
            args["_request_id"] = request_id
        result = cap.run(**args)
        if not isinstance(result, dict):
            raise ToolError(f"{cap.name} returned {type(result).__name__} instead of facts",
                            "That is a bug in the capability, not in what you asked.")
        return result

    sig: list[inspect.Parameter] = []
    KW = inspect.Parameter.KEYWORD_ONLY
    for p in cap.params:
        sig.append(inspect.Parameter(
            p.name, KW, default=inspect.Parameter.empty if p.required else p.default))
    sig.append(inspect.Parameter("provenance", KW, default="schedule"))
    if can_hold:
        sig.append(inspect.Parameter("confirmed", KW, default=False))
    if tier == "red":
        sig.append(inspect.Parameter("_approved_by_surface", KW, default=False))
        sig.append(inspect.Parameter("_request_id", KW, default=""))
    handler.__signature__ = inspect.Signature(sig)  # type: ignore[attr-defined]
    handler.__name__ = f"capability_{cap.name.replace('.', '_')}"
    handler.__doc__ = f"{cap.name} ({tier}, {cap.capability}). Tier enforced here; see core/system/capability.py."
    handler.__wrapped_capability__ = cap  # type: ignore[attr-defined]
    return handler


def bind(cap: Capability, guard: Guard | None = None) -> Any:
    """
    Capability -> ToolSpec. Raises `CapabilityError` when the module's tier
    disagrees with permissions.yaml. An UNLISTED key binds RED (see module
    docstring) — `bound_specs()` refuses to register those; this function
    still returns the red spec so a caller can see what the guard ruled.
    """
    from core.tools.base import ToolSpec

    guard = guard or Guard(PERMISSIONS)
    ruling = resolve_tier(cap, guard)
    note = cap.note
    if ruling.untiered:
        note = (f"UNTIERED -> RED. {ruling.reason}. Add {cap.capability!r} to "
                f"core/config/permissions.yaml." + (f" {cap.note}" if cap.note else ""))
    elif cap.tier != ruling.tier:
        raise CapabilityError(
            f"{cap.name}: module declares tier {cap.tier!r} but permissions.yaml says "
            f"{ruling.tier!r} for {cap.capability!r} — permissions.yaml is the authority; "
            f"fix the module or the file, never both silently")
    tier = ruling.tier
    return ToolSpec(
        name=cap.name, tier=tier, capability=cap.capability,
        handler=_make_handler(cap, tier, guard),
        phrasings=tuple(cap.phrasings), success=cap.success, failure=cap.failure,
        holds=(tier != "green" or cap.mutating), audit=cap.audit, note=note,
        frozen=tuple(cap.frozen or ()),
    )


# ─────────────────────────────────────────────────────────────────────────────
# discovery and registration
# ─────────────────────────────────────────────────────────────────────────────

def discover(package: str = "core.system.abilities") -> list[Capability]:
    """
    Import every module under `package` and collect its `CAPABILITIES`.
    Sorted by module name so registration order is deterministic.
    """
    pkg = importlib.import_module(package)
    caps: list[Capability] = []
    seen: dict[str, str] = {}
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{package}.{info.name}")
        found = getattr(mod, "CAPABILITIES", None)
        if not found:
            raise CapabilityError(f"{mod.__name__} exports no CAPABILITIES")
        for cap in found:
            if not isinstance(cap, Capability):
                raise CapabilityError(f"{mod.__name__}: {cap!r} is not a Capability")
            if cap.name in seen:
                raise CapabilityError(f"{cap.name} is declared twice: {seen[cap.name]} and {mod.__name__}")
            seen[cap.name] = mod.__name__
            caps.append(cap)
    return caps


def bound_specs(guard: Guard | None = None, *, strict: bool = True) -> list[Any]:
    """
    Every discovered capability, bound. THIS IS WHAT core/tools/__init__.py
    CALLS. With `strict` (the daemon's setting) an unlisted key is a
    `CapabilityError` naming the missing permissions.yaml line, so the daemon
    refuses to start rather than shipping a capability nobody classified.
    Without it, the unlisted ones come back bound RED for inspection.
    """
    guard = guard or Guard(PERMISSIONS)
    caps = discover()
    specs = []
    missing = []
    for cap in caps:
        ruling = resolve_tier(cap, guard)
        if ruling.untiered:
            missing.append(f"{cap.name}: {cap.capability!r} is not in permissions.yaml")
        specs.append(bind(cap, guard))
    if strict and missing:
        raise CapabilityError("core/system: capabilities without a tier line "
                              "(add each to core/config/permissions.yaml):\n  " + "\n  ".join(missing))
    return specs


__all__ = ["Capability", "CapabilityError", "Param", "PERMISSIONS", "TierRuling",
           "bind", "bound_specs", "discover", "resolve_tier"]
