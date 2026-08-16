"""
core/voice/speaker.py — she answers HIM.

────────────────────────────────────────────────────────────────────────────────
WHY THIS IS NOT A WAKE-WORD FEATURE

The wake phrase is blocked on a model that does not exist. This is not, and it
was worth building anyway for a reason that has nothing to do with waking: a
keypress proves someone is at the keyboard, and NOTHING ELSE in this system
proves it is Gerald. Speaker verification is the only control that gates the
approval path and the amber/red tiers on WHO IS SPEAKING rather than on WHO IS
IN THE ROOM.

────────────────────────────────────────────────────────────────────────────────
NO TORCH. THAT IS THE WHOLE REASON THIS SHAPE WAS CHOSEN.

    speechbrain    -> torch >= 2.1 + torchaudio      hundreds of MB
    resemblyzer    -> torch >= 1.0.1                 hundreds of MB
    pyannote.audio -> torch >= 2.8 + torchcodec      hundreds of MB
    sherpa-onnx    -> NOTHING. onnxruntime only.     verified: torch not imported

The model is WeSpeaker CAM++ trained on VoxCeleb. **It is not literally
ECAPA-TDNN** and it is not called that here — the task is identical (an embedding
plus a cosine similarity) and CAM++ benchmarks at or above ECAPA on VoxCeleb,
but naming it ECAPA would be repeating a claim I did not verify.

────────────────────────────────────────────────────────────────────────────────
IT SCORES THE SEGMENT, NOT THE WAKE PHRASE

"Hey Zoey" is under a second. Speaker embeddings need seconds of speech to be
stable, and scoring a sub-second phrase produces exactly the false rejections of
HIM that this module must not produce. So the phrase wakes her and the SEGMENT
is verified — which is also the right security shape: a stranger can wake her
and cannot command her.

────────────────────────────────────────────────────────────────────────────────
BIOMETRIC DATA — THE RULES, NOT ASPIRATIONS

  * It lives at `data/voiceprint/owner.json`. `data/` is gitignored (.gitignore
    line 35), so it cannot reach the published repo.
  * It is a 192-float embedding and a few integers. **No audio is stored.** The
    embedding is not invertible to his voice.
  * It NEVER leaves the machine. Nothing in this module transmits, and the
    embedding is never put in a prompt, a log line, or an audit detail.
  * `clear()` deletes it in one action.

────────────────────────────────────────────────────────────────────────────────
FAIL OPEN OR CLOSED — DECIDED CASE BY CASE, AND THE UNENROLLED CASE FIRST

    NOT ENROLLED        -> FAIL OPEN, always, every tier.
    MODEL MISSING/BROKE -> FAIL OPEN, always, every tier.
    ENROLLED, >= confident -> pass, every tier
    ENROLLED, >= accept    -> pass green; amber/red need the approval card
    ENROLLED, <  accept    -> refuse the action, and SAY the way through

THE UNENROLLED CASE IS THE ONE HE HITS FIRST and it is the one that would do
real damage. He installs this, has no voiceprint, and a fail-closed default
would refuse every command he speaks — including any command that could enrol
him. He cannot talk his way out of a system that will not listen to him. So an
absent voiceprint is not a failed check, it is NO CHECK, and the daemon says so
at boot rather than pretending to be secure.

The same applies to a missing model or a load failure. A verification layer that
turns a broken ONNX file into a mute assistant has taken away more than it
protected: he is alone in his flat with this machine, and a false rejection of
HIM costs him his assistant, while a false acceptance of a stranger costs him a
folder opening. Those are not symmetric and the thresholds do not pretend they
are.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = REPO_ROOT / "data" / "models" / "speaker" / "wespeaker_en_voxceleb_CAM++.onnx"
VOICEPRINT_PATH = REPO_ROOT / "data" / "voiceprint" / "owner.json"

SAMPLE_RATE = 16_000

#: Cosine similarity at or above which the speaker is accepted for GREEN work.
#:
#: MEASURED, AND MY FIRST GUESS WAS WRONG. I set this to 0.45 by reasoning from
#: sherpa-onnx's 0.6 example plus "err toward accepting him", then measured it:
#:
#:   leave-one-out over five real recordings of Gerald, enrolling on four and
#:   scoring the fifth, with two synthetic clips as impostors --
#:       GENUINE   n=5   0.682 .. 0.903   mean 0.798
#:       IMPOSTOR  n=10  0.479 .. 0.521   mean 0.507
#:
#: 0.45 sits BELOW the best impostor, so it would have accepted Piper's own TTS
#: voice as Gerald. 0.55 clears every impostor by 0.029 and sits 0.132 below the
#: worst genuine score, which is the margin that matters — it is HIS side that
#: must not be clipped.
#:
#: THE ASYMMETRY IS DELIBERATE AND I AGREE WITH GERALD ABOUT IT. He is alone
#: with this machine. A false rejection of him costs him his assistant; a false
#: acceptance of a stranger costs him a folder opening, and every action that
#: could actually hurt him is RED and already needs the approval card regardless
#: of who spoke.
#:
#: THE MEASUREMENT'S LIMITS, STATED: five clips, one speaker, and the impostors
#: are SYNTHETIC. Synthetic speech may sit further from a human than another
#: human would, so the 0.161 gap is an upper bound on how easy this job is. A
#: real second human has never been tested against this print.
DEFAULT_ACCEPT = 0.55

#: Required before an AMBER or RED action will even be proposed.
#:
#: A borderline score is enough to open a folder and not enough to approve a
#: tweet — item 2f's question, and the answer is yes, gated separately.
#:
#: 0.62 sits 0.099 above the best measured impostor and 0.062 below the worst
#: genuine score. That second margin is thin on purpose: the cost of being wrong
#: here is that an AMBER hold needs the approval card instead of his voice, and
#: red needed the card regardless. Nothing is lost, only slowed.
DEFAULT_CONFIDENT = 0.62

#: ENROL FROM SEVERAL CLIPS, NOT ONE. This is not a preference, it is the
#: difference between working and not.
#:
#: Enrolling on a SINGLE 15 s clip produced scores of 0.242-0.527 against his own
#: other recordings — indistinguishable from an impostor. Enrolling on four
#: produced 0.682-0.903 against held-out recordings of him. One clip captures one
#: distance, one angle, one AGC state; the mean of several captures the speaker.
MIN_ENROL_CLIPS = 3

#: Below this much speech, do not score at all — report UNSURE and fail open.
#:
#: A 0.4 s "yes" cannot produce a stable embedding, and scoring it would
#: manufacture exactly the false rejections this module must avoid. Confirmations
#: are short by nature, so this case is common rather than exotic.
MIN_SPEECH_S = 1.5


@dataclass
class Verdict:
    """The decision, the number behind it, and whether it is even usable."""
    ok: bool
    score: float
    reason: str
    #: True when no judgement was possible (unenrolled, no model, too short).
    #: Distinct from ok=True: "I checked and it is him" and "I could not check"
    #: must never collapse into the same value, or a broken model reads as a
    #: successful verification for the rest of the codebase.
    unknown: bool = False
    elapsed_ms: float = 0.0

    def allows(self, tier: str, confident_at: float) -> bool:
        """Tier-aware gate. Green is permissive; amber and red are not."""
        if self.unknown:
            return True                      # fail open, by design — see module doc
        if not self.ok:
            return False
        if tier in ("amber", "red"):
            return self.score >= confident_at
        return True


@dataclass
class Voiceprint:
    embedding: np.ndarray
    created: str
    samples: int
    seconds: float
    model: str
    #: WHICH CHAIN IT WAS ENROLLED THROUGH.
    #:
    #: Item 3c. If noise suppression is toggled after enrolment, the audio being
    #: scored no longer matches the audio that was enrolled, and the scores move
    #: for a reason nothing in the log explains. Recording the chain here lets
    #: the verifier NOTICE rather than silently drift — which is the difference
    #: between a bug he can find and an intermittent he cannot.
    suppression: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "embedding": [float(x) for x in self.embedding],
            "created": self.created,
            "samples": self.samples,
            "seconds": round(self.seconds, 2),
            "model": self.model,
            "suppression": self.suppression,
            "note": "BIOMETRIC. Never transmitted, never sent to a model. "
                    "No audio is stored; this is a non-invertible embedding.",
        }


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class SpeakerVerifier:
    """
    Cosine similarity against one enrolled voiceprint.

    ONE speaker, deliberately. `SpeakerEmbeddingManager` supports many and this
    does not use it: a multi-speaker store invites "who is this?", and the only
    question this system needs answered is "is this him?". Fewer answers, fewer
    ways to be wrong.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        accept: float = DEFAULT_ACCEPT,
        confident: float = DEFAULT_CONFIDENT,
        voiceprint_path: str | Path | None = None,
        num_threads: int = 1,
    ) -> None:
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL
        self.voiceprint_path = Path(voiceprint_path) if voiceprint_path else VOICEPRINT_PATH
        self.accept = accept
        self.confident = confident
        self.num_threads = num_threads
        self._extractor: Any = None
        self._print: Voiceprint | None = None
        self.load_error: str | None = None
        self.enrol_warning: str | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def load(self) -> bool:
        """Load the model and any existing voiceprint. Never raises."""
        try:
            import sherpa_onnx
        except ImportError as exc:
            self.load_error = f"sherpa-onnx not installed ({exc})"
            return False
        if not self.model_path.exists():
            self.load_error = f"speaker model not found: {self.model_path}"
            return False
        try:
            cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self.model_path), num_threads=self.num_threads,
                provider="cpu", debug=False,
            )
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        except Exception as exc:  # noqa: BLE001
            self.load_error = f"{type(exc).__name__}: {exc}"
            return False
        self._load_voiceprint()
        return True

    @property
    def enrolled(self) -> bool:
        return self._print is not None

    @property
    def dim(self) -> int:
        return int(self._extractor.dim) if self._extractor is not None else 0

    def describe(self) -> str:
        if self._extractor is None:
            return f"speaker: UNAVAILABLE ({self.load_error})"
        if self._print is None:
            return ("speaker: model ready, NOT ENROLLED - verification is OFF "
                    "and every tier fails open")
        return (f"speaker: enrolled {self._print.created} from "
                f"{self._print.seconds:.1f}s over {self._print.samples} sample(s), "
                f"accept>={self.accept:.2f} confident>={self.confident:.2f}")

    # ── embedding ────────────────────────────────────────────────────────────

    def embed(self, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray | None:
        """
        One L2-normalised embedding, or None if it could not be produced.

        Accepts float32 in [-1, 1] or int16, because the two live side by side in
        this codebase — `read_wav` returns float32 for Whisper and the microphone
        ring is int16 — and a scale mix-up here would be silent. `audio_io`
        already had exactly that bug in `replay_silence_decision`, where int16
        thresholds were compared against float32 samples and reported his own
        speech as silence. Coerce once, here, rather than at every call site.
        """
        if self._extractor is None:
            return None
        a = np.asarray(audio).reshape(-1)
        if np.issubdtype(a.dtype, np.integer):
            a = a.astype(np.float32) / 32768.0
        else:
            a = a.astype(np.float32)
        if a.size == 0:
            return None
        try:
            stream = self._extractor.create_stream()
            stream.accept_waveform(sample_rate=sample_rate, waveform=a)
            stream.input_finished()
            if not self._extractor.is_ready(stream):
                return None
            vec = np.asarray(self._extractor.compute(stream), dtype=np.float32)
        except Exception:  # noqa: BLE001
            return None
        n = np.linalg.norm(vec)
        return vec / n if n > 0 else vec

    # ── enrolment ────────────────────────────────────────────────────────────

    def enrol(self, clips: list[np.ndarray], sample_rate: int = SAMPLE_RATE,
              suppression: bool = False) -> Voiceprint | None:
        """
        Build a voiceprint from one or more clips and persist it.

        The embeddings are L2-normalised BEFORE averaging, so a loud clip cannot
        dominate a quiet one. His AGC has been measured winding capture gain from
        979 down to 264 RMS across a single fifteen-second hold, so unnormalised
        averaging would weight the beginning of a session over the end of it.
        """
        vecs, total_s = [], 0.0
        for clip in clips:
            v = self.embed(clip, sample_rate)
            if v is not None:
                vecs.append(v)
                total_s += len(np.asarray(clip).reshape(-1)) / sample_rate
        if not vecs:
            return None
        if len(vecs) < MIN_ENROL_CLIPS:
            # NOT a hard failure — a caller may legitimately be rebuilding from
            # one long recording — but it must not pass silently, because a
            # single-clip print measured indistinguishable from an impostor and
            # the failure is invisible until he is refused by his own machine.
            self.enrol_warning = (
                f"enrolled from only {len(vecs)} clip(s); {MIN_ENROL_CLIPS}+ "
                f"separate utterances are needed for a print that reliably "
                f"recognises him")
        else:
            self.enrol_warning = None
        mean = np.mean(np.stack(vecs), axis=0)
        n = np.linalg.norm(mean)
        mean = mean / n if n > 0 else mean

        vp = Voiceprint(
            embedding=mean,
            created=time.strftime("%Y-%m-%dT%H:%M:%S"),
            samples=len(vecs), seconds=total_s,
            model=self.model_path.name,
            suppression=suppression,
        )
        self._print = vp
        self._save_voiceprint(vp)
        return vp

    def clear(self) -> bool:
        """Delete the voiceprint. One action, as promised."""
        self._print = None
        if self.voiceprint_path.exists():
            self.voiceprint_path.unlink()
            return True
        return False

    def _save_voiceprint(self, vp: Voiceprint) -> None:
        # REFUSE TO WRITE BIOMETRIC DATA OUTSIDE data/. Belt and braces against a
        # future caller passing a path into the repo proper, where .gitignore
        # would not cover it and he publishes this repo.
        resolved = self.voiceprint_path.resolve()
        if (REPO_ROOT / "data") not in resolved.parents:
            raise ValueError(
                f"refusing to write a voiceprint outside data/: {resolved}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(json.dumps(vp.to_json(), indent=2), encoding="utf-8")

    def _load_voiceprint(self) -> None:
        if not self.voiceprint_path.exists():
            return
        try:
            raw = json.loads(self.voiceprint_path.read_text(encoding="utf-8"))
            self._print = Voiceprint(
                embedding=np.asarray(raw["embedding"], dtype=np.float32),
                created=raw.get("created", "?"),
                samples=int(raw.get("samples", 0)),
                seconds=float(raw.get("seconds", 0.0)),
                model=raw.get("model", "?"),
                suppression=bool(raw.get("suppression", False)),
            )
        except Exception:  # noqa: BLE001
            # A corrupt voiceprint is treated as NO voiceprint, which fails open.
            # The alternative — refusing to start — would lock him out over a
            # truncated JSON file.
            self._print = None

    # ── the decision ─────────────────────────────────────────────────────────

    def verify(self, audio: np.ndarray, sample_rate: int = SAMPLE_RATE,
               suppression: bool = False) -> Verdict:
        """Score a segment. Every failure path returns `unknown` and fails open."""
        t0 = time.perf_counter()

        def done(v: Verdict) -> Verdict:
            v.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return v

        if self._extractor is None:
            return done(Verdict(True, 0.0, f"no model ({self.load_error})", unknown=True))
        if self._print is None:
            return done(Verdict(True, 0.0, "not enrolled", unknown=True))

        a = np.asarray(audio).reshape(-1)
        if len(a) / sample_rate < MIN_SPEECH_S:
            return done(Verdict(True, 0.0,
                                f"only {len(a)/sample_rate:.2f}s of audio, "
                                f"below the {MIN_SPEECH_S}s minimum", unknown=True))

        # THE CHAIN CHANGED UNDER THE VOICEPRINT — item 3c.
        if suppression != self._print.suppression:
            return done(Verdict(
                True, 0.0,
                f"enrolled with suppression={self._print.suppression} but scoring "
                f"with suppression={suppression}; re-enrol before trusting this",
                unknown=True))

        vec = self.embed(a, sample_rate)
        if vec is None:
            return done(Verdict(True, 0.0, "embedding failed", unknown=True))

        score = _cosine(vec, self._print.embedding)
        if score >= self.confident:
            return done(Verdict(True, score, "confident"))
        if score >= self.accept:
            return done(Verdict(True, score, "accepted (green only)"))
        return done(Verdict(False, score, "below accept threshold"))


#: What she says when the voice is not his.
#:
#: SILENCE IS SAFEST AND MOST CONFUSING, and confusing is the worse failure here.
#: If it IS him and the score dipped — a cold, a bad angle to the microphone, a
#: noisy room — silence tells him the machine is broken and gives him nothing to
#: do about it. The line therefore does three things: it does not accuse, it does
#: not claim certainty it does not have, and it names the way through.
#:
#: It leaks nothing useful to a stranger. The voiceprint is not a password, and
#: telling someone "that did not sound like the owner" gives them no lever —
#: they still cannot enrol, and every consequential action is red-gated anyway.
REFUSAL = ("I am not sure that is you, Emperor. "
           "Use the chord and I will listen.")


# ── enrolment, which only Gerald can do ──────────────────────────────────────
#
#   python -m core.voice.speaker --enrol
#   python -m core.voice.speaker --check
#   python -m core.voice.speaker --clear
#
# I cannot enrol him. I have no voice and no microphone, so this is the one part
# of the feature that has to be handed over rather than delivered finished.

ENROL_PROMPTS = [
    "Zoey, open my downloads folder and show me what changed last night.",
    "What is the weather like today, and how much of my budget is left?",
    "Kill process four two four two, then tell me the time.",
    "I want you to search for the LedgerWatch repository and read me the summary.",
    "Good evening Zoey. Status report, please — memory, uptime, everything.",
]


def _enrol_cli(seconds: float = 6.0) -> int:
    """Record several utterances and build the print. Returns an exit code."""
    from core.voice.audio_io import record

    v = SpeakerVerifier()
    if not v.load():
        print(f"cannot enrol: {v.load_error}")
        return 1

    print("ENROLMENT — five short readings, about six seconds each.\n")
    print("  Speak normally, at the distance you actually use, in the room you")
    print("  actually use. A print built from one careful sitting will not")
    print("  recognise you on an ordinary evening.\n")
    print("  MEASURED: enrolling from ONE clip scored 0.242-0.527 against his")
    print("  own other recordings — no better than an impostor. Enrolling from")
    print("  FOUR scored 0.682-0.903. The repetition is the whole mechanism.\n")

    clips = []
    for i, prompt in enumerate(ENROL_PROMPTS, 1):
        input(f"  [{i}/{len(ENROL_PROMPTS)}] Press ENTER, then read:\n"
              f"        \"{prompt}\"\n      > ")
        cap = record(seconds)
        mark = "TOO QUIET - redo this one" if cap.is_silence else "ok"
        print(f"        captured {cap.duration_s:.1f}s  peak {cap.peak}  "
              f"rms {cap.rms:.0f}  [{mark}]\n")
        if not cap.is_silence:
            clips.append(cap.samples)

    if len(clips) < MIN_ENROL_CLIPS:
        print(f"only {len(clips)} usable clip(s). Nothing was saved.")
        return 1

    vp = v.enrol(clips)
    if vp is None:
        print("enrolment failed: no embedding could be produced.")
        return 1

    print(f"\nENROLLED. {vp.samples} clips, {vp.seconds:.1f}s total.")
    print(f"  stored: {v.voiceprint_path}")
    print("  contains: a 512-float embedding. NO AUDIO. Not invertible to your")
    print("            voice. data/ is gitignored, so it cannot be published.\n")

    # THE NUMBER HE CAN CHECK, rather than a hope.
    print("SELF-CHECK — each clip scored against the print built from all of them:")
    scores = [v.verify(c).score for c in clips]
    for i, s in enumerate(scores, 1):
        print(f"    clip {i}: {s:.3f}")
    worst = min(scores)
    print(f"\n  worst {worst:.3f}   mean {float(np.mean(scores)):.3f}   "
          f"accept threshold {v.accept:.2f}   confident {v.confident:.2f}")
    if worst >= v.confident:
        print("  GOOD. Every clip clears the confident threshold; amber and red")
        print("  actions will accept your voice.")
    elif worst >= v.accept:
        print("  USABLE but tight. Green work will accept you; some turns will")
        print("  fall short of `confident` and route amber/red to the card.")
        print("  Re-run enrolment in the room you actually use if that annoys you.")
    else:
        print("  POOR. At least one clip scores below the accept threshold, which")
        print("  means this print would refuse YOU. Re-enrol before turning")
        print("  verification on.")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Zoey speaker verification")
    ap.add_argument("--enrol", action="store_true", help="record and build the voiceprint")
    ap.add_argument("--check", action="store_true", help="report the current state")
    ap.add_argument("--clear", action="store_true", help="delete the voiceprint")
    ap.add_argument("--seconds", type=float, default=6.0)
    args = ap.parse_args()

    if args.clear:
        sv = SpeakerVerifier()
        print("voiceprint deleted" if sv.clear() else "no voiceprint to delete")
        raise SystemExit(0)

    if args.enrol:
        raise SystemExit(_enrol_cli(args.seconds))

    sv = SpeakerVerifier()
    sv.load()
    print(sv.describe())
    print(f"  model: {sv.model_path}")
    print(f"  voiceprint: {sv.voiceprint_path} "
          f"(exists: {sv.voiceprint_path.exists()})")
