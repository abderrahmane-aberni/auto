"""Weekly summary report: a plain markdown digest of what the agent has
processed, written to reports/latest-summary.md and committed back to the
repo by the GitHub Actions workflow - this file, refreshed on a schedule and
visible in the commit history, is the actual "deployment" artifact for this
project (see README "Deployment").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import AgentStep, JobPosting


def generate_summary_markdown(db: Session, *, since_days: int = 7) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    postings = (
        db.execute(
            select(JobPosting).where(JobPosting.created_at >= cutoff).order_by(JobPosting.created_at.desc())
        )
        .scalars()
        .all()
    )

    total = len(postings)
    sponsor_checked = [p for p in postings if p.sponsor_checked]
    licensed = [p for p in sponsor_checked if p.sponsor_licensed]
    salaries = [p for p in postings if p.salary_text]
    sponsorship_mentioned = [p for p in postings if p.sponsorship_mentioned]
    failed = [p for p in postings if p.status not in ("ok", "duplicate")]

    error_steps = (
        db.execute(
            select(AgentStep)
            .where(AgentStep.created_at >= cutoff, AgentStep.is_error.is_(True))
            .order_by(AgentStep.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )

    lines: list[str] = []
    lines.append(f"# Auto - weekly summary ({datetime.now(timezone.utc):%Y-%m-%d})")
    lines.append("")
    lines.append(f"Covering job postings processed in the last {since_days} days.")
    lines.append("")
    lines.append(f"- **Processed:** {total}")
    if sponsor_checked:
        pct = round(100 * len(licensed) / len(sponsor_checked), 1)
        lines.append(
            f"- **Sponsor-register matches:** {len(licensed)}/{len(sponsor_checked)} "
            f"companies checked are on the register ({pct}%)"
        )
    lines.append(f"- **Postings that mention sponsorship explicitly:** {len(sponsorship_mentioned)}")
    lines.append(f"- **Postings with a stated salary:** {len(salaries)}/{total}" if total else "- **Postings with a stated salary:** 0")
    if failed:
        lines.append(f"- **Runs that didn't finish cleanly:** {len(failed)} (see below)")
    lines.append("")

    if postings:
        lines.append("## Postings")
        lines.append("")
        lines.append("| Role | Company | Sponsor licensed? | Salary | Status |")
        lines.append("|---|---|---|---|---|")
        for p in postings:
            sponsor_cell = (
                "✅" if p.sponsor_licensed else ("❌" if p.sponsor_checked else "—")
            )
            lines.append(
                f"| {p.role or '—'} | {p.company or '—'} | {sponsor_cell} | "
                f"{p.salary_text or '—'} | {p.status} |"
            )
        lines.append("")

    if error_steps:
        lines.append("## Errors in this window")
        lines.append("")
        for step in error_steps:
            lines.append(f"- `{step.tool_name}` on {step.url}: {step.tool_result_summary}")
        lines.append("")

    if total == 0:
        lines.append(
            "No job postings were added to `inbox/urls.txt` in this window - "
            "nothing to report."
        )
        lines.append("")

    return "\n".join(lines)
