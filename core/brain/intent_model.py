"""
core/brain/intent_model.py — what he MEANS, decided by her brain, not by a table.

THE COMPLAINT THIS ANSWERS (his transcript, 2026-09-22)

    "open X so I can log in"   -> "I do not know what CAN LOGIN is"
    "open X"                   -> "Did you mean Xagent, Xftp, Xshell?"
    "read my X timeline"       -> "X timeline is not there, give me another path"

Every one of those went wrong the same way: a regex table (phrasings.py) matched
literally or not at all, and what fell through hit a dead end — an app index
that thinks "X" is a substring of Xshell, a file rule that thinks "X timeline"
is a path, or `unresolved_refusal` asking for a full path. Adding three more
regexes would fix three sentences and leave the architecture exactly as
brittle for the fourth.

THE DESIGN

The fast path STAYS FIRST. It is free, offline and 0.3 ms, and "set volume to
40" must never wait on a network round trip. What changes is what happens at
its edges:

  1. WHEN THE FAST PATH IS SURE, nothing here runs.
  2. WHEN THE FAST PATH IS IN DOUBT — an ambiguous application, a fuzzy guess,
     or a platform name (X, Twitter) sitting in a file path or a window name —
     the router marks the result (`Routed.doubt`) and `second_opinion` asks the
     model to confirm or correct it, with the router's guess as a hint.
  3. WHEN THE FAST PATH HAS NOTHING, the utterance goes to the model with the
     catalogue of tools she actually has, and the model returns ONE tool name
     plus structured arguments — or says it is a question for the chat brain,
     or asks one short clarifying question. Never "give me the full path".

THE MODEL NEVER GETS A COMMAND STRING TO RUN (CLAUDE.md invariant 4). It gets
tool NAMES and argument schemas, and hands back a name and a dict. Python owns
execution, exactly as before. And every call it builds is stamped
`origin="agent"` — the card, the hold, the chain and shell.execute's own guard
all see a model-built call for what it is. Understanding got broad; the gates
did not move. See core/brain/executor.py for what "agent" costs a call.

WHAT THE MODEL IS NOT OFFERED

`shell.execute` (a model is never even asked to compose a command line),
`claims.confirm` / `claims.reject` (owner-only by signature), `context.forget`
(his explicit act to drop the fence), `browser.submit` (a red submit with no
target — nothing to resolve), and both DM tools: the direct-message round ruled
that his private correspondence never routes through a cloud model, and a
tool the model cannot name is a tool it cannot reach.

HONEST COSTS

One model call, ~3.5 s on gemini-3.6-flash with JSON output (measured; the
lite model was no faster and answered "Here is the JSON"). Under a spent quota
the existing fallback applies — this is ROUTINE work, so the local model may
answer, and a garbage answer is caught by validation: an unknown tool or a
missing required argument is "none", never an action. With no brain at all,
every caller falls back to exactly what she did before this file existed.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from .tools_local import ToolCall

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "core" / "config" / "settings.yaml"

KIND_TOOL, KIND_CHAT, KIND_CLARIFY, KIND_NONE = "tool", "chat", "clarify", "none"
KINDS = (KIND_TOOL, KIND_CHAT, KIND_CLARIFY, KIND_NONE)

#: Tokens for the whole JSON answer. A name, a small dict and ten words of
#: reasoning fit in a third of this; the headroom is for a clarifying question.
MAX_TOKENS = 220

#: Tools the model is never offered — see the module docstring.
NEVER_OFFERED = frozenset({
    "shell.execute", "claims.confirm", "claims.reject", "context.forget",
    "browser.submit", "x.read_dm", "x.send_dm",
})

#: Parameters the EXECUTOR supplies by signature. Never in a schema, never
#: accepted from a model — the executor strips them from args anyway
#: (`_FORGEABLE_ARG_KEYS`), this just keeps them out of the model's sight.
_EXECUTOR_PARAMS = frozenset({"provenance", "confirmed", "_approved_by_surface", "_request_id"})

#: THE ONE CLOSED SET OF NAME CLASHES. "X" is a social platform she has eleven
#: tools for and also the first letter of three programs on his Start Menu.
#: These words, appearing as a whole token in the TARGET of a tool that is not
#: an X or browser tool, mean the fast path may have guessed wrong — and the
#: model, which has the sentence, decides. Derived from the tool family she
#: owns (`x.*`), not from a phrase list.
PLATFORM_WORDS = frozenset({"x", "twitter", "x.com", "twitter.com"})
_PLATFORM_FAMILIES = frozenset({"x", "browser", "web"})
_PLATFORM_EXEMPT = frozenset({"app.open_url"})

#: The hand-written tools the executor still serves outside `core/tools`
#: (`Executor._LEGACY_TIERS`). The model needs `app.open` above all: it is the
#: only way to launch an installed program, and it is where "X" went wrong.
_LEGACY: dict[str, tuple[str, str, dict[str, str]]] = {
    "app.open": ("green",
                 "launch an INSTALLED Windows program by its name — Chrome, Notepad, "
                 "VS Code, Xshell. Not for websites or the X platform.",
                 {"app": "str — the program's name as he said it"}),
    "app.open_folder": ("green",
                        "open a folder in Explorer: Downloads, Documents, Desktop, or a path.",
                        {"path": "str — a folder name he uses or a full path"}),
    "app.open_url": ("green",
                     "open a web address in his default browser.",
                     {"url": "str — the address"}),
}


@dataclass(frozen=True)
class ToolInfo:
    name: str
    tier: str
    summary: str
    args: dict[str, str]

    def line(self) -> str:
        args = ", ".join(f"{k}: {v}" for k, v in self.args.items()) or "no arguments"
        return f"- {self.name} [{self.tier}] ({args}) — {self.summary}"


@dataclass
class Resolution:
    """One answer from the model, validated. `kind` decides what the caller does."""
    kind: str = KIND_NONE
    call: ToolCall | None = None
    question: str = ""
    why: str = ""
    engine: str = ""
    ms: float = 0.0
    raw: str = ""
    error: str = ""
    #: A sentence to SAY when the brain itself failed (`LLMUnavailable.spoken`).
    spoken: str = ""
    dropped_args: list[str] = field(default_factory=list)


# ── the catalogue ────────────────────────────────────────────────────────────

_CATALOGUE: list[ToolInfo] = []
_CATALOGUE_KEY: tuple[int, ...] = ()


def _hint(param: inspect.Parameter) -> str:
    d = param.default
    if d is inspect._empty:
        return "REQUIRED"
    if isinstance(d, bool):
        return f"bool, default {d}"
    if isinstance(d, int):
        return f"int, default {d}"
    if isinstance(d, float):
        return f"number, default {d}"
    if d is None:
        return "optional"
    return f"str, default {d!r}"


def _summary(spec: Any) -> str:
    """Two of his phrasings and the note's first sentence — ~4k tokens for the
    whole catalogue, which is the per-call price of understanding him."""
    examples = ", ".join(f'"{p}"' for p in (getattr(spec, "phrasings", ()) or ())[:2])
    note = str(getattr(spec, "note", "") or "").split(". ")[0].strip()
    parts = []
    if examples:
        parts.append(f"e.g. {examples}")
    if note:
        parts.append(note[:90])
    return "; ".join(parts) or spec.name


def catalogue() -> list[ToolInfo]:
    """
    Every tool the model may name, one line each. Rebuilt when the registry
    changes size (the vault registers its tool at boot), cached otherwise.
    """
    global _CATALOGUE, _CATALOGUE_KEY
    from core.tools import REGISTRY

    key = (len(REGISTRY), len(_LEGACY))
    if _CATALOGUE and _CATALOGUE_KEY == key:
        return _CATALOGUE
    out: list[ToolInfo] = []
    for name, spec in REGISTRY.items():
        if name in NEVER_OFFERED:
            continue
        args: dict[str, str] = {}
        try:
            for p in inspect.signature(spec.handler).parameters.values():
                if p.name in _EXECUTOR_PARAMS or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                    continue
                args[p.name] = _hint(p)
        except (TypeError, ValueError):
            pass
        out.append(ToolInfo(name, spec.tier, _summary(spec), args))
    for name, (tier, summary, args) in _LEGACY.items():
        out.append(ToolInfo(name, tier, summary, dict(args)))
    _CATALOGUE, _CATALOGUE_KEY = out, key
    return out


def catalogue_text() -> str:
    return "\n".join(t.line() for t in catalogue())


def _known(name: str) -> ToolInfo | None:
    return next((t for t in catalogue() if t.name == name), None)


# ── the prompt ───────────────────────────────────────────────────────────────

SYSTEM = """You resolve what the OWNER of a Windows PC means by one spoken or typed instruction to his assistant, Tessa. Speech-to-text may have mangled words ("login" for "log in", "Taluts" for "documents").

Pick exactly one tool from TOOLS, or say it is not a tool request. Rules:
- "X" and "Twitter" mean the social platform X (x.com) — opening it, logging into it, getting on it, bringing it up all mean x.login; his timeline, feed, "what's on X" mean x.read_timeline. Only a clearly named installed program ("Xshell", "the Xftp app") is an application.
- Never invent a tool or an argument. Use only the listed names and argument keys. Copy his own words verbatim into text arguments; never add options he did not say.
- A question he wants ANSWERED — an explanation, a definition, maths, advice, an opinion, general knowledge, or just talking to Tessa — is kind "chat": Tessa answers those herself from what she knows. Choose web.search or browser.search only when he asks to search or look something up, or asks for LIVE facts (weather, prices, news, scores).
- If it is an instruction you cannot map to any tool, kind is "none". If two tools fit equally and the difference matters, kind is "clarify" with one short question in Tessa's voice (she calls him Emperor). Clarify rarely.
- Anything dangerous (delete, kill, post, send, shutdown, install) may still be picked: Tessa's own approval gates decide whether it runs. Your job is only WHAT he meant.

Answer with ONE JSON object and nothing else:
{"kind": "tool" | "chat" | "clarify" | "none", "tool": "<name or null>", "args": {}, "question": "<only for clarify>", "why": "<under 12 words>"}

TOOLS (name [tier] (arguments) — what it does):
"""


def _system_prompt() -> str:
    return SYSTEM + catalogue_text()


def _user_prompt(text: str, hint: ToolCall | None) -> str:
    out = f"Instruction: {text.strip()}"
    if hint is not None:
        out += (f"\nThe fast pattern router guessed {hint.name} with args {json.dumps(hint.args)}. "
                f"Confirm it only if that is what he meant; otherwise correct it.")
    return out


# ── settings ─────────────────────────────────────────────────────────────────

_SETTINGS: dict[str, Any] = {"stamp": None, "brain": {}, "value": {}}


def settings() -> dict[str, Any]:
    """`brain.intent` from settings.yaml, cached on mtime. Missing means enabled."""
    try:
        st = SETTINGS_PATH.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        if _SETTINGS["stamp"] != stamp:
            import yaml

            raw = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
            _SETTINGS["brain"] = dict(raw.get("brain") or {})
            _SETTINGS["value"] = dict((_SETTINGS["brain"].get("intent") or {}))
            _SETTINGS["stamp"] = stamp
    except Exception:  # noqa: BLE001 — a settings hiccup must never break a turn
        pass
    return _SETTINGS["value"]


def enabled() -> bool:
    return bool(settings().get("enabled", True))


# ── which engine answers ─────────────────────────────────────────────────────
#
# ITS OWN MODEL, ITS OWN QUOTA. The free tier caps REQUESTS PER DAY PER MODEL,
# and the first live run of this file spent gemini-3.6-flash's cap in an
# afternoon: every intent call is one request, on top of every chat answer.
# `brain.intent.model` names a model for intent alone — gemini-3.1-flash-lite,
# which returned valid JSON for 7 of 7 utterances in 3–5 s on the full
# catalogue — so understanding him does not eat the budget he talks to her on.
# Built through the same `_build` selector as the daemon's brain (same engine
# family, same key, same wire code), bare: no quota wrapper, because a spent
# quota here must be SAID, never answered by the 0.5B model (see `resolve`).
_ENGINES: dict[str, Any] = {}


def _engine_for(brain: Any) -> Any:
    settings()                                   # refresh the cache
    model = str(settings().get("model") or "").strip()
    if not model:
        return brain
    if model in _ENGINES:
        return _ENGINES[model]
    try:
        from .llm import _build

        family = str(_SETTINGS["brain"].get("engine", "gemini")).strip().lower()
        cfg = dict(_SETTINGS["brain"].get(family) or {})
        cfg["models"] = {**dict(cfg.get("models") or {}), "main": model}
        engine = _build(family, cfg)
        ok, _why = engine.available
        _ENGINES[model] = engine if ok else brain
    except Exception:  # noqa: BLE001 — a bad intent model name falls back to his brain
        _ENGINES[model] = brain
    return _ENGINES[model]


def _too_slow(engine: Any) -> bool:
    """
    The local 0.5B model cannot do this in acceptable time — measured 138 s
    for one pick on the 4k-token catalogue, on two cores. Unless he says
    `allow_local: true`, a local-only brain skips intent resolution and she
    answers the way she did before this file existed.
    """
    if bool(settings().get("allow_local", False)):
        return False
    # THE PRIMARY, NOT THE WRAPPER'S CURRENT ANSWERER. `FallbackLLM.name`
    # reports whichever engine will answer NOW, so a wrapper sitting on its
    # local backup after a 429 reads as "local:" — and skipping it here would
    # swallow the spoken quota sentence the wrapper is about to raise
    # (proven: she said "give me the full path" instead of "over its quota").
    primary = getattr(engine, "primary", engine)
    return str(getattr(primary, "name", "")).startswith("local:")


# ── parsing and validation ───────────────────────────────────────────────────

def _parse_json(raw: str) -> dict[str, Any] | None:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    for candidate in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t and "}" in t else ""):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _coerce(value: Any, hint: str) -> Any:
    if hint.startswith("int"):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if hint.startswith("bool"):
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1")
        return bool(value)
    if hint.startswith("number"):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if isinstance(value, (dict, list)):
        return value
    return str(value)


def _resolve_app(args: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """
    `app.open` needs an index KEY; the model has a name. Resolve it once,
    through the same index the fast path uses, and ask rather than guess when
    the index itself is unsure — `appindex.resolve` returning several entries
    is genuine ambiguity, and this is the one place a model-picked launch could
    otherwise land on the wrong program.
    """
    from .appindex import get_index

    name = str(args.get("app") or "").strip()
    if not name:
        return None, "Which program, Emperor?"
    entries, how = get_index().resolve(name)
    if len(entries) == 1:
        return {"app": entries[0].key, "match": how}, ""
    if len(entries) > 1:
        names = ", ".join(e.name for e in entries[:3])
        return None, f"Which one, Emperor? {names}."
    return None, f"I cannot find a program called {name}, Emperor."


def _validate(obj: dict[str, Any], text: str) -> Resolution:
    kind = str(obj.get("kind") or "").strip().lower()
    tool = obj.get("tool")
    tool = str(tool).strip() if tool else ""
    args_in = obj.get("args") if isinstance(obj.get("args"), dict) else {}
    why = str(obj.get("why") or "")[:120]
    question = str(obj.get("question") or "").strip()

    if kind not in KINDS:
        kind = KIND_TOOL if tool else KIND_NONE
    if kind == KIND_CLARIFY:
        if not question:
            return Resolution(kind=KIND_NONE, why=why, error="clarify with no question")
        return Resolution(kind=KIND_CLARIFY, question=question, why=why)
    if kind in (KIND_CHAT, KIND_NONE):
        return Resolution(kind=kind, why=why)

    info = _known(tool)
    if info is None:
        return Resolution(kind=KIND_NONE, why=why, error=f"unknown tool {tool!r}")

    args: dict[str, Any] = {}
    dropped: list[str] = []
    for k, v in args_in.items():
        k = str(k)
        if k in info.args and k not in _EXECUTOR_PARAMS:
            args[k] = _coerce(v, info.args[k])
        else:
            dropped.append(k)
    missing = [k for k, h in info.args.items() if h == "REQUIRED" and args.get(k) in (None, "")]
    if missing:
        return Resolution(kind=KIND_CLARIFY, why=why, dropped_args=dropped,
                          question=f"Which {missing[0].replace('_', ' ')}, Emperor?")

    if tool == "app.open":
        resolved, ask = _resolve_app(args)
        if resolved is None:
            return Resolution(kind=KIND_CLARIFY, question=ask, why=why, dropped_args=dropped)
        args = resolved

    # ORIGIN IS "agent", AND IT IS SET HERE, ONCE, BY THE BUILDER. The executor
    # reads only this field for provenance — a `provenance` key the model put
    # in args was dropped above and would be stripped again at dispatch.
    call = ToolCall(name=tool, args=args, tier=info.tier, speech="", origin="agent")
    return Resolution(kind=KIND_TOOL, call=call, why=why, dropped_args=dropped)


# ── the model call ───────────────────────────────────────────────────────────

def resolve(text: str, brain: Any, *, hint: ToolCall | None = None,
            log: Callable[[str], None] | None = None) -> Resolution:
    """
    Ask the brain what `text` means. Never raises: a brain that cannot answer
    comes back as `kind="none"` with `error` set and, for an `LLMUnavailable`,
    the sentence she should say in `spoken`.
    """
    say = log or (lambda _m: None)
    if brain is None:
        return Resolution(kind=KIND_NONE, error="no brain")
    if not enabled():
        return Resolution(kind=KIND_NONE, error="brain.intent.enabled is false")
    from .llm import QUALITY_CRITICAL, DegradedRefusal, LLMUnavailable, Message, RateLimited
    from .repair import strip_wake_name

    engine_obj = _engine_for(brain)
    engine_name = str(getattr(engine_obj, "name", ""))
    if _too_slow(engine_obj):
        say(f"intent: {engine_name} is too slow for intent resolution — skipped")
        return Resolution(kind=KIND_NONE, error="local engine skipped", engine=engine_name)

    asked = strip_wake_name(text or "")[0] or (text or "")
    max_tokens = int(settings().get("max_tokens", MAX_TOKENS) or MAX_TOKENS)
    messages = [Message(role="user", content=_user_prompt(asked, hint))]
    t0 = time.perf_counter()
    raw = ""
    for attempt in (1, 2):
        try:
            # CRITICAL, NOT ROUTINE, AND THE FIRST LIVE RUN IS WHY. A per-minute
            # 429 sent this call to the local 0.5B model: 138 seconds on a
            # 5k-token catalogue, and it answered "show my feed" with win.focus
            # on a window called "my_feed". A tool pick is an ACTION, not a chat
            # line he can judge on sight, and two minutes of silence is a broken
            # voice turn. So under a spent quota the fallback wrapper raises
            # instead of substituting, this returns "none" with a sentence to
            # say, and the callers fall back to what she did before this file.
            done = engine_obj.complete(_system_prompt(), messages, max_tokens=max_tokens,
                                       thinking=False, quality=QUALITY_CRITICAL,
                                       json_object=True)
            raw = done.text
            break
        except LLMUnavailable as exc:
            ms = (time.perf_counter() - t0) * 1000
            # ONE RETRY ON A 5xx. Google's "high demand" 503 came back on 3 of
            # 25 live calls today and clears in a second; a 4xx or a quota does
            # not, and is not retried.
            if attempt == 1 and not isinstance(exc, RateLimited) and "HTTP 5" in str(exc):
                say(f"intent: {exc} — retrying once")
                time.sleep(1.5)
                continue
            say(f"intent: brain unavailable after {ms:.0f} ms — {exc}")
            spoken = exc.spoken
            if isinstance(exc, (DegradedRefusal, RateLimited)):
                # The wrapper's sentence is about DRAFTING on the weak model,
                # the engine's about trying again; for working out what he
                # meant, the honest line is this one.
                spoken = ("My thinking brain is over its quota, Emperor, so I cannot work "
                          "out what you meant just now. Say it the plain way, or try me later.")
            return Resolution(kind=KIND_NONE, error=str(exc), spoken=spoken, ms=ms,
                              engine=engine_name)
        except Exception as exc:  # noqa: BLE001
            ms = (time.perf_counter() - t0) * 1000
            say(f"intent: brain failed {type(exc).__name__}: {exc}")
            return Resolution(kind=KIND_NONE, error=f"{type(exc).__name__}: {exc}", ms=ms,
                              engine=engine_name)
    ms = (time.perf_counter() - t0) * 1000
    engine = getattr(engine_obj, "last_answered_by", None) or engine_name

    obj = _parse_json(raw)
    if obj is None:
        say(f"intent: unparseable answer from {engine} in {ms:.0f} ms: {raw[:80]!r}")
        return Resolution(kind=KIND_NONE, error="unparseable", raw=raw, ms=ms, engine=engine)
    res = _validate(obj, asked)
    res.raw, res.ms, res.engine = raw, ms, engine
    tool = res.call.name if res.call else "-"
    say(f"intent: {res.kind} {tool} in {ms:.0f} ms via {engine}"
        + (f" ({res.error})" if res.error else "")
        + (f" dropped={res.dropped_args}" if res.dropped_args else ""))
    return res


# ── the fast path's doubt ────────────────────────────────────────────────────

def doubt_for(calls: list[ToolCall]) -> str:
    """
    A reason the router should not trust its own parse, or "".

    Only the platform-name clash is decided here; the app index reports its
    own ambiguity and fuzziness through `Parse.doubt`. A word from
    `PLATFORM_WORDS` inside the target of a non-X, non-browser tool — a file
    path of "X timeline", a window called "up X" — is that reason.
    """
    for call in calls:
        family = call.name.split(".", 1)[0]
        if family in _PLATFORM_FAMILIES or call.name in _PLATFORM_EXEMPT:
            continue
        for key, value in (call.args or {}).items():
            # A URL is allowed to be x.com: "open x.com in the browser" was
            # parsed right, and a web address is never an application name.
            if not isinstance(value, str) or key == "url":
                continue
            for word in re.findall(r"[a-z][\w.]*", value.lower()):
                if word.rstrip(".") in PLATFORM_WORDS:
                    return f"platform word {word!r} in {call.name} {key}"
    return ""


def second_opinion(routed: Any, text: str, brain: Any, *,
                   log: Callable[[str], None] | None = None) -> Any:
    """
    Let the model confirm or correct a fast-path result the router doubted.

    Returns `routed` itself when there is no doubt, no brain, or the model
    declined (chat / none / failure) — the fast path's own answer stands, which
    is exactly what she did before. A model TOOL replaces the calls; a
    CLARIFY replaces the speech with the question and runs nothing.
    """
    doubt = getattr(routed, "doubt", "")
    if not doubt or brain is None or not enabled():
        return routed
    from .router import Intent

    say = log or (lambda _m: None)
    hint = routed.calls[0] if routed.calls else None
    say(f"intent: second opinion — {doubt}")
    res = resolve(text, brain, hint=hint, log=log)
    if res.kind == KIND_TOOL and res.call is not None:
        return replace(routed, intent=Intent.TOOL, speech="", score=1.0,
                       calls=[res.call], doubt="")
    if res.kind == KIND_CLARIFY and res.question:
        return replace(routed, intent=Intent.TOOL, speech=res.question, score=1.0,
                       calls=[], doubt="")
    return routed
