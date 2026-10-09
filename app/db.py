"""SQLAlchemy models + lazy engine/session creation.

Engine/sessionmaker are built lazily (never at import time) - this is a
defensive pattern born from a real production bug in the Ledger project
(os.getenv(key, default) silently not falling back on an empty-string env
var from a hosting dashboard) and reapplied in every project since.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobPosting(Base):
    """One row per job posting the agent has processed."""

    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Normalized form of `url` (lowercased, tracking params stripped, trailing
    # slash removed) - this is what dedupe actually keys on. See dedupe.py.
    url_normalized: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)

    company: Mapped[str | None] = mapped_column(String(512), nullable=True)
    role: Mapped[str | None] = mapped_column(String(512), nullable=True)
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    salary_text: Mapped[str | None] = mapped_column(String(256), nullable=True)
    deadline_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sponsorship_mentioned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    sponsor_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sponsor_licensed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sponsor_match_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sponsor_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # "ok" | "extraction_failed" | "fetch_failed" | "max_steps_exceeded"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    raw_text_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    steps: Mapped[list["AgentStep"]] = relationship(
        "AgentStep", back_populates="job_posting", cascade="all, delete-orphan"
    )


class AgentStep(Base):
    """One row per tool call the agent made while processing a job posting.

    This is what the "failure cases" section of the README is built from -
    it's a genuine log of what the agent actually did, not a reconstruction.
    """

    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_posting_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_postings.id"), nullable=True
    )
    # Not every step resolves to a JobPosting row (e.g. a run that errors out
    # before extraction ever succeeds) - nullable FK, and url is kept here too
    # so the step is still legible on its own.
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_args: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tool_result_summary: Mapped[str] = mapped_column(Text, nullable=False)
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    job_posting: Mapped[JobPosting | None] = relationship("JobPosting", back_populates="steps")


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def get_session() -> Session:
    return get_sessionmaker()()
