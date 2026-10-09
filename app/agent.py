"""The hand-rolled plan -> act -> observe agent loop.

Deliberately not built on a heavy agent framework (LangChain/AutoGPT/etc) -
the point of this project is to show the mechanics are understood, not to
configure someone else's black box. The loop itself (`run_agent`) is a plain
`while` loop with an explicit step counter and an explicit stopping
condition; "agent-ness" comes entirely from the LLM deciding *which* tool to
call next and with *what* arguments, via native function-calling.

Testability is why the LLM is behind the narrow `AgentSession.step()`
interface: `GeminiAgentSession` is the real thing, `tests/test_agent_loop.py`
uses a scripted fake that returns a pre-programmed sequence of tool calls, so
the loop's control flow (guardrails, error handling, the max-steps cutoff)
is tested without needing a live API key or network access.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import AgentStep, JobPosting
from app.dedupe import normalize_url
from app.sponsor_register import SponsorRegister
from app.tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS, ToolContext

SYSTEM_INSTRUCTION = """You are a careful job-posting triage agent. For the \
URL you are given, you must, in order:
1. Call fetch_job_posting to read the page.
2. Call check_duplicate to make sure this posting hasn't already been processed.
   If it is a duplicate, call finish with status="duplicate" immediately - do
   not extract or sponsor-check a duplicate.
3. Call record_extraction with the structured fields you can read from the
   fetched text. Never invent a value (salary, deadline, company) that isn't
   actually stated on the page - leave it null instead. Only set
   sponsorship_mentioned=true if the page explicitly says something about
   visa sponsorship either way.
4. If you extracted a company name, call check_sponsor_register with it.
5. Call finish with status="ok" (or "fetch_failed"/"extraction_failed" if an
   earlier step failed) exactly once, to end the run.

Call exactly one tool per turn. Do not call the same tool twice in a row
with the same arguments. Do not respond with plain text - every turn must be
a tool call, including the last one."""


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


class AgentSession(Protocol):
    def step(self, tool_result: dict[str, Any] | None) -> ToolCall: ...


class GeminiAgentSession:
    """Wraps a google-genai chat session behind the AgentSession protocol."""

    def __init__(self, *, api_key: str, model_name: str, url: str) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        declarations = [types.FunctionDeclaration(**decl) for decl in TOOL_DECLARATIONS]
        tool = types.Tool(function_declarations=declarations)
        # Keep a strong reference to the Client on self - a Client created as
        # a bare local here would otherwise be garbage-collected as soon as
        # __init__ returns (nothing else holds it), which closes its
        # underlying httpx client and makes every later self._chat.send_message()
        # call in step() fail with "Cannot send a request, as the client has
        # been closed." This is a documented google-genai gotcha, not
        # something specific to this project - see
        # https://github.com/googleapis/python-genai/issues/1763.
        self._client = genai.Client(api_key=api_key)
        self._chat = self._client.chats.create(
            model=model_name,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[tool],
            ),
        )
        self._url = url
        self._pending_function_name: str | None = None
        self._first_message_sent = False

    def step(self, tool_result: dict[str, Any] | None) -> ToolCall:
        types = self._types
        if not self._first_message_sent:
            self._first_message_sent = True
            response = self._chat.send_message(f"Process this job posting URL: {self._url}")
        else:
            response = self._chat.send_message(
                types.Part.from_function_response(
                    name=self._pending_function_name, response=tool_result or {}
                )
            )

        call = self._extract_function_call(response)
        if call is None:
            text = self._extract_text(response)
            return ToolCall(
                name="finish",
                args={
                    "status": "extraction_failed",
                    "notes": f"Model returned plain text instead of a tool call: {text!r}",
                },
            )
        self._pending_function_name = call.name
        return call

    @staticmethod
    def _extract_function_call(response) -> ToolCall | None:
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if getattr(part, "function_call", None):
                    fc = part.function_call
                    return ToolCall(name=fc.name, args=dict(fc.args or {}))
        return None

    @staticmethod
    def _extract_text(response) -> str:
        chunks = []
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if getattr(part, "text", None):
                    chunks.append(part.text)
        return " ".join(chunks)[:300]


def _summarize(result: dict[str, Any], *, max_chars: int = 600) -> str:
    try:
        text = json.dumps(result, default=str)
    except TypeError:
        text = str(result)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def _persist_job_posting(ctx: ToolContext, db: Session) -> JobPosting | None:
    if ctx.duplicate_of_id is not None:
        return None

    extraction = ctx.extraction
    posting = JobPosting(
        url=ctx.url,
        url_normalized=normalize_url(ctx.url),
        company=extraction.company if extraction else None,
        role=extraction.role if extraction else None,
        location=extraction.location if extraction else None,
        salary_text=extraction.salary_text if extraction else None,
        deadline_text=extraction.deadline_text if extraction else None,
        sponsorship_mentioned=extraction.sponsorship_mentioned if extraction else None,
        sponsor_checked=ctx.sponsor_checked,
        sponsor_licensed=ctx.sponsor_licensed,
        sponsor_match_name=ctx.sponsor_match_name,
        sponsor_match_score=ctx.sponsor_match_score,
        status=ctx.final_status or "ok",
        raw_text_excerpt=(ctx.fetched_text[:500] if ctx.fetched_text else None),
    )
    db.add(posting)
    try:
        db.flush()
    except IntegrityError:
        # Defense in depth: the agent is instructed to call check_duplicate
        # before extracting, but nothing forces it to. If it skips straight
        # to record_extraction + finish on a URL already in the DB, the
        # unique constraint on url_normalized is what actually stops a
        # duplicate row from being written - not the agent's good behavior.
        db.rollback()
        return None
    return posting


def run_agent(
    url: str,
    *,
    db: Session,
    settings: Settings,
    sponsor_register: SponsorRegister | None = None,
    session_factory: Any = None,
) -> tuple[JobPosting | None, list[AgentStep]]:
    """Process one job posting URL end to end. Returns (job_posting_or_None,
    steps) - job_posting is None for a duplicate (nothing new was written).
    """
    ctx = ToolContext(db=db, settings=settings, url=url, sponsor_register=sponsor_register)

    if session_factory is None:
        def session_factory():
            return GeminiAgentSession(
                api_key=settings.gemini_api_key, model_name=settings.gemini_model, url=url
            )

    session: AgentSession = session_factory()

    steps: list[AgentStep] = []
    tool_result: dict[str, Any] | None = None
    step_index = 0
    guardrail_tripped = False

    while step_index < settings.max_agent_steps:
        call = session.step(tool_result)
        fn = TOOL_FUNCTIONS.get(call.name)

        if fn is None:
            tool_result = {"ok": False, "error": f"Unknown tool {call.name!r}"}
            is_error = True
        else:
            try:
                tool_result = fn(ctx, **call.args)
                is_error = tool_result.get("ok") is False
            except TypeError as exc:
                # Wrong/missing arguments - a real failure mode (the model
                # calling record_extraction without the required `role`
                # field, for example). Reported back to the model as a tool
                # error rather than crashing the run.
                tool_result = {"ok": False, "error": f"Bad arguments for {call.name}: {exc}"}
                is_error = True
            except Exception as exc:  # noqa: BLE001 - must never crash the loop
                tool_result = {"ok": False, "error": f"{call.name} raised: {exc}"}
                is_error = True

        steps.append(
            AgentStep(
                url=url,
                step_index=step_index,
                tool_name=call.name,
                tool_args=call.args,
                tool_result_summary=_summarize(tool_result),
                is_error=is_error,
            )
        )
        step_index += 1

        if call.name == "finish" or ctx.finished:
            break
    else:
        guardrail_tripped = True

    if guardrail_tripped:
        ctx.finished = True
        ctx.final_status = "max_steps_exceeded"
        steps.append(
            AgentStep(
                url=url,
                step_index=step_index,
                tool_name="__guardrail__",
                tool_args={},
                tool_result_summary=(
                    f"Stopped after {settings.max_agent_steps} steps without the "
                    "agent calling finish."
                ),
                is_error=True,
            )
        )

    job_posting = _persist_job_posting(ctx, db)
    for step in steps:
        step.job_posting_id = job_posting.id if job_posting else None
        db.add(step)
    db.commit()

    return job_posting, steps
