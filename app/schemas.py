"""Pydantic models for the structured extraction step."""
from __future__ import annotations

from pydantic import BaseModel, Field


class JobExtraction(BaseModel):
    """What the LLM is asked to pull out of a job posting's raw text.

    Every field except `role` is nullable - a real posting routinely omits
    salary or a deadline, and the model should say so honestly rather than
    inventing a plausible-looking number. That's the same "don't guess"
    principle Recall's RetrievalAnswer schema used for citations.
    """

    company: str | None = Field(None, description="Hiring company's name, as written on the page")
    role: str = Field(..., description="Job title / role being advertised")
    location: str | None = Field(None, description="Primary work location, e.g. 'London, UK' or 'Remote'")
    salary_text: str | None = Field(
        None, description="Salary or pay range exactly as stated, e.g. '£45,000-£55,000'. Null if not mentioned."
    )
    deadline_text: str | None = Field(
        None, description="Application deadline exactly as stated. Null if not mentioned."
    )
    sponsorship_mentioned: bool = Field(
        ...,
        description=(
            "True only if the posting explicitly says something about visa "
            "sponsorship (either offering it or ruling it out). False if "
            "sponsorship simply isn't mentioned at all - do not infer either "
            "way from silence."
        ),
    )
    sponsorship_detail: str | None = Field(
        None, description="The exact sentence/phrase about sponsorship, if sponsorship_mentioned is true."
    )
