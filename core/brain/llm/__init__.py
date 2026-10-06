"""
core/brain/llm/ — one interface, three engines, chosen in settings.yaml.

    brain:
      engine: gemini | anthropic | local

Changing that word is the whole switch. No code moves, nothing is imported
conditionally at the call site, and every caller keeps talking to `LLMAdapter`.

THE ENGINE IS NEVER SUBSTITUTED SILENTLY, AND THAT IS THE POINT OF THIS FILE

`make_engine()` builds what settings.yaml asked for and returns it EVEN WHEN IT
IS NOT USABLE. It does not helpfully fall back to a working one.

That looks unfriendly and it is deliberate. A brain that quietly answers from a
different model is lying about what answered him — the same objection as an
Opus-to-Sonnet downgrade nobody was told about. An unusable engine reports
itself through `available`, and the first call raises `LLMUnavailable` carrying
a sentence she SAYS OUT LOUD. He hears "my thinking brain is not connected"
rather than getting a worse answer he has no way to attribute.

⚠ THE QUOTA FALLBACK IS THE ONE EXCEPTION, AND IT IS NOT SILENT

`brain.fallback` wraps the selected engine in `FallbackLLM` so a SPENT FREE-
TIER QUOTA — and nothing else — can be answered by the local model instead of
stopping her for the day. Read `fallback.py` for why that is not the silent
substitution this docstring forbids: only a typed `RateLimited` triggers it
(never an error, so bugs stay visible), `name` reports whichever engine will
actually answer so the health frame and the logs say so by themselves, and
anything quality-critical — X drafting above all — STOPS AND ASKS rather than
writing in his voice on a 0.5B model.

An UNUSABLE engine is still never replaced: the wrapper is built only when both
sides are usable, and a broken primary raises exactly as before. Quota is not
brokenness.

`describe_engines()` exists so the daemon can log all three at boot: which one
is selected, and whether each is usable. That line is how he finds out the key
is missing at start-up rather than in the middle of asking a question.
"""

from __future__ import annotations

from typing import Any

from .base import (
    QUALITIES,
    QUALITY_CRITICAL,
    QUALITY_ROUTINE,
    Completion,
    LLMAdapter,
    LLMUnavailable,
    Message,
    RateLimited,
    ToolDef,
    Usage,
)
from .fallback import DegradedRefusal, FallbackLLM

ENGINES = ("gemini", "anthropic", "local")


def _build(name: str, cfg: dict[str, Any]) -> LLMAdapter:
    """One engine, exactly as named. The switch, with no policy in it."""
    if name == "gemini":
        from .gemini import GeminiLLM

        return GeminiLLM(cfg)
    if name == "anthropic":
        from .anthropic_llm import AnthropicLLM

        return AnthropicLLM(cfg)
    if name == "local":
        from .local import LocalLLM

        return LocalLLM(cfg)

    raise ValueError(
        f"brain.engine is {name!r}; it must be one of {', '.join(ENGINES)}. "
        f"Refusing to guess — picking one for him would be the silent "
        f"substitution this module exists to prevent."
    )


def make_engine(settings: dict[str, Any] | None = None, *,
                fallback: bool | None = None,
                log: Any = None) -> LLMAdapter:
    """
    Build the engine named in `brain.engine`, wrapped for quota fallback when
    `brain.fallback.enabled` allows it. Never substitutes a DIFFERENT engine
    for a broken one — see the module docstring.

    `fallback=False` forces the bare engine. `describe_engines()` uses that so
    the boot report describes the three real engines rather than a wrapper, and
    a proof can get at the unwrapped one.

    THE WRAPPING HAPPENS HERE, in the existing selector, rather than in a new
    router beside it. Everything that already builds a brain — `core/server.py`
    at boot, `core/system/abilities/x_draft.py` per draft — gets the fallback
    without being edited, and there is still exactly one place that decides
    what answers.
    """
    brain = ((settings or {}).get("brain") or {})
    name = str(brain.get("engine", "gemini")).strip().lower()
    primary = _build(name, brain.get(name) or {})

    fb = (brain.get("fallback") or {})
    want = bool(fb.get("enabled", True)) if fallback is None else bool(fallback)
    if not want:
        return primary

    backup_name = str(fb.get("backup", "local")).strip().lower()
    if backup_name == name or backup_name not in ENGINES:
        # Nothing to fall back TO. A local primary already has no cloud quota
        # to spend, and an unknown backup name is not something to guess at.
        return primary

    try:
        backup = _build(backup_name, brain.get(backup_name) or {})
        usable, why = backup.available
    except Exception as exc:  # noqa: BLE001
        usable, why, backup = False, f"{type(exc).__name__}: {exc}", None

    if not usable or backup is None:
        # ⚠ A MISSING BACKUP MUST NOT BREAK THE GOOD BRAIN. She runs exactly as
        # she did before this feature existed: primary only, and a quota
        # refusal is spoken rather than absorbed.
        if log:
            log(f"brain: fallback disabled — {backup_name} unusable ({why})")
        return primary

    return FallbackLLM(primary, backup, cfg=fb, log=log)


def describe_engines(settings: dict[str, Any] | None = None) -> list[tuple[str, bool, str, bool]]:
    """
    (engine, is_selected, why-not-or-empty, usable) for every engine.

    Constructing an adapter is cheap for all three — none of them loads a model
    or opens a socket in `__init__`, and `available` is a key check plus an
    import check. So the daemon can report the truth about all three at boot for
    about a millisecond.
    """
    brain = ((settings or {}).get("brain") or {})
    selected = str(brain.get("engine", "gemini")).strip().lower()
    out = []
    for name in ENGINES:
        try:
            # `fallback=False`: this reports the THREE ENGINES, not the wrapper.
            # A boot line saying "gemini: usable" must mean gemini, or the one
            # report that tells him his key is missing would be answered by the
            # backup and say everything is fine.
            adapter = make_engine({"brain": {"engine": name, name: brain.get(name) or {}}},
                                  fallback=False)
            usable, why = adapter.available
        except Exception as exc:  # noqa: BLE001
            usable, why = False, f"{type(exc).__name__}: {exc}"
        out.append((name, name == selected, why, usable))
    return out


__all__ = [
    "Completion", "DegradedRefusal", "ENGINES", "FallbackLLM", "LLMAdapter",
    "LLMUnavailable", "Message", "QUALITIES", "QUALITY_CRITICAL",
    "QUALITY_ROUTINE", "RateLimited", "ToolDef", "Usage", "describe_engines",
    "make_engine",
]
