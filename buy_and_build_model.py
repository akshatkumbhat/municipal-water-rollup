"""Five-year buy-and-build model for municipal water/wastewater asset-integrity services.

The model is intentionally transparent rather than over-engineered: every
reported line reconciles to an identity that a reviewer can check by hand, and
no return is produced by an assumption that is not written down in
`assumptions.json`.

Scenarios are data, not code. `base` and `downside` are specified in
`PROJECT_BLUEPRINT.md`; `upside` is author-defined and marked as such. Custom
scenarios can be supplied with `--scenario-file` without editing any formula.

Run:
    python buy_and_build_model.py --output-dir outputs/model
    python buy_and_build_model.py --scenario downside
    python buy_and_build_model.py --scenario-file scenarios.json --scenario all
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class AddOn:
    name: str
    close_year: int
    revenue_at_close: float
    ebitda_margin: float
    entry_multiple: float = 3.5

    @property
    def ebitda_at_close(self) -> float:
        return self.revenue_at_close * self.ebitda_margin

    @property
    def enterprise_value(self) -> float:
        return self.ebitda_at_close * self.entry_multiple


@dataclass(frozen=True)
class Assumptions:
    forecast_years: int = 5
    platform_revenue: float = 10.0
    platform_ebitda_margin: float = 0.20
    platform_entry_multiple: float = 6.0
    initial_debt_to_ebitda: float = 3.0
    annual_organic_growth: float = 0.05
    platform_margin_expansion_bps_per_year: int = 50
    platform_margin_cap: float = 0.22
    add_on_sgna_pct_revenue: float = 0.20
    sgna_synergy_capture: float = 0.15
    first_year_synergy_realization: float = 0.50
    interest_rate: float = 0.08
    tax_rate: float = 0.25
    capex_pct_revenue: float = 0.03
    nwc_pct_incremental_revenue: float = 0.01
    transaction_fee_pct_ev: float = 0.02
    terminal_multiple: float = 6.5
    # Acquisition financing governor. Add-on uses are funded with debt only up
    # to this pro-forma leverage level; the remainder is funded with sponsor
    # equity. At 4.0x this does not bind in the blueprint case (pro-forma
    # leverage peaks at 2.31x at the Add-on A draw), so the documented base
    # case is unchanged. It does bind under leverage stress, which is the
    # point: the blueprint requires financing sized to covenant headroom
    # rather than to a headline IRR.
    max_pro_forma_leverage: float = 4.0


DEFAULT_ADDONS = (
    AddOn("Add-on A", close_year=2, revenue_at_close=2.0, ebitda_margin=0.18),
    AddOn("Add-on B", close_year=3, revenue_at_close=2.5, ebitda_margin=0.18),
    AddOn("Add-on C", close_year=4, revenue_at_close=3.0, ebitda_margin=0.20),
)

# Module-level singleton so it can serve as an immutable default argument
# without constructing a new instance on every import (ruff B008).
DEFAULT_ASSUMPTIONS = Assumptions()

# Assumptions constrained to the half-open interval [0, 1). Reaching 100% is
# not meaningful for any of these: a 100% EBITDA margin, a 100% tax rate, or
# consolidating 100% of an acquired company's SG&A are all degenerate.
_UNIT_INTERVAL_FIELDS = (
    "platform_ebitda_margin",
    "annual_organic_growth",
    "platform_margin_cap",
    "add_on_sgna_pct_revenue",
    "sgna_synergy_capture",
    "interest_rate",
    "tax_rate",
    "capex_pct_revenue",
    "nwc_pct_incremental_revenue",
    "transaction_fee_pct_ev",
)

# Realization timing assumptions, validated on the closed interval [0, 1].
# Full realization in the acquisition year is a legitimate input — an owner
# salary eliminated at close is captured immediately, not phased — so 1.0 must
# be accepted rather than rejected as out of range.
_REALIZATION_FIELDS = ("first_year_synergy_realization",)

_POSITIVE_FIELDS = (
    "platform_revenue",
    "platform_entry_multiple",
    "terminal_multiple",
    "max_pro_forma_leverage",
)


@dataclass(frozen=True)
class FundingEvent:
    """One closing, stated as explicit sources and uses.

    Uses are the purchase price plus transaction fees. Sources are the debt
    drawn at that closing plus sponsor equity. `total_uses` and `total_sources`
    are equal by construction; `test_sources_and_uses_balance` enforces it.
    """

    label: str
    year: int
    purchase_price: float
    transaction_fees: float
    debt_drawn: float
    sponsor_equity: float

    @property
    def total_uses(self) -> float:
        return self.purchase_price + self.transaction_fees

    @property
    def total_sources(self) -> float:
        return self.debt_drawn + self.sponsor_equity


@dataclass(frozen=True)
class YearCash:
    """Solved cash lines for one forecast year."""

    cash_interest: float
    cash_taxes: float
    free_cash_flow: float


@dataclass(frozen=True)
class Scenario:
    """A named, fully specified case. `source` records where it comes from."""

    name: str
    description: str
    source: str
    assumptions: Assumptions
    add_ons: tuple[AddOn, ...]


@dataclass(frozen=True)
class ModelResult:
    scenario: str
    schedule: pd.DataFrame
    funding: tuple[FundingEvent, ...]
    returns: dict[str, float]


def validate_assumptions(a: Assumptions, add_ons: Iterable[AddOn]) -> None:
    if a.forecast_years < 1:
        raise ValueError("forecast_years must be positive")
    for field_name in _UNIT_INTERVAL_FIELDS:
        value = getattr(a, field_name)
        if not 0 <= value < 1:
            raise ValueError(f"{field_name} must be between 0 and 1")
    for field_name in _REALIZATION_FIELDS:
        value = getattr(a, field_name)
        if not 0 <= value <= 1:
            raise ValueError(f"{field_name} must be between 0 and 1 inclusive")
    for field_name in _POSITIVE_FIELDS:
        if getattr(a, field_name) <= 0:
            raise ValueError(f"{field_name} must be positive")
    if a.initial_debt_to_ebitda < 0:
        raise ValueError("initial_debt_to_ebitda must not be negative")
    if a.initial_debt_to_ebitda > a.max_pro_forma_leverage:
        raise ValueError(
            "initial_debt_to_ebitda exceeds max_pro_forma_leverage: the platform "
            "cannot be financed above its own covenant ceiling"
        )
    if a.platform_margin_cap < a.platform_ebitda_margin:
        raise ValueError("platform_margin_cap is below the entry EBITDA margin")

    seen: set[str] = set()
    for add_on in add_ons:
        if add_on.name in seen:
            raise ValueError(f"duplicate add-on name: {add_on.name}")
        seen.add(add_on.name)
        if add_on.close_year < 1 or add_on.close_year > a.forecast_years:
            raise ValueError(f"{add_on.name}: close_year outside forecast")
        if add_on.revenue_at_close <= 0 or add_on.ebitda_margin <= 0:
            raise ValueError(f"{add_on.name}: revenue and margin must be positive")
        if not 0 < add_on.ebitda_margin < 1:
            raise ValueError(f"{add_on.name}: ebitda_margin must be between 0 and 1")
        if add_on.entry_multiple <= 0:
            raise ValueError(f"{add_on.name}: entry_multiple must be positive")


def solve_year_cash(
    ebitda: float,
    capex: float,
    nwc_investment: float,
    opening_debt: float,
    a: Assumptions,
) -> YearCash:
    """Resolve the interest/tax/cash-sweep circularity in closed form.

    Interest accrues on average debt — opening debt less half of the year's
    sweep — but the sweep is free cash flow, which depends on interest. Rather
    than iterate, the system is solved algebraically:

        F = EBITDA - capex - dNWC - I - T
        I = r * (D0 - 0.5 * F)
        T = t * (EBITDA - capex - I)

    Let A = EBITDA - capex. Substituting I and T into F and collecting terms:

        F * (1 - 0.5 * r * (1 - t)) = (1 - t) * (A - r * D0) - dNWC

    If that solution implies negative taxes (cash interest exceeding EBITDA
    less capex), taxes floor at zero and the system is re-solved with t = 0.
    The returned lines are exact, not approximate: F equals
    A - dNWC - I - T to floating-point precision.
    """
    a_ebitda_less_capex = ebitda - capex
    r = a.interest_rate
    t = a.tax_rate

    free_cash_flow = (
        (1 - t) * (a_ebitda_less_capex - r * opening_debt) - nwc_investment
    ) / (1 - 0.5 * r * (1 - t))
    cash_interest = r * (opening_debt - 0.5 * free_cash_flow)
    cash_taxes = t * (a_ebitda_less_capex - cash_interest)

    if cash_taxes < 0:
        # Interest shields the full tax base; re-solve with no cash taxes.
        free_cash_flow = (a_ebitda_less_capex - nwc_investment - r * opening_debt) / (
            1 - 0.5 * r
        )
        cash_interest = r * (opening_debt - 0.5 * free_cash_flow)
        cash_taxes = 0.0

    return YearCash(
        cash_interest=cash_interest,
        cash_taxes=cash_taxes,
        free_cash_flow=a_ebitda_less_capex - nwc_investment - cash_interest - cash_taxes,
    )


def solve_irr(cash_flows: Sequence[float], *, tolerance: float = 1e-12) -> float:
    """Annual IRR of a year-indexed equity cash-flow vector, by bisection.

    `cash_flows[i]` occurs at the end of year `i`; index 0 is the platform
    close. Returns NaN when the vector does not bracket a root (for example an
    all-negative case), which the caller reports rather than hides.
    """

    def npv(rate: float) -> float:
        return sum(cf / (1.0 + rate) ** i for i, cf in enumerate(cash_flows))

    low, high = -0.9999, 10.0
    npv_low, npv_high = npv(low), npv(high)
    if npv_low * npv_high > 0:
        return float("nan")

    for _ in range(200):
        mid = 0.5 * (low + high)
        npv_mid = npv(mid)
        if abs(npv_mid) < tolerance:
            return mid
        if npv_low * npv_mid <= 0:
            high, npv_high = mid, npv_mid
        else:
            low, npv_low = mid, npv_mid
    return 0.5 * (low + high)


def build_model(
    assumptions: Assumptions = DEFAULT_ASSUMPTIONS,
    add_ons: Iterable[AddOn] = DEFAULT_ADDONS,
    scenario_name: str = "base",
) -> ModelResult:
    """Return the annual operating/debt schedule, sources and uses, and returns.

    Modeling conventions:
    * Platform closes at time zero.
    * Add-ons close at the beginning of their designated forecast year.
    * Add-on uses are funded with debt up to `max_pro_forma_leverage` and with
      sponsor equity beyond it, in close order.
    * All levered free cash flow sweeps to debt at year-end; cash accumulates
      only once debt is fully repaid, and a cash shortfall draws the revolver.
    * Terminal value is a year-five mark, not an assumed forced sale.
    """
    add_ons = tuple(add_ons)
    validate_assumptions(assumptions, add_ons)
    a = assumptions

    platform_ebitda_at_close = a.platform_revenue * a.platform_ebitda_margin
    platform_ev = platform_ebitda_at_close * a.platform_entry_multiple
    platform_fees = platform_ev * a.transaction_fee_pct_ev
    initial_debt = platform_ebitda_at_close * a.initial_debt_to_ebitda
    initial_equity = platform_ev + platform_fees - initial_debt

    funding: list[FundingEvent] = [
        FundingEvent(
            label="Platform",
            year=0,
            purchase_price=platform_ev,
            transaction_fees=platform_fees,
            debt_drawn=initial_debt,
            sponsor_equity=initial_equity,
        )
    ]

    rows: list[dict[str, Any]] = []
    ending_debt_prior = initial_debt
    cash_prior = 0.0
    revenue_prior = a.platform_revenue
    full_synergy_pct_revenue = a.add_on_sgna_pct_revenue * a.sgna_synergy_capture

    for year in range(1, a.forecast_years + 1):
        platform_revenue = a.platform_revenue * (1 + a.annual_organic_growth) ** year
        platform_margin = min(
            a.platform_ebitda_margin
            + (a.platform_margin_expansion_bps_per_year / 10_000) * year,
            a.platform_margin_cap,
        )
        platform_ebitda = platform_revenue * platform_margin

        add_on_revenue = 0.0
        add_on_ebitda_pre_synergy = 0.0
        realized_synergies = 0.0
        acquisitions_closed: list[str] = []
        closing_this_year: list[AddOn] = []

        for add_on in add_ons:
            if year >= add_on.close_year:
                years_owned = year - add_on.close_year
                revenue = add_on.revenue_at_close * (1 + a.annual_organic_growth) ** years_owned
                add_on_revenue += revenue
                add_on_ebitda_pre_synergy += revenue * add_on.ebitda_margin
                realization = (
                    a.first_year_synergy_realization if year == add_on.close_year else 1.0
                )
                realized_synergies += revenue * full_synergy_pct_revenue * realization

            if year == add_on.close_year:
                closing_this_year.append(add_on)
                acquisitions_closed.append(add_on.name)

        revenue = platform_revenue + add_on_revenue
        ebitda = platform_ebitda + add_on_ebitda_pre_synergy + realized_synergies

        # Fund this year's closings: debt to the leverage ceiling measured on
        # pro-forma EBITDA including the businesses being acquired, then equity.
        remaining_capacity = max(a.max_pro_forma_leverage * ebitda - ending_debt_prior, 0.0)
        acquisition_debt_draw = 0.0
        acquisition_equity = 0.0
        for add_on in closing_this_year:
            uses = add_on.enterprise_value * (1 + a.transaction_fee_pct_ev)
            debt = min(uses, remaining_capacity)
            equity = uses - debt
            remaining_capacity -= debt
            acquisition_debt_draw += debt
            acquisition_equity += equity
            funding.append(
                FundingEvent(
                    label=add_on.name,
                    year=add_on.close_year,
                    purchase_price=add_on.enterprise_value,
                    transaction_fees=add_on.enterprise_value * a.transaction_fee_pct_ev,
                    debt_drawn=debt,
                    sponsor_equity=equity,
                )
            )

        debt_before_sweep = ending_debt_prior + acquisition_debt_draw
        capex = revenue * a.capex_pct_revenue
        nwc_investment = max(revenue - revenue_prior, 0.0) * a.nwc_pct_incremental_revenue

        year_cash = solve_year_cash(ebitda, capex, nwc_investment, debt_before_sweep, a)
        free_cash_flow = year_cash.free_cash_flow

        cash_available = cash_prior + free_cash_flow
        revolver_draw = 0.0
        if cash_available >= 0:
            debt_paydown = min(cash_available, debt_before_sweep)
            ending_debt = debt_before_sweep - debt_paydown
            ending_cash = cash_available - debt_paydown
        else:
            # A cash shortfall is funded on the revolver rather than silently
            # producing negative cash.
            debt_paydown = 0.0
            revolver_draw = -cash_available
            ending_debt = debt_before_sweep + revolver_draw
            ending_cash = 0.0

        rows.append(
            {
                "Year": year,
                "Acquisitions Closed": ", ".join(acquisitions_closed) or "—",
                "Revenue": revenue,
                "Platform EBITDA": platform_ebitda,
                "Add-on EBITDA": add_on_ebitda_pre_synergy,
                "Realized Synergies": realized_synergies,
                "EBITDA": ebitda,
                "EBITDA Margin": ebitda / revenue,
                "Acquisition Debt Draw": acquisition_debt_draw,
                "Sponsor Equity Funded": acquisition_equity,
                "Cash Interest": year_cash.cash_interest,
                "Maintenance Capex": capex,
                "Cash Taxes": year_cash.cash_taxes,
                "NWC Investment": nwc_investment,
                "Free Cash Flow": free_cash_flow,
                "FCF Conversion": free_cash_flow / ebitda,
                "Debt Paydown": debt_paydown,
                "Revolver Draw": revolver_draw,
                "Ending Debt": ending_debt,
                "Ending Cash": ending_cash,
                "Gross Leverage": ending_debt / ebitda,
                "Net Leverage": (ending_debt - ending_cash) / ebitda,
            }
        )
        ending_debt_prior = ending_debt
        cash_prior = ending_cash
        revenue_prior = revenue

    model = pd.DataFrame(rows)
    final = model.iloc[-1]
    terminal_ebitda = float(final["EBITDA"])
    terminal_debt = float(final["Ending Debt"])
    terminal_cash = float(final["Ending Cash"])
    terminal_ev = terminal_ebitda * a.terminal_multiple
    terminal_equity_value = terminal_ev - terminal_debt + terminal_cash

    total_equity_invested = sum(event.sponsor_equity for event in funding)
    moic = terminal_equity_value / total_equity_invested if total_equity_invested else float("nan")

    equity_flows = [0.0] * (a.forecast_years + 1)
    for event in funding:
        equity_flows[event.year] -= event.sponsor_equity
    equity_flows[a.forecast_years] += terminal_equity_value
    irr = solve_irr(equity_flows)

    total_add_on_ev = sum(add_on.enterprise_value for add_on in add_ons)
    total_entry_ebitda = platform_ebitda_at_close + sum(
        add_on.ebitda_at_close for add_on in add_ons
    )
    blended_entry_multiple = (platform_ev + total_add_on_ev) / total_entry_ebitda
    peak_leverage = float(model["Gross Leverage"].max())

    returns = {
        "platform_enterprise_value": platform_ev,
        "platform_transaction_fees": platform_fees,
        "initial_debt": initial_debt,
        "initial_sponsor_equity": initial_equity,
        "total_add_on_enterprise_value": total_add_on_ev,
        "total_transaction_fees": sum(event.transaction_fees for event in funding),
        "total_debt_raised": sum(event.debt_drawn for event in funding),
        "total_sponsor_equity_invested": total_equity_invested,
        "blended_entry_multiple": blended_entry_multiple,
        "entry_ebitda": total_entry_ebitda,
        "terminal_ebitda": terminal_ebitda,
        "terminal_multiple": a.terminal_multiple,
        "terminal_enterprise_value": terminal_ev,
        "terminal_debt": terminal_debt,
        "terminal_cash": terminal_cash,
        "terminal_equity_value": terminal_equity_value,
        "peak_gross_leverage": peak_leverage,
        "gross_moic": moic,
        "gross_irr": irr,
    }
    return ModelResult(
        scenario=scenario_name,
        schedule=model,
        funding=tuple(funding),
        returns=returns,
    )


def sources_and_uses(result: ModelResult) -> pd.DataFrame:
    """Per-closing sources and uses, with a total row."""
    rows = [
        {
            "Closing": event.label,
            "Year": event.year,
            "Purchase Price": event.purchase_price,
            "Transaction Fees": event.transaction_fees,
            "Total Uses": event.total_uses,
            "Debt Drawn": event.debt_drawn,
            "Sponsor Equity": event.sponsor_equity,
            "Total Sources": event.total_sources,
        }
        for event in result.funding
    ]
    frame = pd.DataFrame(rows)
    total = {
        "Closing": "Total",
        "Year": "—",
        **{
            column: float(frame[column].sum())
            for column in (
                "Purchase Price",
                "Transaction Fees",
                "Total Uses",
                "Debt Drawn",
                "Sponsor Equity",
                "Total Sources",
            )
        },
    }
    return pd.concat([frame, pd.DataFrame([total])], ignore_index=True)


def return_bridge(result: ModelResult, add_ons: Iterable[AddOn] = DEFAULT_ADDONS) -> pd.DataFrame:
    """Walk entry sponsor equity to exit equity value.

    The bridge is an identity, not an attribution heuristic: the components sum
    to the terminal equity value exactly (`test_return_bridge_reconciles`).
    EBITDA growth is valued at the blended entry multiple, so multiple
    arbitrage between the platform and the cheaper add-ons is embedded in that
    entry multiple rather than shown as a separate line.
    """
    add_ons = tuple(add_ons)
    r = result.returns
    final = result.schedule.iloc[-1]
    entry_multiple = r["blended_entry_multiple"]

    platform_ebitda_at_close = r["entry_ebitda"] - sum(a.ebitda_at_close for a in add_ons)
    platform_growth = float(final["Platform EBITDA"]) - platform_ebitda_at_close
    add_on_growth = float(final["Add-on EBITDA"]) - sum(a.ebitda_at_close for a in add_ons)
    synergies = float(final["Realized Synergies"])

    components = [
        ("Entry sponsor equity", r["total_sponsor_equity_invested"]),
        ("Platform organic EBITDA growth", platform_growth * entry_multiple),
        ("Add-on organic EBITDA growth", add_on_growth * entry_multiple),
        ("Synergy realization", synergies * entry_multiple),
        (
            "Multiple change",
            r["terminal_ebitda"] * (r["terminal_multiple"] - entry_multiple),
        ),
        (
            "Net debt paydown",
            r["total_debt_raised"] - r["terminal_debt"] + r["terminal_cash"],
        ),
        ("Transaction fees", -r["total_transaction_fees"]),
    ]

    running = 0.0
    rows: list[dict[str, Any]] = []
    for label, value in components:
        running += value
        rows.append({"Component": label, "Value": value, "Cumulative": running})
    rows.append(
        {
            "Component": "Exit equity value",
            "Value": r["terminal_equity_value"],
            "Cumulative": running,
        }
    )
    return pd.DataFrame(rows)


def sensitivity_grid(
    assumptions: Assumptions,
    add_ons: Iterable[AddOn],
    *,
    metric: str = "gross_moic",
    exit_multiples: Sequence[float] = (5.5, 6.0, 6.5, 7.0, 7.5),
    growth_rates: Sequence[float] = (0.02, 0.035, 0.05, 0.065, 0.08),
) -> pd.DataFrame:
    """Grid of `metric` across exit multiple (columns) and organic growth (rows)."""
    if metric not in {"gross_moic", "gross_irr"}:
        raise ValueError(f"unsupported sensitivity metric: {metric}")
    add_ons = tuple(add_ons)
    rows: list[dict[str, Any]] = []
    for growth in growth_rates:
        row: dict[str, Any] = {"Organic Growth": growth}
        for multiple in exit_multiples:
            candidate = replace(
                assumptions, annual_organic_growth=growth, terminal_multiple=multiple
            )
            row[f"{multiple:.1f}x"] = build_model(candidate, add_ons).returns[metric]
        rows.append(row)
    return pd.DataFrame(rows)


def _blueprint_scenarios() -> dict[str, Scenario]:
    return {
        "base": Scenario(
            name="base",
            description=(
                "5% organic growth, 15% SG&A synergy capture, 8% interest, 6.5x mark."
            ),
            source="PROJECT_BLUEPRINT.md — Operating assumptions",
            assumptions=DEFAULT_ASSUMPTIONS,
            add_ons=DEFAULT_ADDONS,
        ),
        "downside": Scenario(
            name="downside",
            description=(
                "3% organic growth, half synergy capture (7.5%), 9% interest, 6.0x mark."
            ),
            source="PROJECT_BLUEPRINT.md — IC guardrails",
            assumptions=replace(
                DEFAULT_ASSUMPTIONS,
                annual_organic_growth=0.03,
                sgna_synergy_capture=0.075,
                interest_rate=0.09,
                terminal_multiple=6.0,
            ),
            add_ons=DEFAULT_ADDONS,
        ),
        "upside": Scenario(
            name="upside",
            description=(
                "7% organic growth, SG&A synergy capture raised to 20%, 7.5% interest. "
                "The exit mark is deliberately held at the base-case 6.5x."
            ),
            source=(
                "Author-defined; not specified in PROJECT_BLUEPRINT.md. Operational "
                "upside only — no multiple re-rating, because re-rating is the least "
                "defensible driver in the value-creation bridge."
            ),
            assumptions=replace(
                DEFAULT_ASSUMPTIONS,
                annual_organic_growth=0.07,
                sgna_synergy_capture=0.20,
                interest_rate=0.075,
            ),
            add_ons=DEFAULT_ADDONS,
        ),
    }


SCENARIOS = _blueprint_scenarios()

_ASSUMPTION_FIELDS = frozenset(f.name for f in fields(Assumptions))
_ADDON_FIELDS = frozenset(f.name for f in fields(AddOn))


def load_scenarios(path: Path) -> dict[str, Scenario]:
    """Load scenarios from JSON, layering overrides on the base assumptions.

    Expected shape::

        {"scenarios": [{"name": "stress",
                        "description": "...",
                        "assumptions": {"interest_rate": 0.12},
                        "add_ons": [{"name": "A", "close_year": 2,
                                     "revenue_at_close": 2.0,
                                     "ebitda_margin": 0.18}]}]}

    `assumptions` holds overrides only; omitted keys keep their base value.
    `add_ons` is optional and defaults to the blueprint add-on schedule.
    Unknown keys raise rather than being silently ignored.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or "scenarios" not in payload:
        raise ValueError(f"{path}: expected an object with a 'scenarios' key")

    loaded: dict[str, Scenario] = {}
    for entry in payload["scenarios"]:
        name = entry.get("name")
        if not name:
            raise ValueError(f"{path}: every scenario needs a name")

        overrides = entry.get("assumptions", {})
        unknown = set(overrides) - _ASSUMPTION_FIELDS
        if unknown:
            raise ValueError(f"{name}: unknown assumption(s): {', '.join(sorted(unknown))}")

        add_ons: tuple[AddOn, ...] = DEFAULT_ADDONS
        if "add_ons" in entry:
            parsed: list[AddOn] = []
            for spec in entry["add_ons"]:
                unknown_addon = set(spec) - _ADDON_FIELDS
                if unknown_addon:
                    raise ValueError(
                        f"{name}: unknown add-on field(s): {', '.join(sorted(unknown_addon))}"
                    )
                parsed.append(AddOn(**spec))
            add_ons = tuple(parsed)

        loaded[name] = Scenario(
            name=name,
            description=entry.get("description", ""),
            source=entry.get("source", f"User-supplied ({path.name})"),
            assumptions=replace(DEFAULT_ASSUMPTIONS, **overrides),
            add_ons=add_ons,
        )
    return loaded


def run_scenario(scenario: Scenario) -> ModelResult:
    return build_model(scenario.assumptions, scenario.add_ons, scenario_name=scenario.name)


def write_scenario_outputs(scenario: Scenario, result: ModelResult, output_dir: Path) -> None:
    """Write the machine-readable output set for one scenario."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result.schedule.to_csv(output_dir / "five_year_pro_forma.csv", index=False)
    sources_and_uses(result).to_csv(output_dir / "sources_and_uses.csv", index=False)
    return_bridge(result, scenario.add_ons).to_csv(output_dir / "return_bridge.csv", index=False)
    for metric, filename in (
        ("gross_moic", "sensitivity_moic.csv"),
        ("gross_irr", "sensitivity_irr.csv"),
    ):
        sensitivity_grid(scenario.assumptions, scenario.add_ons, metric=metric).to_csv(
            output_dir / filename, index=False
        )
    (output_dir / "return_summary.json").write_text(
        json.dumps(result.returns, indent=2), encoding="utf-8"
    )
    (output_dir / "assumptions.json").write_text(
        json.dumps(
            {
                "scenario": scenario.name,
                "description": scenario.description,
                "source": scenario.source,
                "assumptions": asdict(scenario.assumptions),
                "add_ons": [asdict(add_on) for add_on in scenario.add_ons],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def scenario_comparison(results: Mapping[str, ModelResult]) -> pd.DataFrame:
    rows = [
        {
            "Scenario": name,
            "Terminal EBITDA": result.returns["terminal_ebitda"],
            "Terminal Multiple": result.returns["terminal_multiple"],
            "Terminal Debt": result.returns["terminal_debt"],
            "Equity Invested": result.returns["total_sponsor_equity_invested"],
            "Exit Equity Value": result.returns["terminal_equity_value"],
            "Peak Gross Leverage": result.returns["peak_gross_leverage"],
            "Gross MOIC": result.returns["gross_moic"],
            "Gross IRR": result.returns["gross_irr"],
        }
        for name, result in results.items()
    ]
    return pd.DataFrame(rows)


def format_for_console(model: pd.DataFrame) -> pd.DataFrame:
    formatted = model.copy()
    dollar_cols = [
        "Revenue",
        "EBITDA",
        "Realized Synergies",
        "Acquisition Debt Draw",
        "Sponsor Equity Funded",
        "Cash Interest",
        "Maintenance Capex",
        "Cash Taxes",
        "NWC Investment",
        "Free Cash Flow",
        "Debt Paydown",
        "Ending Debt",
        "Ending Cash",
    ]
    columns = [
        "Year",
        "Acquisitions Closed",
        *dollar_cols,
        "EBITDA Margin",
        "FCF Conversion",
        "Net Leverage",
    ]
    formatted = formatted[columns]
    for col in dollar_cols:
        formatted[col] = formatted[col].map(lambda x: f"${x:,.2f}M")
    for col in ("EBITDA Margin", "FCF Conversion"):
        formatted[col] = formatted[col].map(lambda x: f"{x:.1%}")
    formatted["Net Leverage"] = formatted["Net Leverage"].map(lambda x: f"{x:.2f}x")
    return formatted


def _print_report(scenario: Scenario, result: ModelResult) -> None:
    print(f"\n{'=' * 78}\nSCENARIO: {scenario.name.upper()} — {scenario.description}")
    print(f"Source: {scenario.source}\n{'=' * 78}")
    print("\nSOURCES AND USES ($ millions)\n")
    su = sources_and_uses(result)
    print(su.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print("\nFIVE-YEAR PRO FORMA ($ millions)\n")
    print(format_for_console(result.schedule).to_string(index=False))
    print("\nRETURN BRIDGE ($ millions)\n")
    bridge = return_bridge(result, scenario.add_ons)
    print(bridge.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print("\nRETURN SUMMARY\n")
    for key, value in result.returns.items():
        if key in {"gross_irr"}:
            print(f"{key:32s}: {value:.1%}")
        elif key.endswith(("multiple", "moic", "leverage")):
            print(f"{key:32s}: {value:.2f}x")
        else:
            print(f"{key:32s}: ${value:,.2f}M")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="outputs/model", help="Directory for outputs")
    parser.add_argument(
        "--scenario",
        default="all",
        help="Scenario name to run, or 'all' (default: all)",
    )
    parser.add_argument(
        "--scenario-file",
        type=Path,
        default=None,
        help="JSON file of custom scenarios, replacing the built-in registry",
    )
    args = parser.parse_args()

    registry = load_scenarios(args.scenario_file) if args.scenario_file else SCENARIOS
    if args.scenario == "all":
        selected = list(registry.values())
    elif args.scenario in registry:
        selected = [registry[args.scenario]]
    else:
        raise SystemExit(
            f"unknown scenario '{args.scenario}'; available: {', '.join(registry)}, all"
        )

    output_dir = Path(args.output_dir)
    results: dict[str, ModelResult] = {}
    for scenario in selected:
        result = run_scenario(scenario)
        results[scenario.name] = result
        write_scenario_outputs(scenario, result, output_dir / scenario.name)

    comparison = scenario_comparison(results)
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(output_dir / "scenario_comparison.csv", index=False)

    _print_report(selected[0], results[selected[0].name])
    if len(selected) > 1:
        print("\nSCENARIO COMPARISON\n")
        display = comparison.copy()
        display["Gross MOIC"] = display["Gross MOIC"].map(lambda x: f"{x:.2f}x")
        display["Gross IRR"] = display["Gross IRR"].map(lambda x: f"{x:.1%}")
        display["Peak Gross Leverage"] = display["Peak Gross Leverage"].map(
            lambda x: f"{x:.2f}x"
        )
        print(display.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print(f"\nOutputs written to {output_dir}/")


if __name__ == "__main__":
    main()
