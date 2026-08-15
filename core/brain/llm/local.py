"""
core/brain/llm/local.py — the local brain, on CTranslate2.

WHY CTRANSLATE2 AND NOT llama-cpp-python

The brief suggested llama-cpp-python. It cannot be used on this machine:

    pip install --only-binary=:all: llama-cpp-python
    ERROR: Could not find a version that satisfies the requirement
           (from versions: none)

There is NO prebuilt wheel for cp312/win_amd64 — it is source-only on PyPI and
needs CMake plus MSVC, neither of which exists here. Per the brief's own stop
condition I did not attempt a source build.

CTranslate2 is the answer and it costs ZERO new bytes: it is already installed,
because faster-whisper runs on it. It exposes `Generator` for causal language
models, supports int8 on CPU, and is the same inference engine already proven
working on this exact machine. A second runtime would have been a new download,
a new failure surface, and a second thing to keep alive.

    ctranslate2 4.8.1 | Generator: True
    supported compute types (cpu): {'int8', 'int8_float32', 'int16', 'float32'}

STREAMING IS REAL HERE. `generate_tokens` yields token by token, so Zoey can
begin speaking after the first sentence instead of after the whole answer —
which is the entire reason `stream()` is the primary method in base.py.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

from .base import LLMAdapter, LLMUnavailable, Message, ToolDef, Usage

ROOT = Path(__file__).resolve().parents[3]
MODEL_ROOT = ROOT / "data" / "models"

#: ChatML control tokens. Qwen's template emits these as ordinary tokens and
#: CTranslate2 hands them straight through, so they reach the text she speaks
#: unless they are cut here. `<|endoftext|>` is included for the base-model
#: variants that use it instead.
_STOP_TOKENS = {"<|im_end|>", "<|im_start|>", "<|endoftext|>"}


class LocalLLM(LLMAdapter):
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg or {}
        self.repo = str(self.cfg.get("repo", "jncraton/Qwen2.5-0.5B-Instruct-ct2-int8"))
        self.compute_type = str(self.cfg.get("compute_type", "int8"))
        self.threads = int(self.cfg.get("threads", 4))
        self._gen: Any = None
        self._tok: Any = None
        self._dir: Path | None = None

    @property
    def name(self) -> str:
        return f"local:{self.repo.rsplit('/', 1)[-1]}"

    @property
    def available(self) -> tuple[bool, str]:
        """
        `find_spec`, NOT `import`. Measured: importing transformers to answer
        "is the local brain installed?" added **5 SECONDS** to daemon boot,
        because the daemon reports all three engines at start-up and this was
        the third. It also drags PyTorch's absence warning into the log of a
        process that was never going to use PyTorch.

        `find_spec` answers the same question — is the package importable —
        without executing it, in microseconds. The real import still happens in
        `_load()`, where the cost buys something.
        """
        from importlib.util import find_spec

        for mod in ("ctranslate2", "transformers"):
            try:
                if find_spec(mod) is None:
                    return False, f"missing {mod}"
            except (ImportError, ValueError):
                return False, f"missing {mod}"
        return True, ""

    def _load(self) -> None:
        if self._gen is not None:
            return
        ok, why = self.available
        if not ok:
            raise LLMUnavailable(
                f"local brain unavailable: {why}",
                spoken=f"My local brain is not installed, sir. {why}.",
            )
        import ctranslate2
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer

        self._dir = Path(snapshot_download(self.repo, cache_dir=str(MODEL_ROOT)))
        self._tok = AutoTokenizer.from_pretrained(str(self._dir))
        self._gen = ctranslate2.Generator(
            str(self._dir), device="cpu",
            compute_type=self.compute_type, inter_threads=1, intra_threads=self.threads,
        )

    def _prompt(self, system: str, messages: list[Message]) -> list[str]:
        """
        Chat template applied by the tokenizer, never hand-rolled.

        A hand-built prompt string is how a small model quietly stops following
        its system prompt: the template it was trained on has exact special
        tokens, and an approximation of them degrades instruction-following in a
        way that looks like the model being weak rather than misused.
        """
        chat = [{"role": "system", "content": system}]
        chat += [{"role": m.role, "content": m.content} for m in messages]
        text = self._tok.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        return self._tok.convert_ids_to_tokens(self._tok.encode(text))

    def stream(
        self,
        system: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        max_tokens: int = 1024,
        thinking: bool = False,
    ) -> Iterator[str]:
        self._load()
        tokens = self._prompt(system, messages)
        # `thinking` has no separate budget on a local model — there is no
        # extended-thinking mode to enable. It is honoured as headroom instead,
        # and the difference is stated rather than faked.
        limit = max_tokens * 2 if thinking else max_tokens
        buf: list[str] = []
        for result in self._gen.generate_tokens(
            tokens, max_length=limit, sampling_temperature=0.3, sampling_topk=20,
        ):
            # STOP TOKENS ARE NOT WORDS, AND CTRANSLATE2 DOES NOT STRIP THEM.
            # Measured live: "Ibati, Nigeria's capital.<|im_end|>" — Piper would
            # have read that last part out loud as "im end". The generator emits
            # the chat template's own control tokens as ordinary tokens, so the
            # break has to happen here.
            if result.token in _STOP_TOKENS:
                break
            buf.append(result.token)
            text = self._tok.convert_tokens_to_string(buf)
            if text:
                buf.clear()
                yield text

    def _usage_for(self, text: str) -> Usage:
        # Local inference costs NOTHING. Recording a fabricated naira figure
        # here would corrupt the ledger that the budget gate depends on.
        return Usage(output_tokens=max(1, len(text) // 4), cost_ngn=0.0, model=self.name)
