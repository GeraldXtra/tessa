"""
core/tools/x_tools.py — X (Twitter), driven through the already-authenticated
session in Tessa's own Chrome profile.

NO PASSWORD EVER TOUCHES THIS CODE

There is no credential parameter in this file, no keyring read, no prompt, and
no `fill()` against a password field. Gerald logs in ONCE, himself, in the
dedicated profile; Chrome persists the session cookies to that directory; every
call below drives the session that is already there. 2FA is satisfied at that
moment and never again. Revoking her access to X is `rmdir` on one folder — see
`core/tools/browser.py`.

SELECTORS: ARIA ROLES AND ACCESSIBLE NAMES, NOT CSS CLASSES

X ships obfuscated, generated class names that change without notice —
`css-175oi2r` today, something else next week. Anything built on them breaks
weekly and silently. What is comparatively stable is the ACCESSIBILITY layer,
because X has legal and product reasons to keep it working:

    role="article"    one per post in a timeline
    data-testid       X's own test hooks — `tweetText`, `like`, `retweet`
    aria-label        "Like", "Repost", "Reply" on the action buttons

So: `get_by_role("article")` for posts, accessible names for actions, and
`data-testid` only as a named fallback. `data-testid` is a CSS selector, which
the brief asks me to avoid — I use it second rather than not at all, because it
is maintained by X's own test suite and is materially more stable than the class
names, and preferring an unstable selector on principle would be worse for him.

THESE SELECTORS WILL BREAK. That is not a risk, it is a schedule. When X ships
a markup change the failure is a `ToolError` naming the selector that vanished —
"I cannot find the posts on this page" — and NOT a wrong click. Every lookup
below fails closed. See `_require_one`.

VERIFIED AGAINST LIVE X (2026-09-22, signed in, READ ONLY). The post markup is
exactly what `_one_post` reads today. What was wrong was WHEN the page was
read, not what was looked for — see `_await_posts`.

RATE LIMITS AND INTERSTITIALS ARE ANSWERS, NOT FAILURES. X rate-limits reads as
well as writes and will show a wall. She says so and stops.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlencode

from .base import ToolError, ToolHold
from .browser import LOGIN_GRACE_S, SESSION, NAV_TIMEOUT_MS

X_HOME = "https://x.com/home"
X_NOTIFICATIONS = "https://x.com/notifications"

#: X's session cookie. ITS PRESENCE IS THE AUTHENTICATION TEST, and everything
#: else in this file is a backstop.
#:
#: MEASURED, AND IT REPLACED A WORSE DESIGN. The first version scraped the page
#: for "sign in" / "create your account" and it FAILED on the real logged-out
#: page: `x.com/home` redirects to bare `x.com/`, whose entire body is 175
#: characters — "Happening now. About · Get App · Grok · Help · Terms …" — with
#: zero articles, zero buttons and none of those phrases anywhere. So she said
#: "I cannot find any posts on this page. Either X changed its markup, or
#: nothing has loaded", which is precisely the wrong diagnosis: it sends him to
#: debug Tessa when the answer is that he has never signed in.
#:
#: The cookie is better on every axis: deterministic, instant, language-
#: independent, and immune to the markup churn that will eventually break every
#: selector below it. It is also checkable BEFORE navigating, so the logged-out
#: path costs no page load at all.
AUTH_COOKIE = "auth_token"

#: Backstops, for the case where a cookie exists but is expired or invalidated
#: server-side — the session then looks live to us and X still refuses.
_LOGGED_OUT_URL = ("/i/flow/login", "/login", "/i/flow/signup",
                   "x.com/?", "twitter.com/?")
_LOGGED_OUT_TEXT = ("sign in to x", "sign in to twitter", "create your account",
                    "log in to x", "phone, email, or username", "happening now.")

#: X telling us to slow down, or standing in the way.
_WALL_TEXT = ("rate limit", "try again later", "something went wrong",
              "unusual activity", "verify your identity", "are you a robot",
              "this account is temporarily", "over the limit")
#: The two X also shows TRANSIENTLY, in a sidebar module, on a page whose
#: post rendered fine — see `_check_reachable`.
_TRANSIENT_WALL = ("something went wrong", "try again later")


def _page() -> Any:
    return SESSION.page()


#: THE CHAIN REPORTER (X publishing round 3, 2026-09-12). A thread posts N
#: parts one after another, each chained to the id X handed back for the part
#: before, and the executor writes its REQUESTED / APPROVED pair BEFORE the
#: handler runs — so without this a thread that stopped at part 3 would leave
#: two public posts and no record of their ids. The executor binds its `_log`
#: here (the shape core/system/abilities/power.py uses): a thread reports
#: THREAD-PART k/n with the id as each part lands, and THREAD-STOPPED with the
#: ids that ARE public when a part fails. Nothing here decides anything — an
#: unbound reporter changes what is recorded, never what is posted.
_REPORT: Any = None


def bind_reporter(report: Any) -> None:
    """`report(verb, tool, summary, tier, *, actor)` — the executor's `_log`."""
    global _REPORT
    _REPORT = report


def _report(verb: str, tool: str, summary: str, tier: str, actor: str) -> None:
    if _REPORT is None:
        return
    try:
        _REPORT(verb, tool, summary, tier, actor=actor)
    except Exception:  # noqa: BLE001
        pass    # an audit failure must never change what happens on X


#: X COUNTS EVERY LINK AS 23 CHARACTERS (t.co), whatever its length. A raw
#: `len()` gets it wrong both ways: it refuses a quote X accepts (a 60-character
#: permalink costs 23, not 60) and passes a post X refuses (a 10-character link
#: still costs 23) — and the second failure lands AFTER the compose box has been
#: filled. Every length check in this file counts the way X does.
_URL = re.compile(r"https?://\S+", re.I)


def _x_len(text: str) -> int:
    """Length as X counts it: every URL is 23 characters."""
    return len(_URL.sub("x" * 23, text))


#: HUMAN-LIKE PACING (the reading round, 2026-09-12). X detects automation by
#: rhythm as much as by markup, and a read that lands every 2.5 s exactly is a
#: rhythm. Every X navigation goes through `_goto`, so the floor cannot be
#: forgotten by a new read — the same reasoning as the fence living in the
#: executor rather than in each handler. Honest limit: this LOWERS the
#: footprint of a burst of reads and cannot remove it; heavy reading is still
#: visible to X whatever the gap.
READ_GAP_S = 4.0
READ_JITTER_S = 1.5
_last_nav = 0.0


def _pace() -> float:
    """Wait until READ_GAP_S (+ jitter) has passed since the last X navigation."""
    global _last_nav
    wait = 0.0
    if _last_nav:
        due = _last_nav + READ_GAP_S + random.uniform(0.0, READ_JITTER_S)
        start = time.monotonic()
        # A loop, not one sleep: Windows wakes a sleeper up to ~15 ms early,
        # and a floor that is sometimes 10 ms short is not a floor.
        while (now := time.monotonic()) < due:
            time.sleep(due - now)
        wait = time.monotonic() - start
    _last_nav = time.monotonic()
    return wait


def _goto(page: Any, url: str) -> None:
    _pace()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"X did not load ({type(exc).__name__})",
                        "Check your connection and ask me again.") from None


def _body(page: Any) -> str:
    try:
        return (page.inner_text("body") or "")[:20_000]
    except Exception:  # noqa: BLE001
        return ""


#: HYDRATION IS THE RACE, NOT THE SELECTORS (live-selector round, 2026-09-22).
#: Checked against the owner's SIGNED-IN home timeline on the real machine:
#: the post markup is exactly what `_one_post` reads — `article[role=article]
#: [data-testid=tweet]` inside `[data-testid=cellInnerDiv]`, `User-Name`,
#: `tweetText`, `time[datetime]` inside the `/<handle>/status/<id>` permalink —
#: and the extractor pulled four real posts once they were there. They were
#: NOT there when it looked: X's shell fires `domcontentloaded` with zero
#: articles and fetches the timeline by script afterwards. Measured: none at
#: t+4.2 s, the first four at t+6.8 s. The old fixed 2.5 s beat after the load
#: event therefore landed in the gap every time, and `_posts` said "I cannot
#: find any posts — X changed its markup", which was the wrong diagnosis.
#: `_await_posts` polls for the first article and stops at READ_HYDRATE_S: a
#: CEILING, not a wait — a page that has its posts at one second returns at
#: one second.
READ_HYDRATE_S = 20.0
HYDRATE_POLL_MS = 400
HYDRATE_SETTLE_MS = 700
#: Bounded by polls as well as by the clock, so a page object whose
#: `wait_for_timeout` returns at once (the test fixture) cannot spin.
HYDRATE_MAX_POLLS = 60

#: Pages that will never grow an article: stop waiting and let the caller
#: read the page's own words. DEFINITIVE states only — "something went
#: wrong" and "try again" are left out because X shows them transiently
#: while a timeline is still loading; `_check_reachable` sees them after.
_TERMINAL_TEXT = ("no results for", "doesn't exist", "doesn\u2019t exist",
                  "does not exist", "hmm...this page", "this post was deleted",
                  "post is unavailable", "account suspended", "rate limit")


def _await_posts(page: Any, timeout_s: float = READ_HYDRATE_S) -> int:
    """
    Wait for the page to hydrate — the first article, or a page that says
    it never will — bounded by `timeout_s` AND by HYDRATE_MAX_POLLS. Returns
    the article count seen (0 on timeout or a terminal page); the caller's
    `_posts` still raises on 0, so the refusal wording is unchanged.

    ONLY `get_by_role` / `count` / `inner_text` / `wait_for_timeout`: the same
    read surface every read already uses, so the fixture needs no new method
    and an AST scan of the read path still finds no page action.
    """
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    for _ in range(HYDRATE_MAX_POLLS):
        try:
            n = int(page.get_by_role("article").count())
        except Exception:  # noqa: BLE001
            n = 0
        if n:
            # X renders in batches — the count rose 0 -> 4 in one step — but
            # a short settle lets the batch finish before anything is read.
            page.wait_for_timeout(HYDRATE_SETTLE_MS)
            try:
                return max(n, int(page.get_by_role("article").count()))
            except Exception:  # noqa: BLE001
                return n
        if time.monotonic() >= deadline:
            return 0
        low = _body(page).lower()
        if any(t in low for t in _TERMINAL_TEXT):
            return 0
        page.wait_for_timeout(HYDRATE_POLL_MS)
    return 0


#: THE WRITE SURFACES RACE THE SAME HYDRATION (live-write round, 2026-09-22).
#: Measured on the owner's signed-in session: a permalink's article at 2.8 s
#: (the reply path's old fixed 2.5 s beat then said "not there any more"),
#: his own profile's posts at 4.2 s (`_posted_id`'s fallback), the
#: /compose/post box at 1.3 s after the load event, a profile's Follow
#: button at 1.7-2.3 s WITH ZERO ARTICLES YET (the header hydrates before
#: the posts), the reply dialog's box within 10 ms of the press. So every
#: write path now waits the way every read does: `_await_present` is
#: `_await_posts` for any element — poll the finders, in order, until one
#: has a match, settle, and stop at a ceiling, on a terminal page, or on a
#: logged-out redirect. It only WAITS. Which element is pressed, and
#: `_require_one`'s refusal on zero or several, are exactly as they were.
WRITE_HYDRATE_S = READ_HYDRATE_S


def _await_present(page: Any, *finders: Any, timeout_s: float = WRITE_HYDRATE_S,
                   settle_ms: int = HYDRATE_SETTLE_MS, stop_text: tuple[str, ...] = ()) -> int:
    """
    Wait until the first of `finders` has a match, bounded by `timeout_s` AND
    by HYDRATE_MAX_POLLS (a page whose `wait_for_timeout` returns at once —
    the test fixture — cannot spin). A finder is a zero-argument callable
    returning a locator (its `count()` is the test) or a plain truthy value.
    They are called afresh on every poll, in order, so a fallback lookup is
    only ever built when the primary has nothing — the either/or every caller
    already did. Returns the count seen, 0 on timeout or a page that will
    never grow the element (`_TERMINAL_TEXT`, `stop_text`, a logged-out URL);
    the caller's own lookup and `_require_one` still decide what happens.
    """
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    stops = _TERMINAL_TEXT + tuple(stop_text or ())
    for _ in range(HYDRATE_MAX_POLLS):
        for find in finders:
            try:
                got = find()
                n = int(got.count()) if hasattr(got, "count") else int(bool(got))
            except Exception:  # noqa: BLE001
                n = 0
            if n:
                if settle_ms:
                    page.wait_for_timeout(settle_ms)
                return n
        if time.monotonic() >= deadline:
            return 0
        url = (getattr(page, "url", "") or "").lower()
        if any(m in url for m in _LOGGED_OUT_URL):
            return 0
        low = _body(page).lower()
        if any(t in low for t in stops):
            return 0
        page.wait_for_timeout(HYDRATE_POLL_MS)
    return 0


_NOT_SIGNED_IN = (
    "you have not signed in to X in my browser yet",
    "Say open X. I will bring up the login page, you sign in yourself, and that "
    "is the last time you have to. I never see your password.")


def _require_signed_in() -> None:
    """
    The cookie check. Runs BEFORE any navigation, so the logged-out path costs
    nothing and cannot be mistaken for an empty timeline.
    """
    # SCOPED TO x.com, BOTH WAYS. `cookies()` with no `urls` returns EVERY
    # cookie in the profile, so any site that happens to set one named
    # `auth_token` would have satisfied this check and she would have gone on to
    # scrape a logged-out timeline as though it were his. The domain is also
    # re-checked on the returned cookie, because `urls=` filtering is
    # Playwright's behaviour rather than a guarantee I want to lean on alone.
    try:
        cookies = SESSION.context().cookies(urls=["https://x.com"])
    except Exception:  # noqa: BLE001
        cookies = []
    if not any(c.get("name") == AUTH_COOKIE
               and str(c.get("domain", "")).lstrip(".").endswith(("x.com", "twitter.com"))
               for c in cookies):
        raise ToolError(*_NOT_SIGNED_IN)


def _check_reachable(page: Any) -> None:
    """
    Second pass, AFTER the page has loaded: an expired cookie, or a wall.

    A cookie that exists but no longer works looks authenticated to us and is
    refused by X, so the text backstops stay — they just are not the primary
    test any more.
    """
    url = (page.url or "").lower()
    body = _body(page)
    low = body.lower()

    if any(m in url for m in _LOGGED_OUT_URL) or any(t in low for t in _LOGGED_OUT_TEXT):
        raise ToolError(
            "your X session has expired — the browser has a stale cookie",
            "Say open X and sign in again. It only takes the once.")

    wall = next((w for w in _WALL_TEXT if w in low), None)
    if wall in _TRANSIENT_WALL:
        # SEEN LIVE (media round, 2026-09-22): a permalink whose post HAD
        # rendered, with "Something went wrong. Try reloading." in a sidebar
        # module beside it — X's own widget failing, not X refusing us. The
        # unbookmark press was refused on that text with the button on the
        # page. So the two transient phrases count as a wall only when the
        # page has NO article: a page with posts on it is reachable. (Same
        # read surface as `_await_posts`; the fixture needs nothing new.)
        try:
            if int(page.get_by_role("article").count()) > 0:
                wall = None
        except Exception:  # noqa: BLE001
            pass
    if wall:
        raise ToolError(
            f"X is not letting me read right now — the page says {wall!r}",
            "That is their rate limit, not a fault here. Give it a few minutes.")


def _require_one(locator: Any, what: str) -> Any:
    """
    Exactly one match, or fail.

    NEVER `.first` ON AN AMBIGUOUS MATCH. On a timeline the element next to the
    one he meant belongs to a different person's post, and clicking it is a
    public act performed under his name. Zero matches and several matches are
    both refusals, with different sentences.
    """
    try:
        n = locator.count()
    except Exception:  # noqa: BLE001
        n = 0
    if n == 0:
        raise ToolError(
            f"I cannot find {what} on this page",
            "X changes its markup often and this is what that looks like. "
            "Nothing was clicked.")
    if n > 1:
        raise ToolError(f"{n} things on this page match {what}",
                        "I will not guess which one on a timeline.")
    return locator.first


#: A status permalink: /<handle>/status/<id>. The id is the only STABLE way to
#: name one post; an index is a position in a list that reorders itself.
_STATUS_HREF = re.compile(r"/([A-Za-z0-9_]{1,15})/status/(\d{5,25})")
#: Header and footer lines of an article's inner text that are not the post:
#: the separator dot, "Ad", a relative time ("6h"), a count ("7.1K"), a
#: media duration ("0:14"), an absolute date ("Sep 21" / "Sep 21, 2025").
_NOISE_LINE = re.compile(
    r"^(?:·|Ad|\d+[smhd]|[\d.,]+[KMB]?|\d{1,2}:\d{2}|[A-Z][a-z]{2} \d{1,2}(?:, \d{4})?)$")


def _one_post(art: Any) -> dict[str, str]:
    """
    One article to structured fields, defensively.

    EVERY LOOKUP IS INDIVIDUALLY GUARDED and falls back to the line-splitting
    that came before. X rewrites this markup often — that is the single most
    likely way this whole path breaks — and a changed `data-testid` must
    degrade the answer, never raise out of a read the owner asked for.
    """
    raw = ""
    try:
        raw = (art.inner_text() or "").strip()
    except Exception:  # noqa: BLE001
        pass
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]

    # ── THE ID AND THE PERMALINK. Taken from the status href rather than any
    #    testid, because the URL shape is what X cannot change without breaking
    #    every link ever posted to one of its own tweets.
    post_id = handle = url = ""
    try:
        for href in art.locator('a[href*="/status/"]').evaluate_all(
                "els => els.map(e => e.getAttribute('href'))") or []:
            m = _STATUS_HREF.search(str(href or ""))
            if m:
                handle, post_id = f"@{m.group(1)}", m.group(2)
                url = f"https://x.com/{m.group(1)}/status/{m.group(2)}"
                break
    except Exception:  # noqa: BLE001
        pass

    # ── THE TIMESTAMP. `<time datetime=...>` is ISO-8601 and machine-readable;
    #    the visible text is "2h", which is useless the moment it is stored.
    ts = ""
    try:
        node = art.locator("time").first
        if node.count():
            ts = str(node.get_attribute("datetime") or "")
    except Exception:  # noqa: BLE001
        pass

    # ── THE DISPLAY NAME, SPLIT FROM THE HANDLE BY PATTERN, NOT BY NEWLINE.
    #
    #    `User-Name` holds both, and whether they arrive as "Ada Lovelace\n@ada"
    #    or "Ada Lovelace@ada" depends on how the spans happen to lay out.
    #    Splitting on lines gave "Ada Lovelace@ada" as the author. Matching the
    #    handle shape and taking what precedes it works for both, and recovers
    #    the handle too when the permalink did not supply one.
    author = ""
    try:
        name = art.locator('[data-testid="User-Name"]').first
        if name.count():
            blob = " ".join((name.inner_text() or "").split())
            m = re.match(r"^(.*?)\s*(@[A-Za-z0-9_]{1,15})\b", blob)
            if m:
                author = m.group(1).strip()
                handle = handle or m.group(2)
            else:
                author = blob
    except Exception:  # noqa: BLE001
        pass
    if not author:
        author = lines[0] if lines else "?"

    body = ""
    try:
        node = art.locator('[data-testid="tweetText"]').first
        if node.count():
            body = " ".join((node.inner_text() or "").split())
    except Exception:  # noqa: BLE001
        pass
    if not body:
        # NO `tweetText` MEANS A MEDIA-ONLY POST (seen live, 2026-09-22: a
        # captionless video). The line fallback then read out the header and
        # the counts — "@Big_Bola01 · 6h Fan account 0:14 1 60 289 7.1K" — as
        # the post's text. Say what it is instead; keep the fallback only for
        # a post with neither text nor media, minus the header and count lines.
        media: list[str] = []
        for tid, label in (("videoPlayer", "video"), ("tweetPhoto", "photo"),
                           ("card.wrapper", "link card")):
            try:
                if art.locator(f'[data-testid="{tid}"]').count():
                    media.append(label)
            except Exception:  # noqa: BLE001
                pass
        if "video" in media and "photo" in media:
            media.remove("photo")            # the video player sits inside tweetPhoto
        if media:
            body = f"[{' and '.join(media)}, no text]"
        else:
            body = " ".join(ln for ln in lines[1:]
                            if ln not in (author, handle) and not _NOISE_LINE.match(ln))

    return {
        # `who` is KEPT for the callers and phrasings that already read it.
        # Renaming it would be a breaking change to `x.like`/`x.repost`'s
        # spoken lines for no gain.
        "who": author[:60],
        "author": author[:60],
        "handle": handle[:20],
        "text": body[:400],
        "id": post_id,
        "ts": ts,
        "url": url,
    }


def _posts(page: Any, limit: int) -> list[dict[str, str]]:
    """
    STRUCTURED, NOT JUST READABLE, AND THE ID IS THE REASON.

    This used to return `{who, text}` only. That is enough to read a timeline
    aloud and not enough for anything after it: replying meant naming a post by
    its INDEX, and an index is a position in a list that reorders itself every
    few seconds as X hydrates. Reply to index 2 a moment late and the reply
    lands under a different person's post, publicly, in his name.

    A status id does not move. It is what the drafting and reply rounds will
    address a post by, and it is extracted here so that path never has to guess.
    """
    out: list[dict[str, str]] = []
    try:
        articles = page.get_by_role("article")
        total = articles.count()
    except Exception:  # noqa: BLE001
        total = 0
    if total == 0:
        raise ToolError(
            "I cannot find any posts on this page",
            "Either X changed its markup, or nothing has loaded. Nothing was clicked.")
    for i in range(min(total, limit)):
        try:
            out.append(_one_post(articles.nth(i)))
        except Exception:  # noqa: BLE001
            continue
    return out


#: THE LAST READ, kept so "read the thread on post two" can name a post by the
#: position she just read out. Resolved against THIS snapshot — a list that
#: does not reorder — and then addressed by STATUS ID, never by position on a
#: live page. The engagement round (2026-09-12) resolves "like post two" the
#: same way, ONCE, at request time, and the hold is then armed on the id it
#: resolved to (`like`, `unlike`, ToolHold.resolved) — nothing acts on a position.
_LAST_READ: list[dict[str, str]] = []


def _remember(posts: list[dict[str, str]]) -> None:
    _LAST_READ[:] = [dict(p) for p in posts]


def _from_last_read(index: int) -> dict[str, str]:
    if not _LAST_READ:
        raise ToolError("I have not read any posts yet",
                        "Read the timeline, a profile or a search first, then name one.")
    if index < 1 or index > len(_LAST_READ):
        raise ToolError(f"there is no post number {index} in what I last read, I have "
                        f"{len(_LAST_READ)}", "Say a number I read out to you.")
    picked = _LAST_READ[index - 1]
    if not picked.get("id"):
        raise ToolError(f"post {index} did not carry a status id",
                        "Read again, or give me the id.")
    return picked


# ─────────────────────────────────────────────────────────────────────────────
# GREEN — reads
# ─────────────────────────────────────────────────────────────────────────────

def read_timeline(limit: int = 5) -> dict[str, Any]:
    _require_signed_in()
    page = _page()
    _goto(page, X_HOME)
    _await_posts(page)                   # X hydrates client-side; wait for the first post
    _check_reachable(page)
    posts = _posts(page, int(limit))
    _remember(posts)
    joined = "\n\n".join(f"{p['who']}: {p['text']}" for p in posts)
    return {
        "n": len(posts), "posts": posts,
        "first": posts[0]["who"] if posts else "",
        "head": "; ".join(p["who"] for p in posts[:3]),
        # UNTRUSTED. A timeline is thousands of strangers' text arriving inside
        # her context. It is fenced exactly like a web page.
        "external_source": "x.com timeline",
        "external_text": joined,
    }


def read_user(handle: str, limit: int = 20) -> dict[str, Any]:
    """
    A named account's recent posts. READ ONLY.

    THIS EXISTS TO LEARN HIS VOICE. The drafting round needs a corpus of his
    OWN posts, and "read the home timeline" is the wrong source for that — the
    home timeline is mostly other people. `read_user("gerald")` reads one
    profile, which is where his own writing actually is.

    ⚠ STILL FENCED, EVEN FOR HIS OWN ACCOUNT. Two reasons, and the second is
    the one that matters. First, the parser cannot verify whose profile it
    actually landed on — a redirect, a stale URL or a typo'd handle and this is
    a stranger's page. Second, a profile page contains quoted and embedded
    posts written by other people. Treating "his" profile as trusted would be
    trusting whatever happens to be rendered on it.
    """
    _require_signed_in()
    clean = str(handle or "").strip().lstrip("@")
    if not clean or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", clean):
        raise ToolError(f"{handle!r} is not a usable X handle",
                        "Give me the @name as it appears on the profile.")
    page = _page()
    _goto(page, f"https://x.com/{clean}")
    _await_posts(page)
    _check_reachable(page)
    posts = _posts(page, int(limit))
    _remember(posts)
    joined = "\n\n".join(f"{p['who']}: {p['text']}" for p in posts)
    return {
        "n": len(posts), "handle": f"@{clean}", "posts": posts,
        "head": "; ".join(p["text"][:40] for p in posts[:3]),
        "external_source": f"x.com profile @{clean}",
        "external_text": joined,
    }


def read_notifications(limit: int = 5) -> dict[str, Any]:
    _require_signed_in()
    page = _page()
    _goto(page, X_NOTIFICATIONS)
    _await_posts(page)
    _check_reachable(page)
    posts = _posts(page, int(limit))
    _remember(posts)
    joined = "\n\n".join(f"{p['who']}: {p['text']}" for p in posts)
    return {
        "n": len(posts), "posts": posts,
        "head": "; ".join(p["who"] for p in posts[:3]),
        "external_source": "x.com notifications",
        "external_text": joined,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GREEN — THE READING ROUND (X features round 1, 2026-09-12): search, thread,
# profile. READ ONLY. Nothing in this section clicks, fills, types, presses or
# navigates to a compose page. Each returns the SAME structured `posts` the
# timeline read returns, under the same `external_text` fence, and the
# executor feeds them to the claim and style stores exactly as it does the
# timeline — there is no learning code here and no reference to it.
# `read_user` above (the voice-learning round) is the profile read and is
# registered as `x.read_user` unchanged.
# ─────────────────────────────────────────────────────────────────────────────

X_SEARCH = "https://x.com/search"
#: Longer than any spoken query; a cap so a runaway transcript is not a URL.
MAX_QUERY_CHARS = 200
_POST_ID = re.compile(r"\d{5,25}")


def search(query: str, limit: int = 10, latest: bool = False) -> dict[str, Any]:
    """
    GREEN. Search X for posts. READ ONLY.

    THE QUERY IS HIS; THE RESULTS ARE NOT. The words come from his own routed
    speech and go into the URL through `urlencode`, never into a page as
    typed text. What comes back is strangers' text chosen by a query — the
    easiest place on X to plant a post for her to read — and it is fenced as
    external exactly like the timeline.

    ZERO RESULTS IS AN ANSWER. `_posts` raises on a page with no articles,
    which is right on a timeline (the markup broke) and wrong here (nothing
    matched). X says "No results for ..." on that page, and that phrase turns
    the refusal into an empty list.

    PEOPLE SEARCH IS DEFERRED. X's people tab renders user cells, not
    articles; parsing it needs a live session to verify a second selector
    set against. Posts search is what "what are people saying about" means.
    """
    q = " ".join(str(query or "").split()).strip(" .,?!\"'")
    if not q:
        raise ToolError("there was nothing to search for", "Tell me the words.")
    if len(q) > MAX_QUERY_CHARS:
        raise ToolError(f"that search is {len(q)} characters long",
                        f"Keep it under {MAX_QUERY_CHARS}.")
    _require_signed_in()
    page = _page()
    params = {"q": q, "src": "typed_query"}
    if latest:
        params["f"] = "live"
    _goto(page, f"{X_SEARCH}?{urlencode(params)}")
    _await_posts(page)
    _check_reachable(page)
    try:
        posts = _posts(page, int(limit))
    except ToolError:
        if "no results" not in _body(page).lower():
            raise
        posts = []
    _remember(posts)
    joined = "\n\n".join(f"{p['who']}: {p['text']}" for p in posts)
    return {
        "n": len(posts), "query": q, "posts": posts,
        "first": posts[0]["who"] if posts else "",
        "head": ("; ".join(f"{p['who']}: {p['text'][:40]}" for p in posts[:3])
                 if posts else "Nothing matched"),
        # UNTRUSTED, like every read in this file.
        "external_source": f"x.com search: {q[:60]}",
        "external_text": joined,
    }


def read_thread(post_id: str = "", index: int = 0, limit: int = 20) -> dict[str, Any]:
    """
    GREEN. The conversation around ONE post: what it replied to, the post,
    and the replies under it. READ ONLY.

    ADDRESSED BY STATUS ID. "Post two" is resolved against the snapshot of
    the last read (`_LAST_READ`), which does not reorder, and the page is
    then opened by that id — the rule the reply round settled on, for the
    same reason: an index into a live page drifts.

    The focal post is found among the articles by its id. Everything before
    it is what it answered; everything after it is the replies. If the id is
    not in the articles (X sometimes omits the permalink from the focal
    article's own markup) the first article is taken as the post and the
    result says so in `found`.
    """
    target = str(post_id or "").strip()
    if not target:
        target = _from_last_read(int(index) if index else 1)["id"]
    if not _POST_ID.fullmatch(target):
        raise ToolError(f"{target!r} is not a post id",
                        "Read the timeline again and give me an id from it, or say post two.")
    _require_signed_in()
    page = _page()
    _goto(page, f"https://x.com/i/web/status/{target}")
    _await_posts(page)
    _check_reachable(page)
    try:
        posts = _posts(page, int(limit))
    except ToolError:
        low = _body(page).lower()
        if "exist" in low or "deleted" in low:
            raise ToolError(f"post {target} is not there any more",
                            "It may have been deleted. Read the timeline again.") from None
        raise
    at = next((i for i, p in enumerate(posts) if p.get("id") == target), 0)
    focal = posts[at]
    above, replies = posts[:at], posts[at + 1:]
    _remember(posts)
    joined = "\n\n".join(f"{p['who']}: {p['text']}" for p in posts)
    return {
        "n": len(posts), "post_id": target, "posts": posts,
        "who": focal.get("handle") or focal.get("who") or "?",
        "found": bool(focal.get("id") == target),
        "above": len(above), "replies": len(replies),
        "head": ("; ".join(f"{p['who']}: {p['text'][:40]}" for p in replies[:3])
                 if replies else "no replies yet"),
        "external_source": f"x.com thread {target}",
        "external_text": joined,
    }


# ─────────────────────────────────────────────────────────────────────────────
# AMBER — THE ENGAGEMENT ROUND (X features round 2, 2026-09-12): like/unlike,
# follow/unfollow. A like or a follow is a public statement in his name and it
# is reversible, so each HOLDS for his confirmation — not green, not a card —
# and the reverse of each is one call away.
#
# THE TARGET IS RESOLVED ONCE AND FROZEN. "post two" is an ordinal into the
# snapshot of the last read (`_LAST_READ`, which does not reorder); it is
# resolved HERE, at request time, and the hold is raised with the STATUS ID
# (ToolHold.resolved), so the executor arms the ledger on the id, his "yes" acts
# on the id, and a page that reordered in between changes nothing. `x.reply`
# settled this rule for the red path; this is the amber path keeping it. The
# registry also freezes `post_id` / `handle`, so a card — if one is ever
# raised for an amber tool — may not retarget an approved action.
#
# ONE TARGET PER CALL, BY SHAPE. `post_id` must be exactly one status id and
# `handle` exactly one @name. "Everything", "all", "everyone", a list, a
# wildcard or two ids cannot be expressed, and are refused before any
# navigation — a poisoned tweet's game is mass follow/like, and the refusal
# names bulk as bulk so he hears why.
#
# EVERY CLICK IS `_press`, the one action boundary in this section, so a
# proof can replace it with a recorder and a review can find every press.
# Both directions are idempotent: liking a liked post or following a followed
# account presses nothing and says so.
# ─────────────────────────────────────────────────────────────────────────────

#: Words that name MANY. Any of these in a target is a bulk request.
_BULK_WORDS = re.compile(
    r"\b(?:all|every|everything|everyone|everybody|each|whoever|anyone|anybody|"
    r"followers|following|timeline|feed|them|these|those|and)\b|[*,;/&+]", re.I)


def _one_post_id(raw: Any) -> str:
    """Exactly ONE status id, or a refusal that says why. Bulk is named as bulk."""
    s = " ".join(str(raw or "").split()).strip(" .,?!\"'")
    if _POST_ID.fullmatch(s):
        return s
    if not s:
        raise ToolError("there was no post to act on",
                        "Say post two, or read me the id.")
    if _BULK_WORDS.search(s) or len(re.findall(r"\d{5,25}", s)) > 1:
        raise ToolError(f"that names more than one post: {s[:60]!r}",
                        "I act on ONE post at a time, by its id. Name one.")
    raise ToolError(f"{s[:60]!r} is not a post id",
                    "Read the timeline again and give me an id from it, or say post two.")


def _one_handle(raw: Any) -> str:
    """Exactly ONE @name (returned without the @), or a refusal that says why."""
    s = " ".join(str(raw or "").split()).strip(" .,?!\"'")
    if re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", s):
        # A BARE BULK WORD IS NOT A HANDLE. "unfollow everyone" is a fan-out,
        # not the account @everyone; the @ is how he names that account.
        if not s.startswith("@") and _BULK_WORDS.fullmatch(s):
            raise ToolError(f"{s!r} names more than one account",
                            "I follow or unfollow ONE account at a time. Say the @name if you mean "
                            "an account called that.")
        return s.lstrip("@")
    if not s:
        raise ToolError("there was no account to act on", "Give me the @name.")
    if _BULK_WORDS.search(s) or s.count("@") > 1:
        raise ToolError(f"that names more than one account: {s[:60]!r}",
                        "I follow or unfollow ONE account at a time, by its @name. Name one.")
    raise ToolError(f"{s[:60]!r} is not a usable X handle",
                    "Give me the @name as it appears on the profile.")


def _resolve_post(post_id: Any, index: Any) -> tuple[str, str]:
    """(status id, author) from an id he read out, or an ordinal into the last read."""
    raw = " ".join(str(post_id or "").split())
    if not raw and index:
        picked = _from_last_read(int(index))
        return picked["id"], picked.get("handle") or picked.get("who") or ""
    target = _one_post_id(raw)
    who = next((p.get("handle") or p.get("who") or ""
                for p in _LAST_READ if p.get("id") == target), "")
    return target, who


def _press(button: Any, what: str, target: str) -> None:
    """
    THE ONE ACTION BOUNDARY. Every like, unlike, bookmark, unbookmark, follow
    and unfollow press is
    this call, on the single locator `_require_one` returned. `what` and
    `target` are for the recorder and the error, never for choosing the
    element — the choice was made, and checked, before this is reached.
    """
    try:
        button.click()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(
            f"the {what} button on {target} did not take the press ({type(exc).__name__})",
            "X may have changed its page. Nothing else was pressed.") from None


def _status_hrefs(art: Any) -> list[str]:
    try:
        return [str(h or "") for h in art.locator('a[href*="/status/"]').evaluate_all(
            "els => els.map(e => e.getAttribute('href'))") or []]
    except Exception:  # noqa: BLE001
        return []


def _focal_article(page: Any, post_id: str) -> Any:
    """
    THE article for `post_id` on its own permalink page, or a refusal.

    Ancestors render above the focal post and replies below it, each with a
    permalink of its own; the focal article often carries NONE (it is the
    page). So: an article whose status href names the id wins; failing that,
    exactly ONE article with no status href at all is the focal post; anything
    else — no article, or several candidates — is a refusal, because a like
    on the wrong article of a thread is a public act on a stranger's post.
    """
    articles = page.get_by_role("article")
    try:
        total = articles.count()
    except Exception:  # noqa: BLE001
        total = 0
    if total == 0:
        raise ToolError(f"post {post_id} is not there any more",
                        "It may have been deleted. Read the timeline again.")
    unlinked: list[Any] = []
    for i in range(min(total, 40)):
        art = articles.nth(i)
        matches = [_STATUS_HREF.search(h) for h in _status_hrefs(art)]
        if any(m and m.group(2) == post_id for m in matches):
            return art
        if not any(matches):
            unlinked.append(art)
    if len(unlinked) == 1:
        return unlinked[0]
    raise ToolError(f"I cannot tell which post on this page is {post_id}",
                    "X changed how it marks the post. Nothing was pressed.")


#: X's like button carries its count in the accessible name — "12 Likes. Like"
#: — and reads "12 Likes. Liked" once pressed; its test id flips like -> unlike.
#: Anchored on the last word, so a like can never land on a Liked button and
#: unlike a post he meant to like.
_LIKE_NAME = re.compile(r"(?:^|[\s.])like$", re.I)
_LIKED_NAME = re.compile(r"(?:^|[\s.])(?:liked|unlike)$", re.I)


def _like_buttons(art: Any) -> tuple[Any, Any]:
    """(the Like button, the Liked button) on one article; either may be empty."""
    off = art.get_by_role("button", name=_LIKE_NAME)
    on = art.get_by_role("button", name=_LIKED_NAME)
    try:
        if off.count() == 0 and on.count() == 0:
            off = art.locator('[data-testid="like"]')
            on = art.locator('[data-testid="unlike"]')
    except Exception:  # noqa: BLE001
        pass
    return off, on


def _engage_post(post_id: str, action: str) -> dict[str, Any]:
    """
    The confirmed half of like/unlike — and, since the media round (2026-09-22),
    of bookmark/unbookmark, which is the same act on the button beside it:
    open the permalink, find THE post, press once. `action` names the button
    pair and the direction; nothing else differs between the four.
    """
    _require_signed_in()
    page = _page()
    _goto(page, f"https://x.com/i/web/status/{post_id}")
    _await_posts(page)
    _check_reachable(page)
    art = _focal_article(page, post_id)
    fields = _one_post(art)
    who = fields.get("handle") or fields.get("who") or "?"
    kind = "bookmark" if action in ("bookmark", "unbookmark") else "like"
    off, on = (_bookmark_buttons if kind == "bookmark" else _like_buttons)(art)
    turning_on = action in ("like", "bookmark")
    want, other, label = (off, on, action) if turning_on else (on, off, action)
    try:
        have_want, have_other = want.count(), other.count()
    except Exception:  # noqa: BLE001
        have_want = have_other = 0
    if have_want == 0 and have_other >= 1:
        done = "liked" if kind == "like" else "bookmarked"
        state = done if turning_on else f"not {done}"
        return {"post_id": post_id, "who": who, "already": True,
                "note": f" It was already {state}; nothing changed."}
    btn = _require_one(want, f"the {label} button on post {post_id}")
    _press(btn, label, f"post {post_id}")
    page.wait_for_timeout(600)
    return {"post_id": post_id, "who": who, "already": False, "note": ""}


def like(post_id: str = "", index: int = 0, confirmed: bool = False) -> dict[str, Any]:
    """
    AMBER. Like ONE post, by status id.

    An ordinal ("post two") is resolved against the last read's snapshot HERE,
    once, and the hold is raised with the id — so the executor arms it on the
    id and his "yes" acts on that id whatever the page shows by then.
    `confirmed` is set only by the executor after the ledger accepted his yes
    or his repeat; the key is stripped from any caller's args before this.
    """
    target, who = _resolve_post(post_id, index)
    if not confirmed:
        raise ToolHold(f"liking {who}'s post {target}, publicly, as you" if who
                       else f"liking post {target}, publicly, as you",
                       resolved={"post_id": target})
    return _engage_post(target, "like")


def unlike(post_id: str = "", index: int = 0, confirmed: bool = False) -> dict[str, Any]:
    """AMBER. The reverse of `like`, same target rule, same hold, same boundary."""
    target, who = _resolve_post(post_id, index)
    if not confirmed:
        raise ToolHold(f"unliking {who}'s post {target}" if who else f"unliking post {target}",
                       resolved={"post_id": target})
    return _engage_post(target, "unlike")


#: THE BOOKMARK BUTTON (media round, 2026-09-22) sits beside Like in the same
#: action bar — seen live, signed in: test id `bookmark`, accessible name
#: "Bookmark" on a timeline and "0 Bookmarks. Bookmark" on a permalink, the
#: like button's pattern exactly. Once pressed X flips it to `removeBookmark`
#: with a name ending "Bookmarked". Anchored on the last word, as the like
#: names are, so a bookmark can never land on a Bookmarked button and remove
#: the one he meant to add; a name X changes fails closed in `_require_one`.
_BOOKMARK_NAME = re.compile(r"(?:^|[\s.])bookmark$", re.I)
_BOOKMARKED_NAME = re.compile(r"(?:^|[\s.])(?:bookmarked|remove\s+(?:from\s+)?bookmarks?|unbookmark)$", re.I)


def _bookmark_buttons(art: Any) -> tuple[Any, Any]:
    """(the Bookmark button, the Bookmarked button) on one article; either may be empty."""
    off = art.get_by_role("button", name=_BOOKMARK_NAME)
    on = art.get_by_role("button", name=_BOOKMARKED_NAME)
    try:
        if off.count() == 0 and on.count() == 0:
            off = art.locator('[data-testid="bookmark"]')
            on = art.locator('[data-testid="removeBookmark"]')
    except Exception:  # noqa: BLE001
        pass
    return off, on


def bookmark(post_id: str = "", index: int = 0, confirmed: bool = False) -> dict[str, Any]:
    """
    AMBER. Bookmark ONE post, by status id — `like`'s shape exactly: the
    ordinal resolved once against the last read's snapshot, the hold raised
    with the id so the ledger is armed on it, `confirmed` set only by the
    executor, one press through `_press`. A bookmark is private to his
    account and reversible (`unbookmark`).
    """
    target, who = _resolve_post(post_id, index)
    if not confirmed:
        raise ToolHold(f"bookmarking {who}'s post {target} on your account" if who
                       else f"bookmarking post {target} on your account",
                       resolved={"post_id": target})
    return _engage_post(target, "bookmark")


def unbookmark(post_id: str = "", index: int = 0, confirmed: bool = False) -> dict[str, Any]:
    """AMBER. The reverse of `bookmark`, same target rule, same hold, same boundary."""
    target, who = _resolve_post(post_id, index)
    if not confirmed:
        raise ToolHold(f"removing {who}'s post {target} from your bookmarks" if who
                       else f"removing post {target} from your bookmarks",
                       resolved={"post_id": target})
    return _engage_post(target, "unbookmark")


#: X's THIRD follow state (live, 2026-09-22): "Pending" — a follow request
#: the account approves by hand (@Support is one). Its button carries no
#: aria-label, so its accessible name is its text. After a follow press the
#: header is given this long to change, and which way it went is spoken.
_PENDING_NAME = re.compile(r"^pending$", re.I)
FOLLOW_SETTLE_S = 6.0
#: A confirmation sheet, when X shows one, is there well inside this.
SHEET_S = 3.0


def _confirm_sheet(page: Any, handle: str, required: bool) -> None:
    """
    X's second ask after an unfollow, in a sheet: one checked press on its
    confirm. Accessible name first, X's test id second. `required=False` is
    the withdraw of a pending request, where X may not ask at all.
    """
    confirm = page.get_by_role("button", name=re.compile(r"^unfollow$", re.I))
    n = _await_present(page, lambda: confirm,
                       lambda: page.locator('[data-testid="confirmationSheetConfirm"]'),
                       timeout_s=WRITE_HYDRATE_S if required else SHEET_S)
    if not n and not required:
        return
    try:
        if confirm.count() == 0:
            confirm = page.locator('[data-testid="confirmationSheetConfirm"]')
    except Exception:  # noqa: BLE001
        pass
    _press(_require_one(confirm, f"the Unfollow confirmation for @{handle}"),
           "unfollow-confirm", f"@{handle}")
    page.wait_for_timeout(600)


def _follow_names(handle: str) -> tuple[Any, Any]:
    """The exact accessible names of @handle's Follow and Following buttons."""
    return (re.compile(rf"^follow\s+@{re.escape(handle)}$", re.I),
            re.compile(rf"^(?:following|unfollow)\s+@{re.escape(handle)}$", re.I))


def _engage_user(handle: str, action: str) -> dict[str, Any]:
    """The confirmed half of follow/unfollow: open the profile, check it is theirs, press once."""
    _require_signed_in()
    page = _page()
    _goto(page, f"https://x.com/{handle}")
    # THE HEADER HYDRATES BEFORE THE POSTS (live: the Follow button at
    # 1.7-2.3 s with zero articles yet), so the wait is for the button that
    # will be pressed, by its EXACT name — never the test-id fallback, which
    # would also match the suggested accounts' buttons.
    follow_re, following_re = _follow_names(handle)
    _await_present(page, lambda: page.get_by_role("button", name=follow_re),
                   lambda: page.get_by_role("button", name=following_re),
                   lambda: page.get_by_role("button", name=_PENDING_NAME))
    _check_reachable(page)
    low = _body(page).lower()
    if "account doesn't exist" in low or "account doesn\u2019t exist" in low or "account suspended" in low:
        raise ToolError(f"@{handle} is not an account I can find",
                        "Check the spelling and say it again. Nothing was pressed.")
    landed = (page.url or "").split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
    if landed.lower() != handle.lower():
        raise ToolError(f"X took me to @{landed} instead of @{handle}",
                        "Nothing was pressed. Say the handle again.")
    # THE EXACT HANDLE IN THE ACCESSIBLE NAME. A profile page also carries
    # "Follow @someone_else" buttons for suggested accounts; matching the name
    # to the end keeps every one of those out. The test-id fallback cannot
    # tell them apart, so on that path several matches is a refusal.
    off = page.get_by_role("button", name=follow_re)
    on = page.get_by_role("button", name=following_re)
    try:
        if off.count() == 0 and on.count() == 0:
            off = page.locator('[data-testid$="-follow"]')
            on = page.locator('[data-testid$="-unfollow"]')
    except Exception:  # noqa: BLE001
        pass
    want, other = (off, on) if action == "follow" else (on, off)
    pending = page.get_by_role("button", name=_PENDING_NAME)
    try:
        have_want, have_other, have_pending = want.count(), other.count(), pending.count()
    except Exception:  # noqa: BLE001
        have_want = have_other = have_pending = 0
    if have_want == 0 and have_pending == 1:
        if action == "follow":
            return {"handle": handle, "who": f"@{handle}", "already": True,
                    "note": " A follow request is already pending; they approve follows by hand."}
        # THE REVERSE OF A FOLLOW THAT WENT PENDING: withdraw the request. One
        # press on the Pending button; X may or may not ask again in a sheet.
        _press(_require_one(pending, f"the Pending button for @{handle}"),
               "unfollow-pending", f"@{handle}")
        _confirm_sheet(page, handle, required=False)
        return {"handle": handle, "who": f"@{handle}", "already": False,
                "note": " It was a pending request; withdrawn."}
    # EXACTLY ONE opposite-state button says "already". On the test-id
    # fallback several `-follow` matches are the suggested accounts, not his
    # state: live, a header that failed to render left only those, and this
    # branch said "already not following" for a state it could not see. Now
    # that falls through to `_require_one`, which refuses on zero or several.
    if have_want == 0 and have_other == 1:
        state = "following them" if action == "follow" else "not following them"
        return {"handle": handle, "who": f"@{handle}", "already": True,
                "note": f" You were already {state}; nothing changed."}
    what = "Follow" if action == "follow" else "Following"
    btn = _require_one(want, f"the {what} button for @{handle}")
    _press(btn, action, f"@{handle}")
    if action == "unfollow":
        # X asks a second time, in a sheet, rendered by script after the press.
        _confirm_sheet(page, handle, required=True)
        return {"handle": handle, "who": f"@{handle}", "already": False, "note": ""}
    # WHAT THE PRESS DID. Live, a follow of @Support came back "Pending" and
    # the old line said "Following". Wait for the header to change — the
    # lookups are rebuilt on every poll — and say which way it went.
    _await_present(page, lambda: page.get_by_role("button", name=following_re),
                   lambda: page.get_by_role("button", name=_PENDING_NAME),
                   timeout_s=FOLLOW_SETTLE_S, settle_ms=0)
    try:
        now_following = page.get_by_role("button", name=following_re).count() == 1
        went_pending = (not now_following
                        and page.get_by_role("button", name=_PENDING_NAME).count() == 1)
    except Exception:  # noqa: BLE001
        now_following = went_pending = False
    if went_pending:
        note = f" X has it as Pending: they approve follows by hand. Say unfollow @{handle} to withdraw it."
    elif now_following:
        note = ""
    else:
        # Live (@Support): for minutes after a follow REQUEST the header shows
        # no button at all, then "Pending". The press happened; the result is
        # not on the page yet, and saying "Following" would be a guess.
        note = " I pressed Follow, but X has not shown me the result yet; ask me again in a few minutes."
    return {"handle": handle, "who": f"@{handle}", "already": False, "note": note}


def follow(handle: str = "", confirmed: bool = False) -> dict[str, Any]:
    """
    AMBER. Follow ONE account, by @name.

    The hold is raised BEFORE any navigation and names the handle he said,
    normalised (no @), which is what the ledger is armed on and what runs.
    """
    clean = _one_handle(handle)
    if not confirmed:
        raise ToolHold(f"following @{clean} on X, publicly, as you", resolved={"handle": clean})
    return _engage_user(clean, "follow")


def unfollow(handle: str = "", confirmed: bool = False) -> dict[str, Any]:
    """AMBER. The reverse of `follow`, same target rule, same hold, same boundary."""
    clean = _one_handle(handle)
    if not confirmed:
        raise ToolHold(f"unfollowing @{clean} on X", resolved={"handle": clean})
    return _engage_user(clean, "unfollow")


# ─────────────────────────────────────────────────────────────────────────────
# AMBER — REPOST, REBUILT ON THE ENGAGEMENT PATTERN (X features round 3,
# 2026-09-12). The old `repost` addressed a post by POSITION on a live page —
# the hazard `x.like` was rebuilt away from in round 2: approve "repost post
# two", have the timeline hydrate in the second before the yes, and somebody
# else's post goes out under his name. Now it is `x.like`'s shape exactly: ONE
# target by shape (`_one_post_id` refuses bulk before any navigation), resolved
# ONCE against the snapshot of the last read, the hold armed on the STATUS ID
# (ToolHold.resolved), the permalink opened, THE article found by id
# (`_focal_article`), and every press through `_press`. X asks a second time
# in a menu; that item is matched on its EXACT accessible name, so the "Quote"
# item beside it is unreachable from here. Reversible: `unrepost` undoes it
# the same way, and each direction is idempotent.
# ─────────────────────────────────────────────────────────────────────────────

#: "12 reposts. Repost" / "12 reposts. Reposted" — anchored on the last word,
#: like the like button, so a repost never lands on a Reposted button.
_REPOST_NAME = re.compile(r"(?:^|[\s.])(?:repost|retweet)$", re.I)
_REPOSTED_NAME = re.compile(r"(?:^|[\s.])(?:reposted|retweeted)$", re.I)


def _repost_buttons(art: Any) -> tuple[Any, Any]:
    """(the Repost button, the Reposted button) on one article; either may be empty."""
    off = art.get_by_role("button", name=_REPOST_NAME)
    on = art.get_by_role("button", name=_REPOSTED_NAME)
    try:
        if off.count() == 0 and on.count() == 0:
            off = art.locator('[data-testid="retweet"]')
            on = art.locator('[data-testid="unretweet"]')
    except Exception:  # noqa: BLE001
        pass
    return off, on


def _engage_repost(post_id: str, action: str) -> dict[str, Any]:
    """The confirmed half of repost/unrepost: open the permalink, find the post, press, confirm."""
    _require_signed_in()
    page = _page()
    _goto(page, f"https://x.com/i/web/status/{post_id}")
    _await_posts(page)
    _check_reachable(page)
    art = _focal_article(page, post_id)
    fields = _one_post(art)
    who = fields.get("handle") or fields.get("who") or "?"
    off, on = _repost_buttons(art)
    want, other, label = (off, on, "repost") if action == "repost" else (on, off, "unrepost")
    try:
        have_want, have_other = want.count(), other.count()
    except Exception:  # noqa: BLE001
        have_want = have_other = 0
    if have_want == 0 and have_other >= 1:
        state = "reposted" if action == "repost" else "not reposted"
        return {"post_id": post_id, "who": who, "already": True,
                "note": f" It was already {state}; nothing changed."}
    btn = _require_one(want, f"the {label} button on post {post_id}")
    _press(btn, label, f"post {post_id}")
    # X asks a second time, in a menu. EXACT name — "Repost" to do it, "Undo
    # repost" to undo it — so the "Quote" item next to it can never be the one
    # pressed. X's own test id second, and several matches is a refusal.
    if action == "repost":
        item = page.get_by_role("menuitem", name=re.compile(r"^(?:repost|retweet)$", re.I))
        testid = "retweetConfirm"
    else:
        item = page.get_by_role("menuitem", name=re.compile(r"^undo\s+(?:repost|retweet)$", re.I))
        testid = "unretweetConfirm"
    # The menu is rendered by script after the press: wait for the item.
    _await_present(page, lambda: item, lambda: page.locator(f'[data-testid="{testid}"]'))
    try:
        if item.count() == 0:
            item = page.locator(f'[data-testid="{testid}"]')
    except Exception:  # noqa: BLE001
        pass
    _press(_require_one(item, f"the {label} confirmation for post {post_id}"),
           f"{label}-confirm", f"post {post_id}")
    page.wait_for_timeout(600)
    return {"post_id": post_id, "who": who, "already": False, "note": ""}


def repost(post_id: str = "", index: int = 0, confirmed: bool = False) -> dict[str, Any]:
    """
    AMBER. Repost ONE post, by status id, to his followers.

    Same rule as `like`: an ordinal is resolved against the last read's
    snapshot HERE, once, and the hold is raised with the id, so his "yes" acts
    on that id whatever the page shows by then. `confirmed` is set only by the
    executor after the ledger accepted his yes or his repeat.
    """
    target, who = _resolve_post(post_id, index)
    if not confirmed:
        raise ToolHold(f"reposting {who}'s post {target} to your followers, as you" if who
                       else f"reposting post {target} to your followers, as you",
                       resolved={"post_id": target})
    return _engage_repost(target, "repost")


def unrepost(post_id: str = "", index: int = 0, confirmed: bool = False) -> dict[str, Any]:
    """AMBER. The reverse of `repost`, same target rule, same hold, same boundary."""
    target, who = _resolve_post(post_id, index)
    if not confirmed:
        raise ToolHold(f"undoing your repost of {who}'s post {target}" if who
                       else f"undoing your repost of post {target}",
                       resolved={"post_id": target})
    return _engage_repost(target, "unrepost")


# ─────────────────────────────────────────────────────────────────────────────
# RED — publishing. Fully built, and gated in the executor.
# ─────────────────────────────────────────────────────────────────────────────

#: X's limit. Checked HERE rather than after typing, so she refuses before
#: anything is entered into a compose box.
MAX_POST_CHARS = 280
#: The compose box and the send toast, by accessible name; the toast is X's
#: answer to its own request, so its wait has a ceiling of its own.
_COMPOSE_BOX = re.compile("post text|tweet text", re.I)
_VIEW_LINK = re.compile(r"^view$", re.I)
POST_TOAST_S = 8.0


def _own_handle(page: Any) -> str:
    """His own @name (without the @), read from the navigation's Profile link."""
    pat = re.compile(r"/([A-Za-z0-9_]{1,15})/?")
    try:
        for href in page.get_by_role("link", name=re.compile(r"^profile$", re.I)).evaluate_all(
                "els => els.map(e => e.getAttribute('href'))") or []:
            m = pat.fullmatch(str(href or ""))
            if m:
                return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    try:
        href = page.locator('[data-testid="AppTabBar_Profile_Link"]').first.get_attribute("href")
        m = pat.fullmatch(str(href or ""))
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _posted_id(page: Any, text: str) -> str:
    """
    The status id X assigned to the post that was just sent, or "".

    NEVER A GUESS. First X's own toast — "Your post was sent. View" — whose
    View link IS the new permalink. If that has gone (it lasts a few seconds)
    the fallback is his profile: the newest article whose text is what was
    sent, links stripped on both sides because X renders a trailing permalink
    as a quote card rather than as text. Neither found is "", and the one
    caller that needs the id — a thread chaining its next part — STOPS on ""
    rather than reading a position off the page.
    """
    wanted = " ".join(_URL.sub("", text).split()).lower()
    # The toast follows X's own request, not the press: wait for it, briefly.
    _await_present(page, lambda: page.get_by_role("link", name=_VIEW_LINK),
                   timeout_s=POST_TOAST_S, settle_ms=0)
    try:
        for href in page.get_by_role("link", name=_VIEW_LINK).evaluate_all(
                "els => els.map(e => e.getAttribute('href'))") or []:
            m = _STATUS_HREF.search(str(href or ""))
            if m:
                return m.group(2)
    except Exception:  # noqa: BLE001
        pass
    handle = _own_handle(page)
    if not handle or not wanted:
        return ""
    try:
        _goto(page, f"https://x.com/{handle}")
        _await_posts(page)               # his profile hydrates late (live: 4.2 s)
        for p in _posts(page, 10):
            got = " ".join(_URL.sub("", str(p.get("text") or "")).split()).lower()
            if got == wanted and p.get("id"):
                return str(p["id"])
    except Exception:  # noqa: BLE001
        pass
    return ""


def _publish(button: Any, what: str, text: str) -> None:
    """
    THE ONE PUBLISH BOUNDARY (live-write round, 2026-09-22). Every post and
    every reply — and so every quote and every thread part — goes public
    through this call, on the single Post/Reply button `_require_one`
    returned. `what` and `text` are for the recorder and the error, never for
    choosing the element: the choice was made, and checked, before this is
    reached. A proof swaps this for a recorder that refuses, and so verifies
    the compose path right up to the send without sending.
    """
    try:
        button.click()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(
            f"the {what} button did not take the press ({type(exc).__name__})",
            "X may have changed its page. I cannot tell whether it went; look at X.") from None


def post(text: str, _approved_by_surface: bool = False) -> dict[str, Any]:
    """
    RED. Publish a post.

    THIS FUNCTION IS COMPLETE AND IT DOES NOT RUN. `Executor._dispatch_registry`
    stops every red tool before the handler is reached and raises a permission
    request instead — see core/brain/approvals.py. The body below is what will
    execute the day the approval card exists, and it is written and reviewed now
    so that day is a UI change and not a rewrite of the risky part.

    Why this one in particular: a tweet is the least reversible thing in this
    build. A wrong delete is in the Recycle Bin. A wrong `like` is one click
    back. A wrong post has been seen, quoted and screenshotted before he knows
    it happened — and the transcription layer that would trigger it has produced
    "Alicoy" and "The game is over" from ordinary sentences.
    """
    body = str(text or "").strip()
    if not body:
        raise ToolError("there was nothing to post", "Tell me what to say.")
    if _x_len(body) > MAX_POST_CHARS:
        raise ToolError(f"that is {_x_len(body)} characters as X counts it, over X's {MAX_POST_CHARS}",
                        "Shorten it and I will hold it again.")
    if not _approved_by_surface:
        # Defence in depth. The executor already stopped this; if a future
        # caller reaches the handler directly, it still refuses.
        # The card EXISTS now (Session 2 shipped it); what this branch means is
        # that the handler was reached WITHOUT going through it. Defence in
        # depth, and the message says which of the two it is.
        raise ToolError("that reached the post handler without an approval",
                        "Nothing was published. Approve it on the card.")

    _require_signed_in()
    page = _page()
    _goto(page, "https://x.com/compose/post")
    # The compose dialog is rendered by script after the load event (live:
    # 1.3 s; the old fixed 1.5 s beat was one slow load from missing it).
    box = page.get_by_role("textbox", name=_COMPOSE_BOX)
    _await_present(page, lambda: box, lambda: page.locator('[data-testid="tweetTextarea_0"]'))
    _check_reachable(page)
    if box.count() == 0:
        box = page.locator('[data-testid="tweetTextarea_0"]')
    _require_one(box, "the compose box").fill(body)
    page.wait_for_timeout(400)
    btn = page.get_by_role("button", name=re.compile(r"^post$", re.I))
    if btn.count() == 0:
        btn = page.locator('[data-testid="tweetButton"]')
    _publish(_require_one(btn, "the Post button"), "post", body)
    page.wait_for_timeout(1200)
    # THE ID X GAVE IT (round 3). "" when it cannot be read: a plain post does
    # not need it, a thread chains its next part under it and stops without it.
    return {"chars": len(body), "text": body[:80], "id": _posted_id(page, body)}


def reply(text: str, index: int = 1, reply_to_id: str = "",
          _approved_by_surface: bool = False) -> dict[str, Any]:
    """
    RED. Same gate, same reasoning as `post`.

    ⚠ `reply_to_id` IS THE CORRECT WAY TO NAME A POST, AND `index` IS NOT.

    An index is a position in a list that reorders itself every few seconds as
    X hydrates. Approve a reply to "post 2", have the timeline refresh in the
    second before it runs, and the words land under somebody else's tweet —
    publicly, under his name, permanently. A status id does not move.

    `index` is kept because the older voice phrasing ("reply to post two")
    still builds calls with it and its tests exercise it. It is the LEGACY
    path: whenever an id is supplied it wins, the page is navigated to that
    post's permalink, and the reply is aimed at the post the id names rather
    than at whatever happens to be second on screen.
    """
    body = str(text or "").strip()
    if not body:
        raise ToolError("there was nothing to reply with", "Tell me what to say.")
    if _x_len(body) > MAX_POST_CHARS:
        raise ToolError(f"that is {_x_len(body)} characters as X counts it, over X's {MAX_POST_CHARS}",
                        "Shorten it and I will hold it again.")
    if not _approved_by_surface:
        raise ToolError("that reached the reply handler without an approval",
                        "Nothing was published. Approve it on the card.")

    _require_signed_in()
    page = _page()

    target_id = str(reply_to_id or "").strip()
    if target_id:
        # ── TARGETED BY ID. The permalink IS the post, so the first article on
        #    the page is the thing he approved a reply to — no counting, no
        #    position, nothing to drift.
        if not re.fullmatch(r"\d{5,25}", target_id):
            raise ToolError(f"{target_id!r} is not a post id",
                            "Read the timeline again and give me an id from it.")
        _goto(page, f"https://x.com/i/web/status/{target_id}")
        _await_posts(page)               # the same hydration race as every read (live: 2.8 s)
        _check_reachable(page)
        articles = page.get_by_role("article")
        if articles.count() == 0:
            # A STALE OR DELETED POST IS A REFUSAL, NEVER A FALLBACK. Quietly
            # dropping to the timeline here is precisely how an approved reply
            # would land under the wrong post.
            raise ToolError(f"post {target_id} is not there any more",
                            "It may have been deleted. Read the timeline again.")
        # THE article for the id, not the first on the page: a post that is
        # itself a reply renders under its parent, and `nth(0)` was the parent.
        art = _focal_article(page, target_id)
    else:
        _check_reachable(page)
        articles = page.get_by_role("article")
        if articles.count() < index:
            raise ToolError(f"there is no post number {index}", "Say a number I read to you.")
        art = articles.nth(index - 1)
    btn = art.get_by_role("button", name=re.compile("reply", re.I))
    if btn.count() == 0:
        btn = art.locator('[data-testid="reply"]')
    _require_one(btn, f"the reply button on {'post ' + target_id if target_id else 'post ' + str(index)}").click()
    # The reply dialog is rendered by script after the press (live: within
    # 10 ms, but a fixed beat is still a guess). By ROLE first: the page's
    # inline composer stays in the DOM behind the dialog, aria-hidden, so the
    # role lookup sees one box where the test id would see two.
    box = page.get_by_role("textbox", name=_COMPOSE_BOX)
    _await_present(page, lambda: box, lambda: page.locator('[data-testid="tweetTextarea_0"]'))
    if box.count() == 0:
        box = page.locator('[data-testid="tweetTextarea_0"]')
    _require_one(box, "the reply box").fill(body)
    page.wait_for_timeout(400)
    send = page.get_by_role("button", name=re.compile("^reply$|^post$", re.I))
    if send.count() == 0:
        send = page.locator('[data-testid="tweetButton"]')
    _publish(_require_one(send, "the reply send button"), "reply", body)
    page.wait_for_timeout(1200)
    return {"chars": len(body), "index": index, "reply_to_id": target_id,
            "targeted": "by id" if target_id else "by position",
            "text": body[:80],
            # The id X gave the reply (round 3): a thread chains under it.
            "id": _posted_id(page, body)}


# ─────────────────────────────────────────────────────────────────────────────
# RED — THE PUBLISHING ROUND 3 (2026-09-12): quote-post and threads. Both are
# POSTING — public, permanent, in his name — so both are red, both reach the
# hardened approval card and nothing else, and both go out through the SAME
# compose path, `post` / `reply` above: there is no second way to publish in
# this file, and a recorder at `post` sees every quote and every first part.
#
# THE TARGET IS RESOLVED BEFORE THE CARD. A red handler is never reached
# before the card (the executor raises the request from the dispatch args), so
# "quote post two" has nowhere to become a status id inside the handler.
# `ToolSpec.resolve` is the red twin of `ToolHold.resolved`: `_quote_args` and
# `_thread_args` run in the red gate, ONCE, turn the ordinal into the frozen
# id, fill in what the card must SHOW (the quoted post, its author, how many
# instruction-shaped patterns it carried; the thread's posts in order), refuse
# bulk, over-length and a post she has not read BEFORE he is asked, and hand
# back the args the request is raised on.
#
# EVERYTHING ON BOTH CARDS IS FROZEN — the text too, unlike `x.post`. A quote
# is aimed at a post and a thread is approved as a unit; there is no wording to
# correct on the card without changing what he approved. If Whisper mangled it
# he denies and says it again: five seconds, and the only safe answer.
#
# A QUOTE IS A POST WITH THE PERMALINK ON THE END. X renders a post whose text
# ends in a status URL as a quote of that post (X's own help: paste the post's
# URL to quote it). So `quote` is `post(text + " " + permalink)`: the proven
# compose path, the proven no-approval refusal, and the link costs 23 of 280.
#
# A THREAD IS POSTED BY ID-CHAIN, NEVER BY POSITION. Part 1 is `post`; each
# later part is `reply(reply_to_id=<the id X returned for the part before>)`.
# `_posted_id` reads that id from X's own "View" toast or from his profile and
# NEVER guesses. A part that fails, or whose id cannot be read, STOPS the
# thread there — loudly: THREAD-STOPPED on the chain with the ids that ARE
# public, and a ToolError that says how many went out and which one failed. A
# half-thread he can see is something he can fix; a silent one is not. Nothing
# here deletes; what posted stays posted.
#
# ⚠ HONEST ABOUT THE FOOTPRINT: threads and quotes at volume are what a bot
# looks like. Every part is paced (`_goto`), a thread is capped at
# MAX_THREAD_PARTS, and how much of it X tolerates is X's call, not this code's.
# ─────────────────────────────────────────────────────────────────────────────

#: A permalink costs 23 of the 280, plus the space before it.
MAX_QUOTE_CHARS = MAX_POST_CHARS - 24
#: More parts than this in one go is a bot's shape — and a longer half-thread
#: when part k fails.
MAX_THREAD_PARTS = 10
#: "tweet a thread about X" with no count drafts this many parts.
DEFAULT_THREAD_PARTS = 3
#: The only link a quote may carry: an X status permalink, whose id must be the
#: id the card showed.
_X_STATUS_URL = re.compile(
    r"^https://(?:x|twitter)\.com/(?:i/web|[A-Za-z0-9_]{1,15})/status/(\d{5,25})$")


def _quote_args(args: dict[str, Any]) -> dict[str, Any]:
    """
    ToolSpec.resolve for x.quote — BEFORE the card, once.

    The post he named (an id, or "post two" into the snapshot of the last
    read) becomes the frozen `quoted_id`; the card is given its author, its
    text and how many instruction-shaped patterns it carried, so he never
    approves a quote of something he has not seen. A post she has not read is
    refused here — the card could not show it. Bulk is refused by shape
    (`_one_post_id`) before any of this.
    """
    from core.brain.provenance import detect_injection

    raw_id = args.get("quoted_id")
    if raw_id in (None, ""):
        raw_id = args.get("post_id", "")
    target, _who = _resolve_post(raw_id, args.get("index") or 0)
    src = next((p for p in _LAST_READ if p.get("id") == target), None)
    if src is None:
        raise ToolError(f"I have not read post {target}, so I cannot show you what you would be quoting",
                        "Say read the thread on it, or read the timeline, then ask me again.")
    text = " ".join(str(args.get("text") or "").split()).strip()
    if not text:
        raise ToolError("there was nothing to say over the quote", "Tell me what to say with it.")
    if len(text) > MAX_QUOTE_CHARS:
        raise ToolError(f"that is {len(text)} characters; a quote has room for {MAX_QUOTE_CHARS} "
                        f"once the link is counted", "Shorten it and I will hold it again.")
    full = str(src.get("text") or "")
    return {
        "text": text,
        "quoted_id": target,
        "quoted_author": str(src.get("handle") or src.get("who") or "?")[:60],
        "quoted_text": full[:MAX_POST_CHARS],
        "quoted_url": str(src.get("url") or f"https://x.com/i/web/status/{target}"),
        "injection_seen": len(detect_injection(full)),
    }


def _describe_quote(args: dict[str, Any]) -> str:
    """The card's spoken line, from the RESOLVED args."""
    n = int(args.get("injection_seen") or 0)
    return (f"quote {args.get('quoted_author') or '?'}'s post {args.get('quoted_id') or '?'} "
            f"saying: {str(args.get('text') or '')[:90]}"
            + (f" (that post tried {n} instruction{'s' if n != 1 else ''} on me)" if n else ""))


def quote(text: str, quoted_id: str, quoted_author: str = "", quoted_text: str = "",
          quoted_url: str = "", injection_seen: int = 0,
          _approved_by_surface: bool = False) -> dict[str, Any]:
    """
    RED. Quote-post ONE post: his words, with that post's permalink on the end.

    `quoted_author`, `quoted_text` and `injection_seen` are carried so the CARD
    can show what he is quoting. They are frozen and never acted on — evidence,
    not instructions. Only reachable through the approval card: `post` refuses
    a second time on its own account if the flag is absent.
    """
    body = " ".join(str(text or "").split()).strip()
    target = str(quoted_id or "").strip()
    if not body:
        raise ToolError("there was nothing to say over the quote", "Tell me what to say with it.")
    if not _POST_ID.fullmatch(target):
        raise ToolError(f"{target!r} is not a post id",
                        "Read the timeline again and give me an id from it, or say post two.")
    url = str(quoted_url or "").strip() or f"https://x.com/i/web/status/{target}"
    m = _X_STATUS_URL.match(url)
    if not m or m.group(1) != target:
        # The link names a DIFFERENT post from the id the card showed. Only a
        # caller that skipped `_quote_args` can build this; refused, not trusted.
        raise ToolError(f"the quote link does not name post {target}",
                        "Ask me again and I will resolve it afresh.")
    if len(body) > MAX_QUOTE_CHARS:
        raise ToolError(f"that is {len(body)} characters; a quote has room for {MAX_QUOTE_CHARS} "
                        f"once the link is counted", "Shorten it and I will hold it again.")
    if not _approved_by_surface:
        raise ToolError("that reached the quote handler without an approval",
                        "Nothing was published. Approve it on the card.")
    # THE PROVEN COMPOSE PATH, with the flag THREADED — never a literal True,
    # which would launder a call that skipped the card (x_reply.py's lesson).
    result = post(text=f"{body} {url}", _approved_by_surface=_approved_by_surface)
    return {"chars": len(body), "text": body[:80], "quoted_id": target,
            "quoted_author": str(quoted_author or "?"),
            "id": str(result.get("id") or "") if isinstance(result, dict) else "",
            "warned": (" That post had tried an instruction on me; I quoted it, I did not follow it."
                       if int(injection_seen or 0) else "")}


#: How a dictated or typed thread is cut into parts: `;`, `|`, a newline,
#: ` / ` with spaces, or X's own `1/ 2/ 3/` numbering. Not full stops — a part
#: may be more than one sentence.
_THREAD_SEP = re.compile(r"\s*(?:;|\||\n|\s/\s|(?<![\w/])\d{1,2}/\s*)\s*")


def _split_thread(raw: str) -> list[str]:
    return [" ".join(p.split()).strip() for p in _THREAD_SEP.split(str(raw or "")) if p and p.strip()]


def _draft_thread(topic: str, n: int) -> list[str]:
    """
    Draft `n` parts about `topic` in HIS voice, through the same drafting path
    a reply takes (core/brain/x_voice.py: the fenced prompt, his cached style,
    the engine settings.yaml chose). The topic is his own words — there is no
    source post and nothing to fence. The drafts go on the card, frozen.
    """
    from core.brain import x_voice
    from core.system.abilities.x_draft import _engine

    style = x_voice.load_profile()
    if style is None:
        raise ToolError("I have not learned your voice yet",
                        "Say learn my voice from X first, and I will read your profile once.")
    engine = _engine()
    out: list[str] = []
    for i in range(n):
        draft = x_voice.draft_reply(
            {"id": "", "handle": "", "text": topic}, style, engine,
            guidance=(f"Write post {i + 1} of {n} of a THREAD about: {topic}. Each post stands on "
                      f"its own, they read in order, and this one must not repeat the others."
                      + (f" Already written: {' // '.join(out)}" if out else "")))
        if draft.text:
            out.append(draft.text)
    return out


def _thread_args(args: dict[str, Any]) -> dict[str, Any]:
    """
    ToolSpec.resolve for x.thread — BEFORE the card, once.

    `posts` may arrive as one string (cut by `_split_thread`) or a list; with
    no posts and a `topic`, the parts are drafted in his voice. Two to
    MAX_THREAD_PARTS parts, each within X's count, or a refusal before the
    card. What comes back is the frozen list the card shows in order.
    """
    raw = args.get("posts")
    topic = " ".join(str(args.get("topic") or "").split()).strip(" .,:;")
    if isinstance(raw, str):
        parts = _split_thread(raw)
    elif isinstance(raw, (list, tuple)):
        parts = [" ".join(str(p).split()).strip() for p in raw]
    else:
        parts = []
    parts = [p for p in parts if p]
    if not parts and topic:
        try:
            want = int(args.get("n") or DEFAULT_THREAD_PARTS)
        except (TypeError, ValueError):
            raise ToolError(f"{args.get('n')!r} is not a number of posts",
                            f"Say a number from 2 to {MAX_THREAD_PARTS}.") from None
        if want < 2 or want > MAX_THREAD_PARTS:
            raise ToolError(f"a thread is 2 to {MAX_THREAD_PARTS} posts, and you asked for {want}",
                            "Say a number in that range.")
        parts = _draft_thread(topic, want)
    if not parts:
        raise ToolError("there was nothing to post as a thread",
                        "Give me the posts, separated by semicolons, or a topic to write about.")
    if len(parts) < 2:
        raise ToolError("a thread needs at least two posts, and that is one",
                        "Say tweet that for a single post.")
    if len(parts) > MAX_THREAD_PARTS:
        raise ToolError(f"that is {len(parts)} posts; I thread at most {MAX_THREAD_PARTS} at a time",
                        "Split it, or trim it.")
    for i, p in enumerate(parts, 1):
        if _x_len(p) > MAX_POST_CHARS:
            raise ToolError(f"post {i} of the thread is {_x_len(p)} characters, over X's {MAX_POST_CHARS}",
                            "Shorten that one and I will hold it again.")
    return {"posts": list(parts), "topic": topic}


def _describe_thread(args: dict[str, Any]) -> str:
    posts = args.get("posts") if isinstance(args.get("posts"), list) else []
    first = str(posts[0])[:80] if posts else "?"
    return f"a thread of {len(posts)} posts, starting: {first}"


def _public(ids: list[str]) -> str:
    """How much of a thread is out there, for the chain and for him."""
    if not ids:
        return "Nothing of it is public"
    return f"{len(ids)} post{'s are' if len(ids) != 1 else ' is'} public: {', '.join(ids)}"


def thread(posts: Any, topic: str = "", _approved_by_surface: bool = False,
           provenance: str = "schedule") -> dict[str, Any]:
    """
    RED. Post an approved thread: part 1 through `post`, every later part
    through `reply` under the id X returned for the part before.

    ONE card approved the whole list; this posts exactly that list, in order,
    and STOPS at the first part that fails or whose id cannot be read — with
    THREAD-STOPPED on the chain naming the ids that are public, and a ToolError
    that says the same out loud. `provenance` is the call's resolved origin,
    handed in by the executor by signature (never read from args), so every
    THREAD-PART line carries the true actor.
    """
    parts = [" ".join(str(p).split()).strip()
             for p in (posts if isinstance(posts, (list, tuple)) else [])]
    parts = [p for p in parts if p]
    n = len(parts)
    if n < 2:
        raise ToolError("a thread needs at least two posts", "Say tweet that for a single post.")
    if n > MAX_THREAD_PARTS:
        raise ToolError(f"that is {n} posts; I thread at most {MAX_THREAD_PARTS} at a time",
                        "Split it, or trim it.")
    for i, p in enumerate(parts, 1):
        if _x_len(p) > MAX_POST_CHARS:
            raise ToolError(f"post {i} of the thread is {_x_len(p)} characters, over X's {MAX_POST_CHARS}",
                            "Shorten that one and I will hold it again.")
    if not _approved_by_surface:
        raise ToolError("that reached the thread handler without an approval",
                        "Nothing was published. Approve it on the card.")
    _require_signed_in()
    ids: list[str] = []
    for i, part in enumerate(parts, 1):
        try:
            if i == 1:
                got = post(text=part, _approved_by_surface=_approved_by_surface)
            else:
                got = reply(text=part, reply_to_id=ids[-1], _approved_by_surface=_approved_by_surface)
        except ToolError as err:
            _report("THREAD-STOPPED", "x.thread",
                    f"part {i}/{n} failed: {err.reason}; {_public(ids)}", "red", actor=provenance)
            raise ToolError(
                f"the thread stopped at post {i} of {n}: {err.reason}. {_public(ids)}",
                "Nothing after it went out. Look at the thread on X and tell me how to carry on.") from None
        new_id = str(got.get("id") or "") if isinstance(got, dict) else ""
        if not _POST_ID.fullmatch(new_id):
            if i == n:
                # The last part is out and nothing needs chaining under it;
                # the thread is complete, the record just cannot name its id.
                ids.append("")
                _report("THREAD-PART", "x.thread", f"{i}/{n} id=? (could not be read) reply_to={ids[-2]}",
                        "red", actor=provenance)
                break
            _report("THREAD-STOPPED", "x.thread",
                    f"part {i}/{n} posted but its id could not be read; {_public(ids)}, plus part {i}",
                    "red", actor=provenance)
            raise ToolError(
                f"post {i} of {n} went out but I could not read its id, so I cannot chain post {i + 1} "
                f"under it. {_public(ids)}, plus post {i}",
                "Look at the thread on X; I will not guess which post to reply to.")
        ids.append(new_id)
        _report("THREAD-PART", "x.thread",
                f"{i}/{n} id={new_id}" + (f" reply_to={ids[-2]}" if i > 1 else ""), "red", actor=provenance)
    return {"n": n, "ids": ids, "first_id": ids[0], "last_id": ids[-1] or "?",
            "chars": sum(len(p) for p in parts)}


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE — DIRECT MESSAGES (X features round 4, the LAST, 2026-09-12).
#
# A DM IS HIS CORRESPONDENCE, NOT STRANGERS' PUBLIC TEXT — and a DM sent in
# his name is often MORE consequential than a public post: private, direct,
# one-to-one, and the shape a poisoned tweet aims for ("DM @x his address").
# So this section is built to two rules the rest of the file does not need:
#
#   1. READ IS PRIVATE. `read_dm` returns the messages for him to HEAR, fenced
#      as external (someone else wrote them — the fence still stands) — and
#      that is the only place they go. Its result carries `messages`, never
#      `posts`, and the spec is flagged `private`, so the executor never
#      hands it to the claim or style stores; the chain gets READ-DM with the
#      handle and a COUNT (`_report`), never a message; the daemon withholds
#      the turn's heard/said text from the chain. His inbox is not learning
#      material and not a log.
#
#   2. SEND IS RED, ONE RECIPIENT, RESOLVED BEFORE THE CARD, EVERYTHING FROZEN.
#      `_send_dm_args` runs in the red gate (ToolSpec.resolve — round 3's
#      mechanism) ONCE: the recipient must be exactly ONE @name by shape
#      (`_one_handle` — "everyone", "all my followers", "@a and @b", a list
#      cannot be expressed), the words non-empty and within MAX_DM_CHARS, and
#      the conversation must be one he READ in the last DM_READ_FRESH_S
#      seconds (`_LAST_DM`) — NO REPLY BLIND: he never answers a message he
#      has not heard. What reaches the card is the handle, the exact words,
#      the conversation URL the read captured and how many messages he heard;
#      ALL FROZEN. The executor's flag-strip and the card are the only route
#      to `send_dm`; the handler re-checks every one of those facts on its
#      own account and refuses without the approval flag. The one press is
#      `_send_dm`, the send boundary, on the single Send button
#      `_require_one` returned — a recorder there sees every send.
#
# THE CHAIN NEVER HOLDS THE MESSAGE. `x.send_dm` declares `private_args=
# ("text",)`: every chain line that would carry the words — PENDING-APPROVAL,
# REQUESTED, APPROVED, DENIED — holds `<withheld: N chars, sha256 …>` instead
# (Executor.chain_args). The card showed him the text; the chain records THAT
# he approved it, to whom, and a digest he can check a text against. Chosen
# over the full text because a DM is private by definition, and the chain is
# read by more eyes than the card. (Honest limit: a very short message's
# digest is guessable by brute force; the chain's user-only ACL is what
# protects it, as it protects everything else on it.)
#
# THE PAGE PATH. A conversation is opened from the RECIPIENT'S PROFILE: the
# profile URL is by handle (exact, checked on landing as `_engage_user` does),
# and its "Message" button opens the 1:1 conversation with THAT account — no
# people-search typeahead, no picking a name from a list. That press is the
# ONE press in the read path (`_open_conversation`), it sends nothing, and it
# is how the conversation URL the send needs is learned. The send navigates
# to THAT URL, checks the page names the handle, fills the composer, presses
# Send. Every lookup fails closed (`_require_one`).
#
# ⚠ SELECTORS UNVERIFIED LIVE. He is not signed in on this machine, so the
# DM markup — the profile's Message button (`sendDMFromProfile`), the
# conversation URL shape (/messages/<id>), `messageEntry`, the composer
# (`dmComposerTextInput`) and its Send (`dmComposerSendButton`) — is taken
# from X's current test ids and accessible names, NOT from a live page.
# Every one of them fails closed: a mismatch is "I cannot find the Send
# button", never a wrong send. Verify on his signed-in session before
# trusting the live path.
#
# ⚠ FOOTPRINT AND PRIVACY, PLAINLY: mass or automated DMing is the most
# spam-flagged behaviour on X and a suspension risk — this sends one message
# to one person he named, paced like every other X action, and refuses any
# fan-out by shape. And DMs are private: from this round she can read his
# inbox. It is fenced, never learned and never logged as text, and the key
# (`x.dm_read`) can be withdrawn in permissions.yaml without losing anything
# else.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# LIVE, 2026-09-22: x.com/messages now 302s to X's NEW chat page, x.com/i/chat,
# and a conversation is x.com/i/chat/<a>-<b>. The inbox lists rows as
# `dm-conversation-item-<a>:<b>` (the @handle is in the row's aria-description,
# never its visible text); a thread renders `dm-message-scroller` with
# `message-<uuid>` rows, each carrying `message-text-<uuid>`; the composer is a
# real `<textarea data-testid="dm-composer-textarea">` and the Send control
# renders only AFTER text is typed (so its exact test id cannot be read on an
# empty composer — the send path finds it by its accessible name "Send" after
# the fill, and fails closed if it is not there). Verified read-only on his
# signed-in session; no press, nothing typed. NO E2EE / PIN gate stood on any
# thread — but `_refuse_chat_gate` fails closed with a spoken unlock
# instruction if X ever shows one, and never types a passcode.
# ─────────────────────────────────────────────────────────────────────────────

X_CHAT = "https://x.com/i/chat"
#: x.com/messages 302s to /i/chat; kept as the entry the read navigates to.
X_MESSAGES = "https://x.com/messages"
#: X allows 10,000 characters in a DM; a dictated message is not that, and the
#: card must stay readable. Over this is a refusal before the card.
MAX_DM_CHARS = 1000
#: How long a conversation read counts as READ for the no-reply-blind rule:
#: the approval window. Past it he reads again, so he hears whatever arrived
#: since — a reply to a message he never heard is exactly the trick this
#: rule exists to refuse.
DM_READ_FRESH_S = 30 * 60

#: A conversation route on the new chat page. The id is opaque and never guessed.
_DM_URL = re.compile(r"^https://x\.com/i/chat/[A-Za-z0-9:_-]{1,80}$")
_HANDLE_IN_TEXT = re.compile(r"@([A-Za-z0-9_]{1,15})\b")

#: The new-page markup the read and send paths touch.
_CONV_ITEM = '[data-testid^="dm-conversation-item"]'
_MSG_TEXT = '[data-testid^="message-text-"]'
_COMPOSER_BOX = '[data-testid="dm-composer-textarea"]'
_COMPOSER_CONTAINER = '[data-testid="dm-composer-container"]'
_READONLY_NOTICE = '[data-testid="dm-read-only-notice"]'
#: The header's username is an <a href="/<handle>"> — the reliable @handle for
#: the send's "the page names the handle" check (the @handle is NOT in the
#: thread body on the new page).
_CONV_USERNAME_LINK = 'a:has([data-testid="dm-conversation-username"])'
#: The Send control by accessible name, once it renders (after a fill).
_DM_SEND_NAME = re.compile(r"^send$", re.I)
#: X's own words for an encryption / passcode unlock gate. None was seen live;
#: if one appears the DM paths fail closed and tell him to unlock it by hand.
#: X's own words for a genuinely EMPTY inbox — a stop for the wait so an
#: empty inbox returns fast instead of spinning to the ceiling.
_INBOX_EMPTY_TEXT = ("no conversations", "start a conversation", "welcome to your inbox")
_CHAT_GATE_TEXT = ("enter your passcode", "enter your pin", "enter passcode",
                   "unlock your chats", "unlock this chat", "unlock your messages",
                   "chat is locked", "enter the code to unlock", "verify to unlock")


#: The conversations he has READ, by handle (lower-cased): {handle, url, n, at}.
#: A send is refused unless its recipient is here and fresh, and the handler
#: refuses again unless the card's URL is the one recorded here.
_LAST_DM: dict[str, dict[str, Any]] = {}


def dm_digest(text: str) -> str:
    """What the chain holds instead of a private message: 16 hex of its sha256."""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def _refuse_chat_gate(page: Any) -> None:
    """Fail closed if X asks to unlock chats with a passcode/PIN. Never types it."""
    low = _body(page).lower()
    hit = next((g for g in _CHAT_GATE_TEXT if g in low), None)
    if hit:
        raise ToolError(
            f"X wants a passcode to unlock your messages first (it says {hit!r})",
            "Open X yourself and unlock your chats — I never type your passcode. "
            "Then ask me again. Nothing was sent.")


def _send_dm(button: Any, handle: str, text: str) -> None:
    """
    THE ONE SEND BOUNDARY. Every private message goes out through this call,
    on the single Send button `_require_one` returned. `handle` and `text`
    are for the recorder and the error — the target was chosen, and checked,
    before this is reached.
    """
    try:
        button.click()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(
            f"the Send button for @{handle} did not take the press ({type(exc).__name__})",
            "X may have changed its page. I cannot tell whether it went; look at the conversation.") from None


def _dm_texts(page: Any, limit: int) -> list[str]:
    """The message texts on a conversation page, oldest first — the last `limit`."""
    loc = page.locator(_MSG_TEXT)
    try:
        n = loc.count()
    except Exception:  # noqa: BLE001
        n = 0
    out: list[str] = []
    for i in range(max(0, n - limit), n):
        try:
            raw = loc.nth(i).inner_text()
        except Exception:  # noqa: BLE001
            continue
        text = " ".join((raw or "").split())
        if text:
            out.append(text[:400])
    return out


def _inbox(page: Any, limit: int) -> list[dict[str, str]]:
    """
    The inbox's conversation rows: the other party's @handle (from the row's
    aria-description, since it is not in the visible text on the new page), the
    preview line, and the conversation route the row links to.
    """
    loc = page.locator(_CONV_ITEM)
    try:
        n = loc.count()
    except Exception:  # noqa: BLE001
        n = 0
    out: list[dict[str, str]] = []
    for i in range(min(n, limit)):
        row = loc.nth(i)
        try:
            desc = row.get_attribute("aria-description") or ""
        except Exception:  # noqa: BLE001
            desc = ""
        m = _HANDLE_IN_TEXT.search(desc)
        handle = f"@{m.group(1)}" if m else "?"
        try:
            href = row.locator("a").first.get_attribute("href") or ""
        except Exception:  # noqa: BLE001
            href = ""
        url = f"https://x.com{href}" if href.startswith("/i/chat/") else ""
        preview = ""
        if m:
            preview = desc[m.end():].lstrip(", ").strip()
        if not preview:
            try:
                raw = row.inner_text() or ""
            except Exception:  # noqa: BLE001
                raw = ""
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            preview = lines[-1] if lines else ""
        out.append({"handle": handle, "preview": preview[:200], "url": url})
    return out


def _conversation_is(page: Any, handle: str) -> bool:
    """True when the open conversation's header names @handle (by its username link)."""
    want = handle.lstrip("@").lower()
    try:
        link = page.locator(_CONV_USERNAME_LINK)
        if link.count():
            href = (link.first.get_attribute("href") or "").rstrip("/")
            if href.lstrip("/").split("/")[-1].lower() == want:
                return True
    except Exception:  # noqa: BLE001
        pass
    return f"@{want}".lower() in _body(page).lower()


def read_dm(handle: str = "", limit: int = 20, provenance: str = "schedule") -> dict[str, Any]:
    """
    GREEN, PRIVATE. His inbox (no handle), or the conversation with ONE
    account he already has, for him to hear.

    RETARGETED to x.com/i/chat (2026-09-22). His inbox, no press: a conversation
    with @handle is found by MATCHING the inbox row for that handle and
    navigating to its route — so a handle he has never messaged fails closed
    ("no conversation with @x") instead of opening a fresh empty chat, and the
    read path presses NOTHING. Fenced as external like every read here — and
    that is the only place the words go: the spec is `private`, so the executor
    never hands the result to the claim or style stores, and the chain gets
    READ-DM with the handle and a COUNT (`provenance` is filled by the executor
    by signature, never from args). A conversation read is remembered in
    `_LAST_DM` so a reply can be sent into it — and refused into any conversation
    he has not read.
    """
    want = max(1, min(int(limit or 20), 50))
    clean = _one_handle(handle) if str(handle or "").strip() else ""
    _require_signed_in()
    page = _page()
    _goto(page, X_MESSAGES)
    _await_present(page, lambda: page.locator(_CONV_ITEM),
                   lambda: page.locator('[data-testid="dm-empty-inbox-state"]'),
                   stop_text=_INBOX_EMPTY_TEXT)
    _refuse_chat_gate(page)
    _check_reachable(page)
    rows = _inbox(page, 200 if clean else want)
    if not clean:
        cells = rows[:want]
        handles = [c["handle"] for c in cells]
        _report("READ-DM", "x.read_dm",
                f"inbox: {len(cells)} conversation(s) with {', '.join(handles[:10]) or 'nobody'}; "
                f"previews withheld from the chain, never learned", "green", actor=provenance)
        spoken = (f"{len(cells)} conversation{'s' if len(cells) != 1 else ''} in your inbox, Emperor"
                  + (f": {', '.join(handles[:5])}." if handles else ".")
                  + (f" The newest, from {cells[0]['handle']}: {cells[0]['preview'][:160]}"
                     if cells and cells[0]["preview"] else ""))
        return {"n": len(cells), "handle": "", "who": "your inbox", "senders": handles,
                "messages": [], "last": "", "spoken": spoken,
                # UNTRUSTED like every read — and PRIVATE: see the section note.
                "external_source": "x.com DM inbox",
                "external_text": "\n\n".join(f"{c['handle']}: {c['preview']}" for c in cells)}
    match = next((c for c in rows
                  if c["handle"].lstrip("@").lower() == clean.lower() and c["url"]), None)
    if match is None:
        raise ToolError(
            f"I do not see a conversation with @{clean} in your inbox",
            f"Open one with @{clean} yourself and I will read it. I do not start new chats. "
            f"Nothing was sent.")
    _goto(page, match["url"])
    # WAIT FOR THE MESSAGES THEMSELVES, not the composer: measured live, the
    # composer hydrates ~1.5 s but the message rows can take ~4.8 s, so a
    # composer-OR-messages wait read a full thread as empty. A genuinely empty
    # or broadcast (read-only) thread simply returns 0 after the ceiling.
    _await_present(page, lambda: page.locator(_MSG_TEXT))
    _refuse_chat_gate(page)
    _check_reachable(page)
    url = (page.url or "").split("?")[0].split("#")[0]
    if not _DM_URL.match(url) or url != match["url"]:
        raise ToolError(f"X did not open the conversation with @{clean} (it went to {url[:60]!r})",
                        "X may have changed its page. Nothing was sent.")
    if not _conversation_is(page, clean):
        raise ToolError(f"the conversation page does not name @{clean}",
                        "I will not read a conversation I cannot place. Nothing was sent.")
    msgs = _dm_texts(page, want)
    _LAST_DM[clean.lower()] = {"handle": clean, "url": url, "n": len(msgs), "at": time.time()}
    _report("READ-DM", "x.read_dm",
            f"{len(msgs)} message(s) in the conversation with @{clean}; "
            f"content withheld from the chain, never learned", "green", actor=provenance)
    last = msgs[-1] if msgs else ""
    spoken = (f"{len(msgs)} message{'s' if len(msgs) != 1 else ''} with @{clean}, Emperor."
              + (f" The latest: {last[:200]}" if last else " Nothing in it yet."))
    return {"n": len(msgs), "handle": f"@{clean}", "who": f"@{clean}", "senders": [f"@{clean}"],
            "messages": [{"text": t} for t in msgs], "last": last[:200], "spoken": spoken,
            "external_source": f"x.com DM with @{clean}",
            "external_text": "\n\n".join(msgs)}


def _send_dm_args(args: dict[str, Any]) -> dict[str, Any]:
    """
    ToolSpec.resolve for x.send_dm — in the red gate, BEFORE the card, once.

    ONE recipient by shape, non-empty words within the cap, and a
    conversation he READ in the last DM_READ_FRESH_S seconds — or a refusal
    before he is ever asked. What comes back is what the card shows and
    freezes: the handle, the exact words, the conversation URL the read
    captured, and how many messages he heard.
    """
    raw = " ".join(str(args.get("handle") or "").split())
    words = raw.split()
    if len(words) > 3:
        # THE RECIPIENT PHRASE RAN INTO THE MESSAGE. "dm everyone my pin is
        # 1234" arrives with no separator, so the router hands the whole
        # tail over as the recipient, and the refusal below — spoken, and
        # REFUSED on the chain — quotes it. Three words name any bulk shape
        # ("all my followers", "@ada and @bob", "everyone who follows");
        # what he dictated after them stays off the chain.
        raw = " ".join(words[:3]) + " \u2026"
    try:
        clean = _one_handle(raw)
    except ToolError as err:
        raise ToolError(err.reason, "I send a private message to ONE account at a time, by its @name. "
                                    "Name one.") from None
    text = " ".join(str(args.get("text") or "").split()).strip()
    if not text:
        raise ToolError(f"there was nothing to send to @{clean}", "Tell me what to say to them.")
    if len(text) > MAX_DM_CHARS:
        raise ToolError(f"that is {len(text)} characters; I send private messages up to {MAX_DM_CHARS}",
                        "Shorten it and I will hold it again.")
    seen = _LAST_DM.get(clean.lower())
    if seen is None:
        raise ToolError(f"I have not read your conversation with @{clean}, so I will not send into it blind",
                        f"Say read my DMs with @{clean}, hear what is there, then ask me again.")
    age = time.time() - float(seen.get("at") or 0.0)
    if age > DM_READ_FRESH_S:
        raise ToolError(f"it has been {int(age // 60)} minutes since I read your conversation with @{clean}, "
                        f"and something may have arrived since",
                        f"Say read my DMs with @{clean} again, then ask me again.")
    return {"handle": clean, "text": text, "conversation_url": str(seen.get("url") or ""),
            "read_n": int(seen.get("n") or 0)}


def _describe_send_dm(args: dict[str, Any]) -> str:
    """The card's spoken line, from the RESOLVED args: the recipient and the exact words."""
    return (f"send a private message to @{args.get('handle') or '?'} saying: "
            f"{str(args.get('text') or '')[:120]}")


def send_dm(handle: str, text: str, conversation_url: str = "", read_n: int = 0,
            _approved_by_surface: bool = False) -> dict[str, Any]:
    """
    RED. Send ONE private message to ONE account, into the conversation he read.

    RETARGETED to x.com/i/chat (2026-09-22). Only reachable through the approval
    card (`_approved_by_surface` is put on the args by the executor after his
    approval; the key is stripped from any caller's args before that). Every
    fact the card froze is re-checked here on the handler's own account: one
    handle by shape, the words, and that `conversation_url` is exactly the
    /i/chat route `read_dm` recorded for that handle — a caller that skipped
    `_send_dm_args` cannot aim this at a conversation she never opened. The
    page's header must name the handle and the conversation must not be
    read-only before the composer is touched; the one press is `_send_dm`. The
    composer is emptied on EVERY exit — X keeps drafts, and a left-behind draft
    is an accidental-send hazard on the next visit.
    """
    clean = _one_handle(handle)
    body = " ".join(str(text or "").split()).strip()
    if not body:
        raise ToolError(f"there was nothing to send to @{clean}", "Tell me what to say to them.")
    if len(body) > MAX_DM_CHARS:
        raise ToolError(f"that is {len(body)} characters; I send private messages up to {MAX_DM_CHARS}",
                        "Shorten it and I will hold it again.")
    url = str(conversation_url or "").strip()
    seen = _LAST_DM.get(clean.lower())
    if not _DM_URL.match(url) or seen is None or str(seen.get("url") or "") != url:
        raise ToolError(f"the conversation page for @{clean} is not the one I read",
                        f"Say read my DMs with @{clean}, then ask me again. Nothing was sent.")
    if not _approved_by_surface:
        raise ToolError("that reached the message handler without an approval",
                        "Nothing was sent. Approve it on the card.")
    _require_signed_in()
    page = _page()
    _goto(page, url)
    _await_present(page, lambda: page.locator(_COMPOSER_BOX),
                   lambda: page.locator(_READONLY_NOTICE),
                   lambda: page.locator(_MSG_TEXT))
    _refuse_chat_gate(page)
    _check_reachable(page)
    landed = (page.url or "").split("?")[0].split("#")[0]
    if not _DM_URL.match(landed) or landed != url:
        raise ToolError(f"X did not open the conversation I read for @{clean}",
                        "Nothing was sent. Read the conversation again.")
    try:
        read_only = page.locator(_READONLY_NOTICE).count() > 0
    except Exception:  # noqa: BLE001
        read_only = False
    if read_only:
        raise ToolError(f"X shows the conversation with @{clean} as read-only",
                        "That account cannot be messaged. Nothing was sent.")
    if not _conversation_is(page, clean):
        raise ToolError(f"the conversation page does not name @{clean}",
                        "Nothing was sent. Read the conversation again.")
    box = _require_one(page.locator(_COMPOSER_BOX), f"the message box for @{clean}")
    filled = False
    try:
        box.fill(body)
        filled = True
        page.wait_for_timeout(400)
        # The Send control renders only once there is text; find it by name
        # inside the composer, then fall through to a page-wide "Send".
        comp = page.locator(_COMPOSER_CONTAINER)
        btn = comp.get_by_role("button", name=_DM_SEND_NAME)
        try:
            if btn.count() == 0:
                btn = comp.locator('[data-testid="dm-composer-send-button"]')
            if btn.count() == 0:
                btn = page.get_by_role("button", name=_DM_SEND_NAME)
        except Exception:  # noqa: BLE001
            pass
        _send_dm(_require_one(btn, f"the Send button for @{clean}"), clean, body)
        page.wait_for_timeout(1200)
    finally:
        # COMPOSER HYGIENE: leave nothing sitting in the box on ANY exit —
        # a successful send (X clears it anyway), a refusal, or the proof's
        # intercepted press. A leftover draft is an accidental send next time.
        if filled:
            try:
                page.locator(_COMPOSER_BOX).first.fill("")
            except Exception:  # noqa: BLE001
                pass
    return {"handle": f"@{clean}", "chars": len(body), "digest": dm_digest(body)}


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA — a link, a saved image, a saved video (the media round, 2026-09-22)
# ─────────────────────────────────────────────────────────────────────────────
#
# SHARE IS A READ, AND NOT EVEN A PAGE LOAD. A post's permalink is already in
# what every read extracts (`_one_post`'s `url`, built from the status href BY
# SHAPE — up to 15 handle characters and 5-25 digits, nothing an attacker
# typed), so "share that post" resolves the post against the last read's
# snapshot and hands the link back. The "Share post" button beside Bookmark
# on X's action bar opens a menu whose first item copies this same link; it
# is never pressed.
#
# A SAVE IS A DOWNLOAD THAT WRITES ONE NEW FILE. The post is untrusted (its
# words and its images' alt text are fenced as external content, as a read's
# are), the bytes are a binary fetched through the browser's own session from
# X's media hosts and nowhere else, and the file lands in a FIXED folder of
# his — Pictures\Tessa or Videos\Tessa, neither under a protected root —
# under a name that is checked against permissions.yaml `protected_paths`
# (through files.py's loader, the Guard's own list) and opened create-only
# (`xb`), so it cannot overwrite anything, anywhere, ever. The folder is not
# an argument: a spoken sentence cannot aim a write at a path.
#
# WHERE THE URLS COME FROM — seen live, signed in, 2026-09-22. A photo renders
# as `tweetPhoto > img` with `pbs.twimg.com/media/<key>?format=jpg&name=small`;
# the full-size file is the same key with `name=orig` (HEAD 200, image/jpeg,
# the same bytes as `large`). A video renders as `videoPlayer > video[src=
# blob:]` — the blob is X's own player and names no file — and the real
# addresses are in the page's OWN data answer (the TweetDetail /
# TweetResultByRestId GraphQL responses the page fetches to render the post):
# `video_info.variants` lists the HLS playlist (application/x-mpegURL) AND
# progressive MP4 renditions (`video/mp4`, a bitrate each; HEAD 200 with the
# size in Content-Length — 0.55 / 1.07 / 1.65 MB for a 320 / 480 / 576-wide
# clip). So a video is ONE file: the best MP4 that fits the byte cap. No
# ffmpeg, no segment stitching. `_MediaCapture` records those answers while
# the permalink loads — the page requested them, not this code — and detaches
# before the call returns. An HLS-only video (none seen; X ships MP4
# renditions for `video` and `animated_gif` alike) is REFUSED with the reason
# unless ffmpeg is on PATH, and only then is ffmpeg run, as a fixed argv on a
# video.twimg.com URL (`_save_hls`). THAT BRANCH IS UNTESTED HERE: ffmpeg is
# not installed on this machine, and nothing in this file installs it.
#
# THE BYTE CAPS ARE THE METERED-DATA RULE (CLAUDE.md; permissions.yaml
# `hydration.warn_bytes` is 50 MB): a video over VIDEO_MAX_BYTES in its
# smallest rendition is refused with the size named and nothing downloaded;
# the largest rendition that fits is the one saved; the spoken line says
# which resolution and how many megabytes.

SAVE_IMAGE_DIR = Path.home() / "Pictures" / "Tessa"
SAVE_VIDEO_DIR = Path.home() / "Videos" / "Tessa"
IMAGE_MAX_BYTES = 25 * 1024 * 1024
VIDEO_MAX_BYTES = 50 * 1024 * 1024
MAX_IMAGES_PER_POST = 4
FETCH_TIMEOUT_MS = 60_000   # legacy; the streaming download below no longer uses it
#: HEAD is a metadata round-trip; keep it short.
HEAD_TIMEOUT_MS = 20_000
#: THE METERED-SAVE HOLD (carry-over 1a). At or above this a save HOLDS for his
#: yes with the size named; below it a save runs at once and speaks the size.
#: An unknown size (no HEAD length) is treated as above the threshold. The
#: 25 MB / 50 MB hard caps still stand on top of this.
SAVE_HOLD_BYTES = 10 * 1024 * 1024
#: THE DOWNLOAD (carry-over 1b). A 60 s TOTAL timeout and a 50 MB cap cannot
#: both hold on this connection, so the file is streamed as Range chunks: each
#: chunk carries a STALL timeout (no bytes for DOWNLOAD_STALL_S = a stall) and
#: the whole download is bounded by a total CEILING. Chunk size chosen against
#: the throughput the proof measured (see dm-round-NUMBERS). Bytes land in a
#: `.part` beside the target and are renamed onto the checked name only on a
#: clean, complete, magic-checked download; the `.part` is deleted on any
#: failure, so a listing after a failed candidate shows no stray file.
DOWNLOAD_CHUNK_BYTES = 4 * 1024 * 1024
DOWNLOAD_STALL_S = 30.0
DOWNLOAD_CEILING_S = 300.0
#: The only hosts a saved byte may come from. A media address is read out of
#: X's page data, which is untrusted; one pointing anywhere else is refused.
_MEDIA_HOSTS = ("https://pbs.twimg.com/", "https://video.twimg.com/")
_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
_VIDEO_TYPES = {"video/mp4": "mp4"}
_MAGIC = {"jpg": (b"\xff\xd8\xff",), "png": (b"\x89PNG",), "gif": (b"GIF87a", b"GIF89a"), "webp": (b"RIFF",)}
_PBS_MEDIA = re.compile(r"^https://pbs\.twimg\.com/media/([A-Za-z0-9_-]{4,64})"
                        r"(?:\.([A-Za-z0-9]{2,5})|\?format=([A-Za-z0-9]{2,5})(?:&name=[\w:]+)?)?$", re.I)
_RESOLUTION = re.compile(r"/(\d{2,4}x\d{2,4})/")
_GRAPHQL_OPS = ("TweetDetail", "TweetResultByRestId")
FFMPEG_DOWNLOAD_NOTE = ("ffmpeg is not installed; winget's Gyan.FFmpeg.Essentials is about a 30 MB download "
                        "and 90 MB on disk")


def _size_words(b: int) -> str:
    if b < 1_000_000:
        return f"{b / 1e3:.0f} kilobytes"
    return f"{b / 1e6:.1f} megabytes"


class _MediaCapture:
    """
    Records the data answers the page fetches for a post while it loads, reads
    their bodies once (on the browser thread), and detaches. READ-ONLY: it
    requests nothing the page did not request itself, and it sees only the
    two post queries — not the timeline, not the inbox.
    """

    def __init__(self, page: Any) -> None:
        self.page = page
        self.responses: list[Any] = []

    def _on_response(self, resp: Any) -> None:
        try:
            url = str(resp.url)
        except Exception:  # noqa: BLE001
            return
        if "/graphql/" in url and any(op in url for op in _GRAPHQL_OPS):
            self.responses.append(resp)

    def __enter__(self) -> "_MediaCapture":
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self.page.remove_listener("response", self._on_response)
        except Exception:  # noqa: BLE001
            pass

    def bodies(self) -> list[Any]:
        """The parsed JSON of each recorded answer; an unreadable one is skipped."""
        def _read() -> list[str]:
            out: list[str] = []
            for r in list(self.responses):
                try:
                    out.append(str(r.text()))
                except Exception:  # noqa: BLE001
                    continue
            return out
        parsed: list[Any] = []
        for text in SESSION.call(_read):
            try:
                parsed.append(json.loads(text))
            except Exception:  # noqa: BLE001
                continue
        return parsed


def _media_items(bodies: list[Any], post_id: str) -> list[dict[str, Any]]:
    """
    THE post's media entries out of the page's data answers, in display order.
    Matched on the entry's own `expanded_url` naming `/status/<id>/`, so a
    reply's or an ancestor's picture on the same page is never taken for his.
    """
    mark = f"/status/{post_id}/"
    found: dict[str, dict[str, Any]] = {}

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            kind = obj.get("type")
            url = obj.get("media_url_https")
            if (kind in ("photo", "video", "animated_gif") and isinstance(url, str)
                    and mark in str(obj.get("expanded_url") or "")):
                key = str(obj.get("media_key") or url)
                prev = found.get(key)
                # `entities.media` omits video_info; `extended_entities.media` has it.
                if prev is None or ("video_info" in obj and "video_info" not in prev):
                    found[key] = obj
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    for body in bodies:
        walk(body)
    return list(found.values())


def _orig_url(url: Any) -> str:
    """`pbs.twimg.com/media/<key>.jpg` or `…?format=jpg&name=small` -> the full-size `name=orig`."""
    m = _PBS_MEDIA.match(str(url or ""))
    if not m:
        raise ToolError(f"that image is not on X's media host ({str(url or '')[:40]!r})",
                        "I only save from pbs.twimg.com. Nothing was written.")
    fmt = (m.group(2) or m.group(3) or "jpg").lower()
    return f"https://pbs.twimg.com/media/{m.group(1)}?format={fmt}&name=orig"


def _head_media(url: str) -> tuple[int, str]:
    """(Content-Length or -1, content type) from a HEAD through the browser session."""
    try:
        resp = SESSION.context().request.head(url, timeout=HEAD_TIMEOUT_MS)
        headers = {str(k).lower(): str(v) for k, v in dict(resp.headers or {}).items()}
        ctype = headers.get("content-type", "").split(";")[0].strip().lower()
        size = int(headers.get("content-length") or -1)
        return (size if int(resp.status) == 200 else -1), ctype
    except Exception:  # noqa: BLE001
        return -1, ""


def _rm_part(part: Path) -> None:
    """Delete a partial download — her own file, seconds old — swallowing any error."""
    try:
        part.unlink(missing_ok=True)
    except OSError:
        pass


def _download_media(url: str, expected: int, cap: int, what: str, ext: str, final: Path) -> int:
    """
    Stream ONE media file from X's own hosts to `<final>.part`, then rename it
    onto `final` — create-only, no clobber (carry-over 1b). `expected` is the
    HEAD Content-Length (0 or less = unknown). A known size is fetched as Range
    chunks, each under DOWNLOAD_STALL_S (no bytes for a chunk = a stall) and the
    whole under DOWNLOAD_CEILING_S; an unknown size is one GET under the ceiling.
    The first bytes are magic-checked against `ext`. The `.part` is deleted on
    ANY failure. Returns the byte count written.
    """
    if not str(url).startswith(_MEDIA_HOSTS):
        raise ToolError(f"the {what} is not hosted on X's media servers ({str(url)[:40]!r})",
                        "I only save from pbs.twimg.com and video.twimg.com. Nothing was written.")
    if expected > cap:
        raise ToolError(f"that {what} is {_size_words(expected)}, over my {_size_words(cap)} limit",
                        "That is the metered-data rule. Nothing was downloaded.")
    part = final.with_name(final.name + ".part")
    _refuse_protected_write(part)
    if part.exists():
        raise ToolError(f"{part.name} is already in {part.parent}",
                        "A download is in flight or was left behind. Ask me again in a moment.")
    final.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    got = 0
    try:
        with open(part, "xb") as fh:
            ranges = expected > 0
            while True:
                if time.monotonic() - start > DOWNLOAD_CEILING_S:
                    raise ToolError(
                        f"the {what} did not finish within {int(DOWNLOAD_CEILING_S)} seconds on this connection",
                        "That is the download ceiling, not a fault here. Nothing was written.")
                if ranges:
                    end = min(got + DOWNLOAD_CHUNK_BYTES, expected) - 1
                    resp = SESSION.context().request.get(
                        url, headers={"Range": f"bytes={got}-{end}"},
                        timeout=int(DOWNLOAD_STALL_S * 1000))
                else:
                    resp = SESSION.context().request.get(url, timeout=int(DOWNLOAD_CEILING_S * 1000))
                status = int(resp.status)
                if status not in (200, 206):
                    raise ToolError(f"X answered {status} for the {what}",
                                    "It may have been removed. Read the post again. Nothing was written.")
                data = bytes(resp.body())
                if not data:
                    raise ToolError(
                        f"the {what} stalled — no bytes for {int(DOWNLOAD_STALL_S)} seconds",
                        "The connection dropped mid-download. Nothing was written.")
                if got == 0 and not _looks_like(data, ext):
                    raise ToolError(f"the {what} does not start like a {ext} file",
                                    "X sent something else under that name. Nothing was written.")
                got += len(data)
                if got > cap:
                    raise ToolError(f"that {what} is over my {_size_words(cap)} limit",
                                    "That is the metered-data rule. Nothing was written.")
                fh.write(data)
                if not ranges or status == 200 or got >= expected:
                    break
    except ToolError:
        _rm_part(part)
        raise
    except Exception as exc:  # noqa: BLE001
        _rm_part(part)
        raise ToolError(f"X did not hand over the {what} ({type(exc).__name__})",
                        "Check the connection and ask me again. Nothing was written.") from None
    if expected > 0 and got != expected:
        _rm_part(part)
        raise ToolError(f"the {what} arrived short ({got} of {expected} bytes)",
                        "The download was cut off. Nothing was written.")
    try:
        os.rename(part, final)   # fails if `final` appeared meanwhile: no overwrite
    except OSError as exc:
        _rm_part(part)
        raise ToolError(f"could not place the {what} as {final.name} ({type(exc).__name__})",
                        "Nothing was overwritten.") from None
    return got


def _looks_like(data: bytes, ext: str) -> bool:
    if ext == "mp4":
        return len(data) > 12 and data[4:8] == b"ftyp"
    if ext == "webp":
        return data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return any(data.startswith(m) for m in _MAGIC.get(ext, ()))


def _refuse_protected_write(p: Path) -> None:
    """
    A saved file may not be a drive root, a protected root, or ANYTHING UNDER
    one (files.py's `_refuse_root` covers the first two for a delete; a write
    under the Windows folder or the OneDrive tree is refused here as well,
    because a new file there is not a thing he asked for by naming a post).
    """
    from .files import _protected_roots, _refuse_root   # lazy: files.py loads the Guard's list once

    _refuse_root(p)
    try:
        r = p.resolve()
    except (OSError, RuntimeError, ValueError):
        r = Path(os.path.normpath(str(p)))
    cand = PurePath(os.path.normcase(str(r)))
    for root in _protected_roots():
        if cand == root or root.parts == cand.parts[: len(root.parts)]:
            raise ToolError(f"{p} is under the protected path {root}",
                            "I do not write there. Nothing was saved.")


def _new_file(folder: Path, stem: str, ext: str) -> Path:
    """A path that does not exist yet under `folder`, both checked against the protected roots."""
    _refuse_protected_write(folder)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-.")[:80] or "x-media"
    for n in range(1, 1000):
        cand = folder / (f"{safe}.{ext}" if n == 1 else f"{safe}-{n}.{ext}")
        if not cand.exists():
            _refuse_protected_write(cand)
            return cand
    raise ToolError(f"there are already 999 files called {safe} in {folder}",
                    "Tidy that folder and ask me again.")


def _write_new(path: Path, data: bytes) -> None:
    """Create-only: a file that appeared between the name check and the write is never overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as fh:
            fh.write(data)
    except FileExistsError:
        raise ToolError(f"{path.name} appeared in {path.parent} while I was saving",
                        "Nothing was overwritten. Ask me again.") from None


def _open_post(target: str) -> tuple[Any, dict[str, str], list[dict[str, Any]]]:
    """The permalink, hydrated, with the page's own data answers captured: (article, fields, media)."""
    _require_signed_in()
    page = _page()
    with _MediaCapture(page) as cap:
        _goto(page, f"https://x.com/i/web/status/{target}")
        _await_posts(page)
        _check_reachable(page)
        art = _focal_article(page, target)
        fields = _one_post(art)
        bodies = cap.bodies()
    return art, fields, _media_items(bodies, target)


def _fenced_text(who: str, fields: dict[str, str], items: list[dict[str, Any]]) -> str:
    """The post's words and its media alt text — untrusted, loaded into the fence like a read."""
    parts = [f"{who}: {fields.get('text', '')}"]
    for m in items:
        alt = m.get("ext_alt_text")
        if alt:
            parts.append(f"[alt] {str(alt)[:400]}")
    return "\n".join(parts)


def share_link(post_id: str = "", index: int = 0, provenance: str = "schedule") -> dict[str, Any]:
    """
    GREEN, no page load: the permalink of ONE post. From the last read's
    snapshot when he named a post she read out (the url `_one_post` built by
    shape); for a bare id he read elsewhere, X's own id-only permalink. The
    Share button is never pressed.
    """
    target, who = _resolve_post(post_id, index)
    picked = next((p for p in _LAST_READ if p.get("id") == target), None)
    url = (picked or {}).get("url") or f"https://x.com/i/web/status/{target}"
    if not re.fullmatch(r"https://x\.com/(?:[A-Za-z0-9_]{1,15}|i/web)/status/\d{5,25}", url):
        url = f"https://x.com/i/web/status/{target}"
    # CARRY-OVER 1c: log the RESOLVED id here. The executor's own line is built
    # from the pre-resolution args ({index: 2} for an ordinal), so it would
    # record "-"; the registration's audit template is target-less so that line
    # is a clean "ran share a post's link", and THIS row carries the id.
    _report("ran", "x.share_link", f"share link to x post {target}", "green", actor=provenance)
    spoken = (f"Here is the link to {who}'s post, Emperor: {url}" if who
              else f"Here is the link to post {target}, Emperor: {url}")
    return {"post_id": target, "who": who or "?", "url": url, "spoken": spoken}


def save_image(post_id: str = "", index: int = 0, confirmed: bool = False) -> dict[str, Any]:
    """
    GREEN-ish, HOLDS ON SIZE (carry-over 1a): a download that writes NEW files
    under SAVE_IMAGE_DIR — held for his yes when the total is 10 MB or more. Opens
    the post's permalink (paced and hydrated like every read), takes the
    photo list from the page's own data answer — the article's `tweetPhoto`
    images are the fallback — fetches each at full size (`name=orig`) through
    the browser session, and writes it create-only. Up to MAX_IMAGES_PER_POST.
    """
    target, who = _resolve_post(post_id, index)
    _refuse_protected_write(SAVE_IMAGE_DIR)   # before any page load: a bad folder costs no navigation
    art, fields, items = _open_post(target)
    who = fields.get("handle") or fields.get("who") or who or "?"
    photos = [m for m in items if m.get("type") == "photo"]
    if not photos:
        try:
            _await_present(_page(), lambda: art.locator('[data-testid="tweetPhoto"] img'), timeout_s=5.0)
            for src, alt in art.locator('[data-testid="tweetPhoto"] img').evaluate_all(
                    "els => els.map(e => [e.getAttribute('src'), e.getAttribute('alt')])") or []:
                if _PBS_MEDIA.match(str(src or "")):
                    photos.append({"type": "photo", "media_url_https": str(src), "ext_alt_text": alt or ""})
        except Exception:  # noqa: BLE001
            pass
    if not photos:
        has_video = any(m.get("type") in ("video", "animated_gif") for m in items)
        raise ToolError(f"post {target} by {who} has no picture I can save" + (" — it has a video" if has_video else ""),
                        "Say save that video if you meant the video." if has_video
                        else "Name a post with a picture in it. Nothing was written.")
    stem_who = re.sub(r"[^A-Za-z0-9_]", "", str(who).lstrip("@"))[:15] or "x"
    # HEAD every photo first (carry-over 1a): plan the sizes, hold on the total
    # if it is 10 MB or more (or any size is unknown), refuse any single one
    # over the 25 MB cap before a byte is fetched.
    plan: list[tuple[str, int, str]] = []
    for m in photos[:MAX_IMAGES_PER_POST]:
        purl = _orig_url(m.get("media_url_https"))
        psize, pctype = _head_media(purl)
        if psize > IMAGE_MAX_BYTES:
            raise ToolError(f"that image is {_size_words(psize)}, over my {_size_words(IMAGE_MAX_BYTES)} limit",
                            "That is the metered-data rule. Nothing was downloaded.")
        pext = _IMAGE_TYPES.get(pctype) or "jpg"
        plan.append((purl, psize, pext))
    total_head = sum(s for _, s, _ in plan if s > 0)
    unknown = any(s <= 0 for _, s, _ in plan)
    if (unknown or total_head >= SAVE_HOLD_BYTES) and not confirmed:
        named = ("an unknown size" if unknown or total_head <= 0 else _size_words(total_head))
        raise ToolHold(
            f"those {len(plan)} image{'s' if len(plan) != 1 else ''} are {named}; "
            f"saving {who}'s to Pictures\\Tessa",
            resolved={"post_id": target})
    saved: list[dict[str, Any]] = []
    for i, (url, psize, pext) in enumerate(plan, 1):
        path = _new_file(SAVE_IMAGE_DIR, f"x-{stem_who}-{target}-{i}", pext)
        got = _download_media(url, psize, IMAGE_MAX_BYTES, "image", pext, path)
        saved.append({"path": str(path), "name": path.name, "bytes": got, "url": url})
    total = sum(s["bytes"] for s in saved)
    names = ", ".join(s["name"] for s in saved)
    spoken = (f"Saved {len(saved)} image{'s' if len(saved) != 1 else ''} from {who}'s post to Pictures\\Tessa, "
              f"Emperor: {names} ({_size_words(total)}).")
    return {"post_id": target, "who": who, "n": len(saved), "files": saved, "folder": str(SAVE_IMAGE_DIR),
            "bytes": total, "spoken": spoken,
            "external_text": _fenced_text(who, fields, photos), "external_source": "x.save_image"}


def save_video(post_id: str = "", index: int = 0, confirmed: bool = False) -> dict[str, Any]:
    """
    GREEN-ish, HOLDS ON SIZE (carry-over 1a): ONE new file under SAVE_VIDEO_DIR
    — held for his yes when the chosen rendition is 10 MB or more. The best MP4
    rendition the page's own data answer lists that fits VIDEO_MAX_BYTES
    (HEAD first, so nothing over the cap is downloaded). A post carries at
    most one video on X. An HLS-only video goes to `_save_hls`, which refuses
    without ffmpeg.
    """
    target, who = _resolve_post(post_id, index)
    _refuse_protected_write(SAVE_VIDEO_DIR)   # before any page load: a bad folder costs no navigation
    art, fields, items = _open_post(target)
    who = fields.get("handle") or fields.get("who") or who or "?"
    videos = [m for m in items if m.get("type") in ("video", "animated_gif") and isinstance(m.get("video_info"), dict)]
    if not videos:
        has_photo = any(m.get("type") == "photo" for m in items)
        try:
            player = int(art.locator('[data-testid="videoPlayer"]').count())
        except Exception:  # noqa: BLE001
            player = 0
        raise ToolError(f"post {target} by {who} has no video I can save"
                        + (" — it has a picture" if has_photo else "")
                        + (" (a player is on the page but X's data for it did not arrive)" if player else ""),
                        "Say save that image if you meant the picture." if has_photo
                        else "Name a post with a video in it. Nothing was written.")
    variants = list((videos[0].get("video_info") or {}).get("variants") or [])
    mp4s = sorted([v for v in variants if str(v.get("content_type") or "").lower().startswith("video/mp4")
                   and str(v.get("url") or "").startswith("https://video.twimg.com/")],
                  key=lambda v: int(v.get("bitrate") or 0), reverse=True)
    stem_who = re.sub(r"[^A-Za-z0-9_]", "", str(who).lstrip("@"))[:15] or "x"
    if not mp4s:
        playlists = [str(v.get("url") or "") for v in variants if "mpegurl" in str(v.get("content_type") or "").lower()]
        return _save_hls(target, who, stem_who, playlists, fields, videos)
    chosen = None
    sizes: list[int] = []
    for v in mp4s:
        size, ctype = _head_media(str(v["url"]))
        if size > 0:
            sizes.append(size)
        if ctype in _VIDEO_TYPES and 0 < size <= VIDEO_MAX_BYTES:
            chosen = (v, size)
            break
    if chosen is None:
        smallest = min(sizes) if sizes else -1
        if smallest > 0:
            raise ToolError(f"that video is {_size_words(smallest)} even at its smallest rendition, over my "
                            f"{_size_words(VIDEO_MAX_BYTES)} limit",
                            "That is the metered-data rule. Nothing was downloaded.")
        # X answered no size for any rendition: treat the size as unknown and
        # take the highest-bitrate mp4; the size-unknown HOLD below covers it.
        chosen = (mp4s[0], -1)
    v, size = chosen
    # THE METERED HOLD (carry-over 1a): 10 MB or more, or an unknown size, waits
    # for his yes with the size named; below it the save runs and speaks the size.
    if (size < 0 or size >= SAVE_HOLD_BYTES) and not confirmed:
        named = _size_words(size) if size > 0 else "an unknown size"
        raise ToolHold(f"that video is {named}; saving {who}'s video to Videos\\Tessa",
                       resolved={"post_id": target})
    ext = _VIDEO_TYPES.get("video/mp4", "mp4")
    res = _RESOLUTION.search(str(v["url"]))
    resolution = res.group(1) if res else "unknown size"
    path = _new_file(SAVE_VIDEO_DIR, f"x-{stem_who}-{target}", ext)
    got = _download_media(str(v["url"]), size, VIDEO_MAX_BYTES, "video", ext, path)
    spoken = (f"Saved {who}'s video ({resolution}, {_size_words(got)}) to Videos\\Tessa, "
              f"Emperor: {path.name}.")
    return {"post_id": target, "who": who, "n": 1, "files": [{"path": str(path), "name": path.name,
            "bytes": got, "url": str(v["url"]), "resolution": resolution}],
            "folder": str(SAVE_VIDEO_DIR), "bytes": got, "resolution": resolution,
            "renditions": [{"bitrate": x.get("bitrate"), "url": str(x.get("url"))[:120]} for x in mp4s],
            "ffmpeg": False, "spoken": spoken,
            "external_text": _fenced_text(who, fields, videos), "external_source": "x.save_video"}


def _ffmpeg_run(argv: list[str]) -> int:
    """THE ONE SUBPROCESS BOUNDARY of this file: reached only by `_save_hls`, with a fixed argv."""
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return int(subprocess.run(argv, capture_output=True, timeout=900, creationflags=flags, check=False).returncode)


def _save_hls(target: str, who: str, stem_who: str, playlists: list[str], fields: dict[str, str],
              videos: list[dict[str, Any]]) -> dict[str, Any]:
    """
    The HLS-only case — a video X offers as a playlist of segments and no
    MP4 rendition. NOT SEEN ON LIVE X (every video and gif carried MP4
    renditions); built so the path exists once ffmpeg does. Without ffmpeg
    it is a refusal that names the download; with it, ffmpeg is run as a
    fixed argv on the video.twimg.com playlist, `-n` (never overwrite), into
    a `.part` file renamed onto a checked new name. UNTESTED HERE: ffmpeg is
    not installed on this machine, and nothing here installs it.
    """
    playlist = next((p for p in playlists if p.startswith("https://video.twimg.com/")), "")
    if not playlist:
        raise ToolError(f"X lists no file I can fetch for the video on post {target}",
                        "Nothing was written.")
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ToolError(f"X only offers that video as a stream (an HLS playlist), and stitching a stream into "
                        f"one file needs ffmpeg — {FFMPEG_DOWNLOAD_NOTE}",
                        "I have not installed anything. Install ffmpeg and ask me again, or watch it on X.")
    final = _new_file(SAVE_VIDEO_DIR, f"x-{stem_who}-{target}", "mp4")
    part = final.with_name(final.stem + ".part.mp4")
    _refuse_protected_write(part)
    if part.exists():
        raise ToolError(f"{part.name} is already in {part.parent}", "Move it and ask me again.")
    final.parent.mkdir(parents=True, exist_ok=True)
    argv = [exe, "-nostdin", "-loglevel", "error", "-n", "-i", playlist, "-c", "copy", str(part)]
    try:
        code = _ffmpeg_run(argv)
    except Exception as exc:  # noqa: BLE001
        code = -1
        reason = type(exc).__name__
    else:
        reason = f"exit {code}"
    ok = code == 0 and part.exists() and part.stat().st_size > 1024 and _looks_like(part.read_bytes()[:16], "mp4")
    if not ok:
        try:
            part.unlink(missing_ok=True)   # her own partial output, seconds old — never one of his files
        except OSError:
            pass
        raise ToolError(f"ffmpeg could not stitch that stream ({reason})",
                        "Nothing playable was written; the partial file is gone.")
    try:
        os.rename(part, final)             # fails if `final` appeared meanwhile: no overwrite
    except OSError as exc:
        raise ToolError(f"could not place the file as {final.name} ({type(exc).__name__})",
                        f"The stitched video is still at {part}.") from None
    size = final.stat().st_size
    spoken = f"Saved {who}'s video ({_size_words(size)}, stitched from the stream) to Videos\\Tessa, Emperor: {final.name}."
    return {"post_id": target, "who": who, "n": 1, "files": [{"path": str(final), "name": final.name, "bytes": size,
            "url": playlist, "resolution": "stream"}], "folder": str(SAVE_VIDEO_DIR), "bytes": size,
            "resolution": "stream", "renditions": [], "ffmpeg": True, "spoken": spoken,
            "external_text": _fenced_text(who, fields, videos), "external_source": "x.save_video"}


def open_for_login() -> dict[str, Any]:
    """
    GREEN. Open X in her profile so he can sign in HIMSELF.

    This is the entire authentication story: she opens a window, he types his
    own credentials into Chrome, X sets its cookies in her profile directory,
    and she never sees any of it. There is no callback here, no polling for
    success, and no field she fills.

    THE WINDOW IS HIS FOR A WHILE. The browser's idle timer counts tool calls,
    and a hand login is none of those — it took the login page down under him
    at five minutes. `SESSION.hold()` keeps the reaper off for `LOGIN_GRACE_S`.
    `browser` in the result says how Chrome was found (channel or path).
    """
    page = _page()
    hold = getattr(SESSION, "hold", None)
    if hold is not None:
        hold(LOGIN_GRACE_S)
    _goto(page, "https://x.com/login")
    return {"url": page.url,
            "browser": getattr(SESSION, "launch_via", ""),
            "profile": str(__import__("core.tools.browser", fromlist=["DEFAULT_PROFILE"])
                           .DEFAULT_PROFILE)}
