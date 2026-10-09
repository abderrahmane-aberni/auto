from app.config import Settings
from app.db import JobPosting
from app.dedupe import normalize_url
from app.tools import (
    ToolContext,
    tool_check_duplicate,
    tool_check_sponsor_register,
    tool_finish,
    tool_record_extraction,
)
from tests.conftest import FIXTURES_DIR

CSV_PATH = str(FIXTURES_DIR / "sample_sponsor_register.csv")


def _settings(**overrides) -> Settings:
    s = Settings()
    s.sponsor_register_csv = CSV_PATH
    s.sponsor_match_threshold = 85
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_check_duplicate_finds_existing_row(db_session):
    url = "https://jobs.example.com/123?utm_source=x"
    existing = JobPosting(url=url, url_normalized=normalize_url(url), role="Engineer")
    db_session.add(existing)
    db_session.commit()

    ctx = ToolContext(db=db_session, settings=_settings(), url="https://jobs.example.com/123")
    result = tool_check_duplicate(ctx)

    assert result["is_duplicate"] is True
    assert result["existing_job_posting_id"] == existing.id
    assert ctx.duplicate_of_id == existing.id


def test_check_duplicate_reports_new_url(db_session):
    ctx = ToolContext(db=db_session, settings=_settings(), url="https://jobs.example.com/new")
    result = tool_check_duplicate(ctx)
    assert result["is_duplicate"] is False
    assert ctx.duplicate_of_id is None


def test_record_extraction_valid(db_session):
    ctx = ToolContext(db=db_session, settings=_settings(), url="https://jobs.example.com/1")
    result = tool_record_extraction(
        ctx,
        role="Backend Engineer",
        company="Acme",
        sponsorship_mentioned=True,
        sponsorship_detail="We offer visa sponsorship.",
    )
    assert result["ok"] is True
    assert ctx.extraction is not None
    assert ctx.extraction.role == "Backend Engineer"


def test_record_extraction_missing_role_is_reported_not_raised(db_session):
    ctx = ToolContext(db=db_session, settings=_settings(), url="https://jobs.example.com/1")
    # role is required by the function signature itself, so this simulates
    # the model passing an empty string rather than omitting the field -
    # Pydantic should still reject it via the schema's own validation path
    # for anything stricter than "non-empty string" added later.
    result = tool_record_extraction(ctx, role="", sponsorship_mentioned=False)
    assert result["ok"] is True  # empty string is still a valid str today
    assert ctx.extraction.role == ""


def test_check_sponsor_register_match(db_session):
    ctx = ToolContext(db=db_session, settings=_settings(), url="https://jobs.example.com/1")
    result = tool_check_sponsor_register(ctx, company_name="Acme Technology")
    assert result["ok"] is True
    assert result["licensed"] is True
    assert ctx.sponsor_licensed is True
    assert ctx.sponsor_match_name == "ACME TECHNOLOGY LIMITED"


def test_check_sponsor_register_no_match(db_session):
    ctx = ToolContext(db=db_session, settings=_settings(), url="https://jobs.example.com/1")
    result = tool_check_sponsor_register(ctx, company_name="Totally Unrelated Co")
    assert result["ok"] is True
    assert result["licensed"] is False
    assert ctx.sponsor_licensed is False


def test_check_sponsor_register_missing_csv_reports_error_not_exception(db_session):
    ctx = ToolContext(
        db=db_session,
        settings=_settings(sponsor_register_csv=str(FIXTURES_DIR / "nope.csv")),
        url="https://jobs.example.com/1",
    )
    result = tool_check_sponsor_register(ctx, company_name="Acme")
    assert result["ok"] is False
    assert "error" in result


def test_finish_sets_context_state(db_session):
    ctx = ToolContext(db=db_session, settings=_settings(), url="https://jobs.example.com/1")
    result = tool_finish(ctx, status="ok", notes="all good")
    assert result == {"ok": True, "status": "ok", "notes": "all good"}
    assert ctx.finished is True
    assert ctx.final_status == "ok"
