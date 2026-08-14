"""
core/brain/llm/anthropic_llm.py — the Anthropic brain. WRITTEN, NOT EXERCISED.

⚠️ HONEST STATUS, stated here rather than in a report someone may not read:

There is no ANTHROPIC_API_KEY on this machine and the `anthropic` SDK is not
installed. The ONLY path in this file that has ever executed is the no-key
refusal. Everything below the availability check — streaming, tool use, thinking
budgets, token accounting, the Opus→Sonnet fallback — is UNTESTED CODE.

An untested adapter is a promise, not a capability, and it should be read as
one. The first thing to do when a key exists is not to trust this file; it is to
run one call through each path and find what is wrong with it.

MODEL POLICY (Gerald's explicit choice, IDs in settings.yaml):

  claude-opus-5              EVERY substantive answer — summarising, teaching,
                             mathematics, code, judgement, drafting.
  claude-haiku-4-5-...       CLASSIFICATION ONLY. One narrow job: which tool an
                             utterance means when the local router misses. It
                             never writes an answer he hears.
  claude-sonnet-5            FALLBACK ONLY, and the downgrade is SPOKEN. A
                             silent downgrade is a lie about what answered him.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

from .base import Completion, LLMAdapter, LLMUnavailable, Message, ToolDef, Usage


class AnthropicLLM(LLMAdapter):
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg or {}
        models = self.cfg.get("models", {}) or {}
        self.model_main = str(models.get("main", "claude-opus-5"))
        self.model_classify = str(models.get("classify", "claude-haiku-4-5-20251001"))
        self.model_fallback = str(models.get("fallback", "claude-sonnet-5"))
        self.api_key = os.environ.get(str(self.cfg.get("api_key_env", "ANTHROPIC_API_KEY")), "")
        self._client: Any = None
        self.last_fallback: str | None = None

    @property
    def name(self) -> str:
        return f"anthropic:{self.model_main}"

    @property
    def available(self) -> tuple[bool, str]:
        """
        Checked BEFORE a turn so she can say it up front rather than failing
        halfway through an answer he is already waiting for.
        """
        if not self.api_key:
            return False, "no API key is configured"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "the anthropic SDK is not installed"
        return True, ""

    def _require(self) -> Any:
        ok, why = self.available
        if not ok:
            # LOUD and CLEAN: not a crash, not a hang, and explicitly NOT a
            # silent fallback to the local brain. Choosing her brain is Gerald's
            # decision and swapping it without telling him would make every
            # answer's provenance a guess.
            raise LLMUnavailable(
                f"anthropic brain unavailable: {why}",
                spoken=(
                    f"My Anthropic brain is not configured, sir. "
                    f"{why.capitalize()}. I am running on the local model instead, "
                    f"and you should know the difference."
                ),
            )
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    # ── UNTESTED BELOW THIS LINE ─────────────────────────────────────────────

    def stream(
        self,
        system: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        max_tokens: int = 1024,
        thinking: bool = False,
    ) -> Iterator[str]:
        client = self._require()
        kwargs: dict[str, Any] = {
            "model": self.model_main,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ]
        if thinking:
            # Extended thinking. The budget must leave room for the answer, so
            # it is a fraction of max_tokens rather than a fixed number that
            # could exceed it.
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": max(1024, max_tokens // 2)}

        try:
            yield from self._stream_with(client, kwargs)
            self.last_fallback = None
        except Exception as exc:  # noqa: BLE001
            if not self._is_retryable(exc):
                raise
            # FALLBACK — and it is announced, not hidden.
            self.last_fallback = self.model_main
            kwargs["model"] = self.model_fallback
            kwargs.pop("thinking", None)
            yield (f"[Opus was unavailable, so this is {self.model_fallback}.] ")
            yield from self._stream_with(client, kwargs)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        return any(k in text for k in
                   ("overloaded", "rate", "429", "529", "timeout", "credit", "capacity"))

    @staticmethod
    def _stream_with(client: Any, kwargs: dict[str, Any]) -> Iterator[str]:
        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text

    def classify(self, utterance: str, options: list[str]) -> str:
        """
        Haiku's ONLY job. It never writes an answer Gerald hears — this returns
        a tool name from a fixed list, which is a ₦0.05 decision that Opus rates
        would buy nothing on.
        """
        client = self._require()
        resp = client.messages.create(
            model=self.model_classify,
            max_tokens=32,
            system=("Return exactly one option from the list and nothing else. "
                    "If none fit, return NONE."),
            messages=[{"role": "user",
                       "content": f"Options: {', '.join(options)}\n\nUtterance: {utterance}"}],
        )
        return resp.content[0].text.strip() if resp.content else "NONE"
