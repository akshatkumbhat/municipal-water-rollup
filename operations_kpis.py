"""Operating-data preparation and KPI computation for the COO dashboard.

This module is the testable half of Phase 4. It imports no UI framework, makes
no network calls, and is fully deterministic, so every number the dashboard
renders can be asserted in a unit test.

Two data lineages are kept separate and are labelled as such everywhere they
surface:

* **Actual** — the governing KPIs are computed only from columns of the
  monthly operating dataset. No modelled figure is ever substituted into a KPI
  numerator or denominator, and no plan value influences a reported actual.
* **Modelled** — read from the Phase 3 buy-and-build model through
  `buy_and_build_model`. Financial formulas are never re-implemented here;
  plan, debt, leverage, and liquidity figures come from the model's own
  schedule.

One caveat worth stating plainly: the *synthetic sample dataset* seeds several
cash columns (`capex`, `cash_interest`, `cash_taxes`, `delta_nwc`) from the
model's assumption rates so the demo is internally coherent — sample
`cash_interest` therefore totals exactly the model's cash interest. That is a
property of the generated sample data, not of the KPI pipeline: an uploaded
dataset's cash columns are genuine actuals and are never touched by the model.

Targets are author-defined operating thresholds; they are not specified in
`PROJECT_BLUEPRINT.md` and are labelled `Author-defined target`.

Run:
    python operations_kpis.py --output-dir outputs/operations
"""

from __future__ import annotations

import argparse
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from buy_and_build_model import SCENARIOS, ModelResult, Scenario, run_scenario

USD_PER_MILLION = 1_000_000.0

# Columns every operating dataset must provide.
REQUIRED_COLUMNS = frozenset(
    {
        "month",
        "region",
        "service_line",
        "revenue",
        "gross_profit",
        "ebitda",
        "paid_hours",
        "billable_hours",
        "completed_jobs",
        "route_miles",
        "active_customers",
        "lost_customers",
        "recurring_revenue",
        "capex",
        "cash_taxes",
        "cash_interest",
        "delta_nwc",
        "accounts_receivable",
    }
)

# Columns that unlock extra views when present. Their absence degrades the
# dashboard gracefully; it never fabricates a substitute.
OPTIONAL_COLUMNS = frozenset({"business_unit", "lost_recurring_revenue"})

DIMENSION_COLUMNS = ("region", "service_line", "business_unit")

_NON_NUMERIC = frozenset({"month", "region", "service_line", "business_unit"})

# Period labels that ride along with the rows and must never be aggregated.
_CARRY_COLUMNS = frozenset({"model_year"})

# Columns that cannot be negative in a valid operating dataset. EBITDA,
# gross profit, and delta_nwc are deliberately excluded: all three can
# legitimately be negative in a bad month.
_NON_NEGATIVE = (
    "revenue",
    "paid_hours",
    "billable_hours",
    "completed_jobs",
    "route_miles",
    "active_customers",
    "lost_customers",
    "recurring_revenue",
    "capex",
    "accounts_receivable",
)


@dataclass(frozen=True)
class Thresholds:
    """Author-defined operating targets. Not sourced from the blueprint."""

    route_density: float = 7.0  # completed jobs per 100 route miles
    utilization: float = 0.72
    gross_margin: float = 0.42
    recurring_mix: float = 0.60
    monthly_churn: float = 0.008
    fcf_conversion: float = 0.55
    dso: float = 55.0  # days


@dataclass(frozen=True)
class ModelPeriod:
    """The fixed anchor that maps calendar months onto model years.

    Resolved once from the full dataset and reused for every view, so display
    filters cannot shift the period numbering.
    """

    anchor: pd.Timestamp
    basis: str

    @property
    def label(self) -> str:
        return f"Model Year 1 begins {self.anchor:%b %Y} — {self.basis}"


@dataclass(frozen=True)
class MetricDefinition:
    """One governing KPI: how it is computed, from what, and which way is good."""

    key: str
    label: str
    unit: str  # "ratio" | "percent" | "days"
    numerator: str
    denominator: str
    definition: str
    source_columns: tuple[str, ...]
    direction: str  # "higher" | "lower"
    threshold_field: str
    provenance: str
    action: str


METRIC_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        key="route_density",
        label="Route density",
        unit="ratio",
        numerator="completed_jobs x 100",
        denominator="route_miles",
        definition=(
            "Completed jobs per 100 route miles. Links branch geography, dispatch "
            "quality, windshield time, and capacity utilization."
        ),
        source_columns=("completed_jobs", "route_miles"),
        direction="higher",
        threshold_field="route_density",
        provenance="Actual (operating data)",
        action="Re-cluster dispatch zones; review job sequencing with the branch dispatcher.",
    ),
    MetricDefinition(
        key="utilization",
        label="Billable utilization",
        unit="percent",
        numerator="billable_hours",
        denominator="paid_hours",
        definition="Billable field hours divided by paid field hours.",
        source_columns=("billable_hours", "paid_hours"),
        direction="higher",
        threshold_field="utilization",
        provenance="Actual (operating data)",
        action="Review crew sizing, travel time, and rework hours by technician cohort.",
    ),
    MetricDefinition(
        key="gross_margin",
        label="Gross margin",
        unit="percent",
        numerator="gross_profit",
        denominator="revenue",
        definition=(
            "Revenue less direct labor, materials, disposal, and subcontractors, "
            "divided by revenue. Exposes pricing leakage and estimating error."
        ),
        source_columns=("gross_profit", "revenue"),
        direction="higher",
        threshold_field="gross_margin",
        provenance="Actual (operating data)",
        action="Reprice the weakest service line; audit job costing on loss-making work.",
    ),
    MetricDefinition(
        key="recurring_mix",
        label="Recurring revenue mix",
        unit="percent",
        numerator="recurring_revenue",
        denominator="revenue",
        definition=(
            "Revenue under recurring inspection, cleaning, sampling, monitoring, or "
            "maintenance programs, divided by total revenue."
        ),
        source_columns=("recurring_revenue", "revenue"),
        direction="higher",
        threshold_field="recurring_mix",
        provenance="Actual (operating data)",
        action="Convert transactional accounts to programmatic contracts at renewal.",
    ),
    MetricDefinition(
        key="monthly_churn",
        label="Customer churn (monthly)",
        unit="percent",
        numerator="lost_customers",
        denominator="opening active_customers (prior month close)",
        definition=(
            "Customers lost during the month divided by the opening customer base, "
            "per the blueprint's opening-base convention. Undefined for the first "
            "month in the window, which has no prior close. Caveat: counts are "
            "summed across segments, so a customer buying several service lines "
            "or served from several regions is counted more than once, which "
            "distorts this rate. De-duplication requires a customer-level "
            "identifier that this schema does not carry."
        ),
        source_columns=("lost_customers", "active_customers"),
        direction="lower",
        threshold_field="monthly_churn",
        provenance="Actual (operating data)",
        action="Escalate at-risk municipal renewals; run win-back on lapsed accounts.",
    ),
    MetricDefinition(
        key="fcf_conversion",
        label="FCF conversion",
        unit="percent",
        numerator="ebitda - capex - cash_taxes - cash_interest - delta_nwc",
        denominator="ebitda",
        definition=(
            "EBITDA less maintenance capex, cash taxes, cash interest, and "
            "working-capital investment, divided by EBITDA."
        ),
        source_columns=(
            "ebitda",
            "capex",
            "cash_taxes",
            "cash_interest",
            "delta_nwc",
        ),
        direction="higher",
        threshold_field="fcf_conversion",
        provenance="Actual (operating data)",
        action="Tighten collections cadence and defer non-essential fleet capex.",
    ),
    MetricDefinition(
        key="dso",
        label="DSO (supporting)",
        unit="days",
        numerator="accounts_receivable x days_in_period",
        denominator="revenue",
        definition=(
            "Days sales outstanding. Paired with FCF conversion because municipal "
            "receivables can obscure operating quality. Uses closing AR, so the "
            "period value is a point-in-time stock, not an average."
        ),
        source_columns=("accounts_receivable", "revenue"),
        direction="lower",
        threshold_field="dso",
        provenance="Actual (operating data)",
        action="Escalate aged municipal invoices; confirm PO and retainage blockers.",
    ),
)

GOVERNING_KEYS = tuple(m.key for m in METRIC_DEFINITIONS if m.key != "dso")


def metric_definitions_table(thresholds: Thresholds | None = None) -> pd.DataFrame:
    """Human-readable lineage table: metric, formula, sources, target, provenance."""
    t = thresholds or Thresholds()
    return pd.DataFrame(
        [
            {
                "Metric": m.label,
                "Key": m.key,
                "Unit": m.unit,
                "Numerator": m.numerator,
                "Denominator": m.denominator,
                "Definition": m.definition,
                "Source columns": ", ".join(m.source_columns),
                "Better when": m.direction,
                "Target": getattr(t, m.threshold_field),
                "Target provenance": "Author-defined target",
                "Value provenance": m.provenance,
            }
            for m in METRIC_DEFINITIONS
        ]
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_operating_data(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize an operating dataset, or raise an actionable error.

    Errors name the offending columns and, where useful, the offending rows, so
    a COO uploading a spreadsheet can fix it without reading the source.
    """
    if frame is None or len(frame.columns) == 0:
        raise ValueError("Operating data is empty: no columns were found in the file.")

    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing == REQUIRED_COLUMNS:
        raise ValueError(
            f"Operating data has none of the {len(REQUIRED_COLUMNS)} required columns. "
            f"Expected: {', '.join(sorted(REQUIRED_COLUMNS))}."
        )
    if missing:
        raise ValueError(
            "Operating data is missing required column(s): "
            f"{', '.join(sorted(missing))}."
        )

    if frame.empty:
        raise ValueError("Operating data has valid headers but contains no rows.")

    result = frame.copy()

    # Uploads legitimately carry mixed date formats; pandas warns when it cannot
    # infer a single one. We coerce deliberately and raise our own actionable
    # error below, so the inference warning is noise.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        parsed = pd.to_datetime(result["month"], errors="coerce")
    if parsed.isna().any():
        bad = result.loc[parsed.isna(), "month"].astype(str).unique()[:5]
        raise ValueError(
            f"Column 'month' has {int(parsed.isna().sum())} unparseable value(s), "
            f"for example: {', '.join(bad)}. Use an ISO date such as 2024-01-01."
        )
    result["month"] = parsed.dt.to_period("M").dt.to_timestamp()

    numeric_columns = sorted((REQUIRED_COLUMNS | OPTIONAL_COLUMNS) - _NON_NUMERIC)
    present_numeric = [c for c in numeric_columns if c in result.columns]
    for column in present_numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    bad_numeric = [c for c in present_numeric if bool(result[c].isna().any())]
    if bad_numeric:
        raise ValueError(
            "Non-numeric or missing values found in column(s): "
            f"{', '.join(bad_numeric)}. Every numeric cell must be populated."
        )

    negative = [
        c for c in _NON_NEGATIVE if c in result.columns and bool((result[c] < 0).any())
    ]
    if negative:
        raise ValueError(
            f"Negative values are not valid in column(s): {', '.join(negative)}."
        )

    over_billed = result["billable_hours"] > result["paid_hours"]
    if bool(over_billed.any()):
        raise ValueError(
            f"{int(over_billed.sum())} row(s) report more billable hours than paid "
            "hours, which cannot occur. Check the hours export."
        )

    lost_exceeds = result["lost_customers"] > result["active_customers"]
    if bool(lost_exceeds.any()):
        raise ValueError(
            f"{int(lost_exceeds.sum())} row(s) report more lost customers than active "
            "customers. Check the customer master export."
        )

    for text_column in ("region", "service_line"):
        result[text_column] = result[text_column].astype(str).str.strip()
        if bool((result[text_column] == "").any()):
            raise ValueError(f"Column '{text_column}' contains blank values.")
    if "business_unit" in result.columns:
        result["business_unit"] = result["business_unit"].astype(str).str.strip()

    key = ["month", "region", "service_line"]
    if "business_unit" in result.columns:
        key.append("business_unit")
    duplicated = result.duplicated(subset=key, keep=False)
    if bool(duplicated.any()):
        raise ValueError(
            f"{int(duplicated.sum())} duplicate row(s) share the same "
            f"{'/'.join(key)} combination. Aggregate before upload."
        )

    return result.sort_values(key).reset_index(drop=True)


def available_dimensions(frame: pd.DataFrame) -> tuple[str, ...]:
    return tuple(c for c in DIMENSION_COLUMNS if c in frame.columns)


def has_grr_inputs(frame: pd.DataFrame) -> bool:
    """Gross revenue retention needs lost recurring revenue, which is optional."""
    return "lost_recurring_revenue" in frame.columns


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def filter_operating_data(
    frame: pd.DataFrame,
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    regions: Sequence[str] | None = None,
    service_lines: Sequence[str] | None = None,
    business_units: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Apply every filter to the raw rows.

    All downstream views are derived from this one frame, which is what keeps
    KPI cards, charts, and the exception table consistent with each other.
    """
    mask = pd.Series(True, index=frame.index)
    if start is not None:
        mask &= frame["month"] >= pd.Timestamp(start)
    if end is not None:
        mask &= frame["month"] <= pd.Timestamp(end)
    if regions is not None:
        mask &= frame["region"].isin(list(regions))
    if service_lines is not None:
        mask &= frame["service_line"].isin(list(service_lines))
    if business_units is not None and "business_unit" in frame.columns:
        mask &= frame["business_unit"].isin(list(business_units))
    return frame.loc[mask].copy()


# ---------------------------------------------------------------------------
# Aggregation and KPIs
# ---------------------------------------------------------------------------


def _ratio(numerator: float, denominator: float) -> float:
    """Zero and near-zero denominators yield NaN rather than inf or a crash."""
    if denominator == 0 or not np.isfinite(denominator):
        return float("nan")
    return float(numerator) / float(denominator)


def _series_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    safe = denominator.replace(0, np.nan)
    return (numerator / safe).astype(float)


def monthly_rollup(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate to one row per month and derive every KPI as a ratio of sums.

    Ratios are computed from summed numerators and denominators, never as the
    mean of segment-level ratios, which would weight a small branch equally
    with a large one.
    """
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "month",
                *sorted(set(frame.columns) - _NON_NUMERIC),
                *GOVERNING_KEYS,
                "dso",
                "free_cash_flow",
                "ebitda_margin",
            ]
        )

    # `model_year` is a period label, not a measure: carry it through unchanged
    # rather than summing it across the segments in a month.
    numeric = [c for c in frame.columns if c not in _NON_NUMERIC and c not in _CARRY_COLUMNS]
    result = frame.groupby("month", as_index=False)[numeric].sum().sort_values("month")
    result = result.reset_index(drop=True)
    for carried in _CARRY_COLUMNS:
        if carried in frame.columns:
            labels = frame.groupby("month", as_index=False)[carried].first()
            result = result.merge(labels, on="month", how="left")

    result["route_density"] = _series_ratio(result["completed_jobs"] * 100, result["route_miles"])
    result["utilization"] = _series_ratio(result["billable_hours"], result["paid_hours"])
    result["gross_margin"] = _series_ratio(result["gross_profit"], result["revenue"])
    result["recurring_mix"] = _series_ratio(result["recurring_revenue"], result["revenue"])

    # Churn on the opening base: the prior month's closing customer count.
    # The first month in the window has no prior close, so it is NaN, not a
    # silently substituted same-period denominator.
    opening = result["active_customers"].shift(1)
    result["opening_customers"] = opening
    result["monthly_churn"] = _series_ratio(result["lost_customers"], opening)

    if "lost_recurring_revenue" in result.columns:
        opening_recurring = result["recurring_revenue"].shift(1)
        result["gross_revenue_retention"] = 1.0 - _series_ratio(
            result["lost_recurring_revenue"], opening_recurring
        )

    result["free_cash_flow"] = (
        result["ebitda"]
        - result["capex"]
        - result["cash_taxes"]
        - result["cash_interest"]
        - result["delta_nwc"]
    )
    result["fcf_conversion"] = _series_ratio(result["free_cash_flow"], result["ebitda"])
    result["ebitda_margin"] = _series_ratio(result["ebitda"], result["revenue"])

    days = result["month"].dt.days_in_month.astype(float)
    result["dso"] = _series_ratio(result["accounts_receivable"] * days, result["revenue"])
    return result


def dimension_rollup(frame: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """Per-dimension performance table (service line, region, or business unit)."""
    if dimension not in frame.columns:
        raise ValueError(
            f"Dimension '{dimension}' is not present in this dataset. "
            f"Available: {', '.join(available_dimensions(frame)) or 'none'}."
        )
    if frame.empty:
        return pd.DataFrame(
            columns=[dimension, "revenue", "gross_profit", "ebitda", "gross_margin"]
        )

    grouped = frame.groupby(dimension, as_index=False).agg(
        revenue=("revenue", "sum"),
        gross_profit=("gross_profit", "sum"),
        ebitda=("ebitda", "sum"),
        recurring_revenue=("recurring_revenue", "sum"),
        billable_hours=("billable_hours", "sum"),
        paid_hours=("paid_hours", "sum"),
        completed_jobs=("completed_jobs", "sum"),
        route_miles=("route_miles", "sum"),
    )
    grouped["gross_margin"] = _series_ratio(grouped["gross_profit"], grouped["revenue"])
    grouped["ebitda_margin"] = _series_ratio(grouped["ebitda"], grouped["revenue"])
    grouped["recurring_mix"] = _series_ratio(grouped["recurring_revenue"], grouped["revenue"])
    grouped["utilization"] = _series_ratio(grouped["billable_hours"], grouped["paid_hours"])
    grouped["route_density"] = _series_ratio(grouped["completed_jobs"] * 100, grouped["route_miles"])
    grouped["revenue_share"] = _series_ratio(
        grouped["revenue"], pd.Series([grouped["revenue"].sum()] * len(grouped))
    )
    return grouped.sort_values("revenue", ascending=False).reset_index(drop=True)


@dataclass(frozen=True)
class PeriodComparison:
    """One KPI over a trailing window, against the preceding window and target."""

    key: str
    label: str
    unit: str
    current: float
    prior: float
    change: float
    target: float
    direction: str
    on_track: bool
    months_current: int
    months_prior: int


def _window_ratio(frame: pd.DataFrame, metric: MetricDefinition) -> float:
    """Recompute a KPI over a multi-month window as a ratio of sums."""
    if frame.empty:
        return float("nan")
    if metric.key == "route_density":
        return _ratio(float(frame["completed_jobs"].sum()) * 100, float(frame["route_miles"].sum()))
    if metric.key == "utilization":
        return _ratio(float(frame["billable_hours"].sum()), float(frame["paid_hours"].sum()))
    if metric.key == "gross_margin":
        return _ratio(float(frame["gross_profit"].sum()), float(frame["revenue"].sum()))
    if metric.key == "recurring_mix":
        return _ratio(float(frame["recurring_revenue"].sum()), float(frame["revenue"].sum()))
    if metric.key == "monthly_churn":
        opening = frame["opening_customers"]
        valid = frame.loc[opening.notna()]
        if valid.empty:
            return float("nan")
        return _ratio(float(valid["lost_customers"].sum()), float(valid["opening_customers"].sum()))
    if metric.key == "fcf_conversion":
        return _ratio(float(frame["free_cash_flow"].sum()), float(frame["ebitda"].sum()))
    if metric.key == "dso":
        # Stock metric: closing AR against the window's revenue and day count.
        days = float(frame["month"].dt.days_in_month.sum())
        closing_ar = float(frame.sort_values("month").iloc[-1]["accounts_receivable"])
        return _ratio(closing_ar * days, float(frame["revenue"].sum()))
    raise ValueError(f"unsupported metric: {metric.key}")


def period_comparison(
    monthly: pd.DataFrame,
    metric: MetricDefinition,
    thresholds: Thresholds,
    *,
    window: int = 3,
) -> PeriodComparison:
    """Trailing-window value versus the immediately preceding window."""
    ordered = monthly.sort_values("month")
    current_frame = ordered.tail(window)
    prior_frame = ordered.iloc[max(len(ordered) - 2 * window, 0) : len(ordered) - window]

    current = _window_ratio(current_frame, metric)
    prior = _window_ratio(prior_frame, metric) if not prior_frame.empty else float("nan")
    change = current - prior if np.isfinite(current) and np.isfinite(prior) else float("nan")
    target = float(getattr(thresholds, metric.threshold_field))

    if not np.isfinite(current):
        on_track = False
    elif metric.direction == "higher":
        on_track = current >= target
    else:
        on_track = current <= target

    return PeriodComparison(
        key=metric.key,
        label=metric.label,
        unit=metric.unit,
        current=current,
        prior=prior,
        change=change,
        target=target,
        direction=metric.direction,
        on_track=on_track,
        months_current=len(current_frame),
        months_prior=len(prior_frame),
    )


def kpi_summary(
    monthly: pd.DataFrame,
    thresholds: Thresholds | None = None,
    *,
    window: int = 3,
) -> pd.DataFrame:
    """All seven metrics with current, prior, target, status, and provenance."""
    t = thresholds or Thresholds()
    rows = []
    for metric in METRIC_DEFINITIONS:
        comparison = period_comparison(monthly, metric, t, window=window)
        rows.append(
            {
                "Metric": comparison.label,
                "Key": comparison.key,
                "Unit": comparison.unit,
                "Current": comparison.current,
                "Prior": comparison.prior,
                "Change": comparison.change,
                "Target": comparison.target,
                "Better when": comparison.direction,
                "Status": "On track" if comparison.on_track else "Management action",
                "Months in current window": comparison.months_current,
                "Months in prior window": comparison.months_prior,
                "Provenance": metric.provenance,
            }
        )
    return pd.DataFrame(rows)


def exception_report(
    monthly: pd.DataFrame,
    thresholds: Thresholds | None = None,
    *,
    window: int = 3,
) -> pd.DataFrame:
    """Threshold breaches only, with severity and a concrete operating action.

    Severity is the relative shortfall against target: 15% or worse is High.
    A metric that cannot be computed is reported as `Unavailable` rather than
    being silently dropped or treated as passing.
    """
    t = thresholds or Thresholds()
    rows = []
    for metric in METRIC_DEFINITIONS:
        comparison = period_comparison(monthly, metric, t, window=window)
        if not np.isfinite(comparison.current):
            rows.append(
                {
                    "Metric": comparison.label,
                    "Current": float("nan"),
                    "Target": comparison.target,
                    "Gap": float("nan"),
                    "Severity": "Unavailable",
                    "Action": "Insufficient data in the selected window to compute this metric.",
                }
            )
            continue
        if comparison.on_track:
            continue
        gap = (
            comparison.current - comparison.target
            if metric.direction == "higher"
            else comparison.target - comparison.current
        )
        relative = _ratio(abs(gap), abs(comparison.target))
        severity = "High" if np.isfinite(relative) and relative >= 0.15 else "Medium"
        rows.append(
            {
                "Metric": comparison.label,
                "Current": comparison.current,
                "Target": comparison.target,
                "Gap": gap,
                "Severity": severity,
                "Action": metric.action,
            }
        )
    return pd.DataFrame(
        rows, columns=["Metric", "Current", "Target", "Gap", "Severity", "Action"]
    )


# ---------------------------------------------------------------------------
# Modelled views — every figure below comes from the Phase 3 model
# ---------------------------------------------------------------------------


def model_year_index(months: pd.Series, first_month: pd.Timestamp) -> pd.Series:
    """Map calendar months onto model years 1..N, counting from `first_month`."""
    delta = (months.dt.year - first_month.year) * 12 + (months.dt.month - first_month.month)
    result: pd.Series = (delta // 12) + 1
    return result


def resolve_model_anchor(
    frame: pd.DataFrame, model_start: str | pd.Timestamp | None = None
) -> ModelPeriod:
    """Resolve the canonical model start date for period mapping.

    The anchor must be independent of any display filter, so it is resolved
    once from the **full** dataset:

    * an explicit `model_start` wins, normalized to the first of its month;
    * otherwise the default is the earliest month present in the dataset.

    Both are deterministic — the default is a property of the data, not of
    whatever the user happens to be looking at. Mapping is never re-derived
    from a filtered frame, which is what would otherwise relabel a
    mid-horizon selection as Model Year 1.
    """
    if model_start is not None:
        try:
            parsed = pd.Timestamp(model_start)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Invalid model start date {model_start!r}: {exc}. "
                "Use an ISO date such as 2024-01-01."
            ) from None
        if parsed is pd.NaT or pd.isna(parsed):
            raise ValueError(
                f"Invalid model start date {model_start!r}: not a valid date. "
                "Use an ISO date such as 2024-01-01."
            )
        anchor = parsed.to_period("M").to_timestamp()
        return ModelPeriod(anchor=anchor, basis="explicit model start date")

    if "month" not in frame.columns or frame.empty:
        raise ValueError(
            "Cannot resolve a model anchor: the dataset has no months. "
            "Supply an explicit model start date."
        )
    anchor = pd.Timestamp(frame["month"].min()).to_period("M").to_timestamp()
    return ModelPeriod(anchor=anchor, basis="earliest month in the full dataset (default)")


def assign_model_period(frame: pd.DataFrame, period: ModelPeriod) -> pd.DataFrame:
    """Stamp each row with its model year, measured from the fixed anchor.

    This must run on the unfiltered dataset. Every downstream filter then
    carries the assignment along instead of recomputing it, so a month's model
    year does not depend on which rows are currently displayed.
    """
    result = frame.copy()
    result["model_year"] = model_year_index(result["month"], period.anchor)
    return result


def plan_vs_actual(
    monthly: pd.DataFrame,
    result: ModelResult,
    period: ModelPeriod,
) -> pd.DataFrame:
    """Actual operating revenue/EBITDA against the modelled annual plan.

    Model years are read from the `model_year` column stamped on the rows by
    `assign_model_period` **before** filtering. They are never inferred from
    the first month surviving a filter, so selecting a mid-horizon date range
    keeps its true model-year numbering instead of restarting at 1.

    The plan is the Phase 3 annual figure for the corresponding model year; it
    is a modelled value, not an actual, and `Months Covered` shows how much of
    each year the actuals span so a partial year is never silently compared
    against a full-year plan.
    """
    if "model_year" not in monthly.columns:
        raise ValueError(
            "Plan comparison requires a 'model_year' column. Call "
            "assign_model_period() on the full dataset before filtering; the "
            "model year must not be inferred from filtered data."
        )

    columns = [
        "Model Year",
        "Model Anchor",
        "Months Covered",
        "Plan Revenue ($M)",
        "Actual Revenue ($M)",
        "Revenue Variance ($M)",
        "Revenue Variance %",
        "Plan EBITDA ($M)",
        "Actual EBITDA ($M)",
        "EBITDA Variance ($M)",
        "EBITDA Variance %",
    ]
    if monthly.empty:
        return pd.DataFrame(columns=columns)

    working = monthly.sort_values("month").copy()
    working["Model Year"] = working["model_year"]

    schedule = result.schedule
    horizon = int(schedule["Year"].max())
    working = working[(working["Model Year"] >= 1) & (working["Model Year"] <= horizon)]
    if working.empty:
        return pd.DataFrame(columns=columns)

    actual = working.groupby("Model Year", as_index=False).agg(
        months=("month", "count"),
        revenue=("revenue", "sum"),
        ebitda=("ebitda", "sum"),
    )
    plan = schedule[["Year", "Revenue", "EBITDA"]].rename(columns={"Year": "Model Year"})
    merged = actual.merge(plan, on="Model Year", how="left")

    merged["Actual Revenue ($M)"] = merged["revenue"] / USD_PER_MILLION
    merged["Actual EBITDA ($M)"] = merged["ebitda"] / USD_PER_MILLION
    merged = merged.rename(
        columns={
            "months": "Months Covered",
            "Revenue": "Plan Revenue ($M)",
            "EBITDA": "Plan EBITDA ($M)",
        }
    )
    merged["Revenue Variance ($M)"] = merged["Actual Revenue ($M)"] - merged["Plan Revenue ($M)"]
    merged["EBITDA Variance ($M)"] = merged["Actual EBITDA ($M)"] - merged["Plan EBITDA ($M)"]
    merged["Revenue Variance %"] = _series_ratio(
        merged["Revenue Variance ($M)"], merged["Plan Revenue ($M)"]
    )
    merged["EBITDA Variance %"] = _series_ratio(
        merged["EBITDA Variance ($M)"], merged["Plan EBITDA ($M)"]
    )
    # Self-describing output: the anchor travels with the numbers it labels.
    merged["Model Anchor"] = f"{period.anchor:%Y-%m}"
    return merged[columns].reset_index(drop=True)


def capital_structure_view(result: ModelResult, scenario: Scenario) -> pd.DataFrame:
    """Modelled debt, leverage, headroom, and liquidity by year.

    Every column is read from the Phase 3 schedule. Nothing here is an actual,
    and no financial formula is re-implemented: leverage headroom is the
    covenant ceiling already declared in the model's assumptions.
    """
    schedule = result.schedule
    ceiling = float(scenario.assumptions.max_pro_forma_leverage)
    view = pd.DataFrame(
        {
            "Model Year": schedule["Year"],
            "EBITDA ($M)": schedule["EBITDA"],
            "Ending Debt ($M)": schedule["Ending Debt"],
            "Ending Cash ($M)": schedule["Ending Cash"],
            "Gross Leverage (x)": schedule["Gross Leverage"],
            "Net Leverage (x)": schedule["Net Leverage"],
            "Covenant Ceiling (x)": ceiling,
        }
    )
    view["Headroom (turns)"] = view["Covenant Ceiling (x)"] - view["Gross Leverage (x)"]
    view["Incremental Debt Capacity ($M)"] = (
        view["Covenant Ceiling (x)"] * view["EBITDA ($M)"] - view["Ending Debt ($M)"]
    ).clip(lower=0.0)
    view["Liquidity ($M)"] = view["Ending Cash ($M)"]
    return view.reset_index(drop=True)


def synergy_realization_view(result: ModelResult) -> pd.DataFrame:
    """Modelled platform/add-on EBITDA split and synergy capture by year."""
    schedule = result.schedule
    view = pd.DataFrame(
        {
            "Model Year": schedule["Year"],
            "Acquisitions Closed": schedule["Acquisitions Closed"],
            "Platform EBITDA ($M)": schedule["Platform EBITDA"],
            "Add-on EBITDA ($M)": schedule["Add-on EBITDA"],
            "Realized Synergies ($M)": schedule["Realized Synergies"],
            "Total EBITDA ($M)": schedule["EBITDA"],
        }
    )
    view["Synergy % of EBITDA"] = _series_ratio(
        view["Realized Synergies ($M)"], view["Total EBITDA ($M)"]
    )
    view["Add-on % of EBITDA"] = _series_ratio(
        view["Add-on EBITDA ($M)"], view["Total EBITDA ($M)"]
    )
    return view.reset_index(drop=True)


def organic_growth_view(monthly: pd.DataFrame) -> pd.DataFrame:
    """Year-over-year revenue and EBITDA growth from actuals only.

    Compares each month against the same month twelve periods earlier, so it is
    only populated once thirteen months of history are in the window.
    """
    columns = ["month", "revenue", "ebitda", "revenue_yoy", "ebitda_yoy", "ebitda_margin"]
    if monthly.empty:
        return pd.DataFrame(columns=columns)
    view = monthly.sort_values("month").reset_index(drop=True)
    result = view[["month", "revenue", "ebitda", "ebitda_margin"]].copy()
    result["revenue_yoy"] = _series_ratio(view["revenue"], view["revenue"].shift(12)) - 1.0
    result["ebitda_yoy"] = _series_ratio(view["ebitda"], view["ebitda"].shift(12)) - 1.0
    return result[columns]


# ---------------------------------------------------------------------------
# Deterministic sample data
# ---------------------------------------------------------------------------

# Synthetic year-on-year execution variance, so plan-versus-actual shows real
# variances instead of a flat line. Author-defined, not a blueprint figure.
_PERFORMANCE_FACTORS = (0.985, 1.021, 0.964, 1.008, 0.992)

_SERVICES = (
    "CCTV & Condition Assessment",
    "Leak Detection",
    "Valve & Hydrant Programs",
    "Cleaning & Jetting",
    "Compliance Sampling",
)

_SERVICE_MARGIN = {
    "CCTV & Condition Assessment": 0.48,
    "Leak Detection": 0.52,
    "Valve & Hydrant Programs": 0.44,
    "Cleaning & Jetting": 0.38,
    "Compliance Sampling": 0.55,
}

_SERVICE_RECURRING = {
    "CCTV & Condition Assessment": 0.48,
    "Leak Detection": 0.42,
    "Valve & Hydrant Programs": 0.72,
    "Cleaning & Jetting": 0.58,
    "Compliance Sampling": 0.86,
}

_PLATFORM_REGIONS = ("Midwest", "Southeast", "Mid-Atlantic", "Mountain West")

# Branch-level execution differences. Mountain West is deliberately generated
# as an underperforming branch — thin margin, poor utilization, loose routing,
# elevated churn — so that the management-exception workflow is demonstrable
# when filtering to it. This is synthetic colour, not a claim about any market.
_REGION_MARGIN_ADJ = {
    "Midwest": 0.0,
    "Southeast": 0.010,
    "Mid-Atlantic": -0.010,
    "Mountain West": -0.135,
}
_REGION_UTILIZATION_ADJ = {
    "Midwest": 0.0,
    "Southeast": 0.010,
    "Mid-Atlantic": -0.010,
    "Mountain West": -0.115,
}
_REGION_CHURN_ADJ = {
    "Midwest": 0.0,
    "Southeast": -0.001,
    "Mid-Atlantic": 0.001,
    "Mountain West": 0.011,
}
_REGION_MILES_FACTOR = {
    "Midwest": 1.0,
    "Southeast": 0.96,
    "Mid-Atlantic": 1.03,
    "Mountain West": 1.55,
}

# Add-ons are geographic tuck-ins: each joins one or two branches with a
# narrower service mix, and only from its modelled close year onward.
_ADDON_FOOTPRINT = {
    "Add-on A": (("Midwest",), _SERVICES[:3]),
    "Add-on B": (("Southeast",), _SERVICES[1:4]),
    "Add-on C": (("Mid-Atlantic", "Mountain West"), _SERVICES[2:5]),
}


def generate_sample_data(
    seed: int = 17,
    *,
    scenario_name: str = "base",
    start: str = "2024-01-01",
) -> pd.DataFrame:
    """Deterministic synthetic operating data calibrated to the Phase 3 model.

    The dataset is entirely synthetic. Its **annual revenue and EBITDA totals
    are rescaled to the Phase 3 base case** (times a fixed per-year execution
    factor) so that plan-versus-actual is coherent rather than comparing two
    unrelated scales. The region / service-line / business-unit split within a
    year is invented and carries no claim about any real business.

    Business units mirror the model's acquisition schedule: the platform runs
    for the full horizon and each add-on appears from its modelled close year.
    """
    scenario = SCENARIOS[scenario_name]
    result = run_scenario(scenario)
    assumptions = scenario.assumptions
    schedule = result.schedule
    horizon = int(assumptions.forecast_years)

    rng = np.random.default_rng(seed)
    months = pd.date_range(start, periods=12 * horizon, freq="MS")

    close_year = {add_on.name: add_on.close_year for add_on in scenario.add_ons}

    rows: list[dict[str, object]] = []
    for month_index, month in enumerate(months):
        year = month_index // 12 + 1
        seasonality = 1 + 0.07 * np.sin((month.month - 3) / 12 * 2 * np.pi)

        units: list[tuple[str, Sequence[str], Sequence[str]]] = [
            ("Platform", _PLATFORM_REGIONS, _SERVICES)
        ]
        for name, (regions, services) in _ADDON_FOOTPRINT.items():
            if name in close_year and year >= close_year[name]:
                units.append((name, regions, services))

        for unit_index, (unit, unit_regions, unit_services) in enumerate(units):
            for region_index, region in enumerate(unit_regions):
                for service_index, service in enumerate(unit_services):
                    shape = (
                        (1.0 + 0.10 * region_index)
                        * (1.0 + 0.06 * service_index)
                        * (1.0 - 0.18 * unit_index)
                        * seasonality
                        * float(rng.normal(1.0, 0.05))
                    )
                    rows.append(
                        {
                            "month": month,
                            "model_year": year,
                            "business_unit": unit,
                            "region": region,
                            "service_line": service,
                            "_shape": max(shape, 0.15),
                            "_service": service,
                            "_month_index": month_index,
                        }
                    )

    frame = pd.DataFrame(rows)

    # Rescale each model year so annual revenue and EBITDA reconcile to the
    # model, distributing the year's total across segments by their generated
    # shape weights.
    revenue_target = {
        int(row["Year"]): float(row["Revenue"]) * USD_PER_MILLION
        for _, row in schedule.iterrows()
    }
    ebitda_target = {
        int(row["Year"]): float(row["EBITDA"]) * USD_PER_MILLION
        for _, row in schedule.iterrows()
    }
    year_series = frame["model_year"].astype(int)
    factor = year_series.map(
        lambda y: _PERFORMANCE_FACTORS[(int(y) - 1) % len(_PERFORMANCE_FACTORS)]
    )
    weight = frame["_shape"] / frame.groupby("model_year")["_shape"].transform("sum")

    frame["revenue"] = weight * year_series.map(revenue_target) * factor
    frame["ebitda"] = weight * year_series.map(ebitda_target) * factor

    month_index_arr = frame["_month_index"].to_numpy()
    region_margin = frame["region"].map(_REGION_MARGIN_ADJ).to_numpy(dtype=float)
    region_utilization = frame["region"].map(_REGION_UTILIZATION_ADJ).to_numpy(dtype=float)
    region_churn = frame["region"].map(_REGION_CHURN_ADJ).to_numpy(dtype=float)
    region_miles = frame["region"].map(_REGION_MILES_FACTOR).to_numpy(dtype=float)

    margin_noise = rng.normal(0, 0.012, len(frame))
    gross_margin = np.clip(
        frame["_service"].map(_SERVICE_MARGIN).to_numpy(dtype=float)
        + region_margin
        + 0.0009 * month_index_arr
        + margin_noise,
        0.20,
        0.65,
    )
    frame["gross_profit"] = frame["revenue"] * gross_margin

    jobs_divisor = rng.uniform(2_800, 4_500, len(frame))
    frame["completed_jobs"] = np.maximum(12, (frame["revenue"] / jobs_divisor).astype(int))
    frame["route_miles"] = (
        frame["completed_jobs"]
        * rng.uniform(9, 15, len(frame))
        * region_miles
        * (1 - np.minimum(month_index_arr * 0.002, 0.07))
    )
    frame["paid_hours"] = frame["completed_jobs"] * rng.uniform(11, 16, len(frame))
    utilization = np.clip(
        0.67 + region_utilization + 0.0015 * month_index_arr + rng.normal(0, 0.02, len(frame)),
        0.45,
        0.86,
    )
    frame["billable_hours"] = frame["paid_hours"] * utilization

    frame["active_customers"] = (
        42
        + frame.groupby(["business_unit", "region"]).ngroup() * 5
        + (month_index_arr * 0.5).astype(int)
    ).astype(int)
    churn_rate = np.clip(
        0.012 + region_churn - month_index_arr * 0.00012 + rng.normal(0, 0.0018, len(frame)),
        0.002,
        0.030,
    )
    frame["lost_customers"] = np.round(frame["active_customers"] * churn_rate).astype(int)

    recurring_share = np.clip(
        frame["_service"].map(_SERVICE_RECURRING).to_numpy(dtype=float)
        + month_index_arr * 0.0015
        + rng.normal(0, 0.015, len(frame)),
        0.25,
        0.95,
    )
    frame["recurring_revenue"] = frame["revenue"] * recurring_share
    frame["lost_recurring_revenue"] = frame["recurring_revenue"] * churn_rate

    # Cash lines use the model's own assumption rates rather than new constants.
    frame["capex"] = frame["revenue"] * float(assumptions.capex_pct_revenue)
    interest_by_year = {
        int(row["Year"]): float(row["Cash Interest"]) * USD_PER_MILLION
        for _, row in schedule.iterrows()
    }
    revenue_by_year = frame.groupby("model_year")["revenue"].transform("sum")
    frame["cash_interest"] = (
        frame["revenue"] / revenue_by_year * frame["model_year"].map(interest_by_year)
    )
    frame["cash_taxes"] = np.maximum(
        (frame["ebitda"] - frame["capex"] - frame["cash_interest"])
        * float(assumptions.tax_rate),
        0.0,
    )
    frame["delta_nwc"] = frame["revenue"] * float(assumptions.nwc_pct_incremental_revenue)

    dso = np.clip(58 - month_index_arr * 0.25 + rng.normal(0, 3.5, len(frame)), 32, 78)
    days = frame["month"].dt.days_in_month.astype(float)
    frame["accounts_receivable"] = frame["revenue"] * dso / days

    ordered = [
        "month",
        "business_unit",
        "region",
        "service_line",
        "revenue",
        "gross_profit",
        "ebitda",
        "paid_hours",
        "billable_hours",
        "completed_jobs",
        "route_miles",
        "active_customers",
        "lost_customers",
        "recurring_revenue",
        "lost_recurring_revenue",
        "capex",
        "cash_taxes",
        "cash_interest",
        "delta_nwc",
        "accounts_receivable",
    ]
    return frame[ordered].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Headless generation
# ---------------------------------------------------------------------------


def write_operating_outputs(
    frame: pd.DataFrame,
    output_dir: Path,
    *,
    scenario_name: str = "base",
    thresholds: Thresholds | None = None,
    model_start: str | pd.Timestamp | None = None,
) -> dict[str, Path]:
    """Write every dashboard table to CSV for review and regression testing."""
    t = thresholds or Thresholds()
    output_dir.mkdir(parents=True, exist_ok=True)
    scenario = SCENARIOS[scenario_name]
    result = run_scenario(scenario)

    # Anchor on the full dataset before anything narrows it.
    period = resolve_model_anchor(frame, model_start)
    frame = assign_model_period(frame, period)
    monthly = monthly_rollup(frame)
    written: dict[str, Path] = {}

    def _write(name: str, table: pd.DataFrame) -> None:
        path = output_dir / name
        table.to_csv(path, index=False)
        written[name] = path

    _write("operating_data.csv", frame)
    _write("monthly_kpis.csv", monthly)
    _write("kpi_summary.csv", kpi_summary(monthly, t))
    _write("exceptions.csv", exception_report(monthly, t))
    _write("metric_definitions.csv", metric_definitions_table(t))
    _write("organic_growth.csv", organic_growth_view(monthly))
    _write("plan_vs_actual.csv", plan_vs_actual(monthly, result, period))
    _write("capital_structure.csv", capital_structure_view(result, scenario))
    _write("synergy_realization.csv", synergy_realization_view(result))
    for dimension in available_dimensions(frame):
        _write(f"performance_by_{dimension}.csv", dimension_rollup(frame, dimension))
    return written


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", default="outputs/operations", help="Directory for CSV outputs"
    )
    parser.add_argument(
        "--scenario",
        default="base",
        choices=sorted(SCENARIOS),
        help="Phase 3 scenario used for modelled plan and capital-structure views",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Operating CSV to use instead of the deterministic sample dataset",
    )
    parser.add_argument(
        "--model-start",
        default=None,
        help=(
            "Calendar month that is Model Year 1 (ISO, e.g. 2024-01-01). "
            "Defaults to the earliest month in the dataset."
        ),
    )
    args = parser.parse_args(argv)

    try:
        if args.input is not None:
            frame = validate_operating_data(pd.read_csv(args.input))
            source = str(args.input)
        else:
            frame = validate_operating_data(generate_sample_data(scenario_name=args.scenario))
            source = "deterministic sample dataset (synthetic)"
    except FileNotFoundError:
        raise SystemExit(f"error: input file not found: {args.input}") from None
    except (ValueError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise SystemExit(f"error: {exc}") from None

    try:
        period = resolve_model_anchor(frame, args.model_start)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None

    written = write_operating_outputs(
        frame,
        Path(args.output_dir),
        scenario_name=args.scenario,
        model_start=period.anchor,
    )

    frame = assign_model_period(frame, period)
    monthly = monthly_rollup(frame)
    summary = kpi_summary(monthly)
    exceptions = exception_report(monthly)

    print(f"Source: {source}")
    print(f"Scenario for modelled views: {args.scenario}")
    print(f"Model period basis: {period.label}")
    print(f"Rows: {len(frame):,}  Months: {frame['month'].nunique()}  "
          f"Period: {frame['month'].min():%Y-%m} to {frame['month'].max():%Y-%m}")
    print("\nKPI SUMMARY (trailing 3 months vs prior 3 months)\n")
    print(summary[["Metric", "Current", "Prior", "Target", "Status"]].to_string(
        index=False, float_format=lambda x: f"{x:,.4f}"
    ))
    print(f"\nMANAGEMENT EXCEPTIONS: {len(exceptions)}\n")
    if exceptions.empty:
        print("None — every governing KPI is at or above target.")
    else:
        print(exceptions.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))
    print(f"\n{len(written)} file(s) written to {args.output_dir}/")


if __name__ == "__main__":
    main()
