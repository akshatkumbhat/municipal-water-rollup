from __future__ import annotations

import pandas as pd
import pytest

import sourcing_pipeline
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
        scored["age_score"] + scored["workforce_score"] + scored["digital_whitespace_score"]
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


# ---------------------------------------------------------------------------
# Phase 6: documented total ordering (H1).
# ---------------------------------------------------------------------------


def _tied_frame() -> pd.DataFrame:
    """Three records that tie on score and confidence, in a scrambled order."""
    return pd.DataFrame(
        {
            "company_name": ["Zulu Water", "alpha water", "Mike Water"],
            "domain": ["zulu.example.com", "alpha.example.com", "mike.example.com"],
            "company_url": [
                "https://zulu.example.com/",
                "https://alpha.example.com/",
                "https://mike.example.com/",
            ],
            "priority_score": [100.0, 100.0, 100.0],
            "data_confidence": [100, 100, 100],
        }
    )


def test_total_order_is_stable_regardless_of_input_order() -> None:
    frame = _tied_frame()
    expected = list(sourcing_pipeline.order_scored_targets(frame)["company_name"])

    for permutation in ([2, 0, 1], [1, 2, 0], [2, 1, 0]):
        shuffled = frame.iloc[permutation].reset_index(drop=True)
        assert list(sourcing_pipeline.order_scored_targets(shuffled)["company_name"]) == expected


def test_total_order_uses_normalized_name_not_raw_case() -> None:
    """`alpha water` must sort before `Mike Water` despite the lowercase start."""
    ordered = sourcing_pipeline.order_scored_targets(_tied_frame())
    assert list(ordered["company_name"]) == ["alpha water", "Mike Water", "Zulu Water"]


def test_score_still_outranks_every_tiebreak() -> None:
    frame = _tied_frame()
    frame.loc[0, "priority_score"] = 100.0
    frame.loc[1, "priority_score"] = 40.0
    ordered = sourcing_pipeline.order_scored_targets(frame)
    assert ordered.iloc[-1]["company_name"] == "alpha water"


def test_confidence_outranks_name() -> None:
    frame = _tied_frame()
    frame.loc[1, "data_confidence"] = 20  # 'alpha water', otherwise first
    ordered = sourcing_pipeline.order_scored_targets(frame)
    assert ordered.iloc[-1]["company_name"] == "alpha water"


def test_source_identifier_separates_identical_normalized_names() -> None:
    """Duplicate names must still produce a total, reproducible order."""
    frame = pd.DataFrame(
        {
            "company_name": ["Acme Water", "Acme  Water", "ACME WATER"],
            "domain": ["c.example.com", "a.example.com", "b.example.com"],
            "company_url": ["https://c/", "https://a/", "https://b/"],
            "priority_score": [100.0, 100.0, 100.0],
            "data_confidence": [100, 100, 100],
        }
    )
    ordered = sourcing_pipeline.order_scored_targets(frame)
    assert list(ordered["domain"]) == ["a.example.com", "b.example.com", "c.example.com"]


def test_ordering_adds_no_helper_columns_to_the_output() -> None:
    ordered = sourcing_pipeline.order_scored_targets(_tied_frame())
    assert "_name_key" not in ordered.columns
    assert "_source_key" not in ordered.columns


def test_ordering_an_empty_frame_is_safe() -> None:
    assert sourcing_pipeline.order_scored_targets(pd.DataFrame()).empty


def test_offline_pipeline_runs_are_byte_identical_under_concurrency(tmp_path) -> None:
    """Concurrent enrichment must not leak into output order."""
    from sourcing_fixtures import build_offline_dataset

    outputs = []
    for workers in (8, 8, 4, 1):
        raw, pages, blocked, errors = build_offline_dataset()
        fetcher = sourcing_pipeline.OfflineFetcher(pages, blocked, errors)
        cleaned = sourcing_pipeline.clean_and_deduplicate(raw)
        scored = sourcing_pipeline.enrich_and_score(fetcher, cleaned, workers, "2026-01-31")
        outputs.append(scored.reset_index(drop=True))

    first = outputs[0]
    for other in outputs[1:]:
        assert list(other["company_name"]) == list(first["company_name"])
        pd.testing.assert_frame_equal(other, first)


def test_package_and_standalone_share_one_order() -> None:
    """Two ordering rules would eventually disagree; there must be only one."""
    import candidate_package

    frame = _tied_frame()
    pd.testing.assert_frame_equal(
        candidate_package.order_targets(frame),
        sourcing_pipeline.order_scored_targets(frame),
    )
