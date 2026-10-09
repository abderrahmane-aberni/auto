import pytest

from app.scraper import parse_job_html
from tests.conftest import FIXTURES_DIR


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_clean_posting_extracts_title_and_text():
    parsed = parse_job_html(_load("job_posting_clean.html"))
    assert "Backend Engineer" in parsed.title
    assert "Acme Technology" in parsed.text
    assert "£55,000" in parsed.text
    assert "visa sponsorship" in parsed.text.lower()


def test_messy_posting_strips_nav_footer_and_scripts():
    parsed = parse_job_html(_load("job_posting_messy.html"))
    assert "Senior Data Analyst" in parsed.text
    assert "GlobalCorp UK" in parsed.text
    # boilerplate that should have been stripped out entirely
    assert "Sign in" not in parsed.text
    assert "cookies" not in parsed.text.lower()
    assert "console.log" not in parsed.text
    assert "All rights reserved" not in parsed.text


def test_empty_js_shell_raises_value_error():
    with pytest.raises(ValueError, match="almost no text content"):
        parse_job_html(_load("job_posting_empty_shell.html"))


def test_long_page_is_truncated_to_max_chars():
    html = "<html><body>" + ("word " * 5000) + "</body></html>"
    parsed = parse_job_html(html)
    from app.scraper import MAX_TEXT_CHARS

    assert len(parsed.text) <= MAX_TEXT_CHARS
