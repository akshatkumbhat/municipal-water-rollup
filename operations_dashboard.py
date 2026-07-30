"""Executive operating dashboard for a municipal water/wastewater services platform.

Run:
    streamlit run operations_dashboard.py

The app is self-contained and generates realistic sample data when no CSV is
uploaded. An uploaded CSV should follow the schema shown in the sidebar help.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Water Infrastructure Services | COO Dashboard",
    page_icon="💧",
    layout="wide",
)

REQUIRED_COLUMNS = {
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


@dataclass(frozen=True)
class Thresholds:
    route_density: float = 7.0  # completed jobs per 100 route miles
    utilization: float = 0.72
    gross_margin: float = 0.42
    recurring_mix: float = 0.60
    monthly_churn: float = 0.008
    fcf_conversion: float = 0.55


@st.cache_data(show_spinner=False)
def generate_sample_data(seed: int = 17) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    months = pd.date_range("2023-01-01", periods=42, freq="MS")
    regions = ["Midwest", "Southeast", "Mid-Atlantic", "Mountain West"]
    services = [
        "CCTV & Condition Assessment",
        "Leak Detection",
        "Valve & Hydrant Programs",
        "Cleaning & Jetting",
        "Compliance Sampling",
    ]
    service_margin = {
        "CCTV & Condition Assessment": 0.48,
        "Leak Detection": 0.52,
        "Valve & Hydrant Programs": 0.44,
        "Cleaning & Jetting": 0.38,
        "Compliance Sampling": 0.55,
    }
    service_recurring = {
        "CCTV & Condition Assessment": 0.48,
        "Leak Detection": 0.42,
        "Valve & Hydrant Programs": 0.72,
        "Cleaning & Jetting": 0.58,
        "Compliance Sampling": 0.86,
    }
    rows: list[dict[str, object]] = []
    for month_index, month in enumerate(months):
        seasonality = 1 + 0.07 * np.sin((month.month - 3) / 12 * 2 * np.pi)
        trend = (1.012) ** month_index
        for region_index, region in enumerate(regions):
            region_scale = 1 + 0.10 * region_index
            for service_index, service in enumerate(services):
                base_revenue = 90_000 + 14_000 * service_index
                revenue = base_revenue * trend * seasonality * region_scale * rng.normal(1, 0.06)
                gross_margin = np.clip(
                    service_margin[service] + 0.0009 * month_index + rng.normal(0, 0.015),
                    0.28,
                    0.65,
                )
                gross_profit = revenue * gross_margin
                ebitda_margin = np.clip(0.17 + 0.0007 * month_index + rng.normal(0, 0.012), 0.10, 0.28)
                ebitda = revenue * ebitda_margin
                jobs = max(12, int(revenue / rng.uniform(2_800, 4_500)))
                route_miles = jobs * rng.uniform(9, 15) * (1 - min(month_index * 0.002, 0.07))
                paid_hours = jobs * rng.uniform(11, 16)
                utilization = np.clip(0.67 + 0.0015 * month_index + rng.normal(0, 0.025), 0.52, 0.86)
                billable_hours = paid_hours * utilization
                active_customers = int(42 + region_index * 7 + service_index * 4 + month_index * 0.5)
                churn_rate = np.clip(0.012 - month_index * 0.00012 + rng.normal(0, 0.002), 0.002, 0.025)
                lost_customers = int(round(active_customers * churn_rate))
                recurring_revenue = revenue * np.clip(
                    service_recurring[service] + month_index * 0.0015 + rng.normal(0, 0.015),
                    0.25,
                    0.95,
                )
                capex = revenue * rng.uniform(0.025, 0.038)
                cash_interest = ebitda * rng.uniform(0.10, 0.17)
                cash_taxes = max((ebitda - capex - cash_interest) * 0.25, 0)
                delta_nwc = max(revenue * rng.normal(0.008, 0.005), -revenue * 0.004)
                dso = np.clip(58 - month_index * 0.25 + rng.normal(0, 4), 32, 78)
                accounts_receivable = revenue * dso / month.days_in_month
                rows.append(
                    {
                        "month": month,
                        "region": region,
                        "service_line": service,
                        "revenue": revenue,
                        "gross_profit": gross_profit,
                        "ebitda": ebitda,
                        "paid_hours": paid_hours,
                        "billable_hours": billable_hours,
                        "completed_jobs": jobs,
                        "route_miles": route_miles,
                        "active_customers": active_customers,
                        "lost_customers": lost_customers,
                        "recurring_revenue": recurring_revenue,
                        "capex": capex,
                        "cash_taxes": cash_taxes,
                        "cash_interest": cash_interest,
                        "delta_nwc": delta_nwc,
                        "accounts_receivable": accounts_receivable,
                    }
                )
    return pd.DataFrame(rows)


def validate_data(frame: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {sorted(missing)}")
    result = frame.copy()
    result["month"] = pd.to_datetime(result["month"], errors="raise").dt.to_period("M").dt.to_timestamp()
    numeric_columns = REQUIRED_COLUMNS - {"month", "region", "service_line"}
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result[list(numeric_columns)].isna().any().any():
        bad = result[list(numeric_columns)].columns[result[list(numeric_columns)].isna().any()].tolist()
        raise ValueError(f"Non-numeric or missing values detected in: {bad}")
    if (result[["revenue", "paid_hours", "route_miles", "active_customers"]] < 0).any().any():
        raise ValueError("Revenue, hours, route miles, and active customers cannot be negative")
    return result


def safe_divide(numerator: pd.Series | float, denominator: pd.Series | float) -> pd.Series | float:
    if isinstance(denominator, pd.Series):
        return numerator / denominator.replace(0, np.nan)
    return numerator / denominator if denominator else np.nan


def monthly_rollup(frame: pd.DataFrame) -> pd.DataFrame:
    numeric = [c for c in frame.columns if c not in {"month", "region", "service_line"}]
    result = frame.groupby("month", as_index=False)[numeric].sum()
    result["route_density"] = safe_divide(result["completed_jobs"] * 100, result["route_miles"])
    result["utilization"] = safe_divide(result["billable_hours"], result["paid_hours"])
    result["gross_margin"] = safe_divide(result["gross_profit"], result["revenue"])
    result["recurring_mix"] = safe_divide(result["recurring_revenue"], result["revenue"])
    result["monthly_churn"] = safe_divide(result["lost_customers"], result["active_customers"])
    result["free_cash_flow"] = (
        result["ebitda"]
        - result["capex"]
        - result["cash_taxes"]
        - result["cash_interest"]
        - result["delta_nwc"]
    )
    result["fcf_conversion"] = safe_divide(result["free_cash_flow"], result["ebitda"])
    days = result["month"].dt.days_in_month
    result["dso"] = safe_divide(result["accounts_receivable"] * days, result["revenue"])
    result["ebitda_margin"] = safe_divide(result["ebitda"], result["revenue"])
    return result


def period_kpi(frame: pd.DataFrame, column: str, months: int = 3) -> tuple[float, float | None]:
    frame = frame.sort_values("month")
    current = frame.tail(months)[column].mean()
    prior_slice = frame.iloc[-2 * months : -months]
    prior = prior_slice[column].mean() if len(prior_slice) == months else None
    return float(current), float(prior) if prior is not None else None


def delta_text(current: float, prior: float | None, percent: bool = False, inverse: bool = False) -> str | None:
    if prior is None or np.isnan(prior):
        return None
    change = current - prior
    if inverse:
        change *= -1
    return f"{change:+.1%}" if percent else f"{change:+.2f}"


def metric_card(label: str, value: str, delta: str | None, help_text: str) -> None:
    st.metric(label, value, delta=delta, help=help_text)


def main() -> None:
    st.title("Municipal Water & Wastewater Services — COO Dashboard")
    st.caption("Operating cadence: route economics, field labor, retention, margin, and cash conversion.")

    with st.sidebar:
        st.header("Data")
        uploaded = st.file_uploader("Upload monthly operating CSV", type=["csv"])
        with st.expander("Required CSV schema"):
            st.code(", ".join(sorted(REQUIRED_COLUMNS)), language="text")
        st.caption("No upload is required; the dashboard opens with a deterministic sample dataset.")

    try:
        source = pd.read_csv(uploaded) if uploaded else generate_sample_data()
        data = validate_data(source)
    except Exception as exc:
        st.error(f"Data validation failed: {exc}")
        st.stop()

    with st.sidebar:
        min_month, max_month = data["month"].min().date(), data["month"].max().date()
        date_range = st.date_input(
            "Reporting period",
            value=(min_month, max_month),
            min_value=min_month,
            max_value=max_month,
        )
        region_options = sorted(data["region"].unique())
        service_options = sorted(data["service_line"].unique())
        selected_regions = st.multiselect("Regions", region_options, default=region_options)
        selected_services = st.multiselect("Service lines", service_options, default=service_options)

    if len(date_range) != 2:
        st.warning("Select a start and end date.")
        st.stop()
    start_date, end_date = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    filtered = data[
        data["month"].between(start_date, end_date)
        & data["region"].isin(selected_regions)
        & data["service_line"].isin(selected_services)
    ].copy()
    if filtered.empty:
        st.warning("No data matches the selected filters.")
        st.stop()

    monthly = monthly_rollup(filtered)
    thresholds = Thresholds()

    kpis = {
        "route_density": period_kpi(monthly, "route_density"),
        "utilization": period_kpi(monthly, "utilization"),
        "gross_margin": period_kpi(monthly, "gross_margin"),
        "recurring_mix": period_kpi(monthly, "recurring_mix"),
        "monthly_churn": period_kpi(monthly, "monthly_churn"),
        "fcf_conversion": period_kpi(monthly, "fcf_conversion"),
    }

    cols = st.columns(6)
    with cols[0]:
        cur, prior = kpis["route_density"]
        metric_card("Route density", f"{cur:.1f}", delta_text(cur, prior), "Completed jobs per 100 route miles; higher is better.")
    with cols[1]:
        cur, prior = kpis["utilization"]
        metric_card("Billable utilization", f"{cur:.1%}", delta_text(cur, prior, percent=True), "Billable field hours divided by paid field hours.")
    with cols[2]:
        cur, prior = kpis["gross_margin"]
        metric_card("Gross margin", f"{cur:.1%}", delta_text(cur, prior, percent=True), "Revenue less direct labor, materials, and subcontractors.")
    with cols[3]:
        cur, prior = kpis["recurring_mix"]
        metric_card("Recurring revenue", f"{cur:.1%}", delta_text(cur, prior, percent=True), "Revenue under recurring inspection, maintenance, sampling, or monitoring programs.")
    with cols[4]:
        cur, prior = kpis["monthly_churn"]
        metric_card("Monthly churn", f"{cur:.2%}", delta_text(cur, prior, percent=True, inverse=True), "Lost customers divided by active customers; lower is better.")
    with cols[5]:
        cur, prior = kpis["fcf_conversion"]
        metric_card("FCF conversion", f"{cur:.1%}", delta_text(cur, prior, percent=True), "Levered free cash flow divided by EBITDA.")

    st.divider()
    left, right = st.columns((1.55, 1))
    with left:
        financial = monthly.melt(
            id_vars="month",
            value_vars=["revenue", "ebitda", "free_cash_flow"],
            var_name="metric",
            value_name="value",
        )
        fig = px.line(financial, x="month", y="value", color="metric", markers=True, title="Revenue, EBITDA, and free cash flow")
        fig.update_layout(yaxis_title="$", xaxis_title="", legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
    with right:
        service = filtered.groupby("service_line", as_index=False).agg(revenue=("revenue", "sum"), gross_profit=("gross_profit", "sum"))
        service["gross_margin"] = safe_divide(service["gross_profit"], service["revenue"])
        fig = px.bar(service.sort_values("gross_margin"), x="gross_margin", y="service_line", orientation="h", text_auto=".1%", title="Gross margin by service line")
        fig.update_layout(xaxis_tickformat=".0%", xaxis_title="Gross margin", yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        ops = monthly.melt(
            id_vars="month",
            value_vars=["route_density", "utilization"],
            var_name="metric",
            value_name="value",
        )
        fig = px.line(ops, x="month", y="value", color="metric", markers=True, title="Field productivity trend")
        fig.add_hline(y=thresholds.utilization, line_dash="dot", annotation_text="Utilization target")
        fig.update_layout(xaxis_title="", yaxis_title="Mixed units", legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
    with right:
        retention = monthly.melt(
            id_vars="month",
            value_vars=["recurring_mix", "monthly_churn"],
            var_name="metric",
            value_name="value",
        )
        fig = px.line(retention, x="month", y="value", color="metric", markers=True, title="Revenue durability")
        fig.update_layout(xaxis_title="", yaxis_tickformat=".0%", yaxis_title="Rate", legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        region = filtered.groupby("region", as_index=False).agg(
            revenue=("revenue", "sum"),
            ebitda=("ebitda", "sum"),
            paid_hours=("paid_hours", "sum"),
            billable_hours=("billable_hours", "sum"),
        )
        region["ebitda_margin"] = safe_divide(region["ebitda"], region["revenue"])
        region["utilization"] = safe_divide(region["billable_hours"], region["paid_hours"])
        fig = px.scatter(
            region,
            x="utilization",
            y="ebitda_margin",
            size="revenue",
            text="region",
            title="Regional productivity versus EBITDA margin",
        )
        fig.update_traces(textposition="top center")
        fig.update_layout(xaxis_tickformat=".0%", yaxis_tickformat=".0%", xaxis_title="Billable utilization", yaxis_title="EBITDA margin")
        st.plotly_chart(fig, use_container_width=True)
    with right:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=monthly["month"], y=monthly["dso"], name="DSO"))
        fig.add_trace(go.Scatter(x=monthly["month"], y=monthly["fcf_conversion"], name="FCF conversion", yaxis="y2", mode="lines+markers"))
        fig.update_layout(
            title="Working capital and cash conversion",
            xaxis_title="",
            yaxis=dict(title="Days sales outstanding"),
            yaxis2=dict(title="FCF conversion", overlaying="y", side="right", tickformat=".0%"),
            legend=dict(orientation="h"),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Management exception report")
    latest = monthly.sort_values("month").iloc[-1]
    exceptions = pd.DataFrame(
        [
            ("Route density", latest["route_density"], thresholds.route_density, latest["route_density"] >= thresholds.route_density),
            ("Billable utilization", latest["utilization"], thresholds.utilization, latest["utilization"] >= thresholds.utilization),
            ("Gross margin", latest["gross_margin"], thresholds.gross_margin, latest["gross_margin"] >= thresholds.gross_margin),
            ("Recurring revenue", latest["recurring_mix"], thresholds.recurring_mix, latest["recurring_mix"] >= thresholds.recurring_mix),
            ("Monthly churn", latest["monthly_churn"], thresholds.monthly_churn, latest["monthly_churn"] <= thresholds.monthly_churn),
            ("FCF conversion", latest["fcf_conversion"], thresholds.fcf_conversion, latest["fcf_conversion"] >= thresholds.fcf_conversion),
        ],
        columns=["KPI", "Latest", "Target", "On Track"],
    )
    exceptions["Status"] = np.where(exceptions["On Track"], "On track", "Management action")
    st.dataframe(exceptions.drop(columns="On Track"), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
