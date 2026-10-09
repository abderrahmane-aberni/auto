import pytest

from app.sponsor_register import load_sponsor_register, normalize_company_name
from tests.conftest import FIXTURES_DIR

CSV_PATH = FIXTURES_DIR / "sample_sponsor_register.csv"


def test_loads_fixture_csv():
    register = load_sponsor_register(CSV_PATH)
    assert len(register) == 10


def test_missing_file_raises_with_a_helpful_message():
    with pytest.raises(FileNotFoundError, match="update_sponsor_register.py"):
        load_sponsor_register(CSV_PATH.parent / "does_not_exist.csv")


def test_strips_legal_suffixes():
    assert normalize_company_name("Acme Technology Limited") == normalize_company_name("Acme Technology")
    assert normalize_company_name("Monzo Bank Ltd") == normalize_company_name("Monzo Bank LIMITED")


def test_exact_name_minus_legal_suffix_matches():
    register = load_sponsor_register(CSV_PATH)
    match = register.lookup("Acme Technology", threshold=85)
    assert match.licensed is True
    assert match.matched_name == "ACME TECHNOLOGY LIMITED"


def test_colloquial_name_still_matches_above_threshold():
    register = load_sponsor_register(CSV_PATH)
    # job postings often drop "UK"/"Payments" style qualifiers entirely
    match = register.lookup("Stripe", threshold=60)
    assert match.licensed is True
    assert match.matched_name == "STRIPE PAYMENTS UK LIMITED"


def test_unrelated_company_is_not_matched():
    register = load_sponsor_register(CSV_PATH)
    match = register.lookup("Totally Unrelated Startup Co", threshold=85)
    assert match.licensed is False
    assert match.matched_name is None


def test_empty_company_name_is_not_matched():
    register = load_sponsor_register(CSV_PATH)
    match = register.lookup("", threshold=85)
    assert match.licensed is False


def test_threshold_controls_strictness():
    register = load_sponsor_register(CSV_PATH)
    # A near-miss typo scores high but not perfect against "Northwind
    # Logistics Group Limited" - a strict threshold should reject it, a lax
    # one should accept it.
    strict = register.lookup("Northwynd", threshold=95)
    lax = register.lookup("Northwynd", threshold=40)
    assert strict.licensed is False
    assert lax.licensed is True
