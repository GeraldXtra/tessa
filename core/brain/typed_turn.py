"""
core/brain/typed_turn.py — a turn she takes from the keyboard.

════════════════════════════════════════════════════════════════════════════
WHY THIS IS A SIBLING OF THE VOICE LOOP AND NOT A SHORTCUT PAST IT

A typed turn must do everything a spoken one does. The router first, the brain
for what the router cannot place, the same conversation memory, the same
permission tiers, the same injection fence. The ONLY things it drops are the
two that are genuinely about sound: speech-to-text on the way in, and Piper on
the way out.

That is the whole safety argument. A typed request that skipped the fence or
the tiers would be a way around her permission model that exists only because
the input arrived from a keyboard, and the model has no business recognising
that distinction.

WHY IT DOES NOT LIVE INSIDE VoiceLoop.stop()

`stop()` is one method that captures audio, transcribes it, routes, executes
and synthesises. Threading a "no audio" flag through it would put a second
mode inside the most timing-sensitive code in the project, where every branch
is already load-bearing. This module runs the SAME components — the caller
passes in the very Router and Executor the voice loop uses — so the fence, the
confirmation holds and the ledger are literally shared objects, not copies.

That sharing is what makes ruling 2 true at the level that matters: he can
speak a turn, type a follow-up, and answer "yes" to a hold he opened by voice.
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.brain.conversation import CLEARED_LINES, is_clear_request
from core.brain.repair import repair, strip_wake_name
from core.brain.router import Intent, Routed, action_failed, _pick
from core.brain.tools_local import ToolCall
from core.brain.unrouted import (Disposition, action_refusal, classify,
                                 unresolved_refusal)

#: Same ceiling the voice path uses. A typed answer has more room on screen
#: than a spoken one has patience, but the model is the same and so is the bill.
BRAIN_MAX_TOKENS = 700


@dataclass
class TypedTurn:
    """What he typed, what she answered, and how she got there."""

    heard: str
    said: str
    intent: str = "unrouted"
    #: Tools that actually ran, for the audit entry.
    tools: list[str] = field(default_factory=list)
    #: Injection patterns that fired on anything loaded this turn.
    flagged: list[str] = field(default_factory=list)
    #: True when the text was a "I'm done for now" style close.
    ends_session: bool = False
    #: True when a red-tier action was refused and is waiting on the Orb.
    awaiting_approval: bool = False
    ms: float = 0.0


def run_typed_turn(
    text: str,
    *,
    router: Any,
    executor: Any,
    brain: Any | None,
    conversation: Any,
    on_state: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> TypedTurn:
    """
    Text in, text out. Never raises: a failure is an answer in her register.

    The ordering is a deliberate mirror of `VoiceLoop.stop()`. Read them side
    by side — if one grows a step, the other needs it too.
    """
    t0 = time.monotonic()
    said_log = log or (lambda _m: None)
    typed = (text or "").strip()
    if not typed:
        return TypedTurn(heard="", said="", intent="empty", ms=0.0)

    def state(s: str) -> None:
        if on_state is not None:
            try:
                on_state(s)
            except Exception:  # noqa: BLE001 — telemetry must never break a turn
                pass

    state("thinking")

    # ── 1. A BARE "yes"/"no" ANSWERS A HOLD HE MAY HAVE OPENED BY VOICE ──────
    #
    # Checked first, exactly as the voice loop does. This is the single most
    # visible piece of ruling 2: the hold lives on the shared Executor, so a
    # confirmation he was asked for out loud can be answered from the keyboard.
    try:
        answered = executor.answer_confirmation(typed)
    except Exception as exc:  # noqa: BLE001
        answered = None
        said_log(f"typed: answer_confirmation failed {type(exc).__name__}: {exc}")
    if answered is not None:
        conversation.add("user", typed)
        conversation.add("assistant", answered)
        return TypedTurn(heard=typed, said=answered, intent="tool",
                         ms=(time.monotonic() - t0) * 1000)

    # ── 2. "forget that" CLEARS THE THREAD ───────────────────────────────────
    if is_clear_request(typed):
        n = conversation.clear()
        line = _pick(CLEARED_LINES)
        return TypedTurn(heard=typed, said=line, intent="memory",
                         ms=(time.monotonic() - t0) * 1000)

    # ── 3. THE FENCE IS PER TURN ─────────────────────────────────────────────
    #
    # Cleared at the START, for the reason recorded at length in loop.py: the
    # danger is external content influencing an action IN THE SAME exchange,
    # and holding the flag across turns disarmed her hands for a whole session
    # after one web search.
    session = getattr(executor, "session", None)
    if session is not None and getattr(session, "external_content_in_context", 0):
        session.clear_external()
        executor.last_injection = None

    # ── 4. THE ROUTER FIRST. Free, instant, and handles most of what he types.
    routed: Routed = router.route(typed)

    # ── 5. UNROUTED IS A HANDOFF, NOT AN ANSWER ──────────────────────────────
    if routed.intent is Intent.UNROUTED and routed.score == 0.0:
        disposition = classify(typed)
        if disposition is Disposition.FRAGMENT:
            # TYPED FRAGMENTS ARE NOT NOISE. This is the one place the typed
            # path deliberately differs from voice: a stray "the" from Whisper
            # is a microphone artefact and rightly ignored, but a stray "the"
            # in the input box was TYPED and sending it was a decision. Falling
            # silent there would look broken, so it goes to the brain like any
            # other short question.
            if brain is not None:
                routed.speech = _ask_brain(typed, brain, conversation, said_log)
            else:
                routed.speech = action_refusal()
        elif disposition is Disposition.ACTION:
            routed.speech = action_refusal()
        elif disposition is Disposition.UNRESOLVED:
            routed.speech = unresolved_refusal(typed)
        elif disposition is Disposition.LIVE_DATA:
            query = repair(typed)[0] or typed
            routed = Routed(Intent.TOOL, "", score=1.0,
                            calls=[ToolCall(name="web.search", args={"query": query},
                                            speech="Looking it up.")])
        elif brain is not None:
            routed.speech = _ask_brain(typed, brain, conversation, said_log)

    # ── 6. THE TOOL PATH. Every tool speaks, success or failure. ─────────────
    tools: list[str] = []
    awaiting = False
    if routed.calls:
        state("working")
        results: list[str] = []
        for call in routed.calls:
            try:
                results.append(executor.run(call))
                tools.append(call.name)
            except Exception as exc:  # noqa: BLE001
                results.append(action_failed(
                    f"{type(exc).__name__}: {exc}", "Say it again and I will retry."))
                tools.append(f"{call.name}!failed")
        routed.speech = " ".join(r for r in results if r).strip()
        # A red-tier refusal is recognisable by the gate having raised a
        # request; the executor turns that into her refusal sentence.
        awaiting = bool(getattr(executor.approvals, "pending", None))

    flagged: list[str] = []
    inj = getattr(executor, "last_injection", None)
    if isinstance(inj, dict):
        pats = inj.get("patterns") or inj.get("fired") or []
        if isinstance(pats, list):
            flagged = [str(p) for p in pats]

    said = (routed.speech or "").strip()
    return TypedTurn(
        heard=typed,
        said=said,
        intent=getattr(routed.intent, "value", str(routed.intent)),
        tools=tools,
        flagged=flagged,
        awaiting_approval=awaiting,
        ms=(time.monotonic() - t0) * 1000,
    )


def _ask_brain(question: str, brain: Any, conversation: Any,
               log: Callable[[str], None]) -> str:
    """
    The model, with tessa.md as the system prompt and the thread in front.

    A copy of the voice loop's `_ask_brain` minus its stage timing, kept
    deliberately close to it so the two answer the same way. Every failure is
    ANSWERED rather than raised: `LLMUnavailable` already carries a sentence.
    """
    from core.brain.llm import LLMUnavailable, Message
    from core.brain.persona import system_prompt

    question = strip_wake_name(question)[0] or question
    history = conversation.messages()
    try:
        parts = list(brain.stream(
            system_prompt(),
            history + [Message(role="user", content=question)],
            max_tokens=BRAIN_MAX_TOKENS,
        ))
    except LLMUnavailable as exc:
        log(f"typed: brain unavailable — {exc}")
        return exc.spoken
    except Exception as exc:  # noqa: BLE001
        log(f"typed: brain failed {type(exc).__name__}: {exc}")
        return action_failed(f"my thinking brain errored: {type(exc).__name__}",
                             "Ask me again, or check the connection.")
    text = "".join(parts).strip()
    if text:
        conversation.add("user", question)
        conversation.add("assistant", text)
    return text
