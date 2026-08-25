"""
core/tests/test_brain.py — the three engines behind one interface.

WHAT THIS GUARDS

  1. The engine named in settings.yaml is the engine that gets built, and NO
     substitution happens when it is unusable. A brain that silently swaps
     itself is lying about what answered him.
  2. An unusable engine raises `LLMUnavailable` carrying a sentence she SAYS.
     Not a stack trace, not silence, not a worse answer.
  3. A 429 is an ANSWER, in her voice. Simulated deterministically rather than
     by hammering a free tier until Google throttles his key.
  4. The API key never appears in the request URL, in a log line, or in an
     exception message.
  5. `system` never leaks into `contents` as a user turn — that would present
     the system prompt to the model as something Gerald said.

Run: python core/tests/test_brain.py
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.brain.llm import LLMUnavailable, Message, describe_engines, make_engine  # noqa: E402
from core.brain.llm.gemini import GeminiLLM  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label} {extra}")


class _FakeResponse:
    """Enough of httpx.Response for the error branches."""

    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        self.text = text

    def read(self) -> None:
        pass

    def iter_lines(self):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeClient:
    """Records the request so the key can be checked for leaks, then replies."""

    last_url = None
    last_params = None
    last_headers = None
    last_json = None

    def __init__(self, status: int, text: str) -> None:
        self._status, self._text = status, text

    def __call__(self, *_a, **_kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def stream(self, _method, url, *, params=None, headers=None, json=None):
        type(self).last_url = str(url)
        type(self).last_params = params
        type(self).last_headers = headers
        type(self).last_json = json
        return _FakeResponse(self._status, self._text)


def _with_fake(status: int, text: str, key: str = "SECRET-KEY-DO-NOT-LEAK"):
    """Run one stream() against a stubbed transport. Returns the exception raised."""
    import os

    import httpx

    fake = _FakeClient(status, text)
    real_client, real_timeout = httpx.Client, httpx.Timeout
    os.environ["TESSA_TEST_KEY"] = key
    httpx.Client = fake            # type: ignore[assignment]
    httpx.Timeout = lambda *a, **k: None  # type: ignore[assignment]
    try:
        eng = GeminiLLM({"api_key_env": "TESSA_TEST_KEY", "models": {"main": "gemini-3.6-flash"}})
        try:
            list(eng.stream("sys prompt", [Message(role="user", content="hello")]))
        except LLMUnavailable as exc:
            return exc
        return None
    finally:
        httpx.Client, httpx.Timeout = real_client, real_timeout  # type: ignore[assignment]


def main() -> int:
    print("\nbrain: three engines, one interface\n")
    settings = yaml.safe_load((ROOT / "core" / "config" / "settings.yaml").read_text(encoding="utf-8"))

    # ── 1. selection, and no substitution
    for name in ("gemini", "anthropic", "local"):
        eng = make_engine({"brain": {"engine": name}})
        check(f"engine={name!r} builds {type(eng).__name__}", eng.name.startswith(name))
    try:
        make_engine({"brain": {"engine": "gpt4"}})
        check("an unknown engine raises rather than guessing", False)
    except ValueError:
        check("an unknown engine raises rather than guessing", True)

    rows = describe_engines(settings)
    check("describe_engines reports all three", len(rows) == 3)
    check("exactly one engine is marked selected", sum(1 for r in rows if r[1]) == 1)

    # NO FALLBACK: an unusable engine is still the one returned.
    unusable = make_engine({"brain": {"engine": "anthropic",
                                      "anthropic": {"api_key_env": "NOT_SET_ANYWHERE"}}})
    ok, why = unusable.available
    check("an unusable engine reports itself unusable", not ok, why)
    check("...and is STILL the engine returned (no silent substitution)",
          unusable.name.startswith("anthropic"))

    # ── 2 & 3. the rate limit, in her voice
    exc = _with_fake(429, '{"error":{"message":"Quota exceeded"}}')
    check("a 429 raises LLMUnavailable", isinstance(exc, LLMUnavailable))
    spoken = getattr(exc, "spoken", "")
    check("...and she SAYS she is rate limited", "rate limited" in spoken.lower(), spoken)
    check("...naming him", "Emperor" in spoken, spoken)
    print(f'        SHE SAYS: "{spoken}"')

    exc404 = _with_fake(404, '{"error":{"message":"no longer available to new users"}}')
    check("a 404 explains it is the model name", "model name" in getattr(exc404, "spoken", ""),
          getattr(exc404, "spoken", ""))
    print(f'        SHE SAYS: "{exc404.spoken}"')

    exc401 = _with_fake(401, '{"error":{"message":"bad key"}}')
    check("a 401 says the key was rejected", "key" in getattr(exc401, "spoken", "").lower())
    print(f'        SHE SAYS: "{exc401.spoken}"')

    # ── 4. the key does not leak
    key = "SECRET-KEY-DO-NOT-LEAK"
    check("the key is NOT in the request URL", key not in (_FakeClient.last_url or ""))
    check("the key is NOT in the query params", key not in str(_FakeClient.last_params))
    check("the key IS in the x-goog-api-key header",
          (_FakeClient.last_headers or {}).get("x-goog-api-key") == key)
    check("the key is NOT in the exception message", key not in str(exc401))
    check("the key is NOT in the spoken sentence", key not in exc401.spoken)
    src = inspect.getsource(GeminiLLM)
    check("gemini.py never prints or logs the key",
          "print(self._key" not in src and "log(self._key" not in src)

    # ── 5. system never becomes a user turn
    body = _FakeClient.last_json or {}
    roles = [c.get("role") for c in body.get("contents", [])]
    check("no 'system' role in contents", "system" not in roles, str(roles))
    check("system went to system_instruction instead", "system_instruction" in body)
    texts = [p.get("text") for c in body.get("contents", []) for p in c.get("parts", [])]
    check("the system prompt is NOT in contents", "sys prompt" not in texts, str(texts))

    # ── 6. thinking is turned down by default (latency)
    cfg = (body.get("generationConfig") or {}).get("thinkingConfig") or {}
    check("thinkingLevel defaults to low", cfg.get("thinkingLevel") == "low", str(cfg))

    print(f"\n{_passed} passed, {_failed} failed\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
