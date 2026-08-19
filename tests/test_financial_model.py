from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from buy_and_build_model import (
    DEFAULT_ADDONS,
    DEFAULT_ASSUMPTIONS,
    SCENARIOS,
    AddOn,
    Assumptions,
    DriverGroup,
    ModelResult,
    add_on_entry_sensitivity,
    build_model,
    load_scenarios,
    one_at_a_time_diagnostic,
    return_bridge,
    run_scenario,
    scenario_comparison,
    sensitivity_axes,
    sensitivity_grid,
    severe_driver_groups,
    shapley_attribution,
    solve_irr,
    sources_and_uses,
    write_scenario_outputs,
)

TOL = 1e-9


# --------------------------------------------------------------------------
# Golden base case — the documented economics must not drift.
# --------------------------------------------------------------------------


def test_base_case_reproduces_documented_returns() -> None:
    result = build_model()

    assert len(result.schedule) == 5
    assert result.returns["gross_moic"] == pytest.approx(4.5388573508, rel=1e-8)
    assert result.returns["gross_irr"] == pytest.approx(0.3532851206, rel=1e-8)
    assert result.schedule.iloc[-1]["Ending Debt"] == pytest.approx(1.5602039747, rel=1e-8)


def test_base_case_sources_match_blueprint() -> None:
    """PROJECT_BLUEPRINT.md: $12.0M EV, $0.24M fees, $6.0M debt, $6.24M equity."""
    returns = build_model().returns
    assert returns["platform_enterprise_value"] == pytest.approx(12.0)
    assert returns["platform_transaction_fees"] == pytest.approx(0.24)
    assert returns["initial_debt"] == pytest.approx(6.0)
    assert returns["initial_sponsor_equity"] == pytest.approx(6.24)
    assert returns["blended_entry_multiple"] == pytest.approx(4.9663, abs=1e-4)


def test_base_case_keeps_year_end_leverage_below_2_5x() -> None:
    """The blueprint justifies debt-funded add-ons with this leverage claim."""
    result = build_model()
    assert result.returns["peak_gross_leverage"] < 2.5


# --------------------------------------------------------------------------
# Sources and uses.
# --------------------------------------------------------------------------


def test_sources_and_uses_balance_at_every_closing() -> None:
    result = build_model()
    for event in result.funding:
        assert event.total_sources == pytest.approx(event.total_uses, abs=TOL)


def test_sources_and_uses_table_totals_reconcile() -> None:
    result = build_model()
    table = sources_and_uses(result)
    total = table.iloc[-1]

    assert table.iloc[0]["Closing"] == "Platform"
    assert len(table) == len(DEFAULT_ADDONS) + 2  # platform + add-ons + total row
    assert total["Total Uses"] == pytest.approx(total["Total Sources"], abs=TOL)
    assert total["Purchase Price"] == pytest.approx(
        result.returns["platform_enterprise_value"]
        + result.returns["total_add_on_enterprise_value"],
        abs=TOL,
    )
    assert total["Sponsor Equity"] == pytest.approx(
        result.returns["total_sponsor_equity_invested"], abs=TOL
    )
    assert total["Debt Drawn"] == pytest.approx(result.returns["total_debt_raised"], abs=TOL)


def test_add_on_uses_include_transaction_fees() -> None:
    result = build_model()
    add_on = DEFAULT_ADDONS[0]
    event = next(e for e in result.funding if e.label == add_on.name)
    assert event.purchase_price == pytest.approx(add_on.enterprise_value)
    assert event.transaction_fees == pytest.approx(
        add_on.enterprise_value * DEFAULT_ASSUMPTIONS.transaction_fee_pct_ev
    )


# --------------------------------------------------------------------------
# Formula reconciliation — each schedule line is an identity, not an estimate.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_name", sorted(SCENARIOS))
def test_free_cash_flow_identity_reconciles(scenario_name: str) -> None:
    result = run_scenario(SCENARIOS[scenario_name])
    for _, row in result.schedule.iterrows():
        expected = (
            row["EBITDA"]
            - row["Maintenance Capex"]
            - row["NWC Investment"]
            - row["Cash Interest"]
            - row["Cash Taxes"]
        )
        assert row["Free Cash Flow"] == pytest.approx(expected, abs=TOL)


def test_interest_accrues_on_average_debt() -> None:
    a = DEFAULT_ASSUMPTIONS
    result = build_model()
    for _, row in result.schedule.iterrows():
        opening_debt = row["Ending Debt"] + row["Debt Paydown"] - row["Revolver Draw"]
        expected = a.interest_rate * (opening_debt - 0.5 * row["Free Cash Flow"])
        assert row["Cash Interest"] == pytest.approx(expected, abs=TOL)


def test_cash_taxes_follow_the_documented_definition() -> None:
    """Blueprint: 25% of EBITDA less maintenance capex and cash interest."""
    a = DEFAULT_ASSUMPTIONS
    result = build_model()
    for _, row in result.schedule.iterrows():
        expected = a.tax_rate * (row["EBITDA"] - row["Maintenance Capex"] - row["Cash Interest"])
        assert row["Cash Taxes"] == pytest.approx(max(expected, 0.0), abs=TOL)


def test_ebitda_components_sum_to_total() -> None:
    result = build_model()
    for _, row in result.schedule.iterrows():
        assert row["EBITDA"] == pytest.approx(
            row["Platform EBITDA"] + row["Add-on EBITDA"] + row["Realized Synergies"], abs=TOL
        )


def test_debt_roll_forward_reconciles() -> None:
    a = DEFAULT_ASSUMPTIONS
    result = build_model()
    prior = result.returns["initial_debt"]
    for _, row in result.schedule.iterrows():
        expected = prior + row["Acquisition Debt Draw"] - row["Debt Paydown"] + row["Revolver Draw"]
        assert row["Ending Debt"] == pytest.approx(expected, abs=TOL)
        prior = row["Ending Debt"]
    assert prior == pytest.approx(result.returns["terminal_debt"], abs=TOL)
    assert a.forecast_years == len(result.schedule)


# --------------------------------------------------------------------------
# Return bridge.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_name", sorted(SCENARIOS))
def test_return_bridge_reconciles(scenario_name: str) -> None:
    scenario = SCENARIOS[scenario_name]
    result = run_scenario(scenario)
    bridge = return_bridge(result, scenario.add_ons)

    walk = bridge[bridge["Component"] != "Exit equity value"]["Value"].sum()
    assert walk == pytest.approx(result.returns["terminal_equity_value"], abs=TOL)
    assert bridge.iloc[-1]["Cumulative"] == pytest.approx(bridge.iloc[-1]["Value"], abs=TOL)


def test_return_bridge_separates_the_three_required_drivers() -> None:
    result = build_model()
    bridge = return_bridge(result)
    components = set(bridge["Component"])
    assert {
        "Platform organic EBITDA growth",
        "Add-on organic EBITDA growth",
        "Synergy realization",
        "Multiple change",
        "Net debt paydown",
    } <= components


def test_multiple_change_is_zero_when_exit_equals_blended_entry() -> None:
    result = build_model()
    entry = result.returns["blended_entry_multiple"]
    at_entry = build_model(replace(DEFAULT_ASSUMPTIONS, terminal_multiple=entry))
    bridge = return_bridge(at_entry)
    change = bridge.loc[bridge["Component"] == "Multiple change", "Value"].item()
    assert change == pytest.approx(0.0, abs=TOL)


# --------------------------------------------------------------------------
# Debt, cash, and the leverage governor.
# --------------------------------------------------------------------------


def test_debt_never_becomes_negative() -> None:
    result = build_model(Assumptions(annual_organic_growth=0.15, terminal_multiple=6.0))
    assert (result.schedule["Ending Debt"] >= 0).all()


def test_cash_never_becomes_negative() -> None:
    result = build_model(Assumptions(interest_rate=0.25, annual_organic_growth=0.0))
    assert (result.schedule["Ending Cash"] >= 0).all()


def test_excess_cash_accumulates_once_debt_is_repaid() -> None:
    """Without a cash balance, post-repayment free cash flow vanishes."""
    result = build_model(
        Assumptions(initial_debt_to_ebitda=0.5, annual_organic_growth=0.08),
        add_ons=(),
    )
    final = result.schedule.iloc[-1]
    assert final["Ending Debt"] == pytest.approx(0.0, abs=TOL)
    assert final["Ending Cash"] > 0
    assert result.returns["terminal_equity_value"] == pytest.approx(
        result.returns["terminal_enterprise_value"] + final["Ending Cash"], abs=TOL
    )


def test_leverage_governor_does_not_bind_in_the_base_case() -> None:
    """The documented case is fully debt-funded; the governor must be inert."""
    result = build_model()
    assert (result.schedule["Sponsor Equity Funded"] == 0).all()
    assert result.returns["total_sponsor_equity_invested"] == pytest.approx(6.24)


def test_leverage_governor_funds_the_overflow_with_equity() -> None:
    a = Assumptions(initial_debt_to_ebitda=2.5, max_pro_forma_leverage=2.5)
    oversized = (AddOn("Oversized", close_year=2, revenue_at_close=20.0, ebitda_margin=0.18),)
    result = build_model(a, oversized)

    year_two = result.schedule.iloc[1]
    assert year_two["Sponsor Equity Funded"] > 0
    opening_debt = year_two["Ending Debt"] + year_two["Debt Paydown"] - year_two["Revolver Draw"]
    assert opening_debt / year_two["EBITDA"] <= a.max_pro_forma_leverage + TOL
    assert (
        result.returns["total_sponsor_equity_invested"] > result.returns["initial_sponsor_equity"]
    )


def test_leverage_stress_still_reconciles_and_stays_solvent() -> None:
    a = Assumptions(initial_debt_to_ebitda=3.9, max_pro_forma_leverage=4.0, interest_rate=0.12)
    result = build_model(a)
    assert (result.schedule["Ending Debt"] >= 0).all()
    assert (result.schedule["Ending Cash"] >= 0).all()
    bridge = return_bridge(result)
    walk = bridge[bridge["Component"] != "Exit equity value"]["Value"].sum()
    assert walk == pytest.approx(result.returns["terminal_equity_value"], abs=TOL)


# --------------------------------------------------------------------------
# Structural cases: no add-ons, delayed add-ons, high interest.
# --------------------------------------------------------------------------


def test_no_addons_still_builds_valid_schedule() -> None:
    result = build_model(add_ons=())
    assert (result.schedule["Acquisition Debt Draw"] == 0).all()
    assert result.returns["total_add_on_enterprise_value"] == 0
    assert math.isfinite(result.returns["gross_irr"])
    assert result.returns["blended_entry_multiple"] == pytest.approx(
        DEFAULT_ASSUMPTIONS.platform_entry_multiple
    )


def test_delayed_addons_defer_debt_draws_and_synergies() -> None:
    delayed = tuple(replace(add_on, close_year=5) for add_on in DEFAULT_ADDONS)
    result = build_model(add_ons=delayed)

    assert (result.schedule.iloc[:4]["Acquisition Debt Draw"] == 0).all()
    assert (result.schedule.iloc[:4]["Realized Synergies"] == 0).all()
    assert result.schedule.iloc[4]["Acquisition Debt Draw"] > 0
    # Only 50% of run-rate synergies are captured in the acquisition year.
    assert result.schedule.iloc[4]["Realized Synergies"] > 0
    assert result.returns["gross_moic"] < build_model().returns["gross_moic"]


def test_high_interest_reduces_returns_without_breaking_the_model() -> None:
    base = build_model()
    stressed = build_model(Assumptions(interest_rate=0.18))

    assert stressed.returns["gross_moic"] < base.returns["gross_moic"]
    assert stressed.returns["terminal_debt"] > base.returns["terminal_debt"]
    assert (stressed.schedule["Cash Interest"] > 0).all()
    assert (stressed.schedule["Ending Debt"] >= 0).all()


def test_shorter_forecast_horizon_is_supported() -> None:
    short = tuple(add_on for add_on in DEFAULT_ADDONS if add_on.close_year <= 3)
    result = build_model(Assumptions(forecast_years=3), short)
    assert len(result.schedule) == 3
    assert math.isfinite(result.returns["gross_irr"])


# --------------------------------------------------------------------------
# IRR.
# --------------------------------------------------------------------------


def test_irr_matches_cagr_when_equity_is_a_single_upfront_flow() -> None:
    result = build_model()
    moic = result.returns["gross_moic"]
    assert result.returns["gross_irr"] == pytest.approx(moic ** (1 / 5) - 1, abs=1e-9)


def test_irr_diverges_from_the_moic_cagr_when_equity_is_staged() -> None:
    """A MOIC-derived CAGR silently assumes every dollar was in at time zero.

    Equity drawn in year 2 is outstanding for three years, not five, so the
    true IRR is materially higher than the naive conversion. The direction is
    less important than the fact that the shortcut is simply wrong once the
    leverage governor stages equity.
    """
    a = Assumptions(initial_debt_to_ebitda=2.5, max_pro_forma_leverage=2.5)
    oversized = (AddOn("Oversized", close_year=2, revenue_at_close=20.0, ebitda_margin=0.18),)
    result = build_model(a, oversized)

    staged = sum(e.sponsor_equity for e in result.funding if e.year > 0)
    assert staged > 0
    naive = result.returns["gross_moic"] ** (1 / 5) - 1
    assert result.returns["gross_irr"] > naive + 0.01


def test_solve_irr_returns_nan_without_a_sign_change() -> None:
    assert math.isnan(solve_irr([-1.0, -1.0, -1.0]))


# --------------------------------------------------------------------------
# Input validation.
# --------------------------------------------------------------------------


def test_invalid_addon_year_is_rejected() -> None:
    with pytest.raises(ValueError, match="close_year outside forecast"):
        build_model(add_ons=(AddOn("Late", 6, 2.0, 0.20),))


def test_full_first_year_synergy_realization_is_accepted() -> None:
    """100% realization in the acquisition year is a legitimate input.

    An owner salary eliminated at close is captured immediately rather than
    phased, so the closed interval [0, 1] must be accepted. Guards a regression
    in which this field was validated on [0, 1) and 1.0 was rejected.
    """
    a = replace(DEFAULT_ASSUMPTIONS, first_year_synergy_realization=1.0)
    result = build_model(a)

    assert len(result.schedule) == a.forecast_years
    assert math.isfinite(result.returns["gross_moic"])

    # Year 2 is Add-on A's close year: synergies now run at the full rate.
    acquisition_year = result.schedule.iloc[1]
    add_on = DEFAULT_ADDONS[0]
    expected = add_on.revenue_at_close * a.add_on_sgna_pct_revenue * a.sgna_synergy_capture
    assert acquisition_year["Realized Synergies"] == pytest.approx(expected, abs=TOL)

    # And strictly more than the base case's 50% first-year phase-in.
    base_year_two = build_model().schedule.iloc[1]
    assert acquisition_year["Realized Synergies"] > base_year_two["Realized Synergies"]


@pytest.mark.parametrize("value", [-0.01, -1.0, 1.01, 1.5])
def test_first_year_synergy_realization_outside_zero_to_one_is_rejected(
    value: float,
) -> None:
    """The bound is inclusive at 1.0, not absent."""
    with pytest.raises(ValueError, match="first_year_synergy_realization"):
        build_model(replace(DEFAULT_ASSUMPTIONS, first_year_synergy_realization=value))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"forecast_years": 0}, "forecast_years must be positive"),
        ({"interest_rate": 1.5}, "interest_rate must be between 0 and 1"),
        ({"tax_rate": -0.1}, "tax_rate must be between 0 and 1"),
        (
            {"first_year_synergy_realization": 1.5},
            "first_year_synergy_realization must be between 0 and 1 inclusive",
        ),
        # Neighbouring percentage bounds stay half-open: 100% is degenerate for
        # these, so the realization fix must not have loosened them.
        ({"sgna_synergy_capture": 1.0}, "sgna_synergy_capture must be between 0 and 1"),
        ({"platform_margin_cap": 1.0}, "platform_margin_cap must be between 0 and 1"),
        ({"terminal_multiple": 0.0}, "terminal_multiple must be positive"),
        ({"platform_revenue": -1.0}, "platform_revenue must be positive"),
        ({"initial_debt_to_ebitda": -1.0}, "must not be negative"),
        ({"initial_debt_to_ebitda": 5.0}, "exceeds max_pro_forma_leverage"),
        ({"platform_margin_cap": 0.10}, "below the entry EBITDA margin"),
    ],
)
def test_invalid_assumptions_are_rejected(overrides: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_model(replace(DEFAULT_ASSUMPTIONS, **overrides))


@pytest.mark.parametrize(
    ("add_on", "message"),
    [
        (AddOn("Zero", 2, 0.0, 0.18), "revenue and margin must be positive"),
        (AddOn("Rich", 2, 2.0, 1.20), "ebitda_margin must be between 0 and 1"),
        (AddOn("Free", 2, 2.0, 0.18, entry_multiple=0.0), "entry_multiple must be positive"),
    ],
)
def test_invalid_addons_are_rejected(add_on: AddOn, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_model(add_ons=(add_on,))


def test_duplicate_addon_names_are_rejected() -> None:
    twin = AddOn("Twin", 2, 2.0, 0.18)
    with pytest.raises(ValueError, match="duplicate add-on name"):
        build_model(add_ons=(twin, twin))


# --------------------------------------------------------------------------
# Scenarios.
# --------------------------------------------------------------------------


def test_downside_scenario_matches_the_blueprint_guardrail() -> None:
    """Blueprint: 3% growth, half synergy capture, 9% interest, 6.0x mark."""
    a = SCENARIOS["downside"].assumptions
    assert a.annual_organic_growth == pytest.approx(0.03)
    assert a.sgna_synergy_capture == pytest.approx(DEFAULT_ASSUMPTIONS.sgna_synergy_capture / 2)
    assert a.interest_rate == pytest.approx(0.09)
    assert a.terminal_multiple == pytest.approx(6.0)


def test_upside_scenario_does_not_re_rate_the_exit_multiple() -> None:
    upside = SCENARIOS["upside"]
    assert upside.assumptions.terminal_multiple == DEFAULT_ASSUMPTIONS.terminal_multiple
    assert "Author-defined" in upside.source


def test_scenarios_rank_as_expected() -> None:
    results = {name: run_scenario(scenario) for name, scenario in SCENARIOS.items()}
    assert (
        results["downside"].returns["gross_moic"]
        < results["base"].returns["gross_moic"]
        < results["upside"].returns["gross_moic"]
    )
    for result in results.values():
        assert (result.schedule["Ending Debt"] >= 0).all()


def test_scenario_comparison_covers_every_scenario() -> None:
    results: dict[str, ModelResult] = {
        name: run_scenario(scenario) for name, scenario in SCENARIOS.items()
    }
    table = scenario_comparison(results)
    assert set(table["Scenario"]) == set(SCENARIOS)
    assert "Gross IRR" in table.columns


def test_scenarios_load_from_json_without_editing_formulas(tmp_path) -> None:
    path = tmp_path / "scenarios.json"
    path.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "name": "credit-stress",
                        "description": "12% interest, no add-ons",
                        "assumptions": {"interest_rate": 0.12},
                        "add_ons": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = load_scenarios(path)
    scenario = registry["credit-stress"]

    assert scenario.assumptions.interest_rate == pytest.approx(0.12)
    # Omitted keys keep their base value.
    assert scenario.assumptions.annual_organic_growth == pytest.approx(0.05)
    assert scenario.add_ons == ()
    assert run_scenario(scenario).returns["gross_moic"] < build_model().returns["gross_moic"]


def test_scenario_file_rejects_unknown_assumption(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps({"scenarios": [{"name": "typo", "assumptions": {"intrest_rate": 0.12}}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown assumption"):
        load_scenarios(path)


def test_scenario_file_requires_a_scenarios_key(tmp_path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="expected an object with a 'scenarios' key"):
        load_scenarios(path)


# --------------------------------------------------------------------------
# Sensitivities and outputs.
# --------------------------------------------------------------------------


def test_sensitivity_grid_is_monotonic_in_both_axes() -> None:
    grid = sensitivity_grid(DEFAULT_ASSUMPTIONS, DEFAULT_ADDONS)
    multiple_cols = [c for c in grid.columns if c != "Organic Growth"]

    assert len(grid) == 5
    assert len(multiple_cols) == 5
    for _, row in grid.iterrows():
        values = [row[c] for c in multiple_cols]
        assert values == sorted(values), "MOIC must rise with the exit multiple"
    for col in multiple_cols:
        values = list(grid[col])
        assert values == sorted(values), "MOIC must rise with organic growth"


def test_sensitivity_grid_center_matches_the_base_case() -> None:
    grid = sensitivity_grid(DEFAULT_ASSUMPTIONS, DEFAULT_ADDONS)
    center = grid.loc[grid["Organic Growth"] == 0.05, "6.5x"].item()
    assert center == pytest.approx(build_model().returns["gross_moic"], rel=1e-9)


def test_sensitivity_grid_rejects_unknown_metric() -> None:
    with pytest.raises(ValueError, match="unsupported sensitivity metric"):
        sensitivity_grid(DEFAULT_ASSUMPTIONS, DEFAULT_ADDONS, metric="gross_tvpi")


def test_write_scenario_outputs_emits_the_full_output_set(tmp_path) -> None:
    scenario = SCENARIOS["base"]
    write_scenario_outputs(scenario, run_scenario(scenario), tmp_path)

    for filename in (
        "five_year_pro_forma.csv",
        "sources_and_uses.csv",
        "return_bridge.csv",
        "sensitivity_moic.csv",
        "sensitivity_irr.csv",
        "return_summary.json",
        "assumptions.json",
    ):
        assert (tmp_path / filename).exists(), filename

    recorded = json.loads((tmp_path / "assumptions.json").read_text(encoding="utf-8"))
    assert recorded["scenario"] == "base"
    assert recorded["source"].startswith("PROJECT_BLUEPRINT.md")
    assert recorded["assumptions"]["interest_rate"] == pytest.approx(0.08)
    assert len(recorded["add_ons"]) == len(DEFAULT_ADDONS)


# ---------------------------------------------------------------------------
# Phase 6: scenario-centred sensitivities, negative growth, synergy add-backs,
# leverage-limit warnings, and leverage reporting points.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_name", sorted(SCENARIOS))
def test_sensitivity_grid_is_centred_on_its_own_scenario(scenario_name: str) -> None:
    scenario = SCENARIOS[scenario_name]
    a = scenario.assumptions
    multiples, growths = sensitivity_axes(a)

    assert a.terminal_multiple in multiples, "scenario's own exit multiple must be an axis point"
    assert a.annual_organic_growth in growths, "scenario's own growth must be an axis point"
    assert len(multiples) == 5 and len(growths) == 5


@pytest.mark.parametrize("scenario_name", sorted(SCENARIOS))
@pytest.mark.parametrize("metric", ["gross_moic", "gross_irr"])
def test_sensitivity_centre_reconciles_to_the_scenario_headline(
    scenario_name: str, metric: str
) -> None:
    scenario = SCENARIOS[scenario_name]
    a = scenario.assumptions
    grid = sensitivity_grid(a, scenario.add_ons, metric=metric)

    column = f"{a.terminal_multiple:.1f}x"
    row = grid[(grid["Organic Growth"] - a.annual_organic_growth).abs() < 1e-12]
    assert not row.empty, "the scenario's own growth rate is missing from its grid"
    centre = row.iloc[0][column]
    assert centre == pytest.approx(run_scenario(scenario).returns[metric], rel=1e-9)


def test_custom_scenario_grid_is_centred_on_the_custom_assumptions() -> None:
    custom = replace(DEFAULT_ASSUMPTIONS, terminal_multiple=5.0, annual_organic_growth=-0.02)
    multiples, growths = sensitivity_axes(custom)
    assert 5.0 in multiples
    assert -0.02 in growths

    grid = sensitivity_grid(custom, DEFAULT_ADDONS)
    centre = grid[(grid["Organic Growth"] + 0.02).abs() < 1e-12].iloc[0]["5.0x"]
    assert centre == pytest.approx(build_model(custom).returns["gross_moic"], rel=1e-9)


def test_base_sensitivity_axes_are_unchanged_by_centring() -> None:
    """Centring must reproduce the previously shipped base-case axes exactly."""
    multiples, growths = sensitivity_axes(DEFAULT_ASSUMPTIONS)
    assert multiples == (5.5, 6.0, 6.5, 7.0, 7.5)
    assert growths == (0.02, 0.035, 0.05, 0.065, 0.08)


def test_sensitivity_axes_stay_economically_valid() -> None:
    """A grid must not wander into a non-positive multiple or a -100% decline."""
    extreme = replace(DEFAULT_ASSUMPTIONS, terminal_multiple=0.6, annual_organic_growth=-0.97)
    multiples, growths = sensitivity_axes(extreme)
    assert all(m > 0 for m in multiples)
    assert all(g > -1 for g in growths)


# ---- Negative organic growth -------------------------------------------------


def test_negative_organic_growth_is_accepted_and_flows_through() -> None:
    declining = build_model(replace(DEFAULT_ASSUMPTIONS, annual_organic_growth=-0.02))
    schedule = declining.schedule

    # Platform revenue must actually shrink year over year.
    platform = schedule["Revenue"] - schedule["Add-on EBITDA"] * 0  # revenue column as-is
    assert schedule.iloc[-1]["Platform EBITDA"] < schedule.iloc[0]["Platform EBITDA"]
    assert (schedule["Ending Debt"] >= 0).all()
    assert (schedule["Ending Cash"] >= 0).all()
    assert math.isfinite(declining.returns["gross_moic"])
    assert declining.returns["gross_moic"] < build_model().returns["gross_moic"]
    assert platform.notna().all()

    # Every identity still reconciles under decline.
    bridge = return_bridge(declining)
    walk = bridge[bridge["Component"] != "Exit equity value"]["Value"].sum()
    assert walk == pytest.approx(declining.returns["terminal_equity_value"], abs=TOL)
    for _, row in schedule.iterrows():
        expected = (
            row["EBITDA"]
            - row["Maintenance Capex"]
            - row["NWC Investment"]
            - row["Cash Interest"]
            - row["Cash Taxes"]
        )
        assert row["Free Cash Flow"] == pytest.approx(expected, abs=TOL)


@pytest.mark.parametrize("growth", [-0.999, -0.5, -0.01, 0.0, 0.5])
def test_growth_inside_the_supported_range_is_accepted(growth: float) -> None:
    result = build_model(replace(DEFAULT_ASSUMPTIONS, annual_organic_growth=growth))
    assert math.isfinite(result.returns["gross_moic"])


@pytest.mark.parametrize("growth", [-1.0, -1.5, 1.0, 2.0])
def test_growth_outside_the_supported_range_is_rejected(growth: float) -> None:
    with pytest.raises(ValueError, match="must be greater than -1 and less than 1"):
        build_model(replace(DEFAULT_ASSUMPTIONS, annual_organic_growth=growth))


def test_negative_growth_shrinks_revenue_monotonically() -> None:
    schedule = build_model(
        replace(DEFAULT_ASSUMPTIONS, annual_organic_growth=-0.05), add_ons=()
    ).schedule
    revenues = list(schedule["Revenue"])
    assert revenues == sorted(revenues, reverse=True)


# ---- Leverage synergy add-back ----------------------------------------------


def _oversized_case(fraction: float) -> ModelResult:
    a = replace(
        DEFAULT_ASSUMPTIONS,
        initial_debt_to_ebitda=2.5,
        max_pro_forma_leverage=2.5,
        leverage_synergy_addback_fraction=fraction,
    )
    return build_model(a, (AddOn("Oversized", 2, 20.0, 0.18),))


def test_default_excludes_synergies_from_leverage_capacity() -> None:
    assert DEFAULT_ASSUMPTIONS.leverage_synergy_addback_fraction == 0.0
    schedule = _oversized_case(0.0).schedule
    year_two = schedule.iloc[1]
    # Creditable EBITDA excludes the synergies the year's EBITDA contains.
    assert year_two["Creditable EBITDA"] == pytest.approx(
        year_two["Platform EBITDA"] + year_two["Add-on EBITDA"], abs=TOL
    )
    assert year_two["Creditable EBITDA"] < year_two["EBITDA"]


def test_full_addback_credits_the_entire_realized_synergy() -> None:
    schedule = _oversized_case(1.0).schedule
    year_two = schedule.iloc[1]
    assert year_two["Creditable EBITDA"] == pytest.approx(year_two["EBITDA"], abs=TOL)


def test_intermediate_addback_fraction_is_applied_proportionally() -> None:
    schedule = _oversized_case(0.5).schedule
    year_two = schedule.iloc[1]
    expected = (
        year_two["Platform EBITDA"]
        + year_two["Add-on EBITDA"]
        + 0.5 * year_two["Realized Synergies"]
    )
    assert year_two["Creditable EBITDA"] == pytest.approx(expected, abs=TOL)


def test_more_synergy_credit_means_less_sponsor_equity_when_the_governor_binds() -> None:
    conservative = _oversized_case(0.0).returns["total_sponsor_equity_invested"]
    permissive = _oversized_case(1.0).returns["total_sponsor_equity_invested"]
    assert permissive < conservative, "crediting synergies must reduce staged equity"


@pytest.mark.parametrize("fraction", [-0.01, 1.01, 2.0])
def test_invalid_addback_fraction_is_rejected(fraction: float) -> None:
    with pytest.raises(
        ValueError, match="leverage_synergy_addback_fraction must be between 0 and 1 inclusive"
    ):
        build_model(replace(DEFAULT_ASSUMPTIONS, leverage_synergy_addback_fraction=fraction))


@pytest.mark.parametrize("fraction", [0.0, 0.25, 1.0])
def test_addback_fraction_does_not_move_shipped_scenarios(fraction: float) -> None:
    """The governor is inert in shipped cases, so credit policy cannot matter."""
    for name, scenario in SCENARIOS.items():
        baseline = run_scenario(scenario).returns
        altered = build_model(
            replace(scenario.assumptions, leverage_synergy_addback_fraction=fraction),
            scenario.add_ons,
        ).returns
        assert altered["gross_moic"] == pytest.approx(baseline["gross_moic"], rel=1e-12), name
        assert altered["total_sponsor_equity_invested"] == pytest.approx(
            baseline["total_sponsor_equity_invested"], rel=1e-12
        ), name


# ---- Leverage limit warnings and reporting points ---------------------------


def test_shipped_scenarios_report_no_leverage_limit_exceedance() -> None:
    for name, scenario in SCENARIOS.items():
        returns = run_scenario(scenario).returns
        assert returns["leverage_limit_exceeded"] is False, name
        assert returns["leverage_limit_exceeded_years"] == [], name


def test_severe_stress_raises_a_visible_leverage_limit_warning() -> None:
    """Enormous leverage must never be reported silently."""
    stressed = build_model(
        replace(
            DEFAULT_ASSUMPTIONS,
            initial_debt_to_ebitda=3.9,
            max_pro_forma_leverage=4.0,
            interest_rate=0.99,
        )
    )
    returns = stressed.returns
    assert returns["leverage_limit_exceeded"] is True
    assert returns["leverage_limit_exceeded_years"] == [1, 2, 3, 4, 5]
    assert returns["maximum_year_end_gross_leverage"] > returns["leverage_limit"]
    assert stressed.schedule["Leverage Limit Exceeded"].all()


def test_leverage_limit_years_match_the_schedule() -> None:
    stressed = build_model(
        replace(
            DEFAULT_ASSUMPTIONS,
            initial_debt_to_ebitda=3.9,
            max_pro_forma_leverage=4.0,
            interest_rate=0.60,
        )
    )
    schedule = stressed.schedule
    expected = [int(r["Year"]) for _, r in schedule.iterrows() if r["Leverage Limit Exceeded"]]
    assert stressed.returns["leverage_limit_exceeded_years"] == expected


def test_three_leverage_points_are_reported_and_distinct() -> None:
    returns = build_model().returns
    assert returns["gross_leverage_at_close"] == pytest.approx(3.0, abs=1e-12)
    assert returns["maximum_year_end_gross_leverage"] == pytest.approx(2.3022258941, rel=1e-8)
    assert returns["exit_net_leverage"] == pytest.approx(0.3394, abs=1e-3)
    # The retained alias must not drift from the year-end maximum.
    assert returns["peak_gross_leverage"] == returns["maximum_year_end_gross_leverage"]
    # Close leverage is higher than any year-end figure, which is the point.
    assert returns["gross_leverage_at_close"] > returns["maximum_year_end_gross_leverage"]


# ---------------------------------------------------------------------------
# Add-on entry-multiple sensitivity (research priority #6).
# ---------------------------------------------------------------------------


def test_add_on_entry_sensitivity_spans_the_cited_range() -> None:
    table = add_on_entry_sensitivity(DEFAULT_ASSUMPTIONS, DEFAULT_ADDONS)
    multiples = list(table["Add-on Entry Multiple"])

    assert DEFAULT_ADDONS[0].entry_multiple in multiples, "modelled price must appear"
    assert 5.0 in multiples and 6.0 in multiples, "cited 5x-6x range must be covered"
    assert table["Within Cited Range"].any()
    assert not table["Within Cited Range"].all(), "must show outside the range too"


def test_paying_more_for_add_ons_lowers_returns_monotonically() -> None:
    table = add_on_entry_sensitivity(DEFAULT_ASSUMPTIONS, DEFAULT_ADDONS)
    moics = list(table["Gross MOIC"])
    assert moics == sorted(moics, reverse=True)
    assert list(table["Gross IRR"]) == sorted(table["Gross IRR"], reverse=True)


def test_sensitivity_baseline_row_reconciles_to_the_base_case() -> None:
    table = add_on_entry_sensitivity(DEFAULT_ASSUMPTIONS, DEFAULT_ADDONS)
    first = table.iloc[0]
    assert first["Add-on Entry Multiple"] == DEFAULT_ADDONS[0].entry_multiple
    assert first["Gross MOIC"] == pytest.approx(build_model().returns["gross_moic"], rel=1e-9)
    assert first["MOIC vs Modelled"] == pytest.approx(0.0, abs=TOL)


def test_cost_of_paying_the_cited_range_is_quantified() -> None:
    """The point of the table: how much of the return is cheap-add-on dependent."""
    table = add_on_entry_sensitivity(DEFAULT_ASSUMPTIONS, DEFAULT_ADDONS)
    in_range = table[table["Within Cited Range"]]
    assert (in_range["MOIC vs Modelled"] < 0).all()
    # Material but not fatal — the thesis survives paying market for tuck-ins.
    assert in_range["MOIC vs Modelled"].min() > -1.5
    assert (in_range["Gross MOIC"] > 3.0).all()


def test_add_on_sensitivity_handles_a_no_add_on_scenario() -> None:
    table = add_on_entry_sensitivity(DEFAULT_ASSUMPTIONS, ())
    assert table.empty
    assert "Add-on Entry Multiple" in table.columns


def test_blended_entry_multiple_rises_with_add_on_price() -> None:
    table = add_on_entry_sensitivity(DEFAULT_ASSUMPTIONS, DEFAULT_ADDONS)
    blended = list(table["Blended Entry Multiple"])
    assert blended == sorted(blended)


# ---------------------------------------------------------------------------
# Driver attribution.
#
# These exist because an earlier version of the IC summary inferred which
# drivers caused the severe case's loss by subtracting one bundled scenario
# from another. That is not an attribution, and nothing in the suite caught it.
# The properties below are the ones an underwriting reader is entitled to
# assume of any number presented as a driver contribution.
# ---------------------------------------------------------------------------


def _severe() -> object:
    from pathlib import Path

    return load_scenarios(Path("examples/scenarios.json"))["severe-downside"]


def test_driver_groups_reproduce_both_endpoints_exactly() -> None:
    """Nothing may be silently excluded from the decomposition."""
    from buy_and_build_model import _coalition_scenario

    base, severe, groups = SCENARIOS["base"], _severe(), severe_driver_groups()

    empty = _coalition_scenario(base, severe, groups, frozenset())
    assert empty.assumptions == base.assumptions
    assert empty.add_ons == base.add_ons

    full = _coalition_scenario(base, severe, groups, frozenset(range(len(groups))))
    assert full.assumptions == severe.assumptions, "groups do not partition the stress"
    assert full.add_ons == severe.add_ons


def test_shapley_contributions_sum_to_the_endpoint_loss() -> None:
    base, severe, groups = SCENARIOS["base"], _severe(), severe_driver_groups()
    frame = shapley_attribution(base, severe, groups)

    endpoint = run_scenario(base).returns["gross_moic"] - run_scenario(severe).returns["gross_moic"]
    assert frame["Contribution"].sum() == pytest.approx(endpoint, abs=TOL)
    assert frame["Share of total"].sum() == pytest.approx(1.0, abs=TOL)


def test_shapley_is_invariant_to_group_order() -> None:
    """Order-independence is the property that makes this an attribution."""
    base, severe, groups = SCENARIOS["base"], _severe(), severe_driver_groups()

    forward = shapley_attribution(base, severe, groups)
    reversed_ = shapley_attribution(base, severe, tuple(reversed(groups)))

    a = dict(zip(forward["Driver"], forward["Contribution"], strict=True))
    b = dict(zip(reversed_["Driver"], reversed_["Contribution"], strict=True))
    assert a.keys() == b.keys()
    for driver, value in a.items():
        assert value == pytest.approx(b[driver], abs=TOL), driver


def test_an_unstressed_driver_contributes_exactly_zero() -> None:
    """A dummy group identical in base and stressed must attract no blame."""
    base, severe = SCENARIOS["base"], _severe()
    dummy = DriverGroup("Dummy (stresses nothing)", {})
    groups = (*severe_driver_groups(), dummy)

    frame = shapley_attribution(base, severe, groups)
    contribution = frame.loc[frame["Driver"] == dummy.name, "Contribution"].iloc[0]
    assert contribution == pytest.approx(0.0, abs=TOL)


def test_shapley_rejects_an_empty_group_set() -> None:
    with pytest.raises(ValueError, match="at least one driver group"):
        shapley_attribution(SCENARIOS["base"], _severe(), ())


def test_shapley_rejects_overlapping_groups() -> None:
    """Two groups touching one parameter would make coalitions order-dependent."""
    overlapping = (
        DriverGroup("A", {"annual_organic_growth": -0.03}),
        DriverGroup("B", {"annual_organic_growth": 0.0}),
    )
    with pytest.raises(ValueError, match="must not overlap"):
        shapley_attribution(SCENARIOS["base"], _severe(), overlapping)


def test_one_at_a_time_overshoots_and_is_therefore_not_an_attribution() -> None:
    """The diagnostic's whole purpose: isolated losses do not sum to the total."""
    base, severe, groups = SCENARIOS["base"], _severe(), severe_driver_groups()

    isolated = one_at_a_time_diagnostic(base, severe, groups)["Isolated loss"].sum()
    endpoint = run_scenario(base).returns["gross_moic"] - run_scenario(severe).returns["gross_moic"]
    assert isolated > endpoint * 1.2, "if these ever agree, the non-additivity note is wrong"


def test_lower_entry_margin_currently_raises_moic() -> None:
    """Documents the modelling defect the attribution caveat warns about.

    Purchase price and opening debt both derive from the assumed entry margin,
    so cutting the margin alone buys the business cheaper. Until a fixed-dollar
    purchase-price mode exists, the model cannot express a diligence miss, and
    no margin-fragility claim may rest on this parameter. Delete this test when
    that mode lands.
    """
    base = SCENARIOS["base"]
    cheaper = replace(base, assumptions=replace(base.assumptions, platform_ebitda_margin=0.165))

    assert run_scenario(cheaper).returns["gross_moic"] > run_scenario(base).returns["gross_moic"]
