"""Tests for entity-resolution accuracy measurement.

These assert that the measurement itself is correct — a scoring function that
silently reports 1.0 would be worse than no measurement at all.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from sourcing_evaluation import (
    ResolutionMetrics,
    anchor_fit,
    evaluate_fixture_deduplication,
    inject_typographic_noise,
    measure_noise_sensitivity,
    pairwise_metrics,
    predicted_clusters,
    scored_fixture_targets,
    validate_lead_score,
    workforce_curve_vs_anchor_band,
    write_evaluation_outputs,
)
from sourcing_fixtures import PRIMARY_COUNT, build_offline_dataset, ground_truth_clusters

# ---------------------------------------------------------------------------
# The scorer must be correct before its results mean anything.
# ---------------------------------------------------------------------------


def test_perfect_prediction_scores_one() -> None:
    truth = {0: 0, 1: 0, 2: 2}
    assert pairwise_metrics(truth, dict(truth)).f1 == 1.0


def test_missed_merge_costs_recall_not_precision() -> None:
    truth = {0: 0, 1: 0, 2: 2}
    predicted = {0: 0, 1: 1, 2: 2}  # failed to merge the real pair
    metrics = pairwise_metrics(truth, predicted)

    assert metrics.false_negatives == 1
    assert metrics.false_positives == 0
    assert metrics.recall == 0.0
    assert metrics.precision == 1.0


def test_false_merge_costs_precision_not_recall() -> None:
    truth = {0: 0, 1: 1, 2: 2}
    predicted = {0: 0, 1: 0, 2: 2}  # merged two distinct companies
    metrics = pairwise_metrics(truth, predicted)

    assert metrics.false_positives == 1
    assert metrics.false_negatives == 0
    assert metrics.precision == 0.0
    assert metrics.recall == 1.0


def test_partial_credit_on_a_three_way_cluster() -> None:
    """Pairwise scoring must degrade gracefully, not all-or-nothing."""
    truth = {0: 0, 1: 0, 2: 0}  # three records, one entity -> 3 true pairs
    predicted = {0: 0, 1: 0, 2: 2}  # caught one pair of the three
    metrics = pairwise_metrics(truth, predicted)

    assert metrics.true_positives == 1
    assert metrics.false_negatives == 2
    assert metrics.recall == pytest.approx(1 / 3)
    assert metrics.precision == 1.0


def test_all_singletons_is_vacuously_precise() -> None:
    truth = {0: 0, 1: 1}
    metrics = pairwise_metrics(truth, dict(truth))
    assert metrics.precision == 1.0 and metrics.recall == 1.0


def test_mismatched_record_sets_are_rejected() -> None:
    with pytest.raises(ValueError, match="does not cover the same records"):
        pairwise_metrics({0: 0, 1: 1}, {0: 0})


# ---------------------------------------------------------------------------
# Ground truth.
# ---------------------------------------------------------------------------


def test_ground_truth_matches_the_generated_fixtures() -> None:
    records, _pages, _blocked, _errors = build_offline_dataset()
    truth = ground_truth_clusters()

    assert len(truth) == len(records)
    assert len(set(truth.values())) == PRIMARY_COUNT
    # Exactly two records are duplicates of an earlier company.
    assert sorted(k for k, v in truth.items() if k != v) == [PRIMARY_COUNT, PRIMARY_COUNT + 1]


# ---------------------------------------------------------------------------
# The pipeline's measured accuracy.
# ---------------------------------------------------------------------------


def test_deterministic_matcher_is_exact_on_clean_fixtures() -> None:
    metrics = evaluate_fixture_deduplication()
    assert isinstance(metrics, ResolutionMetrics)
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.true_positives == 2
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 0
    assert metrics.predicted_clusters == metrics.true_clusters == PRIMARY_COUNT


def test_cluster_recovery_uses_source_rows_not_names() -> None:
    """One fixture pair normalizes to the same name; positions disambiguate."""
    import sourcing_pipeline as sourcing

    records, _pages, _blocked, _errors = build_offline_dataset()
    deduplicated = sourcing.clean_and_deduplicate(records)
    assert "merged_source_rows" in deduplicated.columns

    clusters = predicted_clusters(deduplicated)
    assert clusters[PRIMARY_COUNT] == clusters[3], "domain-merge pair not recovered"
    assert clusters[PRIMARY_COUNT + 1] == clusters[7], "name/address pair not recovered"


def test_cluster_recovery_rejects_a_frame_without_the_audit_column() -> None:
    with pytest.raises(ValueError, match="no 'merged_source_rows' column"):
        predicted_clusters(pd.DataFrame({"company_name": ["x"]}))


# ---------------------------------------------------------------------------
# Noise sensitivity — the exact-match recall ceiling.
# ---------------------------------------------------------------------------


def test_noise_injection_is_deterministic() -> None:
    records, _p, _b, _e = build_offline_dataset()
    first = inject_typographic_noise(records, rate=0.5, seed=7)
    second = inject_typographic_noise(records, rate=0.5, seed=7)
    assert first == second


def test_noise_injection_leaves_the_input_untouched() -> None:
    records, _p, _b, _e = build_offline_dataset()
    before = [dict(r) for r in records]
    inject_typographic_noise(records, rate=1.0, seed=3)
    assert records == before


def test_zero_noise_changes_nothing() -> None:
    records, _p, _b, _e = build_offline_dataset()
    assert inject_typographic_noise(records, rate=0.0, seed=3) == list(records)


@pytest.mark.parametrize("rate", [-0.1, 1.1])
def test_invalid_noise_rate_is_rejected(rate: float) -> None:
    records, _p, _b, _e = build_offline_dataset()
    with pytest.raises(ValueError, match="noise rate must be between 0 and 1"):
        inject_typographic_noise(records, rate=rate, seed=1)


def test_recall_degrades_as_input_quality_falls() -> None:
    """The documented weakness of exact-match linkage, measured on this pipeline."""
    curve = measure_noise_sensitivity(rates=(0.0, 0.5, 1.0), seeds=tuple(range(20)))

    clean = curve[curve["Noise Rate"] == 0.0].iloc[0]
    heavy = curve[curve["Noise Rate"] == 1.0].iloc[0]

    assert clean["Recall"] == 1.0
    assert heavy["Recall"] < 0.6, "noise must visibly break exact matching"
    assert heavy["Mean Missed Merges"] > clean["Mean Missed Merges"]


def test_noise_can_also_cause_false_merges() -> None:
    """Corruption does not only hide duplicates; it can invent them."""
    curve = measure_noise_sensitivity(rates=(0.0, 1.0), seeds=tuple(range(20)))
    assert curve[curve["Noise Rate"] == 0.0].iloc[0]["Mean False Merges"] == 0.0
    assert curve[curve["Noise Rate"] == 1.0].iloc[0]["Mean False Merges"] > 0.0


def test_noise_curve_reports_its_sample_size_and_spread() -> None:
    curve = measure_noise_sensitivity(rates=(0.25,), seeds=tuple(range(8)))
    row = curve.iloc[0]
    assert row["Draws"] == 8
    assert "Recall StdDev" in curve.columns
    assert row["Recall StdDev"] >= 0


def test_noise_curve_is_reproducible() -> None:
    first = measure_noise_sensitivity(rates=(0.5,), seeds=(1, 2, 3))
    second = measure_noise_sensitivity(rates=(0.5,), seeds=(1, 2, 3))
    pd.testing.assert_frame_equal(first, second)


# ---------------------------------------------------------------------------
# Outputs.
# ---------------------------------------------------------------------------


def test_evaluation_outputs_are_written_and_self_describing(tmp_path) -> None:
    written = write_evaluation_outputs(tmp_path)
    assert {"deduplication_metrics.json", "noise_sensitivity.csv"} <= set(written)

    payload = json.loads((tmp_path / "deduplication_metrics.json").read_text())
    assert "deterministic union-find" in payload["method"]
    assert payload["ground_truth_source"] == "sourcing_fixtures.ground_truth_clusters"
    assert payload["metrics"]["precision"] == 1.0
    # The report must not imply fixture accuracy generalises to live data.
    assert "synthetic fixtures" in payload["note"]

    curve = pd.read_csv(tmp_path / "noise_sensitivity.csv")
    assert len(curve) == 6
    assert curve["Recall"].iloc[0] == 1.0


# ---------------------------------------------------------------------------
# Lead-score validation (research priority #7).
# ---------------------------------------------------------------------------


def test_anchor_fit_uses_the_blueprint_technician_band() -> None:
    targets = pd.DataFrame({"technician_count_est": [2, 14, 15, 30, 60, 61, None]})
    fit = anchor_fit(targets)
    assert list(fit) == [False, False, True, True, True, False, False]


def test_unknown_technician_count_is_not_credited_as_fit() -> None:
    """An unverified target has not been shown to qualify."""
    assert not anchor_fit(pd.DataFrame({"technician_count_est": [None]})).iloc[0]


def test_lead_score_validation_reports_precision_base_rate_and_lift() -> None:
    validation = validate_lead_score(scored_fixture_targets())

    assert validation.k == 15
    assert 0.0 <= validation.precision_at_k <= 1.0
    assert 0.0 <= validation.base_rate <= 1.0
    assert validation.lift == pytest.approx(
        validation.precision_at_k / validation.base_rate
    )
    assert validation.fit_in_top_k <= validation.k
    assert validation.fit_in_universe <= validation.universe


def test_the_score_is_a_weak_discriminator_on_anchor_fit() -> None:
    """The uncomfortable finding, pinned so it cannot quietly disappear.

    The score barely beats random selection at surfacing anchor-profile
    targets. If a future change genuinely improves this, the assertion should
    be updated deliberately — not left to drift.
    """
    validation = validate_lead_score(scored_fixture_targets())
    assert validation.lift < 1.5, "if this now passes, the score improved — update it"
    assert validation.precision_at_k < 0.6


def test_validation_rejects_degenerate_inputs() -> None:
    with pytest.raises(ValueError, match="empty target universe"):
        validate_lead_score(pd.DataFrame())
    with pytest.raises(ValueError, match="k must be positive"):
        validate_lead_score(scored_fixture_targets(), k=0)


def test_workforce_curve_exposes_the_blueprint_contradiction() -> None:
    """Anchor band is 15-60; the workforce curve peaks at 8-35 and then declines."""
    curve = workforce_curve_vs_anchor_band()

    below_band = curve[(curve["Technicians"] == 12)].iloc[0]
    inside_band_high = curve[(curve["Technicians"] == 50)].iloc[0]

    assert not below_band["In Anchor Band"]
    assert inside_band_high["In Anchor Band"]
    # A company that fails the acquisition screen outscores one that passes it.
    assert below_band["Workforce Score (of 40)"] > inside_band_high["Workforce Score (of 40)"]
    assert below_band["Scored As Well As Best In Band"]


def test_evaluation_outputs_include_the_score_validation(tmp_path) -> None:
    written = write_evaluation_outputs(tmp_path)
    assert "lead_score_validation.json" in written
    assert "workforce_curve_vs_anchor_band.csv" in written

    payload = json.loads((tmp_path / "lead_score_validation.json").read_text())
    # The proxy limitation and the absence of AUC must both be stated.
    assert "workforce fit only" in payload["proxy_limitation"]
    assert "outcome labels" in payload["no_auc_reason"]
    assert payload["result"]["lift"] < 1.5
