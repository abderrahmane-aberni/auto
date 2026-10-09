"""URL normalization + duplicate detection. Pure functions, no I/O - the
actual DB lookup lives in tools.py so this stays unit-testable in isolation.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

# Query params that vary between otherwise-identical copies of the same job
# posting URL (ad campaign/referral tracking) - stripped before comparing.
_TRACKING_PARAM_PREFIXES = ("utm_", "ref", "src", "gh_src", "trk", "fbclid", "gclid")


def normalize_url(url: str) -> str:
    """Collapse cosmetic/tracking differences so the same posting shared via
    two different links (e.g. with vs without a UTM campaign tag) normalizes
    to the same string.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/") or "/"

    kept_params = []
    for pair in parts.query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0].lower()
        if any(key.startswith(p) for p in _TRACKING_PARAM_PREFIXES):
            continue
        kept_params.append(pair)
    query = "&".join(sorted(kept_params))

    return urlunsplit((scheme, netloc, path, query, ""))
