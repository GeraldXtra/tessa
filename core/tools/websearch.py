"""
core/tools/websearch.py — a search that does NOT launch Chrome.

WHY THIS EXISTS WHEN `browser.search` ALREADY DOES

`browser.search` is correct and it costs 1.5 s of cold launch and ~560 MB of
resident Chrome. That is the right price for "click the third result and read
it". It is an absurd price for "what is the weather", which is the exact
question Gerald asked and got "not yet" for.

DuckDuckGo's `html` endpoint is server-rendered: no JavaScript, no consent
interstitial, stable semantic markup. `urllib` can read it in one request. So
the cheap path handles the common case — a factual question whose answer is on
the open web — and the browser stays for the cases that genuinely need a live
DOM.

EVERYTHING THIS RETURNS IS UNTRUSTED. Result titles and snippets are strangers'
text. They come back under `external_text` and the executor fences them, exactly
like a page read. A search result that says "ignore your previous instructions"
is data.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import quote_plus, unquote
from urllib.request import Request, urlopen

from .base import ToolError

SEARCH_URL = "https://html.duckduckgo.com/html/?q="

#: A browser-shaped UA. Not deception — the endpoint serves a different, poorer
#: page to obvious scripts, and the point is to read what a person would read.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

TIMEOUT_S = 12.0
MAX_BYTES = 800_000

#: The engine refusing rather than answering. Detected so she can SAY SO —
#: never so she can work around it.
_BLOCK_MARKERS = ("unusual traffic", "are you a robot", "captcha", "recaptcha",
                  "verify you are human", "automated queries", "anomaly",
                  "rate limit", "too many requests")

_RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.I | re.S)
_SNIPPET_RE = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snip>.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(html: str) -> str:
    return unescape(_TAG_RE.sub("", html)).strip()


def _real_url(href: str) -> str:
    """DuckDuckGo wraps results in a redirect: /l/?uddg=<encoded>."""
    m = re.search(r"uddg=([^&]+)", href)
    return unquote(m.group(1)) if m else href


def search(query: str, limit: int = 4) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        raise ToolError("no search terms came through", "Tell me what to look for.")

    req = Request(SEARCH_URL + quote_plus(q), headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 — scheme is fixed
            raw = resp.read(MAX_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"I could not reach the search engine ({type(exc).__name__})",
                        "Check your connection and ask me again.") from None

    html = raw.decode(charset, errors="replace")
    low = html.lower()

    titles = [(_clean(m.group("title")), _real_url(m.group("href")))
              for m in _RESULT_RE.finditer(html)]
    snippets = [_clean(m.group("snip")) for m in _SNIPPET_RE.finditer(html)]

    results = []
    for i, (title, url) in enumerate(titles[:limit]):
        if not title:
            continue
        results.append({"title": title[:160], "url": url,
                        "snippet": (snippets[i][:300] if i < len(snippets) else "")})

    if not results:
        hit = next((m for m in _BLOCK_MARKERS if m in low), None)
        if hit:
            # SHE STOPS. No guessing, no partial page dressed as results, and no
            # attempt at the challenge.
            raise ToolError(
                f"the search engine blocked me — the page mentions {hit!r}",
                "It thinks I am a robot, which I am. Search it yourself and I "
                "will read the page you land on.")
        raise ToolError("the results page had nothing I could read",
                        "Try different words, or ask me to open the site directly.")

    # SPOKEN ANSWER: the top snippet, because for "what is the weather" the
    # answer is usually IN the snippet and reading four link titles aloud is
    # not an answer to a spoken question.
    lead = results[0]["snippet"] or results[0]["title"]
    # TERMINAL PUNCTUATION IS GUARANTEED HERE, not hoped for. `lead` is a
    # stranger's snippet truncated at 280 characters: it routinely ends
    # mid-clause, and Piper given no full stop runs straight into whatever
    # follows with no pause. This is the same defect that made `action_failed`
    # merge two sentences, one layer out.
    lead = lead.strip()
    if lead and lead[-1] not in ".?!":
        lead += "."
    return {
        "n": len(results), "query": q, "results": results,
        "first": results[0]["title"], "lead": lead[:280],
        "head": "; ".join(r["title"][:60] for r in results[:3]),
        "external_source": f"duckduckgo search for {q!r}",
        "external_text": "\n".join(
            f"{r['title']} — {r['url']}\n{r['snippet']}" for r in results),
    }
