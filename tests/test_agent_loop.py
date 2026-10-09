"""Control-flow tests for the plan->act->observe loop, using a scripted fake
in place of the real Gemini session. This is what actually tests the agent
mechanics (guardrails, error handling, dedupe-before-persist) - the LLM's
own tool-choosing behavior is live and un-unit-testable, but how the loop
*reacts* to whatever the LLM decides is deterministic and fully testable.
"""
from __future__ import annotations

from app.agent import ToolCall, run_agent
from app.config import Settings
from app.db import JobPosting
from app.dedupe import normalize_url
from tests.conftest import FIXTURES_DIR

CSV_PATH = str(FIXTURES_DIR / "sample_sponsor_register.csv")


def _settings(**overrides) -> Settings:
    s = Settings()
    s.sponsor_register_csv = CSV_PATH
    s.max_agent_steps = overrides.pop("max_agent_steps", 8)
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class ScriptedSession:
    """Ignores whatever tool_result it's handed and plays back a fixed
    sequence of tool calls - a stand-in for a real LLM that would normally
    react to each result, used here purely to drive the loop's control flow.
    """

    def __init__(self, calls: list[ToolCall]):
        self._calls = list(calls)

    def step(self, tool_result):
        if not self._calls:
            return ToolCall(name="finish", args={"status": "ok", "notes": "script exhausted"})
        return self._calls.pop(0)


def test_happy_path_persists_job_posting(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.scraper.fetch_url",
        lambda url, timeout_seconds=15: __import__("app.scraper", fromlist=["FetchedPage"]).FetchedPage(
            url=url, final_url=url, status_code=200,
            html="<html><body><h1>Backend Engineer</h1>" + "filler text " * 20 + "</body></html>",
        ),
    )
    calls = [
        ToolCall("fetch_job_posting", {}),
        ToolCall("check_duplicate", {}),
        ToolCall(
            "record_extraction",
            {
                "role": "Backend Engineer",
                "company": "Acme Technology",
                "sponsorship_mentioned": True,
                "sponsorship_detail": "We offer sponsorship.",
            },
        ),
        ToolCall("check_sponsor_register", {"company_name": "Acme Technology"}),
        ToolCall("finish", {"status": "ok"}),
    ]
    posting, steps = run_agent(
        "https://jobs.example.com/1",
        db=db_session,
        settings=_settings(),
        session_factory=lambda: ScriptedSession(calls),
    )

    assert posting is not None
    assert posting.role == "Backend Engineer"
    assert posting.company == "Acme Technology"
    assert posting.sponsor_licensed is True
    assert posting.status == "ok"
    assert len(steps) == 5
    assert all(not s.is_error for s in steps)


def test_duplicate_url_is_not_persisted_again(db_session):
    url = "https://jobs.example.com/dup"
    db_session.add(JobPosting(url=url, url_normalized=normalize_url(url), role="Existing Role"))
    db_session.commit()

    calls = [
        ToolCall("check_duplicate", {}),
        ToolCall("finish", {"status": "duplicate"}),
    ]
    posting, steps = run_agent(
        url, db=db_session, settings=_settings(), session_factory=lambda: ScriptedSession(calls)
    )

    assert posting is None
    assert db_session.query(JobPosting).filter(JobPosting.url_normalized == normalize_url(url)).count() == 1


def test_guardrail_trips_when_agent_never_calls_finish(db_session):
    """The documented failure case: a model that keeps calling the same tool
    and never calls finish. Without the max_agent_steps guardrail this would
    spin forever; with it, the loop stops and logs exactly why.
    """
    calls = [ToolCall("check_duplicate", {}) for _ in range(10)]  # more than max_agent_steps
    posting, steps = run_agent(
        "https://jobs.example.com/loop",
        db=db_session,
        settings=_settings(max_agent_steps=3),
        session_factory=lambda: ScriptedSession(calls),
    )

    assert len(steps) == 4  # 3 real steps + 1 guardrail log entry
    assert steps[-1].tool_name == "__guardrail__"
    assert steps[-1].is_error is True
    assert posting is not None
    assert posting.status == "max_steps_exceeded"


def test_unknown_tool_call_is_logged_as_error_but_does_not_crash(db_session):
    calls = [
        ToolCall("totally_made_up_tool", {"x": 1}),
        ToolCall("finish", {"status": "extraction_failed"}),
    ]
    posting, steps = run_agent(
        "https://jobs.example.com/bad-tool",
        db=db_session,
        settings=_settings(),
        session_factory=lambda: ScriptedSession(calls),
    )

    assert steps[0].tool_name == "totally_made_up_tool"
    assert steps[0].is_error is True
    assert "Unknown tool" in steps[0].tool_result_summary
    assert steps[1].tool_name == "finish"


def test_missing_required_argument_is_logged_as_error_but_does_not_crash(db_session):
    calls = [
        ToolCall("record_extraction", {}),  # missing required `role`
        ToolCall("finish", {"status": "extraction_failed"}),
    ]
    posting, steps = run_agent(
        "https://jobs.example.com/bad-args",
        db=db_session,
        settings=_settings(),
        session_factory=lambda: ScriptedSession(calls),
    )

    assert steps[0].tool_name == "record_extraction"
    assert steps[0].is_error is True
    assert "Bad arguments" in steps[0].tool_result_summary


def test_db_unique_constraint_is_the_real_backstop_against_duplicates(db_session):
    """Even if the agent skips check_duplicate entirely and goes straight to
    extraction on a URL that's already in the DB, the unique constraint on
    url_normalized - not the agent's good behavior - is what actually
    prevents a second row. This is the "defense in depth" case called out in
    app/agent.py::_persist_job_posting.
    """
    url = "https://jobs.example.com/sneaky-dup"
    db_session.add(JobPosting(url=url, url_normalized=normalize_url(url), role="Already Here"))
    db_session.commit()

    calls = [
        ToolCall("record_extraction", {"role": "New Role", "sponsorship_mentioned": False}),
        ToolCall("finish", {"status": "ok"}),
    ]
    posting, steps = run_agent(
        url, db=db_session, settings=_settings(), session_factory=lambda: ScriptedSession(calls)
    )

    assert posting is None
    rows = db_session.query(JobPosting).filter(JobPosting.url_normalized == normalize_url(url)).all()
    assert len(rows) == 1
    assert rows[0].role == "Already Here"
