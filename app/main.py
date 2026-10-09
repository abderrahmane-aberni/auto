"""CLI entrypoint. Three subcommands:

    python -m app.main process-url <url>       - run the agent on one URL
    python -m app.main process-inbox           - run the agent on every new
                                                   (non-duplicate) URL in
                                                   inbox/urls.txt
    python -m app.main report                  - write reports/latest-summary.md

`process-inbox` + `report` is what the GitHub Actions workflow runs on a
schedule - see .github/workflows/weekly-agent.yml.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.agent import run_agent
from app.config import get_settings
from app.db import get_session, init_db
from app.reports import generate_summary_markdown


def _read_inbox(path: Path) -> list[str]:
    if not path.exists():
        return []
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def cmd_process_url(url: str) -> int:
    settings = get_settings()
    settings.validate()
    init_db()
    db = get_session()
    try:
        posting, steps = run_agent(url, db=db, settings=settings)
    finally:
        db.close()

    if posting is None:
        print(f"[skip] {url} - duplicate or no new row written ({steps[-1].tool_result_summary if steps else ''})")
        return 0
    print(f"[ok] {url} -> job_posting #{posting.id}: {posting.role!r} at {posting.company!r} "
          f"(sponsor_licensed={posting.sponsor_licensed}, status={posting.status})")
    return 0


def cmd_process_inbox(inbox_path: str) -> int:
    settings = get_settings()
    settings.validate()
    init_db()
    urls = _read_inbox(Path(inbox_path))
    if not urls:
        print(f"No URLs in {inbox_path}.")
        return 0

    db = get_session()
    ok, dup, err = 0, 0, 0
    try:
        for url in urls:
            try:
                posting, steps = run_agent(url, db=db, settings=settings)
            except Exception as exc:  # noqa: BLE001 - one bad URL must not kill the batch
                print(f"[error] {url}: {exc}")
                err += 1
                continue
            if posting is None:
                print(f"[skip] {url} (duplicate)")
                dup += 1
            else:
                print(f"[ok] {url} -> #{posting.id} status={posting.status}")
                ok += 1
    finally:
        db.close()

    print(f"\nDone: {ok} processed, {dup} duplicates, {err} errors.")
    return 0


def cmd_report(since_days: int, out_path: str) -> int:
    settings = get_settings()
    init_db()
    db = get_session()
    try:
        markdown = generate_summary_markdown(db, since_days=since_days)
    finally:
        db.close()

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auto")
    sub = parser.add_subparsers(dest="command", required=True)

    p_url = sub.add_parser("process-url", help="Run the agent on a single job posting URL")
    p_url.add_argument("url")

    p_inbox = sub.add_parser("process-inbox", help="Run the agent on every new URL in inbox/urls.txt")
    p_inbox.add_argument("--inbox-file", default="inbox/urls.txt")

    p_report = sub.add_parser("report", help="Write a weekly markdown summary")
    p_report.add_argument("--since-days", type=int, default=7)
    p_report.add_argument("--out", default="reports/latest-summary.md")

    args = parser.parse_args(argv)

    if args.command == "process-url":
        return cmd_process_url(args.url)
    if args.command == "process-inbox":
        return cmd_process_inbox(args.inbox_file)
    if args.command == "report":
        return cmd_report(args.since_days, args.out)
    return 1


if __name__ == "__main__":
    sys.exit(main())
