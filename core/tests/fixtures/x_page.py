"""
core/tests/fixtures/x_page.py — a fake X page for the READ tools. NO BROWSER.

WHAT IT IS FOR

The reading round (x.search, x.read_thread, x.read_user) has to be proven
against hostile posts — "ignore your rules", "delete C:\\" — and against a
signed-in session, without publishing anything, without a live session, and
without launching Chrome at all. This module duck-types EXACTLY the Playwright
surface `core/tools/x_tools.py`'s read path touches:

    page:     goto · wait_for_timeout · url · inner_text · get_by_role · locator
    locator:  count · nth · first · inner_text · evaluate_all · get_attribute
    context:  cookies

and NOTHING else. Any other attribute — `click`, `fill`, `type`, `press`,
`keyboard`, `mouse` — raises AttributeError, so a read that tried to write
fails loudly instead of quietly succeeding, and every call is recorded so a
proof can assert the whole run was reads. `violations()` lists any attempt to
reach a write.

Install with `install(fixture)`: it rebinds `x_tools.SESSION` to a FakeSession
whose cookies say "signed in" (a fixed non-secret marker, never a real cookie)
and whose one page renders the fixture's posts for whatever URL the tool
navigates to. `uninstall()` puts everything back.

THE ENGAGE SURFACE (X features round 2, 2026-09-12) IS OPT-IN. With
`Fixture.engage = True` the page also renders the buttons the like/unlike and
follow/unfollow handlers look for — a Like/Liked button per article (X's
"12 Likes. Like" / "12 Likes. Liked" names and like/unlike test ids), a
Follow/Following header button on a profile page, a "Follow @someone_else"
suggestion so an exact-name match can be proven, and X's Unfollow
confirmation sheet — and a press on one of them mutates `liked`/`following`
and is recorded as ("button", "click", (kind, target)). With `engage` False
(the default) every one of those lookups is a violation and raises, so the
read-only guarantees of round 1 are unchanged.

ROUND 3 (2026-09-12) ADDS TWO MORE OPT-IN SURFACES. In engage mode each
article also carries X's Repost/Reposted button (`retweet`/`unretweet` test
ids) and a press opens X's second-ask MENU — "Repost" or "Undo repost", with
"Quote" beside it — as `get_by_role("menuitem")`; a press on the confirm item
mutates `reposted`, a press on Quote is recorded as ("quote", id) so a proof
can assert it never happens. With `Fixture.compose = True` the page also
renders the COMPOSE surface the publish path drives: a "Post text" textbox
(`fill`), a Post / Reply send button whose press MINTS a status id, records
("compose", "sent", {...}) in `sent`, adds the post to his profile and its
own permalink, and shows X's "Your post was sent. View" toast link; plus the
navigation's Profile link. `toast=False` hides the toast so the profile
fallback of `_posted_id` can be proven; `sends_allowed=N` removes the send
button after N sends so a mid-thread failure can be provoked on the real path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

COOKIE_MARKER = "FIXTURE-NOT-A-SECRET"

_READ_ONLY = {
    "page": {"goto", "wait_for_timeout", "url", "inner_text", "get_by_role", "locator"},
    "locator": {"count", "nth", "first", "inner_text", "evaluate_all", "get_attribute"},
    "context": {"cookies", "pages", "new_page"},
}

#: Every call made through the fake, in order: (object, method, detail).
LOG: list[tuple[str, str, Any]] = []
#: Attribute lookups that were NOT read methods.
_VIOLATIONS: list[str] = []


def post(handle: str, pid: str, text: str, *, author: str = "",
         ts: str = "2026-09-12T10:00:00.000Z") -> dict[str, str]:
    """One fixture post, in the shape `x_tools._one_post` produces."""
    h = handle.lstrip("@")
    return {"who": author or h, "author": author or h, "handle": f"@{h}",
            "text": text, "id": pid, "ts": ts,
            "url": f"https://x.com/{h}/status/{pid}" if pid else ""}


@dataclass
class Fixture:
    timeline: list[dict[str, str]] = field(default_factory=list)
    notifications: list[dict[str, str]] = field(default_factory=list)
    #: query text -> posts. A query not listed renders X's "No results for".
    search: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    #: status id -> the articles on that permalink page (ancestors, post, replies).
    threads: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    #: handle (no @) -> that profile's articles.
    users: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    #: Extra body text, e.g. "Rate limit exceeded" or "Sign in to X".
    body_extra: str = ""
    #: ── the engage surface (round 2), opt-in ─────────────────────────────
    engage: bool = False
    #: status ids currently liked; a like press adds, an unlike press removes.
    liked: set[str] = field(default_factory=set)
    #: handles (no @) currently followed.
    following: set[str] = field(default_factory=set)
    #: "Follow @<h>" suggestion buttons rendered on EVERY profile page, so a
    #: follow that matched loosely would find several and must refuse.
    suggested: list[str] = field(default_factory=lambda: ["someone_else"])
    #: the like count X puts in front of the button's name.
    like_count: int = 12
    #: ── round 3: repost state, and the compose surface (opt-in) ─────────
    #: status ids currently reposted; the menu's confirm adds/removes.
    reposted: set[str] = field(default_factory=set)
    compose: bool = False
    #: his own handle (no @): the Profile link, and where sent posts land.
    me: str = "gerald"
    #: the next status id the send button mints.
    next_id: int = 5000001
    #: every send, in order: {kind, text, reply_to, id}.
    sent: list[dict[str, str]] = field(default_factory=list)
    #: render X's "View" toast after a send (False proves the profile fallback).
    toast: bool = True
    #: after this many sends the send button is gone (a provoked failure).
    sends_allowed: int | None = None
    #: render the navigation's Profile link (False: X's markup no longer
    #: exposes his handle, so `_posted_id`'s profile fallback has nowhere to look).
    profile_link: bool = True
    #: ── round 4: the DM surface (opt-in). PRIVATE — never rendered as articles.
    #:    A profile page carries X's "Message" button (`sendDMFromProfile`);
    #:    a press opens /messages/<id> for that handle (the id is minted
    #:    here). The conversation page renders a header naming @handle, the
    #:    messages as `messageEntry` nodes, the composer textbox
    #:    (`dmComposerTextInput`, "Start a new message") and a Send button
    #:    (`dmComposerSendButton`) whose press records ("dm", "sent", {...})
    #:    in `dm_sent`. /messages (the inbox) renders `conversation` cells.
    dm: bool = False
    #: handle (no @) -> the conversation, oldest first: {"from": "them"|"me", "text"}.
    dms: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    #: handle -> the id X would put in the conversation URL; minted on first open.
    dm_ids: dict[str, str] = field(default_factory=dict)
    #: every DM send through the fixture's Send button: {handle, text}.
    dm_sent: list[dict[str, str]] = field(default_factory=list)
    #: render the profile's Message button (False: X changed its markup -> the read fails closed).
    dm_button: bool = True
    #: render the conversation header's @handle (False: the page cannot be placed -> fail closed).
    dm_header: bool = True


class _Strict:
    _kind = "?"
    _allowed: frozenset[str] = frozenset()

    def __getattr__(self, name: str) -> Any:
        _VIOLATIONS.append(f"{self._kind}.{name}")
        raise AttributeError(
            f"fake X {self._kind} has no {name!r}: the fixture exposes READ methods only")


class _Node:
    """A leaf the read path inspects: text plus attributes."""

    def __init__(self, text: str = "", **attrs: str) -> None:
        self.text = text
        self.attrs = attrs

    def inner_text(self) -> str:
        return self.text


class FakeLocator(_Strict):
    _kind = "locator"

    def __init__(self, items: list[Any], what: str) -> None:
        self._items = list(items)
        self._what = what

    def count(self) -> int:
        LOG.append(("locator", "count", self._what))
        return len(self._items)

    def nth(self, i: int) -> Any:
        LOG.append(("locator", "nth", (self._what, i)))
        return self._items[i]

    @property
    def first(self) -> "FakeLocator":
        LOG.append(("locator", "first", self._what))
        return FakeLocator(self._items[:1], self._what)

    def inner_text(self) -> str:
        LOG.append(("locator", "inner_text", self._what))
        return self._items[0].inner_text() if self._items else ""

    def get_attribute(self, name: str) -> str | None:
        LOG.append(("locator", "get_attribute", (self._what, name)))
        if not self._items:
            return None
        return getattr(self._items[0], "attrs", {}).get(name)

    def evaluate_all(self, js: str) -> list[Any]:
        LOG.append(("locator", "evaluate_all", (self._what, js[:40])))
        return [getattr(n, "attrs", {}).get("href") for n in self._items]

    def click(self) -> None:
        """A press. Only a FakeButton (engage mode) accepts one; anything else raises."""
        LOG.append(("locator", "click", self._what))
        if len(self._items) != 1 or not isinstance(self._items[0], FakeButton):
            _VIOLATIONS.append(f"locator.click({self._what!r})")
            raise AssertionError(f"fake X: click on {self._what!r} is not a button press")
        self._items[0].click()

    def fill(self, text: str) -> None:
        """Typing. Only the compose textbox accepts it; anything else raises."""
        LOG.append(("locator", "fill", self._what))
        if len(self._items) != 1 or not isinstance(self._items[0], FakeTextbox):
            _VIOLATIONS.append(f"locator.fill({self._what!r})")
            raise AssertionError(f"fake X: fill on {self._what!r} is not the compose box")
        self._items[0].fill(text)


def _name_matches(name: Any, label: str) -> bool:
    """Playwright's `name=`: a Pattern is searched, a string is a substring, case-insensitive."""
    if name is None:
        return True
    if hasattr(name, "search"):
        return bool(name.search(label))
    return str(name).lower() in label.lower()


class FakeButton:
    """One pressable control. `press` runs only when the fixture is in engage mode."""

    def __init__(self, label: str, testid: str, kind: str, target: str,
                 fixture: "Fixture | None", press: Any) -> None:
        self.label, self.testid, self.kind, self.target = label, testid, kind, target
        self._fixture, self._press = fixture, press

    def inner_text(self) -> str:
        return self.label

    def click(self) -> None:
        LOG.append(("button", "click", (self.kind, self.target)))
        if self._fixture is None or not (self._fixture.engage or self._fixture.compose
                                         or self._fixture.dm):
            _VIOLATIONS.append(f"button.click({self.kind}:{self.target})")
            raise AssertionError("fake X: a press outside engage/compose/dm mode")
        self._press()


class FakeTextbox:
    """The compose box (round 3). `fill` sets the page's draft."""

    label = "Post text"

    def __init__(self, page: "FakePage") -> None:
        self._page = page

    def inner_text(self) -> str:
        return self._page._draft

    def fill(self, text: str) -> None:
        LOG.append(("textbox", "fill", text))
        self._page._draft = str(text)


class FakeDMBox(FakeTextbox):
    """The DM composer (round 4). `fill` sets the conversation's draft."""

    label = "Start a new message"

    def inner_text(self) -> str:
        return self._page._dm_draft

    def fill(self, text: str) -> None:
        LOG.append(("dmbox", "fill", text))
        self._page._dm_draft = str(text)


class FakeEntry(_Strict):
    """One `messageEntry` on a conversation page: its text, plus a time line like X's."""

    _kind = "entry"

    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self) -> str:
        LOG.append(("entry", "inner_text", None))
        return f"{self.text}\n2:15 PM"

    def locator(self, selector: str) -> "FakeLocator":
        LOG.append(("entry", "locator", selector))
        if selector == '[data-testid="tweetText"]':
            return FakeLocator([_Node(self.text)], selector)
        return FakeLocator([], selector)


class FakeArticle(_Strict):
    _kind = "article"

    def __init__(self, p: dict[str, str], fixture: "Fixture | None" = None,
                 page: "FakePage | None" = None) -> None:
        self.p = dict(p)
        self._fixture = fixture
        self._page = page

    # ── the engage surface ───────────────────────────────────────────────────
    def _buttons(self) -> list[FakeButton]:
        fx = self._fixture
        if fx is None or not (fx.engage or fx.compose):
            return []
        pid = self.p.get("id", "")
        page = self._page
        n = fx.like_count
        if pid in fx.liked:
            like_btn = FakeButton(f"{n} Likes. Liked", "unlike", "unlike", pid, fx,
                                  lambda: fx.liked.discard(pid))
        else:
            like_btn = FakeButton(f"{n} Likes. Like", "like", "like", pid, fx,
                                  lambda: fx.liked.add(pid))
        # ROUND 3: the repost button carries its state in its name and test id
        # exactly as the like button does, and a press OPENS X's menu — the
        # act happens on the menu's confirm item, never on this button.
        if pid in fx.reposted:
            repost_btn = FakeButton(f"{n} reposts. Reposted", "unretweet", "unrepost", pid, fx,
                                    lambda: page is not None and page._open_menu("unrepost", pid))
        else:
            repost_btn = FakeButton(f"{n} reposts. Repost", "retweet", "repost", pid, fx,
                                    lambda: page is not None and page._open_menu("repost", pid))
        return [FakeButton("3 Replies. Reply", "reply", "reply", pid, fx,
                           lambda: page is not None and page._begin_reply(pid)),
                repost_btn,
                like_btn,
                FakeButton("Share post", "share", "share", pid, fx, lambda: None)]

    def get_by_role(self, role: str, **kw: Any) -> FakeLocator:
        LOG.append(("article", "get_by_role", (role, kw)))
        fx = self._fixture
        if role != "button" or fx is None or not (fx.engage or fx.compose):
            _VIOLATIONS.append(f"article.get_by_role({role!r})")
            raise AttributeError(f"fake X article has no {role!r} buttons outside engage mode")
        name = kw.get("name")
        return FakeLocator([b for b in self._buttons() if _name_matches(name, b.label)],
                           f"article button {name!r}")

    def inner_text(self) -> str:
        LOG.append(("article", "inner_text", self.p.get("id")))
        return f"{self.p.get('author', '')}\n{self.p.get('handle', '')}\n·\n2h\n{self.p.get('text', '')}"

    def locator(self, selector: str) -> FakeLocator:
        LOG.append(("article", "locator", selector))
        p = self.p
        if selector == 'a[href*="/status/"]':
            h = p.get("handle", "").lstrip("@")
            # `nolink`: the focal article of a permalink page, which X renders
            # WITHOUT a permalink of its own (it is the page).
            items = ([_Node(href=f"/{h}/status/{p['id']}")]
                     if p.get("id") and h and not p.get("nolink") else [])
            return FakeLocator(items, selector)
        if selector in ('[data-testid="like"]', '[data-testid="unlike"]',
                        '[data-testid="retweet"]', '[data-testid="unretweet"]'):
            want = selector.split('"')[1]
            return FakeLocator([b for b in self._buttons() if b.testid == want], selector)
        if selector == "time":
            return FakeLocator([_Node(datetime=p["ts"])] if p.get("ts") else [], selector)
        if selector == '[data-testid="User-Name"]':
            return FakeLocator([_Node(f"{p.get('author', '')} {p.get('handle', '')}")], selector)
        if selector == '[data-testid="tweetText"]':
            return FakeLocator([_Node(p.get("text", ""))] if p.get("text") else [], selector)
        return FakeLocator([], selector)


class FakePage(_Strict):
    _kind = "page"

    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture
        self.url = "about:blank"
        self._posts: list[dict[str, str]] = []
        self._extra = ""
        #: the profile this page is on (engage surface), and an open Unfollow sheet.
        self._handle = ""
        self._sheet = ""
        #: round 3: X's repost menu (kind, id); the compose state (kind,
        #: reply_to), the draft in the box, and the id the "View" toast names.
        self._menu: tuple[str, str] | None = None
        self._composing: tuple[str, str] | None = None
        self._draft = ""
        self._toast = ""
        #: round 4: the conversation this page is on (handle, no @), the inbox
        #: flag, and the DM composer's draft.
        self._dm_handle = ""
        self._dm_inbox = False
        self._dm_draft = ""

    # ── what a URL renders ───────────────────────────────────────────────────
    def _render(self, url: str) -> None:
        u = urlparse(url)
        path = u.path.rstrip("/")
        fx = self.fixture
        self._extra = fx.body_extra
        self._dm_handle = ""
        self._dm_inbox = False
        self._dm_draft = ""
        if path == "/search":
            q = (parse_qs(u.query).get("q") or [""])[0]
            self._posts = list(fx.search.get(q, []))
            if q not in fx.search:
                self._extra += f'\nNo results for "{q}"'
        elif path.startswith("/i/web/status/"):
            pid = path.rsplit("/", 1)[-1]
            self._posts = list(fx.threads.get(pid, []))
            if pid not in fx.threads:
                self._extra += "\nHmm...this page doesn't exist. Try searching for something else."
        elif path == "/home":
            self._posts = list(fx.timeline)
        elif path == "/notifications":
            self._posts = list(fx.notifications)
        elif path == "/messages" and fx.dm:
            self._posts = []
            self._dm_inbox = True
            self._extra += "\nMessages\n" + "\n\n".join(self._dm_cell_text(h) for h in fx.dms)
        elif path.startswith("/messages/") and fx.dm:
            cid = path.rsplit("/", 1)[-1]
            h = next((k for k, v in fx.dm_ids.items() if v == cid), "")
            self._posts = []
            self._dm_handle = h
            if not h:
                self._extra += "\nHmm...this page doesn\u2019t exist. Try searching for something else."
            else:
                self._extra += ("\n" + (f"{h.capitalize()}\n@{h}\n" if fx.dm_header else "")
                                + "\n".join(m["text"] for m in fx.dms.get(h, [])))
        elif path in ("/compose/post", "/messages", "/i/flow/login", "/login"):
            self._posts = []
            self._extra += f"\nWRITE-SURFACE {path}"
        else:
            handle = path.lstrip("/")
            self._posts = list(fx.users.get(handle, []))
            if handle not in fx.users:
                self._extra += "\nThis account doesn't exist. Try searching for another."
        self._handle = path.lstrip("/") if path not in ("/search", "/home", "/notifications") \
            and not path.startswith("/i/") and not path.startswith("/messages") \
            and path not in ("/compose/post", "/i/flow/login", "/login") else ""
        self._sheet = ""
        self._menu = None
        self._toast = ""
        self._draft = ""
        # The compose page IS a compose box; anywhere else, composing starts
        # from an article's Reply button.
        self._composing = ("post", "") if (path == "/compose/post" and fx.compose) else None

    def goto(self, url: str, wait_until: str = "", timeout: int = 0) -> None:
        LOG.append(("page", "goto", url))
        self.url = url
        self._render(url)

    # ── the engage surface: follow / following / the unfollow sheet ──────────
    def _buttons(self) -> list[FakeButton]:
        fx = self.fixture
        if not fx.engage:
            return []
        out: list[FakeButton] = []
        h = self._handle
        if h and h in fx.users:
            if h in fx.following:
                def _open_sheet(h: str = h) -> None:
                    self._sheet = h
                out.append(FakeButton(f"Following @{h}", "1001-unfollow", "unfollow", h, fx, _open_sheet))
            else:
                out.append(FakeButton(f"Follow @{h}", "1001-follow", "follow", h, fx,
                                      lambda h=h: fx.following.add(h)))
        for i, s in enumerate(fx.suggested):
            out.append(FakeButton(f"Follow @{s}", f"{2002 + i}-follow", "follow", s, fx,
                                  lambda s=s: fx.following.add(s)))
        if self._sheet:
            def _confirm(h: str = self._sheet) -> None:
                fx.following.discard(h)
                self._sheet = ""
            out.append(FakeButton("Unfollow", "confirmationSheetConfirm", "unfollow-confirm",
                                  self._sheet, fx, _confirm))
            out.append(FakeButton("Cancel", "confirmationSheetCancel", "cancel", self._sheet, fx,
                                  lambda: None))
        return out

    # ── round 3: the repost menu and the compose surface ─────────────────────
    def _open_menu(self, kind: str, pid: str) -> None:
        self._menu = (kind, pid)

    def _menu_items(self) -> list[FakeButton]:
        fx = self.fixture
        if not fx.engage or not self._menu:
            return []
        kind, pid = self._menu

        def _close() -> None:
            self._menu = None

        def _do(pid: str = pid, kind: str = kind) -> None:
            (fx.reposted.add if kind == "repost" else fx.reposted.discard)(pid)
            _close()

        def _quote(pid: str = pid) -> None:
            _close()

        if kind == "repost":
            confirm = FakeButton("Repost", "retweetConfirm", "repost-confirm", pid, fx, _do)
        else:
            confirm = FakeButton("Undo repost", "unretweetConfirm", "unrepost-confirm", pid, fx, _do)
        # "Quote" sits right beside the confirm item on X. A press on it is
        # recorded as ("quote", id) so a proof can assert it never happened.
        return [confirm, FakeButton("Quote", "quoteTweet", "quote", pid, fx, _quote)]

    def _begin_reply(self, pid: str) -> None:
        if self.fixture.compose:
            self._composing = ("reply", pid)
            self._draft = ""

    def _send(self) -> None:
        fx = self.fixture
        kind, reply_to = self._composing or ("post", "")
        text = self._draft
        pid = str(fx.next_id)
        fx.next_id += 1
        rec = {"kind": kind, "text": text, "reply_to": reply_to, "id": pid}
        LOG.append(("compose", "sent", rec))
        fx.sent.append(rec)
        p = post(f"@{fx.me}", pid, text)
        fx.users.setdefault(fx.me, []).insert(0, p)
        fx.threads[pid] = [p]
        self._toast = pid
        self._composing = None
        self._draft = ""

    def _send_buttons(self) -> list[FakeButton]:
        fx = self.fixture
        if not fx.compose or not self._composing:
            return []
        if fx.sends_allowed is not None and len(fx.sent) >= fx.sends_allowed:
            return []    # the button is gone: a provoked mid-thread failure
        kind, reply_to = self._composing
        return [FakeButton("Post" if kind == "post" else "Reply", "tweetButton", "send",
                           reply_to or "new", fx, self._send)]

    # ── round 4: the DM surface ──────────────────────────────────────────────
    def _dm_cell_text(self, h: str) -> str:
        msgs = self.fixture.dms.get(h, [])
        last = msgs[-1]["text"] if msgs else ""
        return f"{h.capitalize()}\n@{h}\n\u00b7 2h\n{last}"

    def _dm_buttons(self) -> list[FakeButton]:
        fx = self.fixture
        if not fx.dm:
            return []
        out: list[FakeButton] = []
        h = self._handle
        if h and (h in fx.users or h in fx.dms) and fx.dm_button:
            out.append(FakeButton("Message", "sendDMFromProfile", "open-dm", h, fx,
                                  lambda h=h: self._open_dm(h)))
        if self._dm_handle:
            out.append(FakeButton("Send", "dmComposerSendButton", "dm-send", self._dm_handle, fx,
                                  self._dm_send))
        return out

    def _dm_entries(self) -> list[FakeEntry]:
        if not self._dm_handle:
            return []
        return [FakeEntry(m["text"]) for m in self.fixture.dms.get(self._dm_handle, [])]

    def _dm_cells(self) -> list[_Node]:
        if not self._dm_inbox:
            return []
        return [_Node(self._dm_cell_text(h)) for h in self.fixture.dms]

    def _open_dm(self, h: str) -> None:
        fx = self.fixture
        cid = fx.dm_ids.setdefault(h, f"{1000 + len(fx.dm_ids)}-{fx.me}")
        fx.dms.setdefault(h, [])
        self.url = f"https://x.com/messages/{cid}"
        self._render(self.url)

    def _dm_send(self) -> None:
        fx = self.fixture
        rec = {"handle": self._dm_handle, "text": self._dm_draft}
        LOG.append(("dm", "sent", rec))
        fx.dm_sent.append(rec)
        fx.dms.setdefault(self._dm_handle, []).append({"from": "me", "text": self._dm_draft})
        self._dm_draft = ""

    def _links(self) -> list[tuple[str, _Node]]:
        fx = self.fixture
        if not fx.compose:
            return []
        out = [("Profile", _Node(href=f"/{fx.me}"))] if fx.profile_link else []
        if self._toast and fx.toast:
            out.append(("View", _Node(href=f"/{fx.me}/status/{self._toast}")))
        return out

    def wait_for_timeout(self, ms: int) -> None:
        LOG.append(("page", "wait_for_timeout", ms))

    def inner_text(self, selector: str) -> str:
        LOG.append(("page", "inner_text", selector))
        body = "X\nHome\nExplore\n" + "\n\n".join(
            FakeArticle(p).p.get("text", "") for p in self._posts)
        return body + self._extra

    def get_by_role(self, role: str, **kw: Any) -> FakeLocator:
        LOG.append(("page", "get_by_role", (role, kw)))
        fx = self.fixture
        name = kw.get("name")
        if role == "button" and (fx.engage or fx.compose or fx.dm):
            return FakeLocator([b for b in self._buttons() + self._send_buttons() + self._dm_buttons()
                                if _name_matches(name, b.label)], f"page button {name!r}")
        if role == "textbox" and fx.dm and self._dm_handle:
            box = [FakeDMBox(self)]
            return FakeLocator([b for b in box if _name_matches(name, b.label)], f"textbox {name!r}")
        if role == "menuitem" and fx.engage:
            return FakeLocator([b for b in self._menu_items() if _name_matches(name, b.label)],
                               f"menuitem {name!r}")
        if role == "textbox" and fx.compose:
            box = [FakeTextbox(self)] if self._composing else []
            return FakeLocator([b for b in box if _name_matches(name, b.label)], f"textbox {name!r}")
        if role == "link" and fx.compose:
            return FakeLocator([n for lbl, n in self._links() if _name_matches(name, lbl)],
                               f"link {name!r}")
        if role != "article":
            _VIOLATIONS.append(f"page.get_by_role({role!r})")
            raise AssertionError(f"fake X page: only role='article' is readable, not {role!r}")
        return FakeLocator([FakeArticle(p, self.fixture, self) for p in self._posts], "article")

    def locator(self, selector: str) -> FakeLocator:
        LOG.append(("page", "locator", selector))
        if self.fixture.engage:
            if selector == '[data-testid$="-follow"]':
                return FakeLocator([b for b in self._buttons() if b.testid.endswith("-follow")], selector)
            if selector == '[data-testid$="-unfollow"]':
                return FakeLocator([b for b in self._buttons() if b.testid.endswith("-unfollow")], selector)
            if selector == '[data-testid="confirmationSheetConfirm"]':
                return FakeLocator([b for b in self._buttons() if b.testid == "confirmationSheetConfirm"],
                                   selector)
            if selector in ('[data-testid="retweetConfirm"]', '[data-testid="unretweetConfirm"]'):
                want = selector.split('"')[1]
                return FakeLocator([b for b in self._menu_items() if b.testid == want], selector)
        if self.fixture.dm:
            if selector == '[data-testid="messageEntry"]':
                return FakeLocator(self._dm_entries(), selector)
            if selector == '[data-testid="conversation"]':
                return FakeLocator(self._dm_cells(), selector)
            if selector == '[data-testid="sendDMFromProfile"]':
                return FakeLocator([b for b in self._dm_buttons() if b.testid == "sendDMFromProfile"], selector)
            if selector == '[data-testid="dmComposerTextInput"]':
                return FakeLocator([FakeDMBox(self)] if self._dm_handle else [], selector)
            if selector == '[data-testid="dmComposerSendButton"]':
                return FakeLocator([b for b in self._dm_buttons() if b.testid == "dmComposerSendButton"], selector)
        if self.fixture.compose:
            if selector == '[data-testid="tweetTextarea_0"]':
                return FakeLocator([FakeTextbox(self)] if self._composing else [], selector)
            if selector == '[data-testid="tweetButton"]':
                return FakeLocator(self._send_buttons(), selector)
            if selector == '[data-testid="AppTabBar_Profile_Link"]':
                return FakeLocator([n for lbl, n in self._links() if lbl == "Profile"], selector)
        _VIOLATIONS.append(f"page.locator({selector!r})")
        raise AssertionError(f"fake X page: page-level locator {selector!r} is a write surface")


class FakeContext(_Strict):
    _kind = "context"

    def __init__(self, page: FakePage, signed_in: bool) -> None:
        self._page = page
        self._signed_in = signed_in

    def cookies(self, urls: Any = None) -> list[dict[str, str]]:
        LOG.append(("context", "cookies", urls))
        if not self._signed_in:
            return []
        return [{"name": "auth_token", "domain": ".x.com", "value": COOKIE_MARKER}]

    @property
    def pages(self) -> list[FakePage]:
        return [self._page]

    def new_page(self) -> FakePage:
        return self._page


class FakeSession:
    """Stands in for core.tools.browser.SESSION. Never launches anything."""

    def __init__(self, fixture: Fixture, *, signed_in: bool = True) -> None:
        self.fixture = fixture
        self.fake_page = FakePage(fixture)
        self._ctx = FakeContext(self.fake_page, signed_in)
        self.is_open = False
        self.closed = 0

    def context(self, *a: Any, **k: Any) -> FakeContext:
        LOG.append(("session", "context", None))
        return self._ctx

    def page(self) -> FakePage:
        LOG.append(("session", "page", None))
        return self.fake_page

    def close(self, reason: str = "asked") -> dict[str, Any]:
        self.closed += 1
        return {"was_open": False, "reason": reason, "up_s": 0.0}


_saved: dict[str, Any] = {}


def install(fixture: Fixture, *, signed_in: bool = True, gap_s: float = 0.0,
            jitter_s: float = 0.0) -> FakeSession:
    """
    Rebind x_tools.SESSION to a fake and shorten the pacing floor so a suite
    does not sleep. Also fences the REAL browser: any attempt to launch it
    raises BrowserUnavailable.
    """
    from core.tools import browser as _browser
    from core.tools import x_tools as _x

    if not _saved:
        _saved.update({
            "SESSION": _x.SESSION,
            "READ_GAP_S": _x.READ_GAP_S, "READ_JITTER_S": _x.READ_JITTER_S,
            "_last_nav": _x._last_nav, "_LAST_READ": list(_x._LAST_READ),
            "_LAST_DM": dict(getattr(_x, "_LAST_DM", {})),
            "ctx": _browser.SESSION.context,
            "pw": _browser.SESSION._require_playwright,
        })

    def _no_browser(*a: Any, **k: Any) -> Any:
        _VIOLATIONS.append("browser.SESSION launch attempted")
        raise _browser.BrowserUnavailable("fixture guard: no real browser in a test")

    _browser.SESSION.context = _no_browser
    _browser.SESSION._require_playwright = _no_browser
    fake = FakeSession(fixture, signed_in=signed_in)
    _x.SESSION = fake
    _x.READ_GAP_S = float(gap_s)
    _x.READ_JITTER_S = float(jitter_s)
    _x._last_nav = 0.0
    _x._LAST_READ.clear()
    if hasattr(_x, "_LAST_DM"):
        _x._LAST_DM.clear()
    return fake


def uninstall() -> None:
    from core.tools import browser as _browser
    from core.tools import x_tools as _x

    if not _saved:
        return
    _x.SESSION = _saved["SESSION"]
    _x.READ_GAP_S = _saved["READ_GAP_S"]
    _x.READ_JITTER_S = _saved["READ_JITTER_S"]
    _x._last_nav = _saved["_last_nav"]
    _x._LAST_READ[:] = _saved["_LAST_READ"]
    if hasattr(_x, "_LAST_DM"):
        _x._LAST_DM.clear()
        _x._LAST_DM.update(_saved.get("_LAST_DM", {}))
    _browser.SESSION.context = _saved["ctx"]
    _browser.SESSION._require_playwright = _saved["pw"]
    _saved.clear()


def reset_log() -> None:
    LOG.clear()
    _VIOLATIONS.clear()


def violations() -> list[str]:
    return list(_VIOLATIONS)


def navigations() -> list[str]:
    return [d for o, m, d in LOG if o == "page" and m == "goto"]


def methods_used() -> set[str]:
    return {f"{o}.{m}" for o, m, _ in LOG}


def clicks() -> list[tuple[str, str]]:
    """Every press on the engage surface, in order: (kind, target)."""
    return [d for o, m, d in LOG if o == "button" and m == "click"]


def sends() -> list[dict[str, str]]:
    """Every send through the compose surface, in order (round 3)."""
    return [d for o, m, d in LOG if o == "compose" and m == "sent"]


def dms_sent() -> list[dict[str, str]]:
    """Every private message sent through the DM surface, in order (round 4)."""
    return [d for o, m, d in LOG if o == "dm" and m == "sent"]
