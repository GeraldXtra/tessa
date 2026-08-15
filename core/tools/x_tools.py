"""
core/tools/x_tools.py — X (Twitter), driven through the already-authenticated
session in Zoey's own Chrome profile.

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

RATE LIMITS AND INTERSTITIALS ARE ANSWERS, NOT FAILURES. X rate-limits reads as
well as writes and will show a wall. She says so and stops.
"""

from __future__ import annotations

import re
from typing import Any

from .base import ToolError, ToolHold
from .browser import SESSION, NAV_TIMEOUT_MS

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
#: debug Zoey when the answer is that he has never signed in.
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


def _page() -> Any:
    return SESSION.page()


def _goto(page: Any, url: str) -> None:
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


def _posts(page: Any, limit: int) -> list[dict[str, str]]:
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
        art = articles.nth(i)
        try:
            text = (art.inner_text() or "").strip()
        except Exception:  # noqa: BLE001
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        who = lines[0] if lines else "?"
        body = " ".join(lines[1:])[:400]
        out.append({"who": who[:60], "text": body})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GREEN — reads
# ─────────────────────────────────────────────────────────────────────────────

def read_timeline(limit: int = 5) -> dict[str, Any]:
    _require_signed_in()
    page = _page()
    _goto(page, X_HOME)
    page.wait_for_timeout(2500)          # X hydrates client-side; give it a beat
    _check_reachable(page)
    posts = _posts(page, int(limit))
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


def read_notifications(limit: int = 5) -> dict[str, Any]:
    _require_signed_in()
    page = _page()
    _goto(page, X_NOTIFICATIONS)
    page.wait_for_timeout(2500)
    _check_reachable(page)
    posts = _posts(page, int(limit))
    joined = "\n\n".join(f"{p['who']}: {p['text']}" for p in posts)
    return {
        "n": len(posts), "posts": posts,
        "head": "; ".join(p["who"] for p in posts[:3]),
        "external_source": "x.com notifications",
        "external_text": joined,
    }


# ─────────────────────────────────────────────────────────────────────────────
# AMBER — reversible interactions
# ─────────────────────────────────────────────────────────────────────────────

def _action_on_post(page: Any, index: int, label: str, testid: str) -> dict[str, Any]:
    articles = page.get_by_role("article")
    total = articles.count()
    if total == 0:
        raise ToolError("I cannot find any posts on this page",
                        "X may have changed its markup. Nothing was clicked.")
    if index < 1 or index > total:
        raise ToolError(f"there is no post number {index} — I can see {total}",
                        "Say a number I read out to you.")
    art = articles.nth(index - 1)
    # ACCESSIBLE NAME FIRST, X's own test hook second.
    btn = art.get_by_role("button", name=re.compile(label, re.I))
    if btn.count() == 0:
        btn = art.locator(f'[data-testid="{testid}"]')
    target = _require_one(btn, f"the {label} button on post {index}")
    target.click()
    page.wait_for_timeout(600)
    who = (art.inner_text() or "").splitlines()[0][:60]
    return {"index": index, "who": who, "action": label}


def like(index: int = 1, confirmed: bool = False) -> dict[str, Any]:
    _require_signed_in()
    page = _page()
    _check_reachable(page)
    if not confirmed:
        articles = page.get_by_role("article")
        who = ""
        if articles.count() >= index >= 1:
            lines = (articles.nth(index - 1).inner_text() or "").splitlines()
            who = lines[0][:50] if lines else ""
        # NAME THE AUTHOR IF SHE CAN SEE ONE, and say nothing extra if she
        # cannot. The first version fell back to the index and produced
        # "liking post 1, from post 1", which reads like a template leaking.
        raise ToolHold(f"liking post {index}, from {who}" if who else f"liking post {index}")
    return _action_on_post(page, int(index), "like", "like")


def repost(index: int = 1, confirmed: bool = False) -> dict[str, Any]:
    _require_signed_in()
    page = _page()
    _check_reachable(page)
    if not confirmed:
        raise ToolHold(f"reposting post {index} to your followers")
    out = _action_on_post(page, int(index), "repost|retweet", "retweet")
    # X asks a second time with a menu. Accessible name again.
    confirm = page.get_by_role("menuitem", name=re.compile("repost|retweet", re.I))
    if confirm.count() == 1:
        confirm.first.click()
        page.wait_for_timeout(400)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# RED — publishing. Fully built, and gated in the executor.
# ─────────────────────────────────────────────────────────────────────────────

#: X's limit. Checked HERE rather than after typing, so she refuses before
#: anything is entered into a compose box.
MAX_POST_CHARS = 280


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
    if len(body) > MAX_POST_CHARS:
        raise ToolError(f"that is {len(body)} characters, over X's {MAX_POST_CHARS}",
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
    page.wait_for_timeout(1500)
    _check_reachable(page)
    box = page.get_by_role("textbox", name=re.compile("post text|tweet text", re.I))
    if box.count() == 0:
        box = page.locator('[data-testid="tweetTextarea_0"]')
    _require_one(box, "the compose box").fill(body)
    page.wait_for_timeout(400)
    btn = page.get_by_role("button", name=re.compile(r"^post$", re.I))
    if btn.count() == 0:
        btn = page.locator('[data-testid="tweetButton"]')
    _require_one(btn, "the Post button").click()
    page.wait_for_timeout(1200)
    return {"chars": len(body), "text": body[:80]}


def reply(text: str, index: int = 1, _approved_by_surface: bool = False) -> dict[str, Any]:
    """RED. Same gate, same reasoning as `post`."""
    body = str(text or "").strip()
    if not body:
        raise ToolError("there was nothing to reply with", "Tell me what to say.")
    if len(body) > MAX_POST_CHARS:
        raise ToolError(f"that is {len(body)} characters, over X's {MAX_POST_CHARS}",
                        "Shorten it and I will hold it again.")
    if not _approved_by_surface:
        raise ToolError("that reached the reply handler without an approval",
                        "Nothing was published. Approve it on the card.")

    _require_signed_in()
    page = _page()
    _check_reachable(page)
    articles = page.get_by_role("article")
    if articles.count() < index:
        raise ToolError(f"there is no post number {index}", "Say a number I read to you.")
    art = articles.nth(index - 1)
    btn = art.get_by_role("button", name=re.compile("reply", re.I))
    if btn.count() == 0:
        btn = art.locator('[data-testid="reply"]')
    _require_one(btn, f"the reply button on post {index}").click()
    page.wait_for_timeout(1200)
    box = page.get_by_role("textbox", name=re.compile("post text|tweet text", re.I))
    if box.count() == 0:
        box = page.locator('[data-testid="tweetTextarea_0"]')
    _require_one(box, "the reply box").fill(body)
    send = page.get_by_role("button", name=re.compile("^reply$|^post$", re.I))
    if send.count() == 0:
        send = page.locator('[data-testid="tweetButton"]')
    _require_one(send, "the reply send button").click()
    page.wait_for_timeout(1200)
    return {"chars": len(body), "index": index, "text": body[:80]}


def open_for_login() -> dict[str, Any]:
    """
    GREEN. Open X in her profile so he can sign in HIMSELF.

    This is the entire authentication story: she opens a window, he types his
    own credentials into Chrome, X sets its cookies in her profile directory,
    and she never sees any of it. There is no callback here, no polling for
    success, and no field she fills.
    """
    page = _page()
    _goto(page, "https://x.com/login")
    return {"url": page.url,
            "profile": str(__import__("core.tools.browser", fromlist=["DEFAULT_PROFILE"])
                           .DEFAULT_PROFILE)}
