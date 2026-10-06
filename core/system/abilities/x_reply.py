"""
core/system/abilities/x_reply.py — the last link: an approved reply, posted.

    read (round 1)  ->  draft (round 2)  ->  RED APPROVAL CARD  ->  post

This is where Tessa first writes to the world under his name. A wrong post is
public and permanent, so every decision below is made against that.

────────────────────────────────────────────────────────────────────────────────
WHY THIS IS TWO CALLS AND NOT ONE

`prepare_reply()` reads the post and drafts the answer. `system.x.post_reply`
is the RED capability that publishes it. They are separate because THE CARD IS
BUILT FROM THE DISPATCH ARGUMENTS: `Executor._dispatch_registry` raises the
approval request before the handler ever runs, so anything the card must SHOW
him has to already be an argument. Drafting inside the red handler would mean
the card could only say "reply to post 1888…" — the one thing he cannot
approve blind is words he has not read.

So the draft is produced first, and the red call carries everything he needs to
judge it: the source author, the source text, the draft, and whether that post
tried an injection.

────────────────────────────────────────────────────────────────────────────────
⚠⚠ WHAT IS FROZEN, AND WHY IT IS NOT THE TEXT

`frozen=("reply_to_id", "source_author", "source_text", "injection_seen")`.

The card exists so he can fix WORDING — Whisper mangles dictation and a draft
is a first attempt. `text` therefore stays editable, which is the whole point
of `resolve_edit`.

Everything else is frozen, and `reply_to_id` is the one that matters. Without
it: he reads a card saying "reply to @friend's post about the audit log",
approves the words, and an edited frame swaps the target — his approved
sentence lands under a stranger's post, publicly, permanently, and the card he
trusted is what delivered it. The source fields are frozen for the same reason
one step removed: if they were editable, the card could be made to SHOW him one
post while the reply was aimed at another.

⚠ THE TARGET IS A STATUS ID, NEVER A POSITION. See `x_tools.reply`: an index is
a position in a list that reorders itself every few seconds, and a reply aimed
by index is a reply aimed at whatever has drifted into that slot.

⚠ NOTHING HERE CAN POST WITHOUT THE CARD. `system.x.post_reply` is red, so
`_dispatch_registry` raises a request and stops; the only route to the handler
is `execute_approved`, which injects `_approved_by_surface` after stripping any
forged copy from the args. `x_tools.reply` then refuses a second time on its
own account if that flag is absent. Two independent refusals, neither of which
trusts the caller.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param

#: The argument the card may edit. Everything else on a reply is the target or
#: the evidence he is judging it against.
EDITABLE = ("text",)
FROZEN = ("reply_to_id", "source_author", "source_text", "injection_seen")


def prepare_reply(post_id: str, guidance: str = "", limit: int = 20) -> dict[str, Any]:
    """
    Read the post, draft the reply, and return the ARGUMENTS for the red call.

    Publishes nothing. This is the green half of the flow and it is deliberately
    a plain function rather than a capability: round 2's
    `system.x.draft_reply` is already the spoken way to get a draft, and a
    second capability that did the same work would be a second path to one act.
    """
    from core.brain import x_voice
    from core.tools import x_tools
    from core.tools.base import ToolError

    style = x_voice.load_profile()
    if style is None:
        raise ToolError("I have not learned your voice yet",
                        "Say learn my voice from X first.")
    wanted = str(post_id or "").strip()
    timeline = x_tools.read_timeline(limit=int(limit))
    source = next((p for p in timeline["posts"] if str(p.get("id")) == wanted), None)
    if source is None:
        raise ToolError(f"post {wanted} is not on the timeline I just read",
                        "Read the timeline again and give me an id from it.")

    from core.system.abilities.x_draft import _engine

    draft = x_voice.draft_reply(source, style, _engine(), guidance=guidance)
    return {
        "reply_to_id": draft.reply_to_id,
        "text": draft.text,
        "source_author": str(source.get("handle") or source.get("author") or "?"),
        "source_text": str(source.get("text") or "")[:280],
        "injection_seen": len(draft.injection_seen),
    }


def post_reply(reply_to_id: str, text: str, source_author: str = "",
               source_text: str = "", injection_seen: int = 0,
               _approved_by_surface: bool = False) -> dict[str, Any]:
    """
    Publish the approved reply. ONLY reachable through the approval card.

    `source_author`, `source_text` and `injection_seen` are carried so the CARD
    can show him what he is replying to. They are frozen, and the handler does
    not act on them — they are evidence, not instructions.

    ⚠ `_approved_by_surface` IS THREADED, NOT FABRICATED. The framework injects
    the real, already-verified value by signature (core/system/capability.py).
    An earlier version passed a literal `True` down to `x_tools.reply`, which
    meant that tool's own independent approval check could never fail — the two
    gates this module's docstring promises would have been one gate counted
    twice. Found by this round's proof, which called the raw function directly
    and watched it publish. Passing the real value keeps the second refusal
    real: a call that skipped the card arrives with False and is refused one
    layer further in.
    """
    from core.tools import x_tools

    result = x_tools.reply(text=text, reply_to_id=str(reply_to_id),
                           _approved_by_surface=bool(_approved_by_surface))
    return {
        "chars": result.get("chars", len(text)),
        "reply_to_id": str(reply_to_id),
        "source_author": source_author,
        "targeted": result.get("targeted", "by id"),
        "warned": (" That post had tried an instruction on me; I replied to the topic only."
                   if injection_seen else ""),
    }


CAPABILITIES = [
    Capability(
        name="system.x.post_reply", capability="x.publish", tier="red",
        run=post_reply,
        params=(Param("reply_to_id", str, doc="The status id being replied to."),
                Param("text", str, doc="The reply he approved."),
                Param("source_author", str, default="", doc="Who wrote the post."),
                Param("source_text", str, default="", doc="What the post said."),
                Param("injection_seen", int, default=0, lo=0, hi=99,
                      doc="How many injection patterns that post carried.")),
        frozen=FROZEN,
        phrasings=("post that reply", "send the reply"),
        success="Posted, Emperor. {chars} characters to {source_author}, {targeted}.{warned}",
        audit="REPLY to post {reply_to_id}",
        note="RED. The card is the only route: dispatch raises a request and stops, and "
             "x_tools.reply refuses again without the approval flag. reply_to_id and the "
             "source fields are FROZEN — only the wording is editable — so an edited "
             "approval cannot land approved words under a different post. Targets a "
             "status id, never a timeline position.",
    ),
]
