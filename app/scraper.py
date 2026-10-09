"""Fetch + parse a job posting page.

Split deliberately into a network half (`fetch_url`) and a pure parsing half
(`parse_job_html`), the same separation Recall used for ingestion - the
parsing logic is what actually has bugs worth catching in tests, and it can
be tested against saved fixture HTML without ever touching the network.

Real job boards are messy and inconsistent (this is the "basic scraping of
messy real-world HTML" the roadmap calls out), so this does NOT try to find
structured fields itself - that's the LLM extraction step's job. All this
module does is turn a page into clean, boilerplate-stripped plain text for
the LLM to read.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (compatible; AutoJobAgent/1.0; "
    "+https://github.com/abderrahmane-aberni/auto)"
)

# Tags that are reliably boilerplate on every job board tried during
# development (nav chrome, share widgets) - stripped before text extraction
# so the LLM isn't fed a page full of "Sign in | Register".
_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "noscript", "form", "svg")

# A plain HTML parser (no CSS/JS execution) can't tell a CSS-hidden cookie
# banner from visible content, and board-to-board markup never agrees on a
# tag for this - so instead of tags, this matches on id/class keywords that
# were genuinely seen on real job-board boilerplate during manual testing.
_BOILERPLATE_KEYWORDS = (
    "cookie",
    "banner",
    "modal",
    "popup",
    "newsletter",
    "subscribe",
    "share-form",
    "social-share",
)

# Hard cap on how much page text gets passed to the LLM. A handful of job
# boards (seen during manual testing) inline an entire related-jobs rail or
# a company's full "About us" essay below the actual posting; past this
# length it's almost always noise, and it protects the extraction call's
# token budget.
MAX_TEXT_CHARS = 12000


class FetchError(Exception):
    """Raised for anything that stops us from getting back a 200 + HTML body."""


@dataclass
class FetchedPage:
    url: str
    final_url: str  # after redirects
    status_code: int
    html: str


def fetch_url(url: str, *, timeout_seconds: int = 15) -> FetchedPage:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_seconds,
            allow_redirects=True,
        )
    except requests.exceptions.Timeout as exc:
        raise FetchError(f"Timed out after {timeout_seconds}s fetching {url}") from exc
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"Network error fetching {url}: {exc}") from exc

    if resp.status_code >= 400:
        raise FetchError(f"{url} returned HTTP {resp.status_code}")

    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type.lower():
        raise FetchError(f"{url} returned non-HTML content-type: {content_type!r}")

    return FetchedPage(url=url, final_url=resp.url, status_code=resp.status_code, html=resp.text)


@dataclass
class ParsedJobPage:
    title: str
    text: str


def parse_job_html(html: str) -> ParsedJobPage:
    """Pure function: HTML string in, cleaned title + body text out.

    Raises ValueError on genuinely empty pages (e.g. a JS-only React shell
    that never got the chance to render - a real failure mode, documented in
    README "Failure cases") so the caller can tell "nothing to extract" apart
    from a successful-but-boring page.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for tag in soup.find_all(True):
        identifiers = f"{tag.get('id', '')} {' '.join(tag.get('class', []))}".lower()
        if any(keyword in identifiers for keyword in _BOILERPLATE_KEYWORDS):
            tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    cleaned = "\n".join(lines)

    if len(cleaned) < 50:
        raise ValueError(
            "Parsed page has almost no text content (possibly a JavaScript-"
            "rendered page that returned an empty shell to a plain HTTP GET)."
        )

    if len(cleaned) > MAX_TEXT_CHARS:
        cleaned = cleaned[:MAX_TEXT_CHARS]

    return ParsedJobPage(title=title, text=cleaned)


def fetch_job_posting(url: str, *, timeout_seconds: int = 15) -> ParsedJobPage:
    page = fetch_url(url, timeout_seconds=timeout_seconds)
    return parse_job_html(page.html)
