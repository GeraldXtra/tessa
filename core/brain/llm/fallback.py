"""
core/brain/llm/fallback.py — keep her working past Gemini's free tier, without
quietly handing him a worse answer.

────────────────────────────────────────────────────────────────────────────────
THE PROBLEM THIS SOLVES, AND THE ONE IT MUST NOT CREATE

Every question the router cannot answer is one Gemini call, and the free tier
runs out. When it does, she currently stops thinking for the rest of the day —
measured, not theoretical: it killed a proof mid-run.

The obvious fix is "use the local model when Gemini fails". That fix is wrong
in two specific ways, and this file is mostly the two guards against them:

  1. FALLING BACK ON EVERY ERROR HIDES BUGS. A rejected key, a DNS failure, a
     timeout, a crash in this repo — if any of those quietly demote her to the
     local model, the symptom is "she seems a bit stupid today" and the cause
     is invisible for a week. So ONLY `RateLimited` triggers a fallback.
     Everything else propagates and he hears what actually went wrong.

  2. FALLING BACK ON HARD WORK HANDS HIM A WORSE TWEET HE CANNOT SPOT. The
     local model is Qwen2.5-0.5B on two cores. It is genuinely fine at "what
     is 17 times 3" and genuinely bad at writing a post in his voice — and a
     bad tweet he did not know was the weak model is worse than no tweet,
     because it goes out under his name. So a `critical` call under a spent
     quota STOPS AND ASKS instead of answering.

Routine work keeps flowing. Quality work waits for him. That asymmetry is the
whole design.

────────────────────────────────────────────────────────────────────────────────
WHAT IS AND IS NOT A DOWNGRADE

`llm/__init__.py` says an engine is never substituted silently, and this file
does not weaken that — it satisfies it. Every substitution here is:

  * TYPED   — only a quota refusal, never an error;
  * VISIBLE — `name` reports the engine that will answer NOW, so the daemon's
              health frame (`brainEngine`), the voice loop's stage log and a
              draft's `model` field all say "local:Qwen…" with no extra wiring;
  * ASKED   — for anything quality-critical, unless he said otherwise;
  * TEMPORARY — the next call after the recheck window goes back to Gemini.

────────────────────────────────────────────────────────────────────────────────
⚠ MID-STREAM IS NEVER SPLICED

If Gemini 429s AFTER it has already yielded text, this does NOT continue the
answer on the local model. Two models writing one paragraph produces something
neither of them would have said, in a voice that is nobody's, and it would be
undetectable in the transcript. Tokens already delivered means the failure
propagates. Only a call that has produced nothing can be re-routed.

────────────────────────────────────────────────────────────────────────────────
SAFETY IS NOT PART OF THE SWAP

This class chooses which model produces text. It does not touch the approval
card, the provenance fence, the audit chain or the tier table, and it cannot:
those live in `core/brain/executor.py`, `core/system/capability.py` and
`core/brain/provenance.py`, all downstream of a string this file returns. A
draft written by the local model reaches exactly the same red card, with the
same frozen arguments, and the same injection detection over the same fenced
source. A weaker writer is not a weaker gate.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator

from .base import (
    QUALITY_CRITICAL,
    LLMAdapter,
    LLMUnavailable,
    Message,
    RateLimited,
    ToolDef,
    Usage,
)

#: How long a per-minute 429 may hold the turn before she gives up waiting.
#: A voice turn that stalls for thirty seconds is a broken assistant even if it
#: eventually answers, so Google's own `retryDelay` is honoured only up to here.
TRANSIENT_MAX_WAIT_S = 8.0

#: How many times a transient limit is re-tried on the good model before it is
#: treated as exhausted. One. A second wait costs more silence than the better
#: model is worth on a turn he is sitting through.
TRANSIENT_RETRIES = 1

#: After a daily exhaustion, how long before the NEXT REAL CALL tries Gemini
#: again. Half an hour: cheap enough to cost at most two probe calls an hour,
#: short enough that a quota that rolled over is picked up the same evening.
RECHECK_AFTER_S = 30 * 60

#: Bounds on anything Google's `retryDelay` asks for, so a malformed or hostile
#: number cannot strand her on the weak model for a week or spin her at 0s.
MIN_RECHECK_S = 60.0
MAX_RECHECK_S = 6 * 3600.0

#: What a quality-critical call does when the quota is spent.
ASK, ALLOW, NEVER = "ask", "local", "never"


class DegradedRefusal(LLMUnavailable):
    """
    "I can do this on the weak model, but you should know first."

    A subclass of `LLMUnavailable` on purpose: every call site in the daemon
    already catches that and SPEAKS `exc.spoken`, so this reaches him as a
    sentence through paths that were written before it existed — including
    `core/brain/x_voice.py`, which this round is not allowed to edit and does
    not need to be.
    """

    def __init__(self, message: str, spoken: str, *, backup: str) -> None:
        super().__init__(message, spoken)
        self.backup = backup


class FallbackLLM(LLMAdapter):
    """
    `primary` normally; `backup` while the primary's quota is spent.

    `clock` and `sleep` are injected for the same reason `ScheduleStore` takes
    a clock: a proof has to drive a daily quota window and a backoff without
    waiting half an hour or burning a real quota to get there.
    """

    def __init__(self, primary: LLMAdapter, backup: LLMAdapter, *,
                 cfg: dict[str, Any] | None = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] | None = None) -> None:
        self.primary = primary
        self.backup = backup
        self.cfg = cfg or {}
        self.clock = clock
        self.sleep = sleep
        self._log_to = log

        self.transient_max_wait_s = float(self.cfg.get("transient_max_wait_s",
                                                       TRANSIENT_MAX_WAIT_S))
        self.transient_retries = int(self.cfg.get("transient_retries", TRANSIENT_RETRIES))
        self.recheck_after_s = float(self.cfg.get("recheck_after_s", RECHECK_AFTER_S))
        drafting = str(self.cfg.get("drafting", ASK)).strip().lower()
        self.drafting = drafting if drafting in (ASK, ALLOW, NEVER) else ASK

        #: Epoch after which the primary is tried again. 0.0 = not exhausted.
        self.exhausted_until = 0.0
        self.exhausted_at = 0.0
        self.last_quota_id = ""
        #: One-shot consent for a single degraded critical call — his "do it
        #: anyway" for THIS draft, never a standing permission.
        self._degraded_once = False
        self.last_answered_by = ""
        self.backup_calls = 0
        self.fallbacks = 0
        self.transient_waits = 0
        self.recoveries = 0
        self.events: list[tuple[float, str]] = []

    # ── identity ─────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """
        WHO WILL ANSWER THIS TURN, not who was configured.

        This is the whole of requirement 1e and it costs no call-site changes:
        `core/server.py` already ships `brainEngine=getattr(self.brain,"name")`
        on the health frame, `core/voice/loop.py` already logs it at the top of
        every turn, and `core/brain/x_voice.py` already stamps it onto a draft
        as `Draft.model`. Making this property tell the truth makes all three
        tell the truth.
        """
        return self.backup.name if self.on_backup else self.primary.name

    @property
    def on_backup(self) -> bool:
        return bool(self.exhausted_until) and self.clock() < self.exhausted_until

    @property
    def available(self) -> tuple[bool, str]:
        """Whichever engine is answering right now."""
        return self.backup.available if self.on_backup else self.primary.available

    @property
    def calls(self) -> int:
        """Both engines. `core/server.py` reports this as `brainCalls`."""
        return int(getattr(self.primary, "calls", 0)) + self.backup_calls

    def describe(self) -> str:
        if not self.exhausted_until:
            return f"{self.primary.name} (fallback armed: {self.backup.name})"
        left = max(0.0, self.exhausted_until - self.clock())
        if left <= 0:
            return f"{self.primary.name} (retrying after quota, backup {self.backup.name})"
        return (f"{self.backup.name} — {self.primary.name} quota spent"
                f"{f' [{self.last_quota_id}]' if self.last_quota_id else ''}, "
                f"retrying in {left / 60:.0f} min")

    # ── his decision ─────────────────────────────────────────────────────────

    def allow_degraded_once(self) -> None:
        """
        His "draft it on the local model anyway", for ONE call.

        One-shot rather than a mode, because the answer to "is a weaker draft
        acceptable" is different for a throwaway reply and for the post that
        goes out under his name at 9am. A standing answer is available in
        settings (`brain.fallback.drafting: local`) and is his to set, with the
        cost written down beside it.
        """
        self._degraded_once = True

    # ── the tick ─────────────────────────────────────────────────────────────

    def _log(self, line: str) -> None:
        self.events.append((self.clock(), line))
        del self.events[:-64]
        if self._log_to:
            try:
                self._log_to(f"brain.fallback: {line}")
            except Exception:  # noqa: BLE001
                pass            # a logging failure must never break a turn

    def _enter_exhausted(self, exc: RateLimited) -> None:
        wait = exc.retry_after if exc.retry_after > 0 else self.recheck_after_s
        wait = max(MIN_RECHECK_S, min(MAX_RECHECK_S, wait))
        now = self.clock()
        self.exhausted_at = now
        self.exhausted_until = now + wait
        self.last_quota_id = exc.quota_id
        self.fallbacks += 1
        self._log(f"{self.primary.name} quota spent "
                  f"({exc.quota_id or 'unnamed quota'}); retrying in {wait / 60:.0f} min")

    def _recovered(self) -> None:
        self.exhausted_until = 0.0
        self.exhausted_at = 0.0
        self.last_quota_id = ""
        self.recoveries += 1
        self._log(f"{self.primary.name} answered again — back on the good model")

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
        kw: dict[str, Any] = {"tools": tools, "max_tokens": max_tokens,
                              "thinking": thinking, "json_object": json_object}
        attempts = self.transient_retries + 1

        # THE PROBE IS THE NEXT REAL CALL. Once the recheck window has passed,
        # `on_backup` goes False and the primary is simply tried again — a
        # dedicated probe call would spend quota to learn what the work itself
        # is about to tell us, and on a free tier that call is not free.
        if not self.on_backup:
            for attempt in range(attempts):
                yielded = 0
                try:
                    for delta in self.primary.stream(system, messages,
                                                     quality=quality, **kw):
                        yielded += 1
                        self.last_answered_by = self.primary.name
                        yield delta
                    if self.exhausted_until:
                        self._recovered()
                    return
                except RateLimited as exc:
                    if yielded:
                        # ⚠ MID-STREAM. Half an answer is already spoken or on
                        # screen; a second model finishing it would splice two
                        # voices into one paragraph. He gets the error.
                        self._log(f"429 after {yielded} chunks — not spliced, raising")
                        raise
                    last_attempt = attempt >= attempts - 1
                    if exc.transient and not last_attempt:
                        wait = min(exc.retry_after or 1.0, self.transient_max_wait_s)
                        self.transient_waits += 1
                        self._log(f"per-minute limit ({exc.quota_id or 'unnamed'}); "
                                  f"waiting {wait:.1f}s and trying {self.primary.name} again")
                        self.sleep(wait)
                        continue
                    # Either a daily cap, or a per-minute cap that did not
                    # clear on the retry. ESCALATION IS THE POINT: a body that
                    # said "transient" but keeps refusing is exhausted in every
                    # way that matters to the person waiting for an answer.
                    if exc.transient:
                        self._log("per-minute limit did not clear on retry — "
                                  "treating as exhausted")
                    self._enter_exhausted(exc)
                    break
                except LLMUnavailable:
                    # ⚠ NOT A QUOTA PROBLEM. A bad key, a timeout, a 500, a bug.
                    # Falling back here would hide it behind a weaker answer;
                    # he hears the real failure instead. This `raise` is the
                    # guard that keeps "fallback" from meaning "swallow".
                    raise

        # ── on the backup ────────────────────────────────────────────────────
        if quality != QUALITY_CRITICAL:
            yield from self._backup_stream(system, messages, kw, quality)
            return

        if self.drafting == NEVER:
            raise DegradedRefusal(
                f"{self.primary.name} quota spent and drafting on {self.backup.name} "
                f"is switched off",
                spoken=(f"{self._primary_word()}'s quota is spent, Emperor, and you have "
                        f"told me not to write for you on the local model. "
                        f"I will have it back {self._when_back()}."),
                backup=self.backup.name)

        if self.drafting == ALLOW or self._degraded_once:
            self._degraded_once = False
            self._log(f"degraded {quality} call allowed on {self.backup.name} "
                      f"({'settings' if self.drafting == ALLOW else 'his one-off yes'})")
            yield from self._backup_stream(system, messages, kw, quality)
            return

        # ASK. The default, and the one that keeps a weak tweet off his timeline.
        raise DegradedRefusal(
            f"{self.primary.name} quota spent; {quality} work needs his decision "
            f"before {self.backup.name} writes it",
            spoken=(f"{self._primary_word()}'s quota is spent, Emperor. I can write this "
                    f"on the local model, but it will not sound like you — say do it "
                    f"anyway, or wait and I will have the good one back {self._when_back()}."),
            backup=self.backup.name)

    def _backup_stream(self, system: str, messages: list[Message],
                       kw: dict[str, Any], quality: str) -> Iterator[str]:
        self.backup_calls += 1
        self.last_answered_by = self.backup.name
        self._log(f"answering a {quality} call on {self.backup.name}")
        yield from self.backup.stream(system, messages, quality=quality, **kw)

    def _primary_word(self) -> str:
        """"gemini:gemini-3.6-flash" is not a word she says out loud."""
        return self.primary.name.split(":", 1)[0].capitalize() or "The cloud model"

    def _when_back(self) -> str:
        left = max(0.0, self.exhausted_until - self.clock())
        if left <= 0:
            return "on your next question"
        if left < 90 * 60:
            return f"in about {max(1, round(left / 60))} minutes"
        return f"in about {left / 3600:.0f} hours"

    # ── attribution ──────────────────────────────────────────────────────────

    def _usage_for(self, text: str) -> Usage:
        """
        WHICH MODEL WROTE THIS, on the record.

        `Usage.fell_back_from` has existed since base.py was written, with the
        comment "set even on fallback, because a silent downgrade is a lie
        about what answered him". This is the first code that can fill it.
        """
        answered = self.last_answered_by or self.name
        fell_back = self.primary.name if answered == self.backup.name else None
        # ₦0 either way — the free tier costs nothing and local inference costs
        # nothing. Inventing a figure here would corrupt the budget ledger, the
        # same reasoning local.py gives for its own zero.
        return Usage(output_tokens=max(1, len(text) // 4), cost_ngn=0.0,
                     model=answered, fell_back_from=fell_back)
