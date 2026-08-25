"""
core/tools/browser.py — a real browser, driven by Playwright.

THE PROFILE DECISION, RESTATED BECAUSE IT IS THE WHOLE SECURITY POSTURE

A DEDICATED `user_data_dir` under `%LOCALAPPDATA%\\Tessa\\browser-profiles\\`.
NEVER his main Chrome profile, for two independent reasons, either of which
would be sufficient:

  1. His main profile holds every session he is signed into — bank, email,
     GitHub, everything. Handing that to an automated agent that reads pages
     containing attacker-controlled text is handing those sessions to the pages.
  2. Playwright cannot attach to a profile Chrome already holds locked. Trying
     it produces either a failure or a second Chrome fighting over the same
     directory.

Revoking everything she can reach in a browser is therefore `rmdir` on one
folder. That is the property worth having.

`channel="chrome"` drives his INSTALLED Chrome. No Chromium download, no
metered bytes. Verified: `C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe`.

HEADFUL, NOT HEADLESS, AND THAT IS DELIBERATE

He has to log into X himself, once, in this profile — which is impossible if the
window never appears. It also means every action she takes in the browser is
visible while it happens, on a machine where the alternative is an invisible
agent clicking things in a session he cannot watch.

EVERYTHING THIS MODULE RETURNS IS UNTRUSTED

Page text, accessible names, ARIA labels, alt text, search results, timelines.
All of it is `Provenance.EXTERNAL`. Handlers return it under `external_text`
with an `external_source`, and the executor fences it — see
`core/brain/executor.py` and `core/brain/provenance.py`. A live page is a worse
target than fetched text because it can hide instructions in places a text
fetch never sees, which is why `read_page` deliberately harvests the hidden
places too rather than only what a human would read.
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .base import ToolError, ToolHold

PROFILE_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Tessa" / "browser-profiles"
DEFAULT_PROFILE = PROFILE_ROOT / "default"

#: Where the live Chrome's PID is recorded, so a daemon that was force-killed
#: can reap the orphan it left behind on its next start. See `reap_orphan`.
PID_FILE = PROFILE_ROOT / "chrome.pid"

#: Close the browser after this long with no browser tool use.
#:
#: FIVE MINUTES, and the number comes from what it costs to keep open rather
#: than from taste — measured below in the report. Chromium is the heaviest
#: thing this daemon can hold on a 2-core machine where the Orb is already
#: rendering. Shorter and he pays the ~2 s cold launch repeatedly during one
#: task; longer and an idle browser sits on hundreds of megabytes all evening
#: because he asked one question at lunchtime.
IDLE_TIMEOUT_S = 300.0

#: Page loads on a metered link with two cores are not fast. This is generous
#: enough not to fail on a slow page and short enough that a dead link does not
#: hold the daemon that owns his microphone.
NAV_TIMEOUT_MS = 30_000

#: Cap on extracted page text. CONTRACT §1 caps a frame at 1 MiB, and a model
#: context is smaller than that anyway.
MAX_PAGE_CHARS = 40_000


class BrowserUnavailable(ToolError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

class BrowserSession:
    """
    One lazily-launched, persistently-profiled Chrome.

    LAZY, and the reason is arithmetic: launching at daemon start would add the
    cold-launch wall clock to every boot and hold Chromium's RSS for the entire
    session on a machine with 15.9 GB where the Orb renders at 30 fps — for a
    tool he might not use that day at all.

    THREE THINGS CLOSE IT, and I want the reasoning on record because the brief
    asked for a choice:

      * IDLE TIMEOUT — the common case. He asks one thing, wanders off, and the
        browser should not still be resident an hour later.
      * EXPLICIT INTENT ("close the browser") — because the idle timer is
        invisible to him, and a resource he can see in Task Manager but cannot
        dismiss by asking is one he will start killing by hand.
      * DAEMON SHUTDOWN — non-negotiable, item 2c. A headful Chrome that
        outlives the daemon is a process he did not start and cannot attribute,
        sitting in a profile with his X session in it.

    All three, not one. They cover different failure modes: the timer handles
    forgetting, the intent handles impatience, and shutdown handles the case
    that actually matters for trust.
    """

    def __init__(self) -> None:
        self._pw: Any = None
        self._ctx: Any = None
        self._lock = threading.RLock()
        self.last_used = 0.0
        self.launched_at = 0.0
        self.cold_launch_s = 0.0
        self.chrome_pid: int | None = None
        self._reaper: threading.Thread | None = None

    # ── launch ───────────────────────────────────────────────────────────────

    def _require_playwright(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserUnavailable(
                "Playwright is not installed",
                "The browser tools need it. Nothing else is affected.") from None
        return sync_playwright

    def context(self) -> Any:
        """The live context, launching Chrome on first use."""
        with self._lock:
            if self._ctx is not None:
                self.last_used = time.monotonic()
                return self._ctx

            sync_playwright = self._require_playwright()
            DEFAULT_PROFILE.mkdir(parents=True, exist_ok=True)
            t0 = time.perf_counter()
            self._pw = sync_playwright().start()
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(DEFAULT_PROFILE),
                    channel="chrome",          # HIS Chrome. No Chromium download.
                    headless=False,            # he must be able to log in himself
                    viewport={"width": 1280, "height": 720},
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as exc:  # noqa: BLE001
                self._shutdown_playwright()
                raise BrowserUnavailable(
                    f"Chrome would not start: {type(exc).__name__}",
                    "Close any Chrome running from this profile and ask me again.") from None

            self.cold_launch_s = time.perf_counter() - t0
            self.launched_at = time.monotonic()
            self.last_used = self.launched_at
            self._ctx.set_default_timeout(NAV_TIMEOUT_MS)
            self._record_pid()
            self._start_reaper()
            return self._ctx

    def _record_pid(self) -> None:
        """
        Write the browser process id to disk.

        THE FORCE-KILL HOLE, NAMED RATHER THAN PAPERED OVER: `atexit` does not
        run when the daemon is killed with `taskkill /F`, which is exactly how I
        kill daemons. In that case Chrome survives, and item 2c says it must
        never outlive the daemon. This file is how the NEXT daemon start finds
        and closes the orphan — see `reap_orphan`, called from server.py.
        """
        try:
            import psutil

            browser = getattr(self._ctx, "browser", None)
            proc = getattr(self._pw.chromium, "_connection", None)
            _ = browser, proc
            # Playwright does not expose the Chrome pid directly; find the
            # chrome.exe whose command line names OUR profile directory. This is
            # a targeted match on a path we own, not a match on an image name.
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                if (p.info["name"] or "").lower() != "chrome.exe":
                    continue
                cl = " ".join(p.info["cmdline"] or [])
                if str(DEFAULT_PROFILE).lower() in cl.lower():
                    self.chrome_pid = p.info["pid"]
                    break
            if self.chrome_pid:
                PID_FILE.write_text(json.dumps({"pid": self.chrome_pid,
                                                "profile": str(DEFAULT_PROFILE)}),
                                    encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _start_reaper(self) -> None:
        if self._reaper is not None and self._reaper.is_alive():
            return

        def run() -> None:
            while True:
                time.sleep(15.0)
                with self._lock:
                    if self._ctx is None:
                        return
                    if time.monotonic() - self.last_used > IDLE_TIMEOUT_S:
                        self.close(reason="idle")
                        return

        self._reaper = threading.Thread(target=run, name="tessa-browser-idle", daemon=True)
        self._reaper.start()

    # ── teardown ─────────────────────────────────────────────────────────────

    def _shutdown_playwright(self) -> None:
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = None

    def close(self, reason: str = "asked") -> dict[str, Any]:
        with self._lock:
            was_open = self._ctx is not None
            up = time.monotonic() - self.launched_at if self.launched_at else 0.0
            try:
                if self._ctx is not None:
                    self._ctx.close()
            except Exception:  # noqa: BLE001
                pass
            self._ctx = None
            self._shutdown_playwright()
            try:
                PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            self.chrome_pid = None
            return {"was_open": was_open, "reason": reason, "up_s": round(up, 1)}

    @property
    def is_open(self) -> bool:
        return self._ctx is not None

    def page(self) -> Any:
        ctx = self.context()
        pages = ctx.pages
        return pages[0] if pages else ctx.new_page()


SESSION = BrowserSession()
atexit.register(lambda: SESSION.close(reason="daemon shutdown"))


def reap_orphan() -> str:
    """
    Close a Chrome left behind by a daemon that was force-killed.

    Called at daemon start. Targets ONE recorded pid, verified to still be a
    chrome.exe running against OUR profile directory before anything is killed —
    the same discipline as `procs.kill`: a pid selected by image name is
    kill-by-name with extra steps.
    """
    try:
        rec = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    pid = int(rec.get("pid", 0))
    if not pid:
        return ""
    try:
        import psutil

        p = psutil.Process(pid)
        if p.name().lower() != "chrome.exe":
            return ""
        # THE OWNERSHIP TEST, and it is a path match rather than a safeproc
        # ancestry walk — deliberately, because ancestry CANNOT work here and
        # saying so is better than a check that looks rigorous and is not.
        #
        # `safeproc.owns()` asks "does this process descend from one I started".
        # An orphan by definition outlived the daemon that launched it, so its
        # parent chain is broken and the answer is always no. What IS provable
        # is that this chrome.exe was launched against TESSA'S OWN profile
        # directory — a path no Chrome of his will ever carry, because he has
        # never opened that folder. That is a stronger claim than image name and
        # it is the one that actually holds for an orphan.
        if str(DEFAULT_PROFILE).lower() not in " ".join(p.cmdline()).lower():
            return ""
        p.terminate()
        p.wait(timeout=5)
        PID_FILE.unlink(missing_ok=True)
        return f"reaped orphaned browser chrome.exe({pid}) from a previous run"
    except Exception:  # noqa: BLE001
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return ""


def status() -> dict[str, Any]:
    """RSS and CPU of the live browser, for the report and for PULSE."""
    out: dict[str, Any] = {"open": SESSION.is_open, "rss_mb": 0.0, "cpu_pct": 0.0,
                           "procs": 0, "cold_launch_s": round(SESSION.cold_launch_s, 2)}
    if not SESSION.is_open:
        return out
    try:
        import psutil

        rss = 0
        n = 0
        cpu = 0.0
        for p in psutil.process_iter(["name", "cmdline"]):
            if (p.info["name"] or "").lower() != "chrome.exe":
                continue
            if str(DEFAULT_PROFILE).lower() not in " ".join(p.info["cmdline"] or []).lower():
                continue
            try:
                rss += p.memory_info().rss
                cpu += p.cpu_percent(None)
                n += 1
            except Exception:  # noqa: BLE001
                continue
        out.update({"rss_mb": round(rss / 1e6, 1), "procs": n, "cpu_pct": round(cpu, 1)})
    except Exception:  # noqa: BLE001
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION — everything a page can hide an instruction in
# ─────────────────────────────────────────────────────────────────────────────

#: THE SCRIPT THAT MAKES THE FENCE HONEST.
#:
#: A text fetch sees rendered text. A live page can carry instructions in places
#: rendered text never reaches, and `browser.click` reads one of those places by
#: design — the accessibility tree. So extraction deliberately harvests:
#:
#:   visible text · display:none and visibility:hidden text · aria-label ·
#:   aria-description · aria-labelledby targets · alt · title · placeholder ·
#:   the accessible NAME of every interactive element
#:
#: All of it is fenced and scanned. Harvesting the hidden places is not
#: thoroughness for its own sake: an injection she cannot see is the only kind
#: worth planting, and one that lands in an accessible name is an injection
#: aimed squarely at `browser.click`.
_EXTRACT_JS = r"""
() => {
  const out = {visible: [], hidden: [], attrs: [], names: []};
  const push = (arr, s) => { if (s && s.trim()) arr.push(s.trim()); };

  const walk = (root) => {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let el = it.currentNode;
    while (el) {
      const st = window.getComputedStyle(el);
      const hidden = st.display === 'none' || st.visibility === 'hidden'
                     || st.opacity === '0' || el.hasAttribute('hidden')
                     || el.getAttribute('aria-hidden') === 'true';
      for (const a of ['alt','title','placeholder','aria-label','aria-description',
                       'aria-roledescription','data-tooltip']) {
        const v = el.getAttribute && el.getAttribute(a);
        if (v) push(out.attrs, a + '=' + v);
      }
      const lb = el.getAttribute && el.getAttribute('aria-labelledby');
      if (lb) {
        for (const id of lb.split(/\s+/)) {
          const t = document.getElementById(id);
          if (t) push(out.attrs, 'aria-labelledby=' + t.textContent);
        }
      }
      const tag = el.tagName.toLowerCase();
      const role = el.getAttribute && el.getAttribute('role');
      const interactive = ['a','button','input','select','textarea','summary'].includes(tag)
        || ['button','link','menuitem','tab','checkbox','radio','option'].includes(role || '');
      if (interactive) {
        const nm = (el.getAttribute('aria-label') || el.innerText || el.value
                    || el.getAttribute('title') || '').trim();
        if (nm) push(out.names, nm.slice(0, 200));
      }
      const own = Array.from(el.childNodes)
        .filter(n => n.nodeType === 3).map(n => n.nodeValue).join(' ');
      if (own && own.trim()) push(hidden ? out.hidden : out.visible, own);
      el = it.nextNode();
    }
  };
  walk(document.body || document.documentElement);
  return out;
}
"""


def _harvest(page: Any) -> dict[str, Any]:
    try:
        raw = page.evaluate(_EXTRACT_JS)
    except Exception:  # noqa: BLE001
        raw = {"visible": [], "hidden": [], "attrs": [], "names": []}
    visible = " ".join(raw.get("visible", []))[:MAX_PAGE_CHARS]
    hidden = " ".join(raw.get("hidden", []))[:MAX_PAGE_CHARS // 4]
    attrs = " | ".join(raw.get("attrs", []))[:MAX_PAGE_CHARS // 4]
    names = raw.get("names", [])
    # ONE STRING, fenced as a unit. The hidden and attribute sections are
    # LABELLED rather than merged silently, so when she reports what a page
    # tried, she can say WHERE it tried it.
    combined = (
        f"{visible}\n\n"
        f"[HIDDEN ELEMENTS ON THIS PAGE]\n{hidden}\n\n"
        f"[ATTRIBUTES: alt/title/aria]\n{attrs}\n\n"
        f"[ACCESSIBLE NAMES OF CLICKABLE ELEMENTS]\n{' | '.join(names[:200])}"
    )
    return {"visible": visible, "hidden": hidden, "attrs": attrs,
            "names": names, "combined": combined}


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS
# ─────────────────────────────────────────────────────────────────────────────

def _goto(page: Any, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"{url} did not load ({type(exc).__name__})",
                        "Check the address, or your connection.") from None


def open_url(url: str) -> dict[str, Any]:
    u = str(url or "").strip()
    if not u:
        raise ToolError("no address came through", "Say the site and I will open it.")
    # `file://` and `about:` are schemes too. The first version prepended
    # https:// to everything that was not http(s), which turned a local test
    # fixture into "https://file:///C:/..." and failed to load. Caught by the
    # injection fixture, which is exactly the kind of thing it should catch.
    if not u.startswith(("http://", "https://", "file://", "about:")):
        u = "https://" + u
    page = SESSION.page()
    _goto(page, u)
    title = (page.title() or "")[:120]
    return {
        "url": page.url, "title": title,
        "cold_launch_s": round(SESSION.cold_launch_s, 2),
        # THE TITLE IS ATTACKER-CONTROLLED AND SHE SPEAKS IT. Opening a page
        # used to leave the fence at zero while reading that page's <title>
        # aloud — so a hostile site could put a sentence in her mouth AND the
        # next amber action would still fire. Navigating anywhere is now itself
        # an external-content event, which is the honest model: she has been to
        # the page, whether or not she has read the body.
        "external_source": page.url,
        "external_text": f"[PAGE TITLE] {title}",
    }


def close_browser(reason: str = "asked") -> dict[str, Any]:
    """
    `reason` exists because server.py passes one on shutdown.

    IT DID NOT, AND THAT WAS A REAL BUG: the shutdown path called
    `close_browser(reason="daemon shutdown")` against a zero-argument function,
    raising TypeError inside the daemon's shutdown tail — which would have
    skipped the `daemon.stop` audit entry AND `rt.remove_runtime_file()`,
    leaving a stale runtime.json every clean exit. Found by review, confirmed by
    calling it.
    """
    return SESSION.close(reason=reason)


#: DUCKDUCKGO'S HTML ENDPOINT, and the argument for it over Google:
#:
#: Google detects automation aggressively and answers with a consent wall or a
#: CAPTCHA. Neither is a search result, and a search tool whose common outcome
#: is "solve this puzzle" is a tool he stops trusting. DuckDuckGo's `html`
#: endpoint is a server-rendered results page with stable, semantic markup — no
#: JavaScript required to read it, no consent interstitial, and materially more
#: automation-tolerant.
#:
#: The honest cost: DuckDuckGo's result quality on obscure technical queries is
#: below Google's, and it does still rate-limit and can still present an
#: anomaly page. It is the better DEFAULT, not a way around the problem — which
#: is why the block path below is built rather than assumed away.
SEARCH_URL = "https://html.duckduckgo.com/html/?q="

#: Signals that the engine is refusing rather than answering. Detected so she
#: can SAY SO — never so she can work around it.
_BLOCK_MARKERS = (
    "unusual traffic", "are you a robot", "captcha", "recaptcha",
    "verify you are human", "detected unusual", "automated queries",
    "anomaly", "blocked", "rate limit", "too many requests",
)


def search(query: str, limit: int = 5) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        raise ToolError("no search terms came through", "Tell me what to look for.")
    from urllib.parse import quote_plus

    page = SESSION.page()
    _goto(page, SEARCH_URL + quote_plus(q))

    body = (page.inner_text("body") or "") if page else ""
    low = body.lower()
    hit = next((m for m in _BLOCK_MARKERS if m in low), None)
    results: list[dict[str, str]] = []
    try:
        for el in page.query_selector_all("a.result__a")[:limit]:
            t = (el.inner_text() or "").strip()
            href = el.get_attribute("href") or ""
            if t:
                results.append({"title": t[:160], "url": href})
    except Exception:  # noqa: BLE001
        pass

    if not results and hit:
        # SHE STOPS. No guessing, no partial page presented as results, and no
        # attempt at the challenge. A search tool that returns something
        # plausible when it was actually blocked is worse than one that fails.
        raise ToolError(
            f"the search engine blocked me — the page mentions {hit!r}",
            "It thinks I am a robot, which I am. Search it yourself and I will "
            "read the page you land on.")
    if not results:
        raise ToolError("the results page had nothing I could read",
                        "Try different words, or open the site directly.")

    return {"n": len(results), "query": q, "results": results,
            "first": results[0]["title"],
            "head": "; ".join(r["title"][:60] for r in results[:3]),
            "external_source": f"duckduckgo search for {q!r}",
            "external_text": "\n".join(f"{r['title']} — {r['url']}" for r in results)}


def read_page(url: str | None = None) -> dict[str, Any]:
    page = SESSION.page()
    if url:
        u = str(url).strip()
        if not u.startswith(("http://", "https://", "file://", "about:")):
            u = "https://" + u
        _goto(page, u)
    got = _harvest(page)
    return {
        "url": page.url, "title": (page.title() or "")[:120],
        "chars": len(got["combined"]), "visible_chars": len(got["visible"]),
        "hidden_chars": len(got["hidden"]), "attr_chars": len(got["attrs"]),
        "names": len(got["names"]),
        "external_source": page.url,
        "external_text": got["combined"],
    }


def screenshot(path: str | None = None) -> dict[str, Any]:
    page = SESSION.page()
    target = Path(path) if path else (
        Path(os.environ.get("LOCALAPPDATA", ".")) / "Tessa" / "screenshots"
        / f"shot-{int(time.time())}.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target), full_page=False)
    return {"path": str(target), "name": target.name, "url": page.url}


def click(name: str | None = None, selector: str | None = None) -> dict[str, Any]:
    """
    AMBER. By accessible name, or by an explicit selector.

    IF IT CANNOT FIND IT, IT FAILS. It does not click the nearest thing, and it
    does not click the first of several. On a page — and especially on a
    timeline — the element next to the one he meant is a public action.
    """
    page = SESSION.page()
    if selector:
        el = page.query_selector(selector)
        if el is None:
            raise ToolError(f"nothing on this page matches {selector!r}",
                            "Tell me what the button says instead.")
        el.click()
        return {"what": selector, "how": "selector", "url": page.url}

    n = str(name or "").strip()
    if not n:
        raise ToolError("no button name came through", "Tell me what it says.")
    loc = page.get_by_role("button", name=n).or_(page.get_by_role("link", name=n))
    count = loc.count()
    if count == 0:
        loc = page.get_by_text(n, exact=False)
        count = loc.count()
    if count == 0:
        raise ToolError(f"I cannot find anything called {n!r} on this page",
                        "Read me what it actually says and I will try that.")
    if count > 1:
        raise ToolError(f"{count} things on this page are called {n!r}",
                        "Which one? I will not guess on a page.")
    loc.first.click()
    return {"what": n, "how": "accessible name", "url": page.url}


def type_text(field: str, text: str) -> dict[str, Any]:
    """AMBER. Into a NAMED field. Same no-guessing rule as click."""
    page = SESSION.page()
    f = str(field or "").strip()
    loc = page.get_by_label(f) if f else None
    if loc is None or loc.count() == 0:
        loc = page.get_by_placeholder(f)
    if loc.count() == 0:
        loc = page.get_by_role("textbox", name=f)
    if loc.count() == 0:
        raise ToolError(f"there is no field called {f!r} on this page",
                        "Tell me the label as it appears.")
    if loc.count() > 1:
        raise ToolError(f"{loc.count()} fields are called {f!r}", "Which one?")
    loc.first.fill(str(text or ""))
    return {"field": f, "chars": len(str(text or "")), "url": page.url}


def submit(confirmed: bool = False) -> dict[str, Any]:
    """
    RED (spec §7.2), and it HOLDS — and then it still does not run.

    See `core/tools/__init__.py`: red tools execute ONLY through
    `cmd.permission.respond`, never from voice. The hold below is what he hears
    if this handler is ever reached directly; the gate above it is what actually
    stops it.
    """
    page = SESSION.page()
    if not confirmed:
        raise ToolHold(f"submitting this form on {page.url}")
    page.keyboard.press("Enter")
    return {"url": page.url}
