"""
core/system/abilities/x_draft.py — learn his voice, and draft a reply in it.

Two GREEN capabilities. Neither publishes anything.

    system.x.learn_voice   read HIS profile, measure how he writes, cache it
    system.x.draft_reply   draft ONE reply to a post, by ID, in that voice

⚠ DRAFT-ONLY, AND THAT IS STRUCTURAL RATHER THAN PROMISED. There is no import
in this module that can reach `x_tools.post`, `x_tools.reply`, `x_tools.like`
or `x_tools.repost`. `draft_reply` returns a string. Publishing is `x.post` /
`x.reply`, which are RED, hold, and execute only through the approval card —
that is round 3's wiring and nothing here anticipates it.

⚠ THE SOURCE POST IS ADDRESSED BY ID, NOT BY INDEX. Round 1 added the status id
to the reader for exactly this: an index is a position in a list that reorders
itself, and drafting a reply to "the second one" a moment late means drafting a
reply to a different person's post. The id is looked up in a freshly read
timeline and the draft carries it, so round 3 can address the reply by id too.

⚠ THE SOURCE POST IS FENCED BEFORE IT REACHES THE MODEL. See
core/brain/x_voice.py, which is where the prompt and the refusals live, and
which is written to be inspectable without running a model.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param


def _engine() -> Any:
    """The brain settings.yaml selected. Never substituted for another."""
    from pathlib import Path

    import yaml

    from core.brain.llm import make_engine

    root = Path(__file__).resolve().parents[3]
    settings = yaml.safe_load((root / "core" / "config" / "settings.yaml")
                              .read_text(encoding="utf-8")) or {}
    return make_engine(settings)


def learn_voice(handle: str, limit: int = 20) -> dict[str, Any]:
    """Read his profile, measure the style, cache it. READ ONLY."""
    from core.brain import x_voice
    from core.tools import x_tools
    from core.tools.base import ToolError
    from core.system.untrusted import fenced

    got = x_tools.read_user(handle, limit=limit)
    profile = x_voice.profile_from(got["posts"], handle=got["handle"])
    if profile.n_posts == 0:
        raise ToolError(f"I could not read any posts on {got['handle']}",
                        "Check the handle, or that the profile is not protected.")
    x_voice.save_profile(profile)
    return {
        "handle": profile.handle, "n": profile.n_posts,
        "median_chars": profile.median_chars,
        "thin": profile.thin,
        "note": " It is a thin sample." if profile.thin else "",
        # His profile page is still untrusted input — it renders other people's
        # quoted posts, and a redirect means it may not be his page at all.
        **fenced(got["external_source"], [p["text"] for p in got["posts"]]),
    }


def draft_reply(post_id: str, guidance: str = "", limit: int = 20) -> dict[str, Any]:
    """
    Draft ONE reply to the post with this id. Returns text. Sends nothing.
    """
    from core.brain import x_voice
    from core.tools import x_tools
    from core.tools.base import ToolError
    from core.system.untrusted import fenced

    style = x_voice.load_profile()
    if style is None:
        raise ToolError("I have not learned your voice yet",
                        "Say learn my voice from X first, and I will read your profile once.")

    wanted = str(post_id or "").strip()
    timeline = x_tools.read_timeline(limit=int(limit))
    source = next((p for p in timeline["posts"] if str(p.get("id")) == wanted), None)
    if source is None:
        raise ToolError(f"post {wanted} is not on the timeline I just read",
                        "Read the timeline again and give me an id from it.")

    draft = x_voice.draft_reply(source, style, _engine(), guidance=guidance)
    warned = (f" That post tried an instruction on me — {len(draft.injection_seen)} "
              f"pattern(s). I ignored it and replied to the topic."
              if draft.injection_seen else "")
    return {
        "draft": draft.text, "chars": len(draft.text),
        "reply_to_id": draft.reply_to_id, "reply_to_handle": draft.reply_to_handle,
        "model": draft.model, "style_posts": draft.style_posts,
        "injection_seen": len(draft.injection_seen),
        "warned": warned,
        # The SOURCE post stays fenced; the draft is Tessa's own words about it.
        **fenced(f"x.com post {draft.reply_to_id}", [source.get("text", "")]),
    }


CAPABILITIES = [
    Capability(
        name="system.x.learn_voice", capability="x.read", tier="green",
        run=learn_voice,
        params=(Param("handle", str, doc="His X handle, without the @."),
                Param("limit", int, default=20, lo=5, hi=50,
                      doc="How many of his posts to measure.")),
        phrasings=("learn my voice from X", "read my profile and learn how I write"),
        success="Read {n} of your posts, Emperor. You write about {median_chars} characters.{note}",
        audit="learn voice from @{handle}",
        note="READ ONLY. Measures length, capitalisation, emoji, hashtags, punctuation and "
             "openers from his own posts and caches it to data/x-style.json. The profile "
             "page is fenced as untrusted — it renders other people's quoted posts.",
    ),
    Capability(
        name="system.x.draft_reply", capability="x.draft", tier="green",
        run=draft_reply,
        params=(Param("post_id", str, doc="The status id from the timeline read."),
                Param("guidance", str, default="", doc="Anything he wants the reply to say."),
                Param("limit", int, default=20, lo=5, hi=50, doc="How far back to look for the id.")),
        phrasings=("draft a reply to that post", "write a reply in my voice"),
        success="Draft ready, Emperor, {chars} characters to {reply_to_handle}: {draft}{warned}",
        audit="draft reply to post {post_id}",
        note="DRAFT ONLY — returns text, publishes nothing, and cannot reach a write function. "
             "The source post is fenced via ExternalContent.framed() before it reaches the "
             "model, so an instruction inside it is data. Addressed by status id, never index.",
    ),
]
