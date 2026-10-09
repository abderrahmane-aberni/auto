"""The agent's toolbelt.

Each tool is a plain Python function taking a shared `ToolContext` plus
keyword arguments, returning a small JSON-safe dict (this is what gets fed
back to the LLM as the tool's result, and also what gets logged verbatim to
AgentStep.tool_result_summary for the failure-cases report). Keeping tools as
plain functions - not methods on the agent class - is what makes
test_agent_loop.py able to swap in fakes without touching agent.py.

`record_extraction` is deliberately the one place structured fields enter the
system: the agent is expected to read the page text from `fetch_job_posting`'s
result (handed to it in conversation) and then CALL this function with the
fields filled in, rather than the server regex-parsing the page itself. That
function call is the "LLM function-calling for structured extraction" the
project is about - not a separate response_schema call bolted on afterward.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.dedupe import normalize_url
from app.db import JobPosting
from app.schemas import JobExtraction
from app.scraper import FetchError, fetch_job_posting as _fetch_job_posting
from app.sponsor_register import SponsorRegister, get_cached_register


@dataclass
class ToolContext:
    """Per-job-posting scratch state the toolbelt reads/writes as the agent
    works. Nothing here is persisted until `finish` runs - that keeps a run
    that errors out midway from leaving a half-written DB row.
    """

    db: Session
    settings: Settings
    url: str
    sponsor_register: SponsorRegister | None = None

    fetched_text: str | None = None
    fetched_title: str | None = None
    extraction: JobExtraction | None = None
    sponsor_checked: bool = False
    sponsor_licensed: bool | None = None
    sponsor_match_name: str | None = None
    sponsor_match_score: float | None = None
    duplicate_of_id: int | None = None

    finished: bool = False
    final_status: str | None = None


def tool_fetch_job_posting(ctx: ToolContext) -> dict[str, Any]:
    try:
        page = _fetch_job_posting(ctx.url, timeout_seconds=ctx.settings.fetch_timeout_seconds)
    except (FetchError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    ctx.fetched_text = page.text
    ctx.fetched_title = page.title
    return {
        "ok": True,
        "title": page.title,
        "text": page.text,
        "char_count": len(page.text),
    }


def tool_check_duplicate(ctx: ToolContext) -> dict[str, Any]:
    normalized = normalize_url(ctx.url)
    existing = (
        ctx.db.query(JobPosting).filter(JobPosting.url_normalized == normalized).first()
    )
    if existing is not None:
        ctx.duplicate_of_id = existing.id
        return {
            "is_duplicate": True,
            "existing_job_posting_id": existing.id,
            "existing_role": existing.role,
            "existing_company": existing.company,
        }
    return {"is_duplicate": False}


def tool_record_extraction(
    ctx: ToolContext,
    *,
    role: str,
    company: str | None = None,
    location: str | None = None,
    salary_text: str | None = None,
    deadline_text: str | None = None,
    sponsorship_mentioned: bool = False,
    sponsorship_detail: str | None = None,
) -> dict[str, Any]:
    try:
        extraction = JobExtraction(
            role=role,
            company=company,
            location=location,
            salary_text=salary_text,
            deadline_text=deadline_text,
            sponsorship_mentioned=sponsorship_mentioned,
            sponsorship_detail=sponsorship_detail,
        )
    except Exception as exc:  # pydantic.ValidationError, kept broad for the tool-result contract
        return {"ok": False, "error": f"Invalid extraction: {exc}"}
    ctx.extraction = extraction
    return {"ok": True, "recorded": extraction.model_dump()}


def tool_check_sponsor_register(ctx: ToolContext, *, company_name: str) -> dict[str, Any]:
    if ctx.sponsor_register is None:
        try:
            ctx.sponsor_register = get_cached_register(ctx.settings.sponsor_register_csv)
        except FileNotFoundError as exc:
            return {"ok": False, "error": str(exc)}

    match = ctx.sponsor_register.lookup(company_name, threshold=ctx.settings.sponsor_match_threshold)
    ctx.sponsor_checked = True
    ctx.sponsor_licensed = match.licensed
    ctx.sponsor_match_name = match.matched_name
    ctx.sponsor_match_score = match.score
    return {
        "ok": True,
        "licensed": match.licensed,
        "matched_register_name": match.matched_name,
        "match_score": match.score,
        "register_size": len(ctx.sponsor_register),
    }


def tool_finish(ctx: ToolContext, *, status: str, notes: str | None = None) -> dict[str, Any]:
    ctx.finished = True
    ctx.final_status = status
    return {"ok": True, "status": status, "notes": notes or ""}


# name -> callable, used by agent.py to dispatch and by the Gemini tool
# declarations below to advertise what's available.
TOOL_FUNCTIONS = {
    "fetch_job_posting": tool_fetch_job_posting,
    "check_duplicate": tool_check_duplicate,
    "record_extraction": tool_record_extraction,
    "check_sponsor_register": tool_check_sponsor_register,
    "finish": tool_finish,
}

# JSON-schema style declarations, framework-agnostic (converted to the
# Gemini SDK's own types.FunctionDeclaration in agent.py, kept separate here
# so this file has zero dependency on google-genai and can be imported by
# anything, including tests that never touch a real LLM).
TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "fetch_job_posting",
        "description": "Fetch and read the job posting page at the URL being processed. Call this first.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "check_duplicate",
        "description": "Check whether this URL has already been processed in a previous run.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "record_extraction",
        "description": (
            "Record the structured fields extracted from the job posting text "
            "you already fetched. Only call this once, after fetch_job_posting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "company": {"type": "string"},
                "location": {"type": "string"},
                "salary_text": {"type": "string"},
                "deadline_text": {"type": "string"},
                "sponsorship_mentioned": {"type": "boolean"},
                "sponsorship_detail": {"type": "string"},
            },
            "required": ["role", "sponsorship_mentioned"],
        },
    },
    {
        "name": "check_sponsor_register",
        "description": (
            "Check whether a company name appears on the UK Home Office's "
            "register of licensed sponsors. Call this after record_extraction, "
            "using the company name you extracted."
        ),
        "parameters": {
            "type": "object",
            "properties": {"company_name": {"type": "string"}},
            "required": ["company_name"],
        },
    },
    {
        "name": "finish",
        "description": (
            "End processing of this job posting. Call this exactly once, after "
            "extraction (and sponsor check, if a company name was found) are "
            "done, or immediately if the posting turned out to be a duplicate "
            "or unreadable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["ok", "duplicate", "fetch_failed", "extraction_failed"],
                },
                "notes": {"type": "string"},
            },
            "required": ["status"],
        },
    },
]
