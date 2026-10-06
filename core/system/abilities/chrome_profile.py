"""
core/system/abilities/chrome_profile.py — her Chrome profiles: make, list, open,
navigate, and ask whether he is signed in.

⚠⚠ THE ONE HARD LINE: NOTHING HERE TYPES A PASSWORD, EVER.

She opens the profile and she opens the login page. HE types the credential and
the 2FA code. There is no fill, no autofill, no submit, no credential store and
no scripted-login path in this module — not as a fallback, not behind a flag,
not as an option. `_login_status` reads a COOKIE that his own hand-login left
behind; it never reads, writes or transports the secret that created it.

This is not fastidiousness. The whole X design rests on his password being
unreachable by the model, because everything Tessa reads from a browser is
attacker-controllable text and a model that can type into a password field is
one injection away from typing it somewhere else.

⚠⚠ THE TIER SPLIT IS TWO CAPABILITIES, NOT ONE CAPABILITY WITH A BRANCH.

    system.chrome.open_profile           GREEN   browser.profile
    system.chrome.open_personal_profile  AMBER   browser.profile.personal

A single capability that chose its tier at runtime would be a capability
picking its own tier, which is the exact thing `core/system/capability.py`
exists to forbid. Splitting them means the GREEN one cannot open one of his
profiles even if the resolver hands it one — it refuses and names the amber
capability. Thirty of his profiles are signed into real accounts; that refusal
is the point.

WHAT THIS DOES NOT DO: click, type, submit, post. Acting inside a profile stays
where it already is — `browser.click`/`browser.type` amber, `browser.submit`
and `x.post` red behind the card.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param

#: Sites whose signed-in state can be answered HONESTLY, by a cookie his own
#: login left. Deliberately a closed table: guessing from "does the page look
#: logged in" is a fabrication, and the standing rule is that she never invents
#: an observation. An unknown site is answered "I cannot tell", not "no".
_AUTH_COOKIES = {
    "x.com": ("auth_token", ("x.com", "twitter.com")),
    "twitter.com": ("auth_token", ("x.com", "twitter.com")),
    "github.com": ("user_session", ("github.com",)),
}


def _site_key(site: str) -> str:
    s = str(site or "").strip().lower().lstrip("@")
    s = s.replace("https://", "").replace("http://", "").strip("/")
    s = s.split("/")[0]
    if s in ("x", "twitter"):
        return "x.com"
    if s == "github":
        return "github.com"
    return s


def create_profile(name: str = "default") -> dict[str, Any]:
    """Make (or confirm) one of HER profiles. Idempotent. Creates no browser."""
    from core.tools import chrome_profiles as cp

    profile, created = cp.ensure_tessa_profile(name)
    return {"name": profile.name, "path": str(profile.path),
            "created": created,
            "state": "Made" if created else "Already there"}


def list_profiles() -> dict[str, Any]:
    """Every profile, hers and his, with WHOSE each one is. Metadata only."""
    from core.tools import chrome_profiles as cp

    mine = cp.tessa_profiles()
    his = cp.chrome_profiles()
    return {
        "n_mine": len(mine), "n_his": len(his),
        "mine": [{"name": p.name, "path": str(p.path)} for p in mine],
        "his": [{"name": p.name, "directory": p.directory, "account": p.account}
                for p in his],
        "head": ", ".join(p.name for p in mine) or "none",
        "summary": (f"{len(mine)} of mine — {', '.join(p.name for p in mine) or 'none'} — "
                    f"and {len(his)} of yours in Chrome. Opening one of yours is a "
                    f"deliberate ask, Emperor; I hold for it."),
    }


def _open(profile: Any) -> dict[str, Any]:
    from core.tools import browser

    browser.SESSION.context(profile=profile.path,
                            profile_directory=profile.directory or "")
    return {"name": profile.name, "label": profile.label,
            "kind": profile.kind, "path": str(profile.path),
            "directory": profile.directory, "account": profile.account}


def open_profile(name: str = "default") -> dict[str, Any]:
    """
    Open one of HER profiles. GREEN.

    ⚠ REFUSES A PERSONAL PROFILE STRUCTURALLY. If the name resolves to one of
    his Chrome profiles this stops and names the amber capability instead. The
    green path cannot drive his signed-in accounts.
    """
    from core.tools import chrome_profiles as cp
    from core.tools.base import ToolError

    profile = cp.resolve(name)
    if profile.is_personal:
        raise ToolError(
            f"{profile.name!r} is one of YOUR Chrome profiles, not mine",
            "That one holds your signed-in accounts. Say open my personal "
            "chrome profile and name it, and I will hold for your yes first.")
    return _open(profile)


def open_personal_profile(name: str) -> dict[str, Any]:
    """
    Open one of HIS Chrome profiles. AMBER — the framework holds for his yes.

    THE PRACTICAL CONSTRAINT, STATED RATHER THAN DISCOVERED: Chrome locks a
    user-data directory while it is running. If his Chrome is open — and it
    usually is — this cannot attach and says so, instead of appearing to work.
    """
    from core.tools import chrome_profiles as cp
    from core.tools.base import ToolError

    profile = cp.resolve(name)
    if not profile.is_personal:
        # Not an error worth stopping for, but it must not be silent: he asked
        # for the amber path and got the green one.
        return {**_open(profile),
                "note": " That one is mine, so it did not need your yes."}
    try:
        return {**_open(profile), "note": ""}
    except Exception as exc:  # noqa: BLE001
        raise ToolError(
            f"Chrome would not hand me {profile.name!r} ({type(exc).__name__})",
            "Close Chrome completely and ask me again — it locks a profile "
            "while it is running.") from None


def open_url(url: str) -> dict[str, Any]:
    """Navigate the OPEN profile to a URL. Does not choose a profile."""
    from core.tools import browser
    from core.tools.base import ToolError

    target = str(url or "").strip()
    if not target:
        raise ToolError("no address came through", "Tell me which site.")
    if not target.startswith(("http://", "https://")):
        target = f"https://{target}"
    page = browser.SESSION.page()
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=browser.NAV_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"{target} did not load ({type(exc).__name__})",
                        "Check the address and your connection.") from None
    return {"url": page.url, "title": (page.title() or "")[:80],
            "profile": browser.SESSION.profile.name}


def login_status(site: str = "x.com") -> dict[str, Any]:
    """
    Is he signed into `site` IN THE OPEN PROFILE?

    ⚠ READS A COOKIE, TYPES NOTHING. When the answer is no, the login page is
    left OPEN and in front of him — that is the entire hand-over: she gets him
    to the door, he uses the key.
    """
    from core.tools import browser
    from core.tools.base import ToolError

    key = _site_key(site)
    known = _AUTH_COOKIES.get(key)
    if known is None:
        raise ToolError(f"I do not know how to check whether {key} is signed in",
                        f"I can check {', '.join(sorted(set(_AUTH_COOKIES)))}.")
    cookie_name, domains = known
    ctx = browser.SESSION.context()
    try:
        cookies = ctx.cookies(urls=[f"https://{key}"])
    except Exception:  # noqa: BLE001
        cookies = []
    signed_in = any(
        c.get("name") == cookie_name
        and str(c.get("domain", "")).lstrip(".").endswith(domains)
        for c in cookies)

    opened = ""
    if not signed_in:
        # LEAVE HIM THE LOGIN PAGE. Not filled in — open.
        try:
            page = browser.SESSION.page()
            page.goto(f"https://{key}/login" if key != "x.com" else "https://x.com/i/flow/login",
                      wait_until="domcontentloaded", timeout=browser.NAV_TIMEOUT_MS)
            opened = " I have put the sign-in page in front of you."
        except Exception:  # noqa: BLE001
            opened = ""
    return {
        "site": key, "signed_in": signed_in,
        "profile": browser.SESSION.profile.name,
        "state": "signed in" if signed_in else "not signed in",
        "advice": "" if signed_in else
                  f"{opened} Sign in yourself — I never see your password.",
    }


CAPABILITIES = [
    Capability(
        name="system.chrome.create_profile", capability="browser.profile", tier="green",
        run=create_profile,
        params=(Param("name", str, default="default", doc="What to call it."),),
        phrasings=("create a chrome profile for yourself", "make your own profile"),
        success="{state}, Emperor. My {name} profile.",
        audit="ensure chrome profile {name}",
        note="Creates a directory under HER root only, never inside his Chrome user data. "
             "Idempotent. Launches no browser.",
    ),
    Capability(
        name="system.chrome.list_profiles", capability="browser.profile", tier="green",
        run=list_profiles,
        phrasings=("list chrome profiles", "what chrome profiles are there"),
        success="{summary}",
        audit="list chrome profiles",
        note="Reads Chrome's Local State for HIS profile names. Metadata only — never "
             "written, no browser launched.",
    ),
    Capability(
        name="system.chrome.open_profile", capability="browser.profile", tier="green",
        run=open_profile,
        params=(Param("name", str, default="default", doc="Which of her profiles."),),
        phrasings=("open your chrome profile", "open the tessa profile"),
        success="Open, Emperor. {label}.",
        audit="open profile {name}",
        note="HER PROFILES ONLY. Resolving to one of his is a refusal that names the "
             "amber capability — the green path cannot drive his signed-in accounts.",
    ),
    Capability(
        name="system.chrome.open_personal_profile",
        capability="browser.profile.personal", tier="amber",
        run=open_personal_profile,
        params=(Param("name", str, doc="Which of HIS Chrome profiles."),),
        phrasings=("open my personal chrome profile", "open my work profile"),
        success="Open, Emperor. {label}.{note}",
        audit="OPEN PERSONAL PROFILE {name}",
        hold="open your own Chrome profile {name}",
        note="AMBER: this drives a browser inside one of his signed-in accounts. Holds "
             "for his yes, refuses a non-human origin, and cannot attach while his "
             "Chrome is running (Chrome locks the profile).",
    ),
    Capability(
        name="system.chrome.open_url", capability="browser.open_url", tier="green",
        run=open_url,
        params=(Param("url", str, doc="The address."),),
        phrasings=("open x.com in your profile", "go to github.com in your browser"),
        success="Open, Emperor. {title}.",
        audit="open {url} in profile",
        note="Navigates whichever profile is already open. Does not choose or switch one.",
    ),
    Capability(
        name="system.chrome.login_status", capability="browser.profile", tier="green",
        run=login_status,
        params=(Param("site", str, default="x.com", doc="Which site."),),
        phrasings=("am I logged into x", "am I signed into x"),
        success="You are {state} to {site} in my {profile} profile, Emperor.{advice}",
        audit="check login for {site}",
        note="⚠ Reads a cookie his own hand-login left. TYPES NOTHING. When the answer is "
             "no it opens the sign-in page and leaves it for him. An unknown site is "
             "answered 'I cannot tell', never guessed.",
    ),
]
