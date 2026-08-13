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

from core.brain.router import Intent, Router
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
            return None
        self._armed = False
        cap = self.mic.disarm()

        self._state(AgentState.THINKING)
        t0 = time.perf_counter()
        audio = cap.samples.astype(np.float32) / 32768.0
        tr = self.stt.transcribe(audio, cap.sample_rate)
        t_stt = time.perf_counter()

        heard = tr.text.strip()
        routed = self.router.route(heard)
        t_route = time.perf_counter()

        if self._on_message is not None and heard:
            self._on_message("user", heard)

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
        return Turn(heard=heard, said=routed.speech, intent=routed.intent, timing=timing)

    def idle(self) -> None:
        self._state(AgentState.IDLE)
