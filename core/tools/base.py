"""
core/tools/base.py — the shape every Windows tool has.

CLAUDE.md INVARIANT 4 IS ENFORCED BY SHAPE, NOT BY DISCIPLINE. A tool is a
NAME, a TIER, and a handler that takes a typed dict of ARGS. There is no field
anywhere in this module that carries a command line, and no handler receives a
string it is expected to execute. `shell.execute` is the single exception and it
is RED, confirmed, and audited — see `core/tools/shell.py`, which explains why it
exists at all rather than pretending it does not.

WHY THE SPEECH LIVES IN THE SPEC

Her success and failure lines are data on the spec, not scattered through the
handlers. Two reasons, and the second is the real one:

  1. The whole surface can be printed as a table — every tool, its tier, what
     she says when it works and when it does not. A voice agent whose replies
     are only discoverable by reading 30 handlers is one whose character drifts
     silently.
  2. tessa.md's rules ("name what broke, offer the nearest real thing", short
     first sentence because Piper streams per sentence and the opener is the
     whole 400 ms budget) are then enforced in ONE place. A handler cannot
     accidentally answer in a different voice.

Handlers return a plain dict of facts. The spec formats it. A handler that
cannot do the job raises `ToolError(reason, alternative)` and never returns a
sentence of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class ToolError(Exception):
    """
    A failure she can explain.

    `reason` names what actually broke. `alternative` is the nearest real thing
    she can still do. tessa.md bans the bare apology: "that failed" tells him
    nothing and sends him looking in the wrong place.
    """

    def __init__(self, reason: str, alternative: str = "Tell me another way and I will try again.") -> None:
        super().__init__(reason)
        self.reason = reason
        self.alternative = alternative


class ToolHold(Exception):
    """
    Not a failure — a deliberate stop before a destructive thing.

    Raised by a handler that has verified WHAT it is about to destroy and wants
    the owner to hear the specifics before it happens. `detail` is spoken back
    to him verbatim, so it must name the actual target ("14 files in
    C:\\Users\\SERIOUS-PC\\Downloads\\old"), never a category.
    """

    def __init__(self, detail: str, resolved: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        #: THE RESOLVED TARGET, when the handler resolved one (X engagement
        #: round, 2026-09-12). "like post two" arrives as `{index: 2}`; the
        #: handler resolves that ordinal against the snapshot of the last read
        #: and hands the STATUS ID back here, so the executor arms the hold on
        #: the id rather than on the position — a green read between the hold
        #: and his "yes" would otherwise move "post two" onto somebody else's
        #: post. None means "arm on the args as they arrived", which is every
        #: hold that existed before this field.
        #:
        #: NAMED `resolved`, NOT `args`, AND THAT IS NOT STYLE. `BaseException.
        #: args` is a descriptor whose setter coerces any value to a tuple:
        #: `self.args = {"post_id": x}` silently became `("post_id",)`, and
        #: `self.args = None` raised. The round-2 proof caught it on its first
        #: run; nothing else would have, because every plain ToolHold would
        #: have died at construction.
        self.resolved: dict[str, Any] | None = dict(resolved) if resolved else None


@dataclass(frozen=True)
class ToolSpec:
    """
    One tool. `capability` is the key in permissions.yaml that governs it, and
    it is checked at import time by `core.tools.REGISTRY` — a tool whose
    capability is not classified in that file cannot be registered at all.

    That check is the point. permissions.yaml is "THE SINGLE AUTHORITY on
    permission tiers" (CONTRACT §6.4), and a tool carrying its own `tier="green"`
    with no entry in that file would be a second authority quietly disagreeing
    with the first.
    """

    name: str
    tier: str                                    # green | amber | red
    capability: str                              # must exist in permissions.yaml
    handler: Callable[..., dict[str, Any]]
    #: What he might actually say. Used by the report and by the phrasing tests,
    #: not by the matcher — the matcher is regex in `core/brain/intents.py`,
    #: because "open my downloads" and "downloads" are one intent and a literal
    #: phrase list can never cover that.
    phrasings: tuple[str, ...] = ()
    #: Formatted with the handler's returned dict.
    success: str = "Done, Emperor."
    #: Formatted with {reason} and {alternative}.
    failure: str = "That failed, sir. {reason} {alternative}"
    #: True when the tool must never fire on the first ask, whatever the tier
    #: says. Every RED tool sets it; `fs.delete` is the reason it exists.
    holds: bool = False
    #: ARGUMENT NAMES THE APPROVAL CARD MAY NOT EDIT.
    #:
    #: `resolve_edit` exists so he can fix wording on the card before a red
    #: action runs — Whisper mangles dictation and the card is where that gets
    #: corrected. But some arguments are not wording, they are the TARGET, and
    #: letting a surface change the target of an already-approved action is a
    #: different thing entirely: he approves a reply to a friend, an edited
    #: frame lands the same words under a stranger's post, publicly, under his
    #: name.
    #:
    #: So a tool may declare which of its arguments are frozen. `x.reply`
    #: freezes `reply_to_id` and leaves `text` editable. Empty by default, so
    #: every existing tool keeps exactly the behaviour it had.
    frozen: tuple[str, ...] = ()
    #: RESOLVE THE TARGET BEFORE THE CARD (X publishing round 3, 2026-09-12).
    #:
    #: A RED handler is never reached before the approval card: the executor
    #: raises the request from the DISPATCH ARGS and stops. So a red tool whose
    #: target arrives as an ordinal — "quote post two" — has nowhere to turn
    #: "two" into a status id before he reads it, and a card that says "post
    #: two" is a card that says nothing. `resolve(args) -> args` runs in the
    #: red gate, ONCE, before the request is raised: it may replace an index
    #: with the frozen id, fill in what the card must SHOW (the quoted post,
    #: its author, how many instruction-shaped patterns it carried, a thread's
    #: posts in order), and raise ToolError to REFUSE before the card (bulk,
    #: over-length, a post she has not read). The amber twin of this is
    #: `ToolHold.resolved`. None for every tool that existed before this field.
    resolve: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    #: What she names back to him for a red tool's card, from the ARGS —
    #: "quote @ada's post 91010 saying: …" rather than "x.quote on …". The
    #: same hook `core/system/capability.Capability.describe` gives an
    #: ability, on a hand-written spec. May raise ToolError to refuse before
    #: the card; any other failure falls back to the generic line, because a
    #: card must never fail to be raised over wording. None keeps the generic
    #: line.
    describe: Callable[[dict[str, Any]], str] | None = None
    #: PRIVATE (X direct messages, round 4, 2026-09-12). A tool whose result,
    #: or whose spoken turn, is HIS private correspondence rather than public
    #: text. The executor never hands a private tool's result to the claim or
    #: style stores (his inbox is not learning material), and the daemon
    #: withholds a private turn's heard/said text from the audit chain and
    #: the log. It changes nothing about tiers, the fence or the card.
    private: bool = False
    #: ARGUMENT NAMES WHOSE VALUES NEVER REACH THE CHAIN. Every chain line
    #: that would carry the argument — PENDING-APPROVAL, REQUESTED, APPROVED,
    #: DENIED, HELD, ran — records `<withheld: N chars, sha256 …>` in its
    #: place (Executor.chain_args). The card still shows the value: the
    #: surface is where he reads what he approves; the chain records THAT he
    #: approved it, and a digest he can check a text against. `x.send_dm`
    #: withholds `text`. Empty for every tool that existed before this field.
    private_args: tuple[str, ...] = ()
    #: One line for the audit entry, formatted with the ARGS (not the result) so
    #: the log records what was ASKED even when the tool then failed.
    audit: str = "{name}"
    #: Free-text note surfaced in the report, for anything a reader would
    #: otherwise have to open the handler to learn.
    note: str = ""


@dataclass
class ToolResult:
    ok: bool
    speech: str
    detail: dict[str, Any] = field(default_factory=dict)
    #: Set when the tool stopped to ask rather than failing.
    held: bool = False


TIERS = ("green", "amber", "red")
