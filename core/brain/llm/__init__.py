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

`describe_engines()` exists so the daemon can log all three at boot: which one
is selected, and whether each is usable. That line is how he finds out the key
is missing at start-up rather than in the middle of asking a question.
"""

from __future__ import annotations

from typing import Any

from .base import (
    Completion,
    LLMAdapter,
    LLMUnavailable,
    Message,
    ToolDef,
    Usage,
)

ENGINES = ("gemini", "anthropic", "local")


def make_engine(settings: dict[str, Any] | None = None) -> LLMAdapter:
    """Build the engine named in `brain.engine`. Never substitutes another."""
    brain = ((settings or {}).get("brain") or {})
    name = str(brain.get("engine", "gemini")).strip().lower()
    cfg = (brain.get(name) or {})

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
            adapter = make_engine({"brain": {"engine": name, name: brain.get(name) or {}}})
            usable, why = adapter.available
        except Exception as exc:  # noqa: BLE001
            usable, why = False, f"{type(exc).__name__}: {exc}"
        out.append((name, name == selected, why, usable))
    return out


__all__ = [
    "Completion", "ENGINES", "LLMAdapter", "LLMUnavailable", "Message",
    "ToolDef", "Usage", "describe_engines", "make_engine",
]
