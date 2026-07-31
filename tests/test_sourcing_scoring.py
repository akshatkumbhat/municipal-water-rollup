from __future__ import annotations

import pandas as pd
import pytest

from sourcing_pipeline import (
    age_score,
    apply_scoring,
    canonicalize_url,
    normalize_name,
    workforce_score,
)


def test_url_and_name_normalization() -> None:
    assert canonicalize_url("Example.com/about/") == "https://example.com/about"
    assert normalize_name("Acme Water Services, LLC") == "Acme Water Services"


@pytest.mark.parametrize(
    ("age", "expected"),
    [(None, 10), (3, 4), (7, 10), (15, 22), (25, 30), (45, 35)],
)
def test_age_score_bands(age: int | None, expected: float) -> None:
    assert age_score(age) == expected


def test_workforce_score_prefers_target_band() -> None:
    assert workforce_score(technicians=12, employees=None) == 40
    assert workforce_score(technicians=2, employees=None) < 40
    assert workforce_score(technicians=120, employees=None) < 40


def test_total_score_is_bounded_and_reconciles() -> None:
    frame = pd.DataFrame(
        [
            {
                "company_age": 42,
                "technician_count_est": 20,
                "employee_count_est": 30,
                "website_status": "ok",
                "https": True,
                "phone_found": True,
                "email_found": True,
                "contact_page_found": True,
                "internal_page_count": 10,
                "social_profile_count": 1,
                "founding_year": 1984,
                "service_keyword_count": 4,
            }
        ]
    )
    scored = apply_scoring(frame).iloc[0]
    component_sum = (
        scored["age_score"]
        + scored["workforce_score"]
        + scored["digital_whitespace_score"]
    )
    assert scored["priority_score"] == pytest.approx(component_sum, abs=0.05)
    assert 0 <= scored["priority_score"] <= 100
    assert 0 <= scored["data_confidence"] <= 100


def test_scoring_methodology_golden_values() -> None:
    """Lock the 0-100 methodology (35 age / 40 workforce / 25 digital) so the
    Phase 2 data_confidence fix cannot silently shift the score."""
    frame = pd.DataFrame(
        [
            {
                "company_age": 42,
                "technician_count_est": 20,
                "employee_count_est": 30,
                "website_status": "ok",
                "https": True,
                "phone_found": True,
                "email_found": True,
                "contact_page_found": True,
                "internal_page_count": 10,
                "social_profile_count": 1,
                "founding_year": 1984,
                "service_keyword_count": 4,
            }
        ]
    )
    scored = apply_scoring(frame).iloc[0]
    assert scored["age_score"] == 35.0
    assert scored["workforce_score"] == 40.0
    assert scored["digital_whitespace_score"] == 25.0
    assert scored["priority_score"] == 100.0
    assert scored["data_confidence"] == 100
