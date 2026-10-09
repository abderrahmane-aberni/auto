"""Download the current UK Register of Licensed Sponsors (Workers) CSV.

Run this yourself, locally - it needs to reach www.gov.uk and
assets.publishing.service.gov.uk, and neither host is reachable from the
sandboxed environment this project was otherwise built in (confirmed: both
hosts come back connection-refused from that sandbox's egress proxy). This
is the same "you run the live step, I write the code" split used for
Recall's eval scripts.

The register changes monthly, so re-run this occasionally - it's not called
from anywhere in the agent loop itself (see README "Why the register isn't
fetched live").

Usage:
    python scripts/update_sponsor_register.py
    python scripts/update_sponsor_register.py --out data/sponsor_register.csv
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import requests

PUBLICATION_PAGE = (
    "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
)
CSV_LINK_RE = re.compile(r'https://assets\.publishing\.service\.gov\.uk[^"\'\s]+\.csv')


def find_csv_url() -> str:
    resp = requests.get(PUBLICATION_PAGE, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    matches = CSV_LINK_RE.findall(resp.text)
    if not matches:
        raise RuntimeError(
            "Couldn't find a .csv asset link on the publication page. gov.uk "
            "may have changed the page layout - open "
            f"{PUBLICATION_PAGE} in a browser and pass the CSV URL directly "
            "with --csv-url instead."
        )
    return matches[0]


def download(csv_url: str, out_path: Path) -> int:
    resp = requests.get(csv_url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    # Count rows for a sanity-check print, not for anything structural.
    text = resp.content.decode("utf-8-sig", errors="replace")
    return max(0, text.count("\n") - 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/sponsor_register.csv")
    parser.add_argument(
        "--csv-url",
        default=None,
        help="Skip page scraping and download this URL directly (use if gov.uk changes its page layout).",
    )
    args = parser.parse_args(argv)

    try:
        csv_url = args.csv_url or find_csv_url()
        print(f"Downloading {csv_url}")
        row_count = download(csv_url, Path(args.out))
    except requests.exceptions.RequestException as exc:
        print(f"Network error reaching gov.uk: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Wrote {args.out} (~{row_count} rows).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
