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

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from core.brain.executor import Executor
from core.brain.router import Intent, Router, action_failed
from core.bus import AgentState, AudioBus


@dataclass
class TurnTiming:
    """Where the wall clock actually goes. Measured, not apportioned."""
    stt_s: float = 0.0
    route_s: float = 0.0
    tts_s: float = 0.0
    playback_start_s: float = 0.0
    audio_s: float = 0.0

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
    ) -> None:
        self.mic = mic
        self.stt = stt
        self.tts = tts
        self.router = router
        self.bus = bus
        self._on_state = on_state
        self._on_message = on_message
        self._armed = False
        self.executor = Executor(on_state=lambda st: self._state(AgentState(st)))
        self.tool_outcomes: list = []

    def _state(self, s: AgentState) -> None:
        self.bus.set_state(s)
        if self._on_state is not None:
            self._on_state(s.value)

    # ── the two halves of a key press ────────────────────────────────────────

    def start(self) -> None:
        """
        Key DOWN. Barge-in first, then arm.

        Stopping the speaker BEFORE arming is the whole of §5.2 rule 1: if she is
        mid-sentence, the owner's key press must silence her, not queue behind
        her. Arming first would capture her own voice into his segment.
        """
        self.bus.stop("barge-in: owner pressed push-to-talk")
        self.mic.arm()
        self._armed = True
        self._state(AgentState.LISTENING)

    def stop(self) -> Turn | None:
        """Key UP. Everything from here to first audio is the §4 budget."""
        if not self._armed:
            self._state(AgentState.IDLE)
            return None
        self._armed = False
        self.tool_outcomes = []
        cap = self.mic.disarm()

        self._state(AgentState.THINKING)
        t0 = time.perf_counter()
        audio = cap.samples.astype(np.float32) / 32768.0
        tr = self.stt.transcribe(audio, cap.sample_rate)
        t_stt = time.perf_counter()

        heard = tr.text.strip()
        # An empty transcript (silence, or a prime echo) must still land on a
        # state. Any path that returns without one leaves the sphere hanging,
        # which is the same bug as the missing `idle` after speaking.
        if not heard:
            self._state(AgentState.IDLE)
            return Turn(heard="", said="", intent=Intent.UNROUTED,
                        timing=TurnTiming(stt_s=t_stt - t0, audio_s=cap.duration_s))
        routed = self.router.route(heard)
        t_route = time.perf_counter()

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
                    tool_results.append(self.executor.run(call))
                    self.tool_outcomes.append((call.name, True, ""))
                except Exception as exc:  # noqa: BLE001
                    # The executor already catches its own failures; this is the
                    # backstop for anything it cannot. Still speaks.
                    tool_results.append(action_failed(
                        f"{type(exc).__name__}: {exc}", "Say it again and I will retry."))
                    self.tool_outcomes.append((call.name, False, f"{type(exc).__name__}: {exc}"))
            routed.speech = " ".join(r for r in tool_results if r).strip()

        # A tool that produced no words is still a bug, but she must not go
        # silent because of it.
        if not routed.speech:
            routed.speech = "Done, Emperor."

        # STOP is not an answer, it is an instruction: comply and stay silent.
        if routed.halts_speech:
            self.bus.stop("owner said stop")
            self._state(AgentState.IDLE)
            timing = TurnTiming(
                stt_s=t_stt - t0, route_s=t_route - t_stt,
                audio_s=cap.duration_s,
            )
            return Turn(heard=heard, said="", intent=routed.intent, timing=timing)

        syn = self.tts.synthesise(routed.speech)
        t_tts = time.perf_counter()

        self._state(AgentState.SPEAKING)
        self.bus.speak(syn.samples, syn.sample_rate)
        t_play = time.perf_counter()

        if self._on_message is not None:
            self._on_message("assistant", routed.speech)

        timing = TurnTiming(
            stt_s=t_stt - t0,
            route_s=t_route - t_stt,
            tts_s=t_tts - t_route,
            playback_start_s=t_play - t_tts,
            audio_s=cap.duration_s,
        )
        return Turn(heard=heard, said=routed.speech, intent=routed.intent,
                    timing=timing, tools=list(self.tool_outcomes))

    def idle(self) -> None:
        self._state(AgentState.IDLE)
