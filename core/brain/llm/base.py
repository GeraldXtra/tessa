"""
core/brain/llm/base.py — the interface both brains implement (spec §8).

Modelled on core/voice/tts/base.py, which exists for exactly this reason: Piper
local and ElevenLabs cloud behind one adapter, chosen in settings.yaml. Same
shape here — a local model runs today, Anthropic drops in behind one line the
day there is credit.

STREAMING IS THE PRIMARY METHOD, not a convenience.

Piper synthesises sentence by sentence, so Tessa can begin speaking as soon as
the model has produced one sentence — she does not have to wait for the whole
answer. An interface whose primary call returns a completed string makes that
impossible by construction and no amount of downstream work recovers it. So
`stream()` yields text deltas and `complete()` is the wrapper over it.

An engine that cannot stream implements `stream()` by yielding once. That is
honest: the caller sees a single late chunk and the measured time-to-first-token
tells the truth about the engine rather than hiding it behind a streaming-shaped
API.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    role: Role
    content: str


@dataclass
class ToolDef:
    """Tool NAME + JSON-schema ARGS. Never a command string — invariant 4."""
    name: str
    description: str
    input_schema: dict[str, Any]
    tier: str = "green"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    #: Real cost in NGN, filled by the engine that knows its own rates.
    cost_ngn: float = 0.0
    #: Which model actually answered. Set even on fallback, because a silent
    #: downgrade is a lie about what answered him.
    model: str = ""
    fell_back_from: str | None = None


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)
    #: Wall clock to the FIRST token. This is the number that decides whether
    #: she can start speaking early; total time does not.
    first_token_ms: float = 0.0
    total_s: float = 0.0
    chunks: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""

    @property
    def streamed(self) -> bool:
        return self.chunks > 1

    @property
    def tokens_per_s(self) -> float:
        return self.usage.output_tokens / self.total_s if self.total_s > 0 else 0.0


class LLMUnavailable(RuntimeError):
    """
    The engine cannot answer, and she must SAY SO rather than degrade quietly.

    Carries a spoken sentence because "handle the error" at the call site
    reliably becomes silence, and silence is indistinguishable from a crash.
    """

    def __init__(self, message: str, spoken: str) -> None:
        super().__init__(message)
        self.spoken = spoken


class LLMAdapter(ABC):
    """Spec §8. Local today; Anthropic behind the same interface."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(usable, why-not). Checked BEFORE a turn, so she can say it early."""

    @abstractmethod
    def stream(
        self,
        system: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        max_tokens: int = 1024,
        thinking: bool = False,
    ) -> Iterator[str]: ...

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        max_tokens: int = 1024,
        thinking: bool = False,
    ) -> Completion:
        """Consume `stream()`, timing the first token separately from the rest."""
        t0 = time.perf_counter()
        first: float | None = None
        parts: list[str] = []
        for delta in self.stream(system, messages, tools=tools,
                                 max_tokens=max_tokens, thinking=thinking):
            if first is None:
                first = (time.perf_counter() - t0) * 1000.0
            parts.append(delta)
        total = time.perf_counter() - t0
        text = "".join(parts)
        return Completion(
            text=text.strip(),
            first_token_ms=first if first is not None else float("nan"),
            total_s=total,
            chunks=len(parts),
            usage=self._usage_for(text),
        )

    def _usage_for(self, text: str) -> Usage:
        return Usage(output_tokens=max(1, len(text) // 4), model=self.name)
