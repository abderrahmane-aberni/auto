from app.db import JobPosting
from app.reports import generate_summary_markdown


def test_summary_shows_matched_register_name_not_just_a_checkmark(db_session):
    db_session.add(
        JobPosting(
            url="https://example.com/1",
            url_normalized="https://example.com/1",
            role="Backend Engineer",
            company="Acme",
            salary_text="£50,000",
            sponsor_checked=True,
            sponsor_licensed=True,
            sponsor_match_name="ACME TECHNOLOGY LIMITED",
            sponsor_match_score=91.3,
            status="ok",
        )
    )
    db_session.commit()

    markdown = generate_summary_markdown(db_session, since_days=7)

    assert "ACME TECHNOLOGY LIMITED" in markdown
    assert "91%" in markdown
    assert "Backend Engineer" in markdown


def test_summary_distinguishes_not_found_from_not_checked(db_session):
    db_session.add(
        JobPosting(
            url="https://example.com/2",
            url_normalized="https://example.com/2",
            role="Checked, no match",
            sponsor_checked=True,
            sponsor_licensed=False,
            status="ok",
        )
    )
    db_session.add(
        JobPosting(
            url="https://example.com/3",
            url_normalized="https://example.com/3",
            role="Never checked",
            sponsor_checked=False,
            status="ok",
        )
    )
    db_session.commit()

    markdown = generate_summary_markdown(db_session, since_days=7)

    assert "Not found" in markdown
    assert "—" in markdown


def test_empty_window_says_so(db_session):
    markdown = generate_summary_markdown(db_session, since_days=7)
    assert "No job postings" in markdown
