from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from buy_and_build_model import SCENARIOS, run_scenario
from operations_kpis import (
    GOVERNING_KEYS,
    METRIC_DEFINITIONS,
    OPTIONAL_COLUMNS,
    REQUIRED_COLUMNS,
    Thresholds,
    assign_model_period,
    available_dimensions,
    capital_structure_view,
    dimension_rollup,
    exception_report,
    filter_operating_data,
    generate_sample_data,
    has_grr_inputs,
    kpi_summary,
    main,
    metric_definitions_table,
    model_year_index,
    monthly_rollup,
    organic_growth_view,
    period_comparison,
    plan_vs_actual,
    resolve_model_anchor,
    synergy_realization_view,
    validate_operating_data,
    write_operating_outputs,
)

TOL = 1e-9


@pytest.fixture(scope="module")
def sample() -> pd.DataFrame:
    return validate_operating_data(generate_sample_data())


@pytest.fixture(scope="module")
def period(sample: pd.DataFrame):
    """Anchor resolved once from the FULL dataset, as production does."""
    return resolve_model_anchor(sample)


@pytest.fixture(scope="module")
def anchored(sample: pd.DataFrame, period) -> pd.DataFrame:
    return assign_model_period(sample, period)


@pytest.fixture(scope="module")
def monthly(anchored: pd.DataFrame) -> pd.DataFrame:
    return monthly_rollup(anchored)


# ---------------------------------------------------------------------------
# Sample data: deterministic, complete, and calibrated to the model.
# ---------------------------------------------------------------------------


def test_sample_data_is_deterministic() -> None:
    first = generate_sample_data()
    second = generate_sample_data()
    pd.testing.assert_frame_equal(first, second)


def test_sample_data_covers_the_model_horizon(sample: pd.DataFrame) -> None:
    schedule = run_scenario(SCENARIOS["base"]).schedule
    assert sample["month"].nunique() == 12 * len(schedule)
    assert set(REQUIRED_COLUMNS) <= set(sample.columns)
    assert set(OPTIONAL_COLUMNS) <= set(sample.columns)


def test_sample_annual_revenue_reconciles_to_the_model(sample: pd.DataFrame) -> None:
    """Calibration is what makes plan-versus-actual meaningful rather than noise."""
    schedule = run_scenario(SCENARIOS["base"]).schedule
    first_month = sample["month"].min()
    years = model_year_index(sample["month"], first_month)
    actual = sample.groupby(years)["revenue"].sum() / 1_000_000.0

    for _, row in schedule.iterrows():
        year = int(row["Year"])
        # Within the deliberate per-year execution factor (max 3.6%).
        assert actual[year] == pytest.approx(row["Revenue"], rel=0.05)


def test_sample_business_units_follow_the_model_acquisition_schedule(
    sample: pd.DataFrame,
) -> None:
    scenario = SCENARIOS["base"]
    first_month = sample["month"].min()
    years = model_year_index(sample["month"], first_month)
    for add_on in scenario.add_ons:
        rows = sample[sample["business_unit"] == add_on.name]
        assert not rows.empty, f"{add_on.name} missing from sample data"
        assert years[rows.index].min() == add_on.close_year
    assert years[sample[sample["business_unit"] == "Platform"].index].min() == 1


def test_sample_data_passes_its_own_validator(sample: pd.DataFrame) -> None:
    revalidated = validate_operating_data(sample)
    assert len(revalidated) == len(sample)


# ---------------------------------------------------------------------------
# Validation: actionable errors.
# ---------------------------------------------------------------------------


def test_missing_columns_are_named_in_the_error(sample: pd.DataFrame) -> None:
    broken = sample.drop(columns=["revenue", "route_miles"])
    with pytest.raises(ValueError, match="missing required column") as info:
        validate_operating_data(broken)
    assert "revenue" in str(info.value)
    assert "route_miles" in str(info.value)


def test_empty_frame_with_valid_headers_is_rejected(sample: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="contains no rows"):
        validate_operating_data(sample.iloc[0:0])


def test_frame_with_no_columns_is_rejected() -> None:
    with pytest.raises(ValueError, match="no columns"):
        validate_operating_data(pd.DataFrame())


def test_unparseable_month_is_rejected(sample: pd.DataFrame) -> None:
    broken = sample.copy()
    broken["month"] = broken["month"].astype(object)
    broken.loc[broken.index[0], "month"] = "not-a-date"
    with pytest.raises(ValueError, match="unparseable"):
        validate_operating_data(broken)


def test_non_numeric_measure_is_rejected(sample: pd.DataFrame) -> None:
    broken = sample.copy()
    broken["revenue"] = broken["revenue"].astype(object)
    broken.loc[broken.index[0], "revenue"] = "n/a"
    with pytest.raises(ValueError, match="Non-numeric or missing values"):
        validate_operating_data(broken)


def test_negative_revenue_is_rejected(sample: pd.DataFrame) -> None:
    broken = sample.copy()
    broken.loc[broken.index[0], "revenue"] = -1.0
    with pytest.raises(ValueError, match="Negative values"):
        validate_operating_data(broken)


def test_negative_ebitda_is_allowed(sample: pd.DataFrame) -> None:
    """A loss-making month is real operating data, not invalid input."""
    allowed = sample.copy()
    allowed.loc[allowed.index[0], "ebitda"] = -5_000.0
    assert len(validate_operating_data(allowed)) == len(sample)


def test_billable_hours_exceeding_paid_hours_is_rejected(sample: pd.DataFrame) -> None:
    broken = sample.copy()
    broken.loc[broken.index[0], "billable_hours"] = broken.loc[broken.index[0], "paid_hours"] * 2
    with pytest.raises(ValueError, match="more billable hours than paid"):
        validate_operating_data(broken)


def test_lost_exceeding_active_customers_is_rejected(sample: pd.DataFrame) -> None:
    broken = sample.copy()
    broken.loc[broken.index[0], "lost_customers"] = 10_000
    with pytest.raises(ValueError, match="more lost customers than active"):
        validate_operating_data(broken)


def test_duplicate_segment_rows_are_rejected(sample: pd.DataFrame) -> None:
    broken = pd.concat([sample, sample.head(1)], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate row"):
        validate_operating_data(broken)


def test_blank_region_is_rejected(sample: pd.DataFrame) -> None:
    broken = sample.copy()
    broken.loc[broken.index[0], "region"] = "   "
    with pytest.raises(ValueError, match="blank values"):
        validate_operating_data(broken)


def _without_optional_columns(sample: pd.DataFrame) -> pd.DataFrame:
    """Collapse to month/region/service_line grain, as a plain upload would be.

    Simply dropping `business_unit` would leave several rows per segment, which
    the validator correctly rejects as duplicates.
    """
    numeric = [c for c in sample.columns if c not in {"month", "region", "service_line"}]
    numeric = [c for c in numeric if c not in OPTIONAL_COLUMNS and c != "model_year"]
    return (
        sample.groupby(["month", "region", "service_line"], as_index=False)[numeric]
        .sum()
        .reset_index(drop=True)
    )


def test_collapsing_business_unit_without_aggregating_is_rejected(sample: pd.DataFrame) -> None:
    """Dropping the column changes the grain; the error must say what to do."""
    with pytest.raises(ValueError, match="Aggregate before upload"):
        validate_operating_data(sample.drop(columns=["business_unit"]))


def test_optional_columns_may_be_absent(sample: pd.DataFrame) -> None:
    trimmed = validate_operating_data(_without_optional_columns(sample))
    assert "business_unit" not in available_dimensions(trimmed)
    assert not has_grr_inputs(trimmed)
    rolled = monthly_rollup(trimmed)
    assert "gross_revenue_retention" not in rolled.columns
    # The governing KPIs still compute without the optional columns.
    assert kpi_summary(rolled)["Current"].notna().all()


# ---------------------------------------------------------------------------
# KPI correctness: reconcile to source data.
# ---------------------------------------------------------------------------


def test_monthly_kpis_reconcile_to_raw_source_rows(sample: pd.DataFrame, monthly) -> None:
    """Each KPI recomputed straight from the raw rows must match the rollup."""
    for month, block in sample.groupby("month"):
        row = monthly[monthly["month"] == month].iloc[0]
        assert row["route_density"] == pytest.approx(
            block["completed_jobs"].sum() * 100 / block["route_miles"].sum(), abs=TOL
        )
        assert row["utilization"] == pytest.approx(
            block["billable_hours"].sum() / block["paid_hours"].sum(), abs=TOL
        )
        assert row["gross_margin"] == pytest.approx(
            block["gross_profit"].sum() / block["revenue"].sum(), abs=TOL
        )
        assert row["recurring_mix"] == pytest.approx(
            block["recurring_revenue"].sum() / block["revenue"].sum(), abs=TOL
        )


def test_free_cash_flow_identity_holds(monthly) -> None:
    expected = (
        monthly["ebitda"]
        - monthly["capex"]
        - monthly["cash_taxes"]
        - monthly["cash_interest"]
        - monthly["delta_nwc"]
    )
    assert np.allclose(monthly["free_cash_flow"], expected, atol=TOL)
    assert np.allclose(
        monthly["fcf_conversion"], monthly["free_cash_flow"] / monthly["ebitda"], atol=TOL
    )


def test_churn_uses_the_opening_base_not_the_current_base(monthly) -> None:
    """The blueprint specifies the opening customer base."""
    assert math.isnan(monthly.iloc[0]["monthly_churn"]), "first month has no prior close"
    for index in range(1, len(monthly)):
        opening = monthly.iloc[index - 1]["active_customers"]
        expected = monthly.iloc[index]["lost_customers"] / opening
        assert monthly.iloc[index]["monthly_churn"] == pytest.approx(expected, abs=TOL)


def test_gross_revenue_retention_is_computed_when_inputs_exist(monthly) -> None:
    assert "gross_revenue_retention" in monthly.columns
    values = monthly["gross_revenue_retention"].dropna()
    assert not values.empty
    assert (values <= 1.0).all()


def test_dso_uses_days_in_month(monthly) -> None:
    row = monthly.iloc[0]
    days = pd.Timestamp(row["month"]).days_in_month
    assert row["dso"] == pytest.approx(
        row["accounts_receivable"] * days / row["revenue"], abs=TOL
    )


def test_period_window_is_a_ratio_of_sums_not_a_mean_of_ratios(sample, monthly) -> None:
    """Averaging monthly margins would weight a small month like a large one."""
    metric = next(m for m in METRIC_DEFINITIONS if m.key == "gross_margin")
    comparison = period_comparison(monthly, metric, Thresholds(), window=3)
    tail = monthly.sort_values("month").tail(3)

    ratio_of_sums = tail["gross_profit"].sum() / tail["revenue"].sum()
    mean_of_ratios = tail["gross_margin"].mean()
    assert comparison.current == pytest.approx(ratio_of_sums, abs=TOL)
    assert comparison.current != pytest.approx(mean_of_ratios, abs=1e-12)


def test_kpi_summary_covers_every_metric(monthly) -> None:
    summary = kpi_summary(monthly)
    assert list(summary["Key"]) == [m.key for m in METRIC_DEFINITIONS]
    assert set(GOVERNING_KEYS) <= set(summary["Key"])
    assert summary["Provenance"].eq("Actual (operating data)").all()


# ---------------------------------------------------------------------------
# Filters propagate consistently.
# ---------------------------------------------------------------------------


def test_filtering_then_rolling_matches_rolling_the_filtered_rows(sample) -> None:
    subset = filter_operating_data(sample, regions=["Midwest"], service_lines=["Leak Detection"])
    direct = sample[
        (sample["region"] == "Midwest") & (sample["service_line"] == "Leak Detection")
    ]
    pd.testing.assert_frame_equal(
        monthly_rollup(subset).reset_index(drop=True),
        monthly_rollup(direct).reset_index(drop=True),
    )


def test_date_filter_bounds_are_inclusive(sample) -> None:
    months = sorted(sample["month"].unique())
    subset = filter_operating_data(sample, start=months[2], end=months[4])
    assert sorted(subset["month"].unique()) == months[2:5]


def test_business_unit_filter_changes_every_downstream_view(sample) -> None:
    platform = filter_operating_data(sample, business_units=["Platform"])
    everything = filter_operating_data(sample)
    assert platform["revenue"].sum() < everything["revenue"].sum()
    assert monthly_rollup(platform)["revenue"].sum() < monthly_rollup(everything)["revenue"].sum()


def test_filters_that_match_nothing_return_empty_not_an_error(sample) -> None:
    empty = filter_operating_data(sample, regions=["Atlantis"])
    assert empty.empty
    rolled = monthly_rollup(empty)
    assert rolled.empty


def test_kpis_on_an_empty_selection_are_nan_not_zero(sample) -> None:
    """Zero would read as catastrophic performance; NaN reads as no data."""
    empty = monthly_rollup(filter_operating_data(sample, regions=["Atlantis"]))
    summary = kpi_summary(empty)
    assert summary["Current"].isna().all()


# ---------------------------------------------------------------------------
# Degenerate inputs.
# ---------------------------------------------------------------------------


def test_zero_denominator_yields_nan_not_infinity(sample) -> None:
    zeroed = sample.copy()
    zeroed["route_miles"] = 0.0
    zeroed["paid_hours"] = 0.0
    zeroed["billable_hours"] = 0.0
    rolled = monthly_rollup(validate_operating_data(zeroed))
    assert rolled["route_density"].isna().all()
    assert rolled["utilization"].isna().all()
    assert not np.isinf(rolled["route_density"].to_numpy(dtype=float)).any()


def test_missing_months_do_not_break_the_rollup(sample) -> None:
    months = sorted(sample["month"].unique())
    gapped = sample[~sample["month"].isin(months[5:8])]
    rolled = monthly_rollup(gapped)
    assert len(rolled) == len(months) - 3
    assert rolled["month"].is_monotonic_increasing


def test_single_month_window_has_no_prior_comparison(sample) -> None:
    months = sorted(sample["month"].unique())
    one = monthly_rollup(filter_operating_data(sample, start=months[0], end=months[0]))
    metric = next(m for m in METRIC_DEFINITIONS if m.key == "gross_margin")
    comparison = period_comparison(one, metric, Thresholds(), window=3)
    assert comparison.months_prior == 0
    assert math.isnan(comparison.prior)
    assert math.isnan(comparison.change)
    assert np.isfinite(comparison.current)


def test_zero_ebitda_month_does_not_crash_fcf_conversion(sample) -> None:
    zeroed = sample.copy()
    zeroed["ebitda"] = 0.0
    rolled = monthly_rollup(validate_operating_data(zeroed))
    assert rolled["fcf_conversion"].isna().all()


# ---------------------------------------------------------------------------
# Exceptions.
# ---------------------------------------------------------------------------


def test_healthy_aggregate_produces_no_exceptions(monthly) -> None:
    assert exception_report(monthly).empty


def test_weak_branch_produces_ranked_exceptions(sample) -> None:
    weak = monthly_rollup(filter_operating_data(sample, regions=["Mountain West"]))
    exceptions = exception_report(weak)

    assert not exceptions.empty
    breached = set(exceptions["Metric"])
    assert "Route density" in breached
    assert "Customer churn (monthly)" in breached
    assert set(exceptions["Severity"]) <= {"High", "Medium", "Unavailable"}
    assert exceptions["Action"].str.len().gt(0).all()


def test_exception_gap_sign_follows_metric_direction(sample) -> None:
    weak = monthly_rollup(filter_operating_data(sample, regions=["Mountain West"]))
    exceptions = exception_report(weak)
    for _, row in exceptions.iterrows():
        if row["Severity"] == "Unavailable":
            continue
        assert row["Gap"] < 0, f"{row['Metric']} breached but reports a positive gap"


def test_uncomputable_metric_is_reported_as_unavailable_not_passing(sample) -> None:
    months = sorted(sample["month"].unique())
    one = monthly_rollup(filter_operating_data(sample, start=months[0], end=months[0]))
    exceptions = exception_report(one)
    churn = exceptions[exceptions["Metric"] == "Customer churn (monthly)"]
    assert len(churn) == 1
    assert churn.iloc[0]["Severity"] == "Unavailable"


def test_thresholds_drive_the_exception_set(monthly) -> None:
    strict = Thresholds(route_density=999.0, utilization=0.99)
    exceptions = exception_report(monthly, strict)
    assert {"Route density", "Billable utilization"} <= set(exceptions["Metric"])


# ---------------------------------------------------------------------------
# Modelled views: read from Phase 3, never recomputed.
# ---------------------------------------------------------------------------


def test_capital_structure_reads_the_model_schedule() -> None:
    scenario = SCENARIOS["base"]
    result = run_scenario(scenario)
    view = capital_structure_view(result, scenario)

    assert list(view["Model Year"]) == list(result.schedule["Year"])
    assert np.allclose(view["Ending Debt ($M)"], result.schedule["Ending Debt"], atol=TOL)
    assert np.allclose(view["Net Leverage (x)"], result.schedule["Net Leverage"], atol=TOL)
    ceiling = scenario.assumptions.max_pro_forma_leverage
    assert np.allclose(
        view["Headroom (turns)"], ceiling - result.schedule["Gross Leverage"], atol=TOL
    )
    assert (view["Incremental Debt Capacity ($M)"] >= 0).all()


def test_capital_structure_reflects_the_selected_scenario() -> None:
    base = capital_structure_view(run_scenario(SCENARIOS["base"]), SCENARIOS["base"])
    downside = capital_structure_view(
        run_scenario(SCENARIOS["downside"]), SCENARIOS["downside"]
    )
    assert downside["Ending Debt ($M)"].iloc[-1] > base["Ending Debt ($M)"].iloc[-1]


def test_synergy_view_matches_the_model_split() -> None:
    result = run_scenario(SCENARIOS["base"])
    view = synergy_realization_view(result)
    assert np.allclose(
        view["Realized Synergies ($M)"], result.schedule["Realized Synergies"], atol=TOL
    )
    total = (
        view["Platform EBITDA ($M)"] + view["Add-on EBITDA ($M)"] + view["Realized Synergies ($M)"]
    )
    assert np.allclose(total, view["Total EBITDA ($M)"], atol=TOL)


def test_plan_vs_actual_uses_model_revenue_as_plan(monthly, period) -> None:
    result = run_scenario(SCENARIOS["base"])
    comparison = plan_vs_actual(monthly, result, period)

    assert len(comparison) == len(result.schedule)
    assert np.allclose(comparison["Plan Revenue ($M)"], result.schedule["Revenue"], atol=TOL)
    assert np.allclose(comparison["Plan EBITDA ($M)"], result.schedule["EBITDA"], atol=TOL)
    assert (comparison["Months Covered"] == 12).all()
    assert np.allclose(
        comparison["Revenue Variance ($M)"],
        comparison["Actual Revenue ($M)"] - comparison["Plan Revenue ($M)"],
        atol=TOL,
    )
    # Calibration keeps variances small and signed both ways.
    assert comparison["Revenue Variance %"].abs().max() < 0.05


def test_plan_vs_actual_shows_partial_year_coverage(anchored, period) -> None:
    months = sorted(anchored["month"].unique())
    partial = monthly_rollup(filter_operating_data(anchored, start=months[0], end=months[5]))
    comparison = plan_vs_actual(partial, run_scenario(SCENARIOS["base"]), period)
    assert len(comparison) == 1
    assert comparison.iloc[0]["Months Covered"] == 6
    assert comparison.iloc[0]["Revenue Variance ($M)"] < 0  # half a year, not a miss


def test_plan_vs_actual_is_empty_when_no_period_overlaps(monthly, period) -> None:
    shifted = monthly.copy()
    shifted["month"] = shifted["month"] + pd.DateOffset(years=40)
    shifted["model_year"] = shifted["model_year"] + 40
    result = run_scenario(SCENARIOS["base"])
    assert plan_vs_actual(shifted, result, period).empty


# ---------------------------------------------------------------------------
# Model-period anchoring. The mapping must be immune to display filters.
# ---------------------------------------------------------------------------


def test_unfiltered_dataset_maps_to_the_expected_model_years(anchored, period) -> None:
    schedule = run_scenario(SCENARIOS["base"]).schedule
    comparison = plan_vs_actual(monthly_rollup(anchored), run_scenario(SCENARIOS["base"]), period)

    assert list(comparison["Model Year"]) == list(schedule["Year"])
    assert (comparison["Months Covered"] == 12).all()
    assert period.anchor == pd.Timestamp("2024-01-01")


def test_mid_horizon_filter_preserves_model_year_numbering(anchored, period) -> None:
    """The regression this fix exists for: filtering must not restart at Year 1."""
    result = run_scenario(SCENARIOS["base"])
    mid = filter_operating_data(anchored, start=pd.Timestamp("2026-07-01"))
    comparison = plan_vs_actual(monthly_rollup(mid), result, period)

    # July 2026 is the seventh month of Model Year 3, not the first of Year 1.
    assert list(comparison["Model Year"]) == [3, 4, 5]
    assert comparison.iloc[0]["Model Year"] == 3
    assert list(comparison["Months Covered"]) == [6, 12, 12]

    # Plan figures must be Year 3/4/5's, not Year 1/2/3's.
    schedule = result.schedule.set_index("Year")
    for _, row in comparison.iterrows():
        assert row["Plan Revenue ($M)"] == pytest.approx(
            schedule.loc[int(row["Model Year"]), "Revenue"], abs=TOL
        )


def test_window_crossing_a_year_boundary_allocates_months_correctly(anchored, period) -> None:
    """Jul 2025 – Jun 2026 straddles Model Years 2 and 3: six months each."""
    window = filter_operating_data(
        anchored, start=pd.Timestamp("2025-07-01"), end=pd.Timestamp("2026-06-01")
    )
    comparison = plan_vs_actual(monthly_rollup(window), run_scenario(SCENARIOS["base"]), period)

    assert list(comparison["Model Year"]) == [2, 3]
    assert list(comparison["Months Covered"]) == [6, 6]


def test_region_and_service_filters_do_not_change_period_mapping(anchored, period) -> None:
    result = run_scenario(SCENARIOS["base"])
    full = plan_vs_actual(monthly_rollup(anchored), result, period)
    narrowed = plan_vs_actual(
        monthly_rollup(
            filter_operating_data(
                anchored, regions=["Midwest"], service_lines=["Leak Detection"]
            )
        ),
        result,
        period,
    )

    assert list(narrowed["Model Year"]) == list(full["Model Year"])
    assert list(narrowed["Months Covered"]) == list(full["Months Covered"])
    assert list(narrowed["Plan Revenue ($M)"]) == list(full["Plan Revenue ($M)"])
    # Only the actuals shrink.
    assert narrowed["Actual Revenue ($M)"].sum() < full["Actual Revenue ($M)"].sum()


def test_business_unit_filter_does_not_change_period_mapping(anchored, period) -> None:
    result = run_scenario(SCENARIOS["base"])
    platform = plan_vs_actual(
        monthly_rollup(filter_operating_data(anchored, business_units=["Platform"])),
        result,
        period,
    )
    assert list(platform["Model Year"]) == [1, 2, 3, 4, 5]


def test_explicit_anchor_overrides_the_default(sample) -> None:
    explicit = resolve_model_anchor(sample, "2023-01-01")
    assert explicit.anchor == pd.Timestamp("2023-01-01")
    assert explicit.basis == "explicit model start date"

    shifted = assign_model_period(sample, explicit)
    # With Year 1 starting a year earlier, Jan 2024 data is now Year 2.
    january_2024 = shifted[shifted["month"] == pd.Timestamp("2024-01-01")]
    assert (january_2024["model_year"] == 2).all()


def test_anchor_is_normalized_to_the_first_of_the_month(sample) -> None:
    assert resolve_model_anchor(sample, "2024-01-17").anchor == pd.Timestamp("2024-01-01")


def test_default_anchor_ignores_filtering(anchored) -> None:
    """Anchoring off a filtered frame is exactly the bug; prove the guard."""
    mid = filter_operating_data(anchored, start=pd.Timestamp("2026-07-01"))
    assert resolve_model_anchor(mid).anchor == pd.Timestamp("2026-07-01")
    # ...which is why production resolves from the full dataset instead:
    assert resolve_model_anchor(anchored).anchor == pd.Timestamp("2024-01-01")


@pytest.mark.parametrize("bad", ["not-a-date", "2024-13-01", "", "31/02/2024"])
def test_invalid_anchor_dates_fail_cleanly(sample, bad: str) -> None:
    with pytest.raises(ValueError, match="Invalid model start date"):
        resolve_model_anchor(sample, bad)


def test_anchor_cannot_be_resolved_from_an_empty_dataset() -> None:
    with pytest.raises(ValueError, match="no months"):
        resolve_model_anchor(pd.DataFrame({"month": []}))


def test_plan_comparison_refuses_to_infer_the_period(monthly, period) -> None:
    """Requirement: never silently infer Year 1 from post-filter data."""
    stripped = monthly.drop(columns=["model_year"])
    with pytest.raises(ValueError, match="must not be inferred from filtered data"):
        plan_vs_actual(stripped, run_scenario(SCENARIOS["base"]), period)


def test_model_year_is_not_summed_during_rollup(anchored) -> None:
    rolled = monthly_rollup(anchored)
    assert rolled["model_year"].max() == 5, "period label was aggregated as a measure"
    assert list(rolled["model_year"]) == sorted(rolled["model_year"])


def test_plan_output_carries_the_anchor_for_traceability(monthly, period) -> None:
    comparison = plan_vs_actual(monthly, run_scenario(SCENARIOS["base"]), period)
    assert (comparison["Model Anchor"] == "2024-01").all()
    assert "Model Year 1 begins Jan 2024" in period.label


def test_churn_definition_discloses_the_overlapping_customer_caveat() -> None:
    churn = next(m for m in METRIC_DEFINITIONS if m.key == "monthly_churn")
    text = churn.definition.lower()
    assert "counted more than once" in text
    assert "customer-level identifier" in text
    # The disclosure must appear in the lineage table the dashboard renders.
    table = metric_definitions_table()
    row = table[table["Key"] == "monthly_churn"].iloc[0]
    assert "customer-level identifier" in row["Definition"]


def test_organic_growth_needs_thirteen_months(sample) -> None:
    months = sorted(sample["month"].unique())
    short = monthly_rollup(filter_operating_data(sample, start=months[0], end=months[5]))
    assert organic_growth_view(short)["revenue_yoy"].isna().all()

    full = monthly_rollup(sample)
    assert organic_growth_view(full)["revenue_yoy"].notna().any()


# ---------------------------------------------------------------------------
# Dimension rollups, definitions, and output generation.
# ---------------------------------------------------------------------------


def test_dimension_rollup_revenue_shares_sum_to_one(sample) -> None:
    for dimension in available_dimensions(sample):
        table = dimension_rollup(sample, dimension)
        assert table["revenue_share"].sum() == pytest.approx(1.0, abs=1e-9)
        assert table["revenue"].sum() == pytest.approx(sample["revenue"].sum(), rel=1e-12)


def test_dimension_rollup_rejects_an_absent_dimension(sample) -> None:
    trimmed = _without_optional_columns(sample)
    with pytest.raises(ValueError, match="not present in this dataset"):
        dimension_rollup(trimmed, "business_unit")


def test_metric_definitions_table_labels_provenance() -> None:
    table = metric_definitions_table()
    assert len(table) == len(METRIC_DEFINITIONS)
    assert table["Target provenance"].eq("Author-defined target").all()
    assert table["Value provenance"].eq("Actual (operating data)").all()
    assert table["Definition"].str.len().gt(20).all()


def test_write_operating_outputs_emits_every_table(tmp_path, sample) -> None:
    written = write_operating_outputs(sample, tmp_path)
    expected = {
        "operating_data.csv",
        "monthly_kpis.csv",
        "kpi_summary.csv",
        "exceptions.csv",
        "metric_definitions.csv",
        "organic_growth.csv",
        "plan_vs_actual.csv",
        "capital_structure.csv",
        "synergy_realization.csv",
        "performance_by_region.csv",
        "performance_by_service_line.csv",
        "performance_by_business_unit.csv",
    }
    assert expected <= set(written)
    for path in written.values():
        assert path.exists() and path.stat().st_size > 0


def test_cli_reports_a_missing_file_cleanly(tmp_path) -> None:
    with pytest.raises(SystemExit, match="input file not found"):
        main(["--input", str(tmp_path / "absent.csv"), "--output-dir", str(tmp_path)])


def test_cli_reports_a_bad_schema_cleanly(tmp_path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("col1,col2\n1,2\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="none of the 18 required columns"):
        main(["--input", str(path), "--output-dir", str(tmp_path)])


def test_cli_writes_outputs_for_the_sample_dataset(tmp_path) -> None:
    main(["--output-dir", str(tmp_path)])
    assert (tmp_path / "kpi_summary.csv").exists()
    assert (tmp_path / "exceptions.csv").exists()


def test_partial_schema_error_names_only_the_missing_columns(sample) -> None:
    broken = sample.drop(columns=["revenue"])
    with pytest.raises(ValueError, match="missing required column") as info:
        validate_operating_data(broken)
    message = str(info.value)
    assert "revenue" in message
    assert "accounts_receivable" not in message  # present columns are not listed


def test_generated_outputs_are_reproducible(tmp_path, sample) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    write_operating_outputs(sample, first)
    write_operating_outputs(sample, second)
    for path in sorted(first.iterdir()):
        assert path.read_bytes() == (second / path.name).read_bytes(), path.name
