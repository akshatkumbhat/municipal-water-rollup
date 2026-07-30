"""Five-year buy-and-build model for municipal water/wastewater asset-integrity services.

The model is intentionally transparent rather than over-engineered. It can be
extended into a full monthly debt schedule, tax model, and purchase accounting
workbook after indications of interest are received.

Run:
    python buy_and_build_model.py --output-dir outputs
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

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


DEFAULT_ADDONS = (
    AddOn("Add-on A", close_year=2, revenue_at_close=2.0, ebitda_margin=0.18),
    AddOn("Add-on B", close_year=3, revenue_at_close=2.5, ebitda_margin=0.18),
    AddOn("Add-on C", close_year=4, revenue_at_close=3.0, ebitda_margin=0.20),
)


def validate_assumptions(a: Assumptions, add_ons: Iterable[AddOn]) -> None:
    if a.forecast_years < 1:
        raise ValueError("forecast_years must be positive")
    for field_name in (
        "platform_ebitda_margin",
        "annual_organic_growth",
        "add_on_sgna_pct_revenue",
        "sgna_synergy_capture",
        "interest_rate",
        "tax_rate",
        "capex_pct_revenue",
        "nwc_pct_incremental_revenue",
        "transaction_fee_pct_ev",
    ):
        value = getattr(a, field_name)
        if not 0 <= value < 1:
            raise ValueError(f"{field_name} must be between 0 and 1")
    for add_on in add_ons:
        if add_on.close_year < 1 or add_on.close_year > a.forecast_years:
            raise ValueError(f"{add_on.name}: close_year outside forecast")
        if add_on.revenue_at_close <= 0 or add_on.ebitda_margin <= 0:
            raise ValueError(f"{add_on.name}: revenue and margin must be positive")


def build_model(
    assumptions: Assumptions = Assumptions(),
    add_ons: Iterable[AddOn] = DEFAULT_ADDONS,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Return the annual operating/debt schedule and sponsor return summary.

    Modeling conventions:
    * Platform closes at time zero.
    * Add-ons close at the beginning of their designated forecast year.
    * Add-ons are funded through the revolver/term facility, subject to the
      modeled pro-forma leverage profile.
    * All levered free cash flow is swept to debt at year-end.
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

    rows: list[dict[str, float | int | str]] = []
    ending_debt_prior = initial_debt
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
        acquisition_debt_draw = 0.0
        acquisitions_closed: list[str] = []

        for add_on in add_ons:
            if year >= add_on.close_year:
                years_owned = year - add_on.close_year
                revenue = add_on.revenue_at_close * (1 + a.annual_organic_growth) ** years_owned
                add_on_revenue += revenue
                add_on_ebitda_pre_synergy += revenue * add_on.ebitda_margin
                realization = (
                    a.first_year_synergy_realization
                    if year == add_on.close_year
                    else 1.0
                )
                realized_synergies += revenue * full_synergy_pct_revenue * realization

            if year == add_on.close_year:
                acquisition_debt_draw += add_on.enterprise_value * (
                    1 + a.transaction_fee_pct_ev
                )
                acquisitions_closed.append(add_on.name)

        revenue = platform_revenue + add_on_revenue
        ebitda = platform_ebitda + add_on_ebitda_pre_synergy + realized_synergies
        debt_before_sweep = ending_debt_prior + acquisition_debt_draw
        capex = revenue * a.capex_pct_revenue
        nwc_investment = max(revenue - revenue_prior, 0.0) * a.nwc_pct_incremental_revenue

        # Solve the mild circularity created by interest on average debt and a
        # year-end cash sweep. Taxes use EBITDA less maintenance capex and cash
        # interest as a simplified cash-tax proxy.
        pre_interest_after_tax_cash = (1 - a.tax_rate) * (ebitda - capex) - nwc_investment
        denominator = 1 - 0.5 * (1 - a.tax_rate) * a.interest_rate
        free_cash_flow = (
            pre_interest_after_tax_cash
            - (1 - a.tax_rate) * a.interest_rate * debt_before_sweep
        ) / denominator
        free_cash_flow = max(free_cash_flow, 0.0)

        average_debt = debt_before_sweep - 0.5 * free_cash_flow
        cash_interest = average_debt * a.interest_rate
        cash_taxes = max((ebitda - capex - cash_interest) * a.tax_rate, 0.0)
        free_cash_flow = ebitda - capex - nwc_investment - cash_interest - cash_taxes
        debt_paydown = min(max(free_cash_flow, 0.0), debt_before_sweep)
        ending_debt = debt_before_sweep - debt_paydown

        rows.append(
            {
                "Year": year,
                "Acquisitions Closed": ", ".join(acquisitions_closed) or "—",
                "Revenue": revenue,
                "EBITDA": ebitda,
                "EBITDA Margin": ebitda / revenue,
                "Realized Synergies": realized_synergies,
                "Acquisition Debt Draw": acquisition_debt_draw,
                "Cash Interest": cash_interest,
                "Maintenance Capex": capex,
                "Cash Taxes": cash_taxes,
                "NWC Investment": nwc_investment,
                "Free Cash Flow": free_cash_flow,
                "FCF Conversion": free_cash_flow / ebitda,
                "Debt Paydown": debt_paydown,
                "Ending Debt": ending_debt,
                "Net Leverage": ending_debt / ebitda,
            }
        )
        ending_debt_prior = ending_debt
        revenue_prior = revenue

    model = pd.DataFrame(rows)
    terminal_ebitda = float(model.iloc[-1]["EBITDA"])
    terminal_debt = float(model.iloc[-1]["Ending Debt"])
    terminal_ev = terminal_ebitda * a.terminal_multiple
    terminal_equity_value = terminal_ev - terminal_debt
    moic = terminal_equity_value / initial_equity
    irr = moic ** (1 / a.forecast_years) - 1

    total_add_on_ev = sum(add_on.enterprise_value for add_on in add_ons)
    total_entry_ebitda = platform_ebitda_at_close + sum(
        add_on.ebitda_at_close for add_on in add_ons
    )
    blended_entry_multiple = (platform_ev + total_add_on_ev) / total_entry_ebitda

    returns = {
        "platform_enterprise_value": platform_ev,
        "platform_transaction_fees": platform_fees,
        "initial_debt": initial_debt,
        "initial_sponsor_equity": initial_equity,
        "total_add_on_enterprise_value": total_add_on_ev,
        "blended_entry_multiple": blended_entry_multiple,
        "terminal_multiple": a.terminal_multiple,
        "terminal_enterprise_value": terminal_ev,
        "terminal_debt": terminal_debt,
        "terminal_equity_value": terminal_equity_value,
        "gross_moic": moic,
        "gross_irr": irr,
    }
    return model, returns


def format_for_console(model: pd.DataFrame) -> pd.DataFrame:
    formatted = model.copy()
    dollar_cols = [
        "Revenue",
        "EBITDA",
        "Realized Synergies",
        "Acquisition Debt Draw",
        "Cash Interest",
        "Maintenance Capex",
        "Cash Taxes",
        "NWC Investment",
        "Free Cash Flow",
        "Debt Paydown",
        "Ending Debt",
    ]
    for col in dollar_cols:
        formatted[col] = formatted[col].map(lambda x: f"${x:,.2f}M")
    for col in ("EBITDA Margin", "FCF Conversion"):
        formatted[col] = formatted[col].map(lambda x: f"{x:.1%}")
    formatted["Net Leverage"] = formatted["Net Leverage"].map(lambda x: f"{x:.2f}x")
    return formatted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="outputs", help="Directory for CSV/JSON outputs")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    assumptions = Assumptions()
    model, returns = build_model(assumptions, DEFAULT_ADDONS)
    model.to_csv(output_dir / "five_year_pro_forma.csv", index=False)
    (output_dir / "return_summary.json").write_text(
        json.dumps(returns, indent=2), encoding="utf-8"
    )
    (output_dir / "assumptions.json").write_text(
        json.dumps(
            {
                "assumptions": asdict(assumptions),
                "add_ons": [asdict(add_on) for add_on in DEFAULT_ADDONS],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\nFIVE-YEAR PRO FORMA ($ millions)\n")
    print(format_for_console(model).to_string(index=False))
    print("\nRETURN SUMMARY\n")
    for key, value in returns.items():
        if key in {"gross_irr"}:
            print(f"{key:32s}: {value:.1%}")
        elif key in {"gross_moic", "blended_entry_multiple", "terminal_multiple"}:
            print(f"{key:32s}: {value:.2f}x")
        else:
            print(f"{key:32s}: ${value:,.2f}M")


if __name__ == "__main__":
    main()
