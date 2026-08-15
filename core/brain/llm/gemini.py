"""
core/brain/llm/gemini.py — the brain Gerald can actually run today.

NO NEW DEPENDENCY, AND THAT IS A DECISION, NOT AN OMISSION

The obvious move is `pip install google-generativeai`. It pulls `grpcio`,
`protobuf`, `google-api-core`, `googleapis-common-protos` and their
dependencies — tens of megabytes on a metered connection, a second gRPC runtime
in a process that already holds CTranslate2 and an ONNX voice, on a laptop with
two cores.

What it would buy is a wrapper over an HTTP endpoint that this file calls
directly in ~80 lines, using `httpx`, which is ALREADY INSTALLED because the
Anthropic SDK depends on it. Zero new bytes. So the REST API it is:

    POST /v1beta/models/{model}:streamGenerateContent?alt=sse

`alt=sse` is the part that matters. Without it the endpoint returns a JSON
array that only completes when the whole answer is done — streaming-shaped, not
actually streaming, and base.py's whole design (Piper speaks the first sentence
while the model is still writing the second) would quietly stop working while
still looking correct.

THE KEY IS READ FROM THE ENVIRONMENT AND NEVER LEAVES THIS PROCESS

Read once from `GEMINI_API_KEY`. Never logged, never written to a file, never
put in a URL query string, never included in an exception message. It goes in
the `x-goog-api-key` HEADER — the same reasoning as CONTRACT §2.1's rule about
the daemon token, and for the same reason: query strings end up in logs,
proxies and crash dumps.

RATE LIMITS ARE AN ANSWER, NOT A FAILURE

The free tier will 429, and often. A 429 that surfaces as a stack trace, a
silent empty answer, or a quiet switch to another model are all the same
failure from where Gerald is sitting: he asked a question and cannot tell what
happened. So a 429 becomes `LLMUnavailable` carrying a SPOKEN sentence, and the
engine never substitutes another one on its own. Choosing her brain is his
decision.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Iterator

from .base import LLMAdapter, LLMUnavailable, Message, ToolDef, Usage

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

#: Free tier. The call count is real and is tracked; the naira figure is not,
#: and this is why `cost_ngn` stays 0.0 rather than being invented — see
#: `core/telemetry/cost.py` and the PULSE note in the report.
COST_NGN_PER_CALL = 0.0

#: Connect fast, then wait. A slow first token is normal on a free tier; a slow
#: TCP connect means the metered link is down and she should say so rather than
#: hold the microphone for thirty seconds.
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 60.0


def _api_message(body: str) -> str:
    """
    Google's own explanation, for the LOG only — never for her speech.

    It is remote text. It goes to the audit trail and the dev console, where it
    is read by a person debugging, and never into a sentence she reads aloud or
    into anything a model sees unfenced.
    """
    try:
        return str(json.loads(body).get("error", {}).get("message", ""))[:300]
    except (json.JSONDecodeError, AttributeError):
        return body[:200]


class GeminiLLM(LLMAdapter):
    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        self.cfg = cfg or {}
        models = self.cfg.get("models", {}) or {}
        self.model_main = str(models.get("main", "gemini-2.0-flash"))
        self.model_classify = str(models.get("classify", "gemini-2.0-flash-lite"))
        self._key_env = str(self.cfg.get("api_key_env", "GEMINI_API_KEY"))
        self.last_usage = Usage()
        #: Real, and shown in PULSE. A free tier costs ₦0 and still has a cost
        #: he needs to see: how much he is leaning on someone else's quota.
        self.calls = 0
        self.rate_limited_at: float | None = None

    # ── identity and availability ────────────────────────────────────────────

    @property
    def name(self) -> str:
        return f"gemini:{self.model_main}"

    @property
    def _key(self) -> str:
        return os.environ.get(self._key_env, "")

    @property
    def available(self) -> tuple[bool, str]:
        if not self._key:
            return False, f"{self._key_env} is not set in this process"
        try:
            import httpx  # noqa: F401
        except ImportError:
            return False, "httpx is not installed"
        return True, ""

    def _require(self) -> None:
        ok, why = self.available
        if not ok:
            raise LLMUnavailable(
                f"gemini brain unavailable: {why}",
                spoken=("My thinking brain is not connected, Emperor. "
                        f"{why.capitalize()}. Everything local still works."),
            )

    # ── the wire format ──────────────────────────────────────────────────────

    @staticmethod
    def _contents(messages: list[Message]) -> list[dict[str, Any]]:
        """
        Gemini has no `assistant` role — it is `model` — and no top-level
        `system` inside `contents`. A system message that leaked in as a user
        turn would read to the model as something GERALD said, which is exactly
        the trust confusion the provenance fence exists to prevent, so system
        content is routed to `system_instruction` by the caller and anything
        still carrying that role here is dropped rather than downgraded.
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            role = "model" if m.role == "assistant" else "user"
            out.append({"role": role, "parts": [{"text": m.content}]})
        return out

    def stream(
        self,
        system: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        max_tokens: int = 1024,
        thinking: bool = False,
    ) -> Iterator[str]:
        import httpx

        self._require()
        # THINKING IS TURNED DOWN BY DEFAULT, AND THIS IS THE SINGLE MOST
        # IMPORTANT LINE IN THE FILE FOR A VOICE ASSISTANT.
        #
        # Gemini 3.x thinks before it answers, and the thinking is billed
        # against `maxOutputTokens`. Measured here with it left at the default:
        # a summary request came back as "The daemon binds strictly to loopback
        # (`127.0." — 14 output tokens, cut mid-word, after 6.4 SECONDS to
        # first token, because the budget went on thoughts nobody ever sees.
        # Three questions, three truncated answers, all three slow.
        #
        # `thinkingLevel`, NOT `thinkingBudget`. The budget form is the 2.5-era
        # knob and this model rejects it outright — 400 "Request contains an
        # invalid argument", which is how the first version of this fix failed.
        # base.py already carries a `thinking` flag for the questions that
        # deserve the extra latency; this is the knob it maps to. She speaks
        # while the model writes, so seconds before the first token are seconds
        # of silence he sits through.
        gen_cfg: dict[str, Any] = {"maxOutputTokens": max_tokens}
        gen_cfg["thinkingConfig"] = {"thinkingLevel": "high" if thinking else "low"}
        body: dict[str, Any] = {
            "contents": self._contents(messages),
            "generationConfig": gen_cfg,
        }
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            # NAME + JSON SCHEMA. The model selects a tool and its arguments;
            # it never returns something to execute. Invariant 4 survives the
            # crossing into a cloud model because the wire format itself cannot
            # express a command string.
            body["tools"] = [{"function_declarations": [
                {"name": t.name, "description": t.description, "parameters": t.input_schema}
                for t in tools]}]

        url = f"{API_ROOT}/models/{self.model_main}:streamGenerateContent"
        self.calls += 1
        t0 = time.perf_counter()
        in_tok = out_tok = 0

        try:
            with httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)) as c:
                with c.stream(
                    "POST", url,
                    params={"alt": "sse"},
                    headers={"x-goog-api-key": self._key,      # HEADER, never the URL
                             "content-type": "application/json"},
                    json=body,
                ) as r:
                    if r.status_code == 429:
                        r.read()
                        self.rate_limited_at = time.time()
                        raise LLMUnavailable(
                            "gemini rate limited (429)",
                            spoken="I am rate limited, Emperor. Try me in a minute.")
                    if r.status_code in (401, 403):
                        r.read()
                        raise LLMUnavailable(
                            f"gemini rejected the key ({r.status_code})",
                            spoken="Google would not accept my key, Emperor. "
                                   "It may need renewing. Everything local still works.")
                    if r.status_code == 404:
                        r.read()
                        # HIT FOR REAL, TWICE. `gemini-2.0-flash` 404'd, then
                        # `gemini-2.5-flash` 404'd even though ListModels
                        # returned it — Google's own message said "no longer
                        # available to new users", and that sentence is the only
                        # thing that made the second failure diagnosable. The
                        # first version of this branch said "Code 404" and sent
                        # me looking at the transport twice.
                        raise LLMUnavailable(
                            f"gemini model {self.model_main!r} rejected (404): "
                            f"{_api_message(r.text)}",
                            spoken=(f"Google will not give me {self.model_main}, Emperor. "
                                    f"The model name in settings needs changing."))
                    if r.status_code >= 400:
                        r.read()
                        # The body may echo the request. It is NEVER interpolated
                        # into the spoken line, only the status code is.
                        raise LLMUnavailable(
                            f"gemini HTTP {r.status_code}: {_api_message(r.text)}",
                            spoken=f"Google returned an error, Emperor. Code {r.status_code}. "
                                   f"Everything local still works.")

                    for line in r.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if not chunk or chunk == "[DONE]":
                            continue
                        try:
                            obj = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        usage = obj.get("usageMetadata") or {}
                        in_tok = usage.get("promptTokenCount", in_tok)
                        out_tok = usage.get("candidatesTokenCount", out_tok)
                        for cand in obj.get("candidates", []) or []:
                            for part in (cand.get("content", {}) or {}).get("parts", []) or []:
                                text = part.get("text")
                                if text:
                                    yield text
        except httpx.TimeoutException as exc:
            raise LLMUnavailable(
                f"gemini timed out: {exc}",
                spoken="Google did not answer in time, Emperor. Your connection may be down.",
            ) from None
        except httpx.HTTPError as exc:
            # `exc` can contain the request URL. It cannot contain the key,
            # because the key is a header — which is half the reason it is one.
            raise LLMUnavailable(
                f"gemini transport error: {type(exc).__name__}",
                spoken="I could not reach Google, Emperor. Everything local still works.",
            ) from None

        self.last_usage = Usage(
            input_tokens=int(in_tok), output_tokens=int(out_tok),
            cost_ngn=COST_NGN_PER_CALL, model=self.model_main,
        )
        self.last_total_s = time.perf_counter() - t0

    def list_models(self) -> list[str]:
        """
        What this key can actually reach.

        Here because the model catalogue moves and a 404 is otherwise a dead
        end: `gemini-2.0-flash` was a perfectly reasonable default and his key
        could not see it. This turns "code 404" into a list he can pick from.
        """
        import httpx

        self._require()
        r = httpx.get(f"{API_ROOT}/models", headers={"x-goog-api-key": self._key}, timeout=30)
        r.raise_for_status()
        return [m["name"].split("/")[-1] for m in r.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]


def probe(candidates: list[str] | None = None) -> list[tuple[str, str, float]]:
    """
    Actually CALL each model and time it. (model, verdict, seconds).

    `list_models()` is not enough and that is the whole reason this exists:
    ListModels returned `gemini-2.5-flash`, and calling it returned 404 "no
    longer available to new users". The catalogue and the entitlement are two
    different questions and only the second one matters.
    """
    names = candidates or GeminiLLM().list_models()
    out: list[tuple[str, str, float]] = []
    for m in names:
        eng = GeminiLLM({"models": {"main": m}})
        t0 = time.perf_counter()
        try:
            text = "".join(eng.stream("", [Message(role="user", content="Reply with exactly: OK")],
                                      max_tokens=300))
            out.append((m, f"ok {text.strip()[:12]!r}", time.perf_counter() - t0))
        except LLMUnavailable as exc:
            out.append((m, str(exc)[:90], time.perf_counter() - t0))
    return out


if __name__ == "__main__":  # python -m core.brain.llm.gemini --models | --probe
    import sys as _sys

    if "--models" in _sys.argv:
        for _m in GeminiLLM().list_models():
            print(_m)
    elif "--probe" in _sys.argv:
        for _m, _v, _s in probe(_sys.argv[2:] or None):
            print(f"{_m:32} {_s:6.2f}s  {_v}")
