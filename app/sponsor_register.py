"""Cross-reference a company name against the UK Home Office's public
Register of Licensed Sponsors (Workers).

The register is a CSV published at a gov.uk URL that changes over time (see
scripts/update_sponsor_register.py) and this sandbox's network can't reach
gov.uk at all (confirmed: both this cloud container and the user's own
machine get connection-refused from the egress proxy on www.gov.uk and
assets.publishing.service.gov.uk). So this module never fetches the register
itself - it only reads a CSV already on disk, and the download step is a
separate script the user runs locally (same pattern as Recall's eval scripts,
which also needed network this sandbox doesn't have).

Matching is fuzzy on purpose: the register lists legal entity names
("ACME TECHNOLOGY LIMITED") while job postings name companies colloquially
("Acme", "Acme Tech"). An exact-string lookup would miss almost everything.
"""
from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Legal-entity suffixes that would otherwise dominate the similarity score
# without carrying any identifying signal.
_SUFFIX_RE = re.compile(
    r"\b(limited|ltd|plc|llp|lp|inc|incorporated|corp|corporation|co|company|group|holdings|uk)\b\.?",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# The register's published column header has been renamed at least once by
# the Home Office; accept either spelling rather than hard-failing on a
# header change that doesn't actually affect the data we need.
_NAME_COLUMNS = ("Organisation Name", "Organisation name", "Name of Organisation")


def normalize_company_name(name: str) -> str:
    name = name.strip().lower()
    name = _SUFFIX_RE.sub(" ", name)
    name = _PUNCT_RE.sub(" ", name)
    name = _WS_RE.sub(" ", name).strip()
    return name


# Below this length, a "match" is essentially coincidental. A found-in-
# production bug: "DataAnnotation" fuzzy-matched "AA LIMITED" at 100%
# confidence, because once "AA LIMITED" is suffix-stripped down to "aa",
# those two letters just happen to appear back-to-back inside
# "dataannotation" ("dat-AA-nnotation"). Any 2-3 character string will turn
# up *somewhere* in almost any sufficiently long string by pure chance - the
# windowed/substring boost below is only meaningful evidence once the
# fragment being matched is long enough that a coincidental hit becomes
# unlikely. Below this, only the plain whole-string ratio is used, which
# correctly scores "aa" vs "dataannotation" as a poor match (their lengths
# are too different for a high ratio).
_MIN_FRAGMENT_LEN_FOR_PARTIAL_MATCH = 4


def _windowed_partial_ratio(shorter: str, longer: str) -> float:
    """How well `shorter` matches *some* substring of `longer`, not just the
    whole string. Job postings routinely refer to a company by a colloquial
    abbreviation ("Stripe") that's a clean prefix/substring of the register's
    full legal name ("STRIPE PAYMENTS UK LIMITED") - a plain whole-string
    ratio scores that low purely because of the length difference, which
    would make the register check nearly useless in practice.

    Only called when `shorter` is at least `_MIN_FRAGMENT_LEN_FOR_PARTIAL_MATCH`
    characters - see that constant's comment for why.
    """
    if not shorter:
        return 0.0
    if shorter in longer:
        return 100.0
    best = 0.0
    window_len = len(shorter)
    matcher = difflib.SequenceMatcher(None, longer, shorter, autojunk=False)
    for block in matcher.get_matching_blocks():
        if block.size == 0:
            continue
        start = max(0, block.a - block.b)
        window = longer[start : start + window_len]
        if not window:
            continue
        ratio = difflib.SequenceMatcher(None, window, shorter).ratio() * 100
        if ratio > best:
            best = ratio
    return best


def _similarity_score(query_norm: str, candidate_norm: str) -> float:
    full_ratio = difflib.SequenceMatcher(None, query_norm, candidate_norm).ratio() * 100
    if len(query_norm) <= len(candidate_norm):
        shorter, longer = query_norm, candidate_norm
    else:
        shorter, longer = candidate_norm, query_norm
    if len(shorter) < _MIN_FRAGMENT_LEN_FOR_PARTIAL_MATCH:
        return full_ratio
    partial_ratio = _windowed_partial_ratio(shorter, longer)
    return max(full_ratio, partial_ratio)


@dataclass(frozen=True)
class SponsorMatch:
    query: str
    matched_name: str | None
    score: float  # 0-100
    licensed: bool

    @property
    def is_confident(self) -> bool:
        return self.matched_name is not None


class SponsorRegister:
    def __init__(self, entries: list[str]) -> None:
        # entries: original (non-normalized) organisation names, as published.
        self._entries = entries
        self._normalized = [normalize_company_name(e) for e in entries]

    def __len__(self) -> int:
        return len(self._entries)

    def lookup(self, company_name: str, *, threshold: int = 85) -> SponsorMatch:
        if not company_name or not company_name.strip():
            return SponsorMatch(query=company_name, matched_name=None, score=0.0, licensed=False)

        query_norm = normalize_company_name(company_name)
        best_score = 0.0
        best_idx: int | None = None
        for idx, candidate in enumerate(self._normalized):
            if not candidate:
                continue
            score = _similarity_score(query_norm, candidate)
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None and best_score >= threshold:
            return SponsorMatch(
                query=company_name,
                matched_name=self._entries[best_idx],
                score=round(best_score, 1),
                licensed=True,
            )
        return SponsorMatch(
            query=company_name,
            matched_name=None,
            score=round(best_score, 1),
            licensed=False,
        )


def load_sponsor_register(csv_path: str | Path) -> SponsorRegister:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Sponsor register CSV not found at {path}. Run "
            "`python scripts/update_sponsor_register.py` first (needs network "
            "access to gov.uk, which this project's own agent run deliberately "
            "doesn't require - see README 'Updating the sponsor register')."
        )
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        name_col = next((c for c in _NAME_COLUMNS if c in (reader.fieldnames or [])), None)
        if name_col is None:
            raise ValueError(
                f"Couldn't find an organisation-name column in {path}. Looked for "
                f"any of {_NAME_COLUMNS}, found columns: {reader.fieldnames}"
            )
        entries = [row[name_col].strip() for row in reader if row.get(name_col, "").strip()]
    return SponsorRegister(entries)


@lru_cache(maxsize=1)
def _cached_register(csv_path: str) -> SponsorRegister:
    return load_sponsor_register(csv_path)


def get_cached_register(csv_path: str | Path) -> SponsorRegister:
    """Cached loader - the real register is ~150k rows; re-parsing the CSV on
    every single job posting inside a batch run would be wasteful.
    """
    return _cached_register(str(csv_path))
