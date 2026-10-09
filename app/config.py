"""Application configuration, read fresh from environment variables on every
call (no caching) so tests can monkeypatch env vars per-test without fighting
stale state. Same pattern as the Recall and CV-Maxxing projects.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        # --- LLM (extraction + agent loop, via native function-calling) ---
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

        # --- Database ---
        # Dedicated Neon project for this project, not a literal reuse of
        # Ledger's production database - see README "Why a separate database".
        # `or`, not a .getenv default: an empty-string env var (what a CI
        # secret or a dashboard field stores when left blank) must fall back
        # too, not just a fully-missing variable.
        self.database_url = os.getenv("DATABASE_URL") or "sqlite:///./auto.db"

        # --- Agent loop guardrails ---
        # Hard ceiling on tool calls per job posting before the agent is force-
        # stopped. This is the single most important safety property of a
        # hand-rolled agent loop: without it, a model that never calls `finish`
        # (e.g. because a tool keeps returning something it doesn't expect)
        # will spin forever, burning API quota. See README "Guardrails".
        self.max_agent_steps = int(os.getenv("MAX_AGENT_STEPS", "8"))
        # How long a single HTTP fetch of a job posting is allowed to take.
        self.fetch_timeout_seconds = int(os.getenv("FETCH_TIMEOUT_SECONDS", "15"))

        # --- Sponsor register ---
        # Path to the gov.uk CSV (downloaded by `scripts/update_sponsor_register.py`,
        # which needs real network access - see README "Updating the sponsor
        # register"). Not fetched live mid-agent-run: the register only changes
        # monthly, and a flaky gov.uk fetch shouldn't be able to fail an
        # otherwise-successful job-posting run.
        self.sponsor_register_csv = os.getenv(
            "SPONSOR_REGISTER_CSV", "data/sponsor_register.csv"
        )
        # Below this fuzzy-match score (0-100), a company is reported as
        # "not found" rather than guessed at - see sponsor_register.py.
        self.sponsor_match_threshold = int(os.getenv("SPONSOR_MATCH_THRESHOLD", "85"))

    def validate(self) -> None:
        if not self.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add "
                "your key from https://aistudio.google.com/apikey"
            )


def get_settings() -> Settings:
    return Settings()
