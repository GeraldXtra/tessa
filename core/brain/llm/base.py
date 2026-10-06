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


class RateLimited(LLMUnavailable):
    """
    The provider refused for QUOTA reasons, specifically — and that word is
    doing all the work.

    ⚠ THIS TYPE IS THE ONLY THING THAT MAY TRIGGER A FALLBACK. Everything else
    an engine can raise — a timeout, a DNS failure, a rejected key, a 500, a
    bug in this repo — stays `LLMUnavailable` and reaches him as an error. A
    fallback layer that catches the base class would turn every bug into "she
    quietly used the weak model", which is how a broken key survives for a week
    looking like a slow day. Quota is a known, expected, non-defective answer;
    nothing else is.

    `transient` separates the two quotas that behave completely differently:

      * TRUE  — a per-minute cap. It clears on its own in seconds, so the right
                move is a short wait and one more try at the good model.
      * FALSE — a daily cap. Waiting eight seconds buys nothing; the model is
                gone until the window rolls over, and something else has to
                answer or she stops working for the day.

    `retry_after` is the provider's own number when it gave one, in seconds.
    `quota_id` is the provider's identifier for WHICH limit was hit, kept for
    the log so a wrong classification is diagnosable rather than mysterious.
    """

    def __init__(self, message: str, spoken: str, *, transient: bool,
                 retry_after: float = 0.0, quota_id: str = "") -> None:
        super().__init__(message, spoken)
        self.transient = bool(transient)
        self.retry_after = float(retry_after)
        self.quota_id = str(quota_id)


#: How much the answer's QUALITY matters, declared by the call site.
#:
#: ⚠ THE DEFAULT IS `critical`, AND THAT IS DELIBERATE. A call site that says
#: nothing gets the careful behaviour: under a spent quota it stops and asks
#: rather than quietly answering from a 0.5B model. Downgrading to `routine`
#: is an explicit act at a call site whose answer he can judge instantly — a
#: chat reply, a status line — where a weaker answer is visibly weaker and
#: costs him nothing but a re-ask.
#:
#: X DRAFTING NEVER DECLARES IT, so it is critical by construction. A tweet in
#: his voice is the one output he CANNOT judge as degraded, because he did not
#: write it and it goes out under his name.
QUALITY_ROUTINE = "routine"
QUALITY_CRITICAL = "critical"
QUALITIES = (QUALITY_ROUTINE, QUALITY_CRITICAL)


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
        quality: str = QUALITY_CRITICAL,
        json_object: bool = False,
    ) -> Iterator[str]:
        """
        `quality` is declared here so every engine ACCEPTS it and only the
        fallback wrapper ACTS on it. An engine answering directly has no second
        model to weigh it against, so it ignores the word — but the parameter
        has to exist on the interface, or a call site would have to know
        whether it is talking to a wrapped engine before it could say how much
        the answer matters. See `core/brain/llm/fallback.py`.

        `json_object` (intent round, 2026-09-22) asks for the answer as ONE
        JSON OBJECT and nothing else. An engine with a native mode for it
        (Gemini's `responseMimeType`) switches it on; one without ignores the
        flag and relies on the prompt having asked. Same rule as `quality`: on
        the interface so every engine accepts it, acted on only where it means
        something. The caller always parses defensively either way. (Not named
        `json`: that shadowed the stdlib module inside the Gemini engine and
        broke its SSE parsing on the first live call.)
        """

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        max_tokens: int = 1024,
        thinking: bool = False,
        quality: str = QUALITY_CRITICAL,
        json_object: bool = False,
    ) -> Completion:
        """Consume `stream()`, timing the first token separately from the rest."""
        t0 = time.perf_counter()
        first: float | None = None
        parts: list[str] = []
        for delta in self.stream(system, messages, tools=tools,
                                 max_tokens=max_tokens, thinking=thinking,
                                 quality=quality, json_object=json_object):
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
