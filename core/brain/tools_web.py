"""
core/brain/tools_web.py — web.fetch, web.check_reputation, file.read.

NONE OF THESE NEEDS A MODEL, and that is the point. The SUMMARY of a page needs
a brain; the FETCH does not. Whether a domain was registered eleven days ago is
a FACT, not a judgement — and it is the fact that actually decides whether a
site is a scam. A model asked "is this a scam" without those facts is guessing
from the prose style, which is exactly what a scammer optimises.

So these work today, offline of any brain, and they keep working the day the
brain changes.

EVERYTHING FETCHED IS EXTERNAL. Every return path here goes through
`core.brain.provenance`: Provenance.EXTERNAL, the per-source nonce fence, and
`external_content_in_context` incremented — so a red-tier action cannot fire
while a fetched page is in context, no matter how persuasive the page is.
"""

from __future__ import annotations

import re
import socket
import ssl
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .provenance import ExternalContent, SessionContext

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ZoeyOS/0.1 (+local assistant)"
FETCH_TIMEOUT_S = 20
MAX_FETCH_BYTES = 4 * 1024 * 1024


# ── web.fetch ────────────────────────────────────────────────────────────────


class _Reader(HTMLParser):
    """
    Minimal readable-text extractor.

    No BeautifulSoup, no readability-lxml: both are downloads on a metered link
    to do something stdlib can do adequately. `script`, `style`, `nav` and
    `footer` are dropped because they are the bulk of a modern page and none of
    it is what he asked about.
    """

    _SKIP = {"script", "style", "noscript", "nav", "footer", "svg", "head"}
    _BREAK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()
            return
        if self._skip_depth:
            return
        t = data.strip()
        if t:
            self.parts.append(t + " ")

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


@dataclass
class Fetched:
    url: str
    title: str
    text: str
    status: int
    bytes_in: int
    chars_out: int
    injection_patterns: list[str] = field(default_factory=list)


def web_fetch(url: str, ctx: SessionContext) -> Fetched:
    """GREEN. Fetch and extract. The result is EXTERNAL and fenced."""
    if not re.match(r"^https?://", url):
        url = "https://" + url
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:  # noqa: S310 - scheme checked
        raw = resp.read(MAX_FETCH_BYTES)
        status = resp.status
        charset = resp.headers.get_content_charset() or "utf-8"
    html = raw.decode(charset, errors="replace")
    parser = _Reader()
    parser.feed(html)
    text = parser.text()

    content = ExternalContent(source=url, text=text)
    fired = ctx.load_external(content)
    return Fetched(url=url, title=parser.title, text=text, status=status,
                   bytes_in=len(raw), chars_out=len(text), injection_patterns=fired)


# ── web.check_reputation ─────────────────────────────────────────────────────


@dataclass
class Reputation:
    domain: str
    checked: list[str] = field(default_factory=list)
    could_not_check: list[str] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)

    def spoken(self) -> str:
        """
        FACTS, in her voice, with the limits stated.

        Deliberately NOT a verdict. "This is a scam" is a judgement she cannot
        support from WHOIS and a certificate, and a confident wrong verdict is
        worse than the facts — he can weigh "registered eleven days ago" himself
        and it is true regardless of what any model thinks.
        """
        bits = [self.facts[k] for k in
                ("registered", "cert", "https", "blocklist") if k in self.facts]
        out = " ".join(bits) if bits else "I could not establish anything."
        if self.could_not_check:
            out += f" I could not check {', '.join(self.could_not_check)}."
        return out


def _whois_age(domain: str) -> tuple[str | None, str | None]:
    """
    Creation date. RDAP first, `whois` CLI as fallback.

    RDAP IS THE PRIMARY because there is no `whois` client on this machine —
    measured, not assumed: the first version of this function returned
    "no whois client" for every domain, which silently removed the single most
    useful scam signal there is. A site registered eleven days ago is the fact
    that decides the question, and losing it to a missing binary would have made
    this tool decorative.

    RDAP is an HTTPS JSON API run by the registries themselves, needs no
    dependency and no key, and returns a structured registration date rather
    than free text to regex at.
    """
    import json as _json

    try:
        req = Request(f"https://rdap.org/domain/{domain}",
                      headers={"User-Agent": USER_AGENT, "Accept": "application/rdap+json"})
        with urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed https host
            data = _json.loads(resp.read(512 * 1024).decode("utf-8", "replace"))
        for ev in data.get("events", []):
            if ev.get("eventAction") in ("registration", "created"):
                return str(ev.get("eventDate", ""))[:10], None
        return None, "domain age (RDAP returned no registration event)"
    except Exception:  # noqa: BLE001 - fall through to whois
        pass

    try:
        res = subprocess.run(["whois", domain], capture_output=True, text=True, timeout=25)
    except FileNotFoundError:
        return None, "domain age (RDAP unreachable and no whois client installed)"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"domain age ({type(exc).__name__})"
    m = re.search(r"(?:Creation Date|created|Registered on)\s*:?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
                  res.stdout, re.I)
    return (m.group(1), None) if m else (None, "domain age (not in the whois response)")


def _cert_info(host: str) -> tuple[dict[str, str] | None, str | None]:
    ctxs = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=15) as sock:
            with ctxs.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
    except Exception as exc:  # noqa: BLE001
        return None, f"the certificate ({type(exc).__name__})"
    issuer = dict(x[0] for x in cert.get("issuer", ()) if x)
    return {
        "not_before": cert.get("notBefore", ""),
        "not_after": cert.get("notAfter", ""),
        "issuer": issuer.get("organizationName", "unknown"),
    }, None


def _days_since(iso_or_cert: str) -> int | None:
    for fmt in ("%Y-%m-%d", "%b %d %H:%M:%S %Y %Z"):
        try:
            dt = datetime.strptime(iso_or_cert.strip(), fmt).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).days
        except ValueError:
            continue
    return None


def check_reputation(url: str) -> Reputation:
    """GREEN. Facts only, and it says what it could not establish."""
    host = urlparse(url if "://" in url else f"https://{url}").hostname or url
    rep = Reputation(domain=host)

    created, err = _whois_age(host)
    if created:
        days = _days_since(created)
        rep.checked.append("domain age")
        rep.facts["registered"] = (
            f"Registered {days} days ago." if days is not None
            else f"Registered {created}.")
    elif err:
        rep.could_not_check.append(err)

    cert, cerr = _cert_info(host)
    if cert:
        rep.checked.append("certificate")
        days = _days_since(cert["not_before"])
        rep.facts["cert"] = (
            f"Certificate issued {days} days ago by {cert['issuer']}."
            if days is not None else f"Certificate issued by {cert['issuer']}.")
        rep.facts["https"] = "HTTPS is valid."
    elif cerr:
        rep.could_not_check.append(cerr)
        rep.facts["https"] = "HTTPS did not validate."

    # Blocklist: DNS against a public blackhole list.
    #
    # THE RETURNED ADDRESS IS THE ANSWER, not the fact that it resolved. The
    # first version of this treated "gethostbyname succeeded" as "listed", and
    # measured against real sites it reported anthropic.com AND github.com as
    # blocklisted — because these lists answer unauthenticated or rate-limited
    # queries with 127.0.0.1, which resolves perfectly well. A reputation tool
    # that falsely accuses legitimate domains is worse than no tool: he would
    # stop believing it on the one occasion it was right.
    #
    # DNSBL convention: 127.0.0.2-127.0.0.254 encode a listing reason;
    # 127.0.0.1 conventionally means "query refused / not a listing".
    try:
        addr = socket.gethostbyname(f"{host}.multi.uribl.com")
        if addr == "127.0.0.1":
            rep.could_not_check.append("blocklists (the list refused the query)")
        elif addr.startswith("127.0.0."):
            rep.facts["blocklist"] = f"It appears on a URI blocklist ({addr})."
            rep.checked.append("blocklist")
        else:
            rep.could_not_check.append(f"blocklists (unexpected answer {addr})")
    except socket.gaierror:
        rep.facts["blocklist"] = "No blocklist match."
        rep.checked.append("blocklist")
    except Exception:  # noqa: BLE001
        rep.could_not_check.append("blocklists")
    return rep


# ── file.read ────────────────────────────────────────────────────────────────


@dataclass
class ReadFile:
    path: str
    chunks: list[str]
    total_chars: int
    truncated: bool = False   # ALWAYS False. See below.

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


def file_read(path: str | Path, ctx: SessionContext, chunk_chars: int = 12_000) -> ReadFile:
    """
    GREEN. Read a document to text, CHUNKED — never truncated.

    An ebook does not fit in a context window, and the tempting shortcut is to
    read the first N characters and summarise those as though they were the
    book. That produces a confident summary of chapter one labelled as a summary
    of the whole, which is a lie he has no way to detect. So the file is split
    and every chunk is kept; `truncated` is always False because nothing here
    ever truncates.
    """
    p = Path(path)
    raw = p.read_bytes()
    for enc in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")

    chunks = [text[i:i + chunk_chars] for i in range(0, len(text), chunk_chars)] or [""]
    ctx.load_external(ExternalContent(source=str(p), text=text[:2000]))
    return ReadFile(path=str(p), chunks=chunks, total_chars=len(text))
