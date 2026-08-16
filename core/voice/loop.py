"""
core/voice/loop.py — key down, speak, key up, hear her answer.

THE MINIMAL CIRCUIT. No Claude API, no tools, no personality. Local handlers
only. The point is that the circuit CLOSES, so that everything after it is
improvement rather than integration — and so that the latency budget is measured
against something real instead of estimated from parts.

    pushToTalk{start}  -> arm the already-live stream (pre-roll is already there)
    pushToTalk{stop}   -> disarm, take segment + pre-roll
                       -> STT -> route -> TTS -> speak
                       -> evt.agent.state at every transition
                       -> evt.transcript.message for both turns

STATE IS EMITTED AT EVERY TRANSITION because Session 2's sphere is driven by it
and has nothing else to go on. A state machine that only reports its endpoints
leaves the sphere frozen through the slowest part of the loop, which is exactly
where the owner most needs to see that something is happening.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from core.brain.conversation import (CLEARED_LINES, Conversation,
                                     is_clear_request)
from core.brain.executor import Executor
from core.brain.persona import system_prompt
from core.brain.repair import repair
from core.brain.unrouted import (Disposition, action_refusal, classify,
                                 unresolved_refusal)
from core.brain.router import (Intent, Routed, Router, action_failed,
                               _SILENCE, _TOO_QUIET, _pick)
from core.brain.tools_local import ToolCall
from core.bus import AgentState, AudioBus


#: How many consecutive no-speech segments before she mentions the microphone.
#:
#: Three, not one: one is a door closing, three in a row is a real fault —
#: his AGC has been measured winding capture gain from 979 down to 264 RMS
#: over a fifteen-second stream. Saying it once and resetting is zoey.md's
#: own rule about opinions: say it once, never nag.
EMPTY_TURNS_BEFORE_SPEAKING = 3

#: Output budget for a model answer.
#:
#: 1500, NOT 600, AND THE REASON IS TRUNCATION RATHER THAN LENGTH. Gemini 3.x
#: bills its hidden thinking against `maxOutputTokens`, so a 600 budget produced
#: "From what I know, Emperor, it is a function that remembers. When you nest a
#: function inside another, the" — cut dead mid-clause. She would have spoken
#: half a sentence and stopped, which is worse than a long answer.
#:
#: Her answers are three or four sentences. The headroom is for the thinking,
#: not for her.
BRAIN_MAX_TOKENS = 1500

#: Output budget for a model answer.
#:
#: 1500, not 600, AND THE REASON IS TRUNCATION RATHER THAN LENGTH.
#: Gemini 3.x bills its hidden thinking against `maxOutputTokens`, so a
#: 600 budget produced 'From what I know, Emperor, it is a function that
#: remembers. When you nest a function inside another, the' — cut dead
#: mid-clause. She would have spoken half a sentence and stopped.
#: Her answers are three or four sentences; the headroom is for the
#: thinking, not for her.


@dataclass
class TurnTiming:
    """Where the wall clock actually goes. Measured, not apportioned."""
    stt_s: float = 0.0
    route_s: float = 0.0
    #: Tool EXECUTION, split out from `tts_s`.
    #:
    #: It used to be inside it: `tts_s` was measured from the end of routing to
    #: the end of synthesis, so every tool turn reported its `os.startfile` and
    #: its Explorer spawn as TTS time. That made the one stage he actually waits
    #: on unattributable — a 12.4 s turn looked like slow Piper when it was
    #: Defender scanning an opened folder.
    tool_s: float = 0.0
    tts_s: float = 0.0
    playback_start_s: float = 0.0
    audio_s: float = 0.0

    def stages(self) -> list[dict]:
        """
        CONTRACT §7.2 additive `evt.turn.timing` — the CLOSED vocabulary.

        `name` is one of stt | route | tool | tts | playback and nothing else.

        THE REASON IT IS CLOSED IS NOT TIDINESS. The daemon's other stage log
        carries prose like `transcribe.returned 'Zoey, open the LedgerWatch
        folder...'` — it contains WHAT HE SAID. Shipping those strings on a
        broadcast event would put his transcript into a field a surface will
        render and may persist, on an event that has nothing to do with
        transcripts. The prose stays in the log; the wire gets five words.

        Zero-length stages are omitted rather than sent as 0, so a conversational
        turn does not claim to have run a tool for no milliseconds.
        """
        out = []
        for name, secs in (("stt", self.stt_s), ("route", self.route_s),
                           ("tool", self.tool_s), ("tts", self.tts_s),
                           ("playback", self.playback_start_s)):
            if secs > 0:
                out.append({"name": name, "ms": round(secs * 1000.0, 1)})
        return out

    @property
    def total_to_first_audio_s(self) -> float:
        """Key-release to first audio out — the spec §4 number (1.5 s)."""
        return self.stt_s + self.route_s + self.tts_s + self.playback_start_s

    def describe(self) -> str:
        t = self.total_to_first_audio_s
        return (
            f"STT {self.stt_s * 1000:.0f} ms + route {self.route_s * 1000:.1f} ms + "
            f"TTS {self.tts_s * 1000:.0f} ms + playback {self.playback_start_s * 1000:.0f} ms "
            f"= {t * 1000:.0f} ms to first audio"
        )


@dataclass
class Turn:
    heard: str
    said: str
    intent: Intent
    timing: TurnTiming = field(default_factory=TurnTiming)
    #: (tool_name, executed_ok, error). Empty for conversational turns.
    #: Without this a successful call, a failed call and a call that never
    #: dispatched all looked identical in the log — which is why the tool path
    #: being dead took three of Gerald's attempts to become visible.
    tools: list = field(default_factory=list)


class VoiceLoop:
    """
    Owns the microphone, the STT model, the router and the speaker for one turn.

    Callbacks rather than a socket: this module must be testable without a
    daemon, and every previous voice measurement that needed a live daemon cost
    a token rotation that Session 2 paid for.
    """

    def __init__(
        self,
        mic,                 # ArmedMicrophone
        stt,                 # WhisperSTT
        tts,                 # TTSAdapter
        router: Router,
        bus: AudioBus,
        on_state: Callable[[str], None] | None = None,
        on_message: Callable[[str, str], None] | None = None,
        on_stage: Callable[[str, float], None] | None = None,
        on_turn_timing: Callable[[dict], None] | None = None,
        dump_segments: bool = False,
        session=None,        # core.brain.provenance.SessionContext — the fence
        audit=None,          # core.security.audit.AuditLog
        brain=None,          # core.brain.llm.LLMAdapter — the fallback for questions
        conversation=None,   # core.brain.conversation.Conversation — the thread
        wake=None,           # core.voice.wake.WakeDetector — optional, may be None
    ) -> None:
        self.mic = mic
        self.stt = stt
        self.tts = tts
        self.router = router
        self.bus = bus
        self._on_state = on_state
        self._on_message = on_message
        self._armed = False
        # The fence and the audit log are INJECTED, not constructed here. The
        # daemon owns one of each; a voice loop that made its own would give the
        # microphone path a second, empty injection flag and a second, unchained
        # audit file — both of which would look like they were working.
        self.executor = Executor(
            on_state=lambda st, detail=None: self._state(AgentState(st), detail),
            session=session, audit=audit)
        # THE BRAIN. Injected, like the fence and the audit log, because the
        # daemon owns one and a second would be a second call counter and a
        # second set of rate-limit state.
        self.brain = brain
        # THE THREAD. Injected like everything else the daemon owns, so a
        # second VoiceLoop cannot open a second file over the same JSON.
        self.conversation = conversation if conversation is not None else Conversation()
        #: The wake detector, or None when the phrase is off.
        #:
        #: Held so "stop listening" can close the ear. Optional throughout: every
        #: use is guarded, because push-to-talk must keep working unchanged when
        #: there is no detector — which is the state Gerald is in until his own
        #: model comes back from Colab.
        self.wake = wake
        self._empty_turns = 0
        self.tool_outcomes: list = []
        self._on_stage = on_stage
        self._on_turn_timing = on_turn_timing
        self.dump_segments = dump_segments
        self.stages: list[tuple[str, float]] = []
        self.last_segment_path: str | None = None
        #: Populated from settings.yaml by the daemon so he can tune without code.
        self.vad_config: dict = {}

    def _state(self, s: AgentState, detail: dict | None = None) -> None:
        """
        ONE OWNER OF THE BROADCAST: the bus.

        This used to call `bus.set_state()` AND `self._on_state()`, and in the
        daemon both are the same function — so every transition went out two or
        three times. Observed on the wire: 3x `listening`, 2x `thinking`.

        The bus wins the ownership because it can emit states VoiceLoop cannot:
        `idle` fires from the output stream's `finished_callback` when audio
        actually drains, which no code path here is awake for. Routing
        everything through it means one source of truth rather than two that
        agree most of the time.
        """
        self.bus.set_state(s, detail)

    def _ask_brain(self, question: str, t0: float) -> str:
        """
        Hand an unresolved question to the model, with zoey.md as the system
        prompt.

        SHE STAYS IN `thinking` FOR THIS, which is correct and is why the state
        is not touched here: the model takes ~4.5 s to first token on this
        connection and the sphere must show that something is happening.

        The stream is DRAINED rather than spoken incrementally. Piper already
        streams per sentence, so the win from token-level streaming is real but
        it needs the playback path to accept a generator — that is a change to
        AudioBus, not to this line, and doing it badly would mean she starts a
        sentence she cannot finish. The measured cost of waiting is recorded in
        the stage log so the trade is visible rather than assumed.

        Every failure here is SPOKEN. `LLMUnavailable` already carries the
        sentence — rate limited, key rejected, model gone — so a brain that
        cannot answer says why instead of falling through to "not mine yet",
        which is the exact failure this whole file is fixing.
        """
        from core.brain.llm import LLMUnavailable, Message
        from core.brain.repair import strip_wake_name

        # HER NAME COMES OFF BEFORE THE MODEL SEES IT.
        #
        # The router already strips it via `normalise`'s filler list, and the
        # search path strips it via `repair()`. This path did not: the model was
        # handed "Hey Zoey, what is a closure in JavaScript?" verbatim. It copes,
        # but it pays for the tokens and it invites her to answer as though a
        # third party had been named — the same failure that made a web search
        # for "Zoey, what is the weather?" return a Zoey-branded weather tweet.
        #
        # `strip_wake_name` is used rather than the router's `_FILLERS` because
        # the filler list only knows the literal spelling, while this regex knows
        # the nine renderings Whisper actually produces (zoi, zoe, joey, soy...).
        question = strip_wake_name(question)[0] or question

        # THE THREAD GOES IN FRONT OF THE QUESTION. This is the whole of the
        # "yes, please" fix: without it every call was standalone and her own
        # offer one turn earlier did not exist.
        history = self.conversation.messages()
        self._stage(f"brain.entered {self.brain.name} "
                    f"[{self.conversation.describe()}]", t0)
        try:
            parts = [d for d in self.brain.stream(
                system_prompt(),
                history + [Message(role="user", content=question)],
                max_tokens=BRAIN_MAX_TOKENS)]
        except LLMUnavailable as exc:
            self._stage(f"brain.unavailable {exc}", t0)
            return exc.spoken
        except Exception as exc:  # noqa: BLE001
            self._stage(f"brain.failed {type(exc).__name__}", t0)
            return action_failed(f"my thinking brain errored: {type(exc).__name__}",
                                 "Ask me again, or check the connection.")
        text = "".join(parts).strip()
        self._stage(f"brain.returned {len(text)}ch", t0)

        # RECORD THE EXCHANGE — the model path only.
        #
        # Tool confirmations are deliberately NOT recorded. "Open, Emperor."
        # twelve times would fill the buffer with noise and evict the exchange
        # that "yes, please" needs to attach to. Memory exists for the thread,
        # and the thread is the conversation, not the folder-opening.
        #
        # `external` is read from the LIVE fence: if untrusted content was in
        # context while she answered, her reply is page-DERIVED and is re-fenced
        # on replay. See core/brain/conversation.py.
        external = bool(getattr(self.executor.session, "external_content_in_context", 0))
        if text:
            self.conversation.add("user", question, external=external)
            self.conversation.add("assistant", text, external=external)
        if not text:
            return ("I got nothing back from my thinking brain, Emperor. "
                    "Ask me again.")
        return text

    def _stage(self, name: str, t0: float) -> float:
        """
        Stage-by-stage wall clock, logged as it happens.

        NOT debugging scaffolding to be removed. A turn that stops between
        `thinking` and a result is indistinguishable from slow STT without this,
        which is why twelve minutes of watching told me less than one stage line
        would have. Gerald hit the same wall and had no way to tell either.
        """
        now = time.perf_counter()
        self.stages.append((name, (now - t0) * 1000.0))
        if self._on_stage is not None:
            self._on_stage(name, (now - t0) * 1000.0)
        return now

    # ── the two halves of a key press ────────────────────────────────────────

    def start(self, on_auto_stop: Callable[[str], None] | None = None) -> None:
        """
        Key DOWN. Barge-in first, arm, then START THE SILENCE WATCHER.

        Stopping the speaker BEFORE arming is the whole of §5.2 rule 1: if she is
        mid-sentence, the owner's key press must silence her, not queue behind
        her. Arming first would capture her own voice into his segment.

        THE WATCHER IS THE PART THAT WAS MISSING. `wait_for_silence` existed and
        was called by nothing, so every segment ran until Gerald pressed the
        chord a second time — the toggle behaviour VAD was built to replace. His
        22.06 s segment contained 2.5 s of speech and 17 s of room, and Whisper
        was billed for all of it.
        """
        self.bus.stop("barge-in: owner pressed push-to-talk")
        self.mic.arm()
        self._armed = True
        self._state(AgentState.LISTENING)

        if on_auto_stop is not None and hasattr(self.mic, "watch_for_silence"):
            cfg = self.vad_config

            def closed(reason: str) -> None:
                # The watcher fires on its own thread. Guard against racing a
                # manual second press, which must remain his escape hatch.
                if not self._armed:
                    return
                self._stage_note(f"vad.closed reason={reason}")
                on_auto_stop(reason)

            self.mic.watch_for_silence(
                on_close=closed,
                silence_ms=int(cfg.get("vad_silence_ms", 1200)),
                floor_rms=float(cfg.get("vad_floor_rms", 150)),
                hard_cap_s=float(cfg.get("vad_hard_cap_s", 20)),
            )

    def _stage_note(self, msg: str) -> None:
        if self._on_stage is not None:
            self._on_stage(msg, 0.0)

    def stop(self) -> Turn | None:
        """Key UP. Everything from here to first audio is the §4 budget."""
        if not self._armed:
            self._state(AgentState.IDLE)
            return None
        self._armed = False
        # A second press must end the segment INSTANTLY — his only escape hatch,
        # and he used it tonight. Cancelling first means the watcher cannot fire
        # a second turn behind this one.
        if hasattr(self.mic, "cancel_watch"):
            self.mic.cancel_watch()
        self.tool_outcomes = []
        self.stages = []
        t0 = time.perf_counter()
        cap = self.mic.disarm()
        self._stage("disarm.returned", t0)

        self._state(AgentState.THINKING)
        audio = cap.samples.astype(np.float32) / 32768.0

        # THE SEGMENT, ON DISK, EXACTLY AS HANDED TO WHISPER.
        # This is the one artefact that distinguishes "Whisper is failing" from
        # "Whisper is being handed the wrong audio", and nothing else does.
        if self.dump_segments:
            try:
                self.last_segment_path = _dump_segment(cap)
                self._stage(f"segment.dumped {self.last_segment_path}", t0)
            except Exception as exc:  # noqa: BLE001
                self._stage(f"segment.dump FAILED {exc}", t0)

        self._stage(f"transcribe.entered dur={cap.duration_s:.2f}s peak={cap.peak} rms={cap.rms:.0f}", t0)
        tr = self.stt.transcribe(audio, cap.sample_rate)
        t_stt = self._stage(f"transcribe.returned {tr.text[:40]!r}", t0)

        heard = tr.text.strip()
        # An empty transcript (silence, or a prime echo) must still land on a
        # state. Any path that returns without one leaves the sphere hanging,
        # which is the same bug as the missing `idle` after speaking.
        if not heard:
            # ── THE EMPTY TURN. NOTHING HEARD, NOTHING SAID. ─────────────────
            #
            # His transcript showed an assistant line — "I did not catch
            # anything, Emperor." — with NO user line above it. A segment fired
            # with no speech in it and she answered out loud, at a moment when
            # he was not talking to her at all.
            #
            # I ARGUED THE OTHER WAY LAST TIME and I was wrong about the common
            # case. "A silent turn is indistinguishable from a crash" is true
            # when he SPOKE and got nothing; it is not true when the microphone
            # tripped on a door closing, which is what actually happens.
            #
            # So: silent, no transcript entry, and NO MEMORY ENTRY — that last
            # one is new and matters more than it looks. An empty exchange
            # written to conversation memory becomes a phantom turn that the
            # next model call reads as real, and memory did not exist when this
            # bug was first seen.
            #
            # THE DIAGNOSTIC SURVIVES, ONCE. If she goes silent three times
            # running, the microphone genuinely is the problem — his AGC has
            # been measured winding capture gain down over a long stream — and
            # she says so a single time, then resets. That is zoey.md's own
            # rule: say it once, never nag.
            self._empty_turns = getattr(self, "_empty_turns", 0) + 1
            self._stage(
                f"silence.gated rms={getattr(tr, 'rms', 0):.0f} "
                f"too_quiet={getattr(tr, 'too_quiet', False)} "
                f"consecutive={self._empty_turns} (silent)", t0)

            if self._empty_turns < EMPTY_TURNS_BEFORE_SPEAKING:
                self._state(AgentState.IDLE)
                return Turn(heard="", said="", intent=Intent.UNROUTED,
                            timing=TurnTiming(stt_s=t_stt - t0, audio_s=cap.duration_s))

            self._empty_turns = 0
            said = _pick(_TOO_QUIET) if getattr(tr, "too_quiet", False) else _pick(_SILENCE)
            syn = self.tts.synthesise(said)
            self._state(AgentState.SPEAKING)
            self.bus.speak(syn.samples, syn.sample_rate)
            if self._on_message is not None:
                self._on_message("assistant", said)
            # STILL no memory entry: she is reporting a microphone fault, not
            # having an exchange with him.
            return Turn(heard="", said=said, intent=Intent.UNROUTED,
                        timing=TurnTiming(stt_s=t_stt - t0, audio_s=cap.duration_s))

        self._empty_turns = 0
        # ── "FORGET THAT" — LOCAL, NO MODEL CALL ─────────────────────────────
        #
        # Checked before routing because it resolves to no tool, so it would
        # otherwise fall through to the model — which would cheerfully say it
        # had forgotten while forgetting nothing.
        if is_clear_request(heard):
            n = self.conversation.clear()
            said = random.choice(CLEARED_LINES)
            self._stage(f"memory.cleared {n} turns", t0)
            if self._on_message is not None:
                self._on_message("user", heard)
            syn = self.tts.synthesise(said)
            self._state(AgentState.SPEAKING)
            self.bus.speak(syn.samples, syn.sample_rate)
            if self._on_message is not None:
                self._on_message("assistant", said)
            return Turn(heard=heard, said=said, intent=Intent.TOOL,
                        timing=TurnTiming(stt_s=t_stt - t0, audio_s=cap.duration_s))

        # ── A PENDING HOLD OWNS THE NEXT UTTERANCE ───────────────────────────
        #
        # Asked before routing, because "yes" routes to nothing: it would come
        # back as "I heard you, Emperor. Not that one yet" and his confirmation
        # would vanish. Returns None when the utterance was not an answer, so
        # asking her the time mid-hold still works and the hold stays pending.
        answered = self.executor.answer_confirmation(heard)
        if answered is not None:
            self._stage("confirm.resolved", t0)
            if self._on_message is not None:
                self._on_message("user", heard)
            syn = self.tts.synthesise(answered)
            self._state(AgentState.SPEAKING)
            self.bus.speak(syn.samples, syn.sample_rate)
            if self._on_message is not None:
                self._on_message("assistant", answered)
            return Turn(heard=heard, said=answered, intent=Intent.TOOL,
                        timing=TurnTiming(stt_s=t_stt - t0, audio_s=cap.duration_s))

        # ── THE FENCE IS PER-TURN ────────────────────────────────────────────
        #
        # Measured in the eight-transcript run: an early "what is the weather"
        # loaded a search result into the fence, and six turns later a tweet was
        # refused with "I have content from duckduckgo search for 'what is the
        # weather' in front of me". Correct by the letter of the rule, wrong in
        # effect — one web search silently disarmed her hands for the rest of
        # the session, and the only way back was a phrase he does not know.
        #
        # The threat the fence exists for is external content influencing an
        # action IN THE SAME EXCHANGE: she reads a page, the page says delete
        # something, she deletes it. That is a within-turn danger and it is
        # fully covered by clearing at the START of a turn — anything read
        # DURING this turn still gates every amber and red action for the rest
        # of it, which is the case the tests exercise.
        #
        # Across turns there is no channel to carry it: each brain call is given
        # ONLY the current utterance, with no conversation history. Keeping the
        # flag set was defending a path that does not exist, at the cost of the
        # tool surface he actually uses.
        if self.executor.session is not None and \
                self.executor.session.external_content_in_context:
            self._stage(f"fence.cleared {self.executor.session.external_content_in_context} "
                        f"source(s) from an earlier turn", t0)
            self.executor.session.clear_external()
            self.executor.last_injection = None

        self._stage("route.entered", t0)
        routed = self.router.route(heard)
        t_route = self._stage(f"route.returned intent={routed.intent.value} calls={len(routed.calls)}", t0)

        # ── UNROUTED IS NOT AN ANSWER. IT IS A HANDOFF. ──────────────────────
        #
        # This is the fix for the turn where "what is a closure in JavaScript?"
        # transcribed perfectly and came back "That is not mine yet." The engine
        # existed, answered in 5 s, and NOTHING IN THIS FILE HAD EVER REFERENCED
        # IT. The router is first because it is free and instant; the model is
        # the default for everything the router cannot resolve.
        if routed.intent is Intent.UNROUTED and routed.score == 0.0:
            disposition = classify(heard)
            self._stage(f"unrouted.classified {disposition.value}", t0)

            if disposition is Disposition.FRAGMENT:
                # NOT A COMMAND. "The" became a full spoken turn once; a reply
                # to noise teaches him that noise gets replies. Silent, logged,
                # straight back to idle.
                self._state(AgentState.IDLE)
                return Turn(heard=heard, said="", intent=Intent.UNROUTED,
                            timing=TurnTiming(stt_s=t_stt - t0, audio_s=cap.duration_s))

            if disposition is Disposition.ACTION:
                routed.speech = action_refusal()

            elif disposition is Disposition.UNRESOLVED:
                # A verb she OWNS, with an object she could not place. This must
                # never reach the model: asked "Open My Taluts" it answered
                # "On it, Emperor. I am opening Taluts for you now" and opened
                # nothing. A fabricated action in her voice is worse than the
                # refusal it replaced, because he would go looking for a folder
                # that never opened.
                routed.speech = unresolved_refusal(heard)

            elif disposition is Disposition.LIVE_DATA:
                # A model cannot know today's weather. She has a search that
                # costs one HTTP request and no browser.
                #
                # THE REPAIRED TEXT, NOT THE RAW TRANSCRIPT. Searching for
                # "Zoey, what is the weather?" returned a Zoey-branded tweet
                # about being a weather girl — her own name poisoned the query.
                query = repair(heard)[0] or heard
                routed = Routed(Intent.TOOL, "", score=1.0,
                                calls=[ToolCall(name="web.search",
                                                args={"query": query},
                                                speech="Looking it up.")])

            elif self.brain is not None:
                routed.speech = self._ask_brain(heard, t0)

        if self._on_message is not None and heard:
            self._on_message("user", heard)

        # ── THE TOOL PATH ────────────────────────────────────────────────────
        #
        # This was missing entirely. `route()` returns tool work in `.calls`
        # with an EMPTY `.speech`, and this method went straight to
        # `synthesise(routed.speech)` — synthesising "" for every tool turn.
        # executor.py was imported by nothing at all. So "open my downloads"
        # routed correctly in 0.3 ms, executed nothing, said nothing, and looked
        # identical in the log to a turn that worked.
        #
        # EVERY TOOL PATH SPEAKS, always, success or failure. A tool that runs
        # silently is indistinguishable from one that failed, which is why
        # Gerald pressed the chord three times — he had no way to tell.
        tool_results: list[str] = []
        if routed.calls:
            for call in routed.calls:
                try:
                    self._stage(f"execute.entered {call.name}", t0)
                    tool_results.append(self.executor.run(call))
                    self._stage(f"execute.returned {call.name}", t0)
                    self.tool_outcomes.append((call.name, True, ""))
                except Exception as exc:  # noqa: BLE001
                    # The executor already catches its own failures; this is the
                    # backstop for anything it cannot. Still speaks.
                    tool_results.append(action_failed(
                        f"{type(exc).__name__}: {exc}", "Say it again and I will retry."))
                    self.tool_outcomes.append((call.name, False, f"{type(exc).__name__}: {exc}"))
            routed.speech = " ".join(r for r in tool_results if r).strip()
        # The tool boundary, so `tts_s` stops absorbing execution time.
        t_tools = time.perf_counter()

        # A tool that produced no words is still a bug, but she must not go
        # silent because of it.
        if not routed.speech:
            routed.speech = "Done, Emperor."

        # ── "STOP LISTENING" — LOCAL, NO MODEL CALL ──────────────────────────
        #
        # Handled here rather than in the tool loop because it resolves to no
        # tool: it is a change to what the daemon is DOING, not something it
        # does for him. She still speaks — unlike STOP, which silences her —
        # because an off switch that goes quiet as it fires is indistinguishable
        # from one that did not work.
        if routed.sleeps_wake:
            if self.wake is not None:
                self.wake.sleep()
                self._stage("wake.slept", t0)
            else:
                # He asked her to stop listening and there was nothing
                # listening. Say the true thing rather than the reassuring one.
                routed.speech = ("The wake phrase is already off, Emperor. "
                                 "Push-to-talk is how I hear you.")
                self._stage("wake.sleep requested but no detector", t0)

        # STOP is not an answer, it is an instruction: comply and stay silent.
        if routed.halts_speech:
            self.bus.stop("owner said stop")
            self._state(AgentState.IDLE)
            timing = TurnTiming(
                stt_s=t_stt - t0, route_s=t_route - t_stt,
                audio_s=cap.duration_s,
            )
            return Turn(heard=heard, said="", intent=routed.intent, timing=timing)

        self._stage(f"synthesise.entered {len(routed.speech)}ch", t0)
        syn = self.tts.synthesise(routed.speech)
        t_tts = self._stage(f"synthesise.returned {syn.duration_s:.2f}s", t0)

        self._state(AgentState.SPEAKING)
        self._stage("playback.entered", t0)
        self.bus.speak(syn.samples, syn.sample_rate)
        t_play = self._stage("playback.returned", t0)

        if self._on_message is not None:
            self._on_message("assistant", routed.speech)

        timing = TurnTiming(
            stt_s=t_stt - t0,
            route_s=t_route - t_stt,
            tool_s=t_tools - t_route,
            tts_s=t_tts - t_tools,
            playback_start_s=t_play - t_tts,
            audio_s=cap.duration_s,
        )
        # ── evt.turn.timing, ONCE, at turn end ───────────────────────────────
        #
        # `on_stage` reached `log()` and nothing else, so this data existed and
        # was thrown away every turn. Emitted through a separate hook rather
        # than folded into `on_stage`, because the two have different audiences:
        # `on_stage` is prose for the log and fires many times per turn, and
        # this is one structured frame for the wire.
        if self._on_turn_timing is not None:
            try:
                self._on_turn_timing({
                    "turnId": f"{int(t0 * 1000)}",
                    "totalMs": round(timing.total_to_first_audio_s * 1000.0, 1),
                    "stages": timing.stages(),
                })
            except Exception:  # noqa: BLE001
                pass          # telemetry must never take a turn down
        return Turn(heard=heard, said=routed.speech, intent=routed.intent,
                    timing=timing, tools=list(self.tool_outcomes))

    def idle(self) -> None:
        self._state(AgentState.IDLE)


def _dump_segment(cap) -> str:
    """Write the exact samples handed to transcribe(), for listening to."""
    import wave
    from datetime import datetime
    from pathlib import Path

    out = Path(__file__).resolve().parents[2] / "data" / "audio" / "segments"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"seg-{datetime.now().strftime('%H%M%S')}.wav"
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(cap.sample_rate)
        fh.writeframes(cap.samples.astype(np.int16).tobytes())
    return str(path)
