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
  2. zoey.md's rules ("name what broke, offer the nearest real thing", short
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
    she can still do. zoey.md bans the bare apology: "that failed" tells him
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

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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
