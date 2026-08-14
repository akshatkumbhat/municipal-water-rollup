"""Executive operating cockpit for the municipal water/wastewater platform.

Run:
    streamlit run operations_dashboard.py

This module is a presentation layer only. Every number it renders is computed
in `operations_kpis`, which has no UI dependency and is covered by unit tests;
nothing is calculated inline here. Financial figures (plan, debt, leverage,
liquidity, synergies) are read from the Phase 3 model rather than re-derived,
and are labelled `Modelled` wherever they appear.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from buy_and_build_model import SCENARIOS, run_scenario
from operations_kpis import (
    METRIC_DEFINITIONS,
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
    metric_definitions_table,
    monthly_rollup,
    organic_growth_view,
    period_comparison,
    plan_vs_actual,
    resolve_model_anchor,
    synergy_realization_view,
    validate_operating_data,
)

st.set_page_config(
    page_title="Water Infrastructure Services | COO Dashboard",
    page_icon="💧",
    layout="wide",
)


@st.cache_data(show_spinner=False)
def _sample_data(scenario_name: str) -> pd.DataFrame:
    return validate_operating_data(generate_sample_data(scenario_name=scenario_name))


def _format_value(value: float, unit: str) -> str:
    if pd.isna(value):
        return "n/a"
    if unit == "percent":
        return f"{value:.1%}"
    if unit == "days":
        return f"{value:.1f} d"
    return f"{value:.2f}"


def _format_delta(change: float, unit: str) -> str | None:
    if pd.isna(change):
        return None
    if unit == "percent":
        return f"{change:+.1%} vs prior"
    if unit == "days":
        return f"{change:+.1f} d vs prior"
    return f"{change:+.2f} vs prior"


def _money_millions(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        out[column] = out[column] / 1_000_000.0
    return out


def main() -> None:
    st.title("Municipal Water & Wastewater Services — COO Dashboard")
    st.caption(
        "Operating cadence: route economics, field labor, retention, margin, and cash "
        "conversion. All monetary values in USD unless labelled ($M)."
    )

    # ---------------- Sidebar: data source, scenario, filters ----------------
    with st.sidebar:
        st.header("Data source")
        uploaded = st.file_uploader("Upload monthly operating CSV", type=["csv"])
        with st.expander("Required CSV schema"):
            st.code(", ".join(sorted(REQUIRED_COLUMNS)), language="text")
            st.caption(
                "Optional columns: business_unit (unlocks platform vs add-on views), "
                "lost_recurring_revenue (unlocks gross revenue retention)."
            )
        scenario_name = st.selectbox(
            "Model scenario for plan and capital structure",
            sorted(SCENARIOS),
            index=sorted(SCENARIOS).index("base"),
            help="Drives modelled sections only. Governing KPIs are always actuals.",
        )
        window = st.slider("Comparison window (months)", 1, 6, 3)

    try:
        if uploaded is not None:
            data = validate_operating_data(pd.read_csv(uploaded))
            source_label = f"Uploaded file — {uploaded.name}"
            source_kind = "Actual (uploaded)"
        else:
            data = _sample_data(scenario_name)
            source_label = "Deterministic sample dataset"
            source_kind = "SAMPLE (synthetic, calibrated to the Phase 3 base case)"
    except Exception as exc:  # surfaced to the user, never swallowed
        st.error(f"Data validation failed: {exc}")
        st.info("Fix the file and re-upload, or clear the upload to use sample data.")
        st.stop()

    dimensions = available_dimensions(data)

    # Resolve the model anchor from the FULL dataset and stamp every row with
    # its model year before any filter narrows the frame. Doing this after
    # filtering would relabel a mid-horizon selection as Model Year 1.
    with st.sidebar:
        st.header("Model period")
        default_anchor = resolve_model_anchor(data).anchor
        anchor_input = st.date_input(
            "Model Year 1 begins",
            value=default_anchor.date(),
            help=(
                "Anchors the mapping of calendar months to model years. Fixed "
                "independently of the reporting-period filter below. Defaults to "
                "the earliest month in the dataset."
            ),
        )
    try:
        period = resolve_model_anchor(data, pd.Timestamp(anchor_input))
    except ValueError as exc:
        st.error(f"Invalid model start date: {exc}")
        st.stop()

    data = assign_model_period(data, period)

    with st.sidebar:
        st.header("Filters")
        min_month = data["month"].min().date()
        max_month = data["month"].max().date()
        date_range = st.date_input(
            "Reporting period",
            value=(min_month, max_month),
            min_value=min_month,
            max_value=max_month,
        )
        regions = sorted(data["region"].unique())
        services = sorted(data["service_line"].unique())
        selected_regions = st.multiselect("Regions", regions, default=regions)
        selected_services = st.multiselect("Service lines", services, default=services)
        if "business_unit" in dimensions:
            units = sorted(data["business_unit"].unique())
            selected_units = st.multiselect("Business units", units, default=units)
        else:
            selected_units = None

    if not isinstance(date_range, tuple) or len(date_range) != 2:
        st.warning("Select both a start and an end date to continue.")
        st.stop()

    filtered = filter_operating_data(
        data,
        start=pd.Timestamp(date_range[0]),
        end=pd.Timestamp(date_range[1]),
        regions=selected_regions,
        service_lines=selected_services,
        business_units=selected_units,
    )

    if filtered.empty:
        st.warning(
            "No rows match the selected filters. Widen the date range or re-select "
            "regions, service lines, or business units."
        )
        st.stop()

    monthly = monthly_rollup(filtered)
    thresholds = Thresholds()
    result = run_scenario(SCENARIOS[scenario_name])

    # ---------------- Provenance banner ----------------
    st.info(
        f"**Data source:** {source_label}  \n"
        f"**Provenance:** {source_kind}  \n"
        f"**Period shown:** {filtered['month'].min():%b %Y} – {filtered['month'].max():%b %Y} "
        f"({monthly.shape[0]} months, {len(filtered):,} rows)  \n"
        f"**Modelled sections use:** Phase 3 `{scenario_name}` scenario — "
        f"{SCENARIOS[scenario_name].description}"
    )

    # ---------------- KPI cards ----------------
    st.subheader("Governing KPIs")
    st.caption(
        f"Trailing {window}-month window versus the preceding {window} months. "
        "Ratios are computed from summed numerators and denominators, not as an "
        "average of monthly ratios. Targets are author-defined illustrative "
        "thresholds — not externally benchmarked and not industry standards."
    )
    cards = st.columns(len(METRIC_DEFINITIONS))
    for column, metric in zip(cards, METRIC_DEFINITIONS, strict=True):
        comparison = period_comparison(monthly, metric, thresholds, window=window)
        with column:
            st.metric(
                label=metric.label,
                value=_format_value(comparison.current, metric.unit),
                delta=_format_delta(comparison.change, metric.unit),
                delta_color="inverse" if metric.direction == "lower" else "normal",
                help=(
                    f"{metric.definition}\n\n"
                    f"Formula: ({metric.numerator}) / ({metric.denominator})\n\n"
                    f"Target: {_format_value(comparison.target, metric.unit)} "
                    f"({'higher' if metric.direction == 'higher' else 'lower'} is better)\n\n"
                    f"Source: {metric.provenance}"
                ),
            )
            status = "On track" if comparison.on_track else "Management action"
            st.caption(
                f"{'✅' if comparison.on_track else '⚠️'} {status} · "
                f"target {_format_value(comparison.target, metric.unit)}"
            )

    if not has_grr_inputs(filtered):
        st.caption(
            "Gross revenue retention is not shown: this dataset has no "
            "`lost_recurring_revenue` column. Customer churn is reported instead."
        )

    st.divider()

    tabs = st.tabs(
        [
            "Performance",
            "Growth & margin",
            "Service line & region",
            "Platform vs add-on",
            "Capital structure (modelled)",
            "Exceptions",
            "Definitions & lineage",
        ]
    )

    # ---------------- Performance ----------------
    with tabs[0]:
        left, right = st.columns((1.5, 1))
        with left:
            trend = _money_millions(monthly, ["revenue", "ebitda", "free_cash_flow"])
            melted = trend.melt(
                id_vars="month",
                value_vars=["revenue", "ebitda", "free_cash_flow"],
                var_name="Measure",
                value_name="Value",
            )
            figure = px.line(
                melted,
                x="month",
                y="Value",
                color="Measure",
                markers=True,
                title="Revenue, EBITDA, and free cash flow (actual, $M)",
            )
            figure.update_layout(yaxis_title="$M", xaxis_title="Month", legend_title_text="")
            st.plotly_chart(figure, width="stretch")
        with right:
            st.markdown("**Plan versus actual** — plan is *modelled* (Phase 3)")
            st.caption(f"Period basis: {period.label}.")
            plan_table = plan_vs_actual(monthly, result, period)
            if plan_table.empty:
                st.info(
                    "Plan plan_table unavailable: the filtered period does not overlap "
                    "the model's five-year horizon."
                )
            else:
                st.dataframe(
                    plan_table.style.format(
                        {
                            "Plan Revenue ($M)": "{:,.2f}",
                            "Actual Revenue ($M)": "{:,.2f}",
                            "Revenue Variance ($M)": "{:+,.2f}",
                            "Revenue Variance %": "{:+.1%}",
                            "Plan EBITDA ($M)": "{:,.2f}",
                            "Actual EBITDA ($M)": "{:,.2f}",
                            "EBITDA Variance ($M)": "{:+,.2f}",
                            "EBITDA Variance %": "{:+.1%}",
                        }
                    ),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(
                    "`Months Covered` shows how much of each model year the actuals "
                    "span — a partial year is not a full-year miss. Model-year "
                    "numbering is fixed to the anchor above and does not restart "
                    "when the reporting period is narrowed."
                )

    # ---------------- Growth & margin ----------------
    with tabs[1]:
        growth = organic_growth_view(monthly)
        left, right = st.columns(2)
        with left:
            if growth["revenue_yoy"].notna().any():
                figure = px.line(
                    growth.dropna(subset=["revenue_yoy"]),
                    x="month",
                    y=["revenue_yoy", "ebitda_yoy"],
                    markers=True,
                    title="Year-over-year growth (actual)",
                )
                figure.update_layout(
                    yaxis_tickformat=".0%",
                    yaxis_title="YoY growth",
                    xaxis_title="Month",
                    legend_title_text="",
                )
                st.plotly_chart(figure, width="stretch")
            else:
                st.info(
                    "Year-over-year growth needs at least 13 months in the window; "
                    f"this selection has {len(growth)}."
                )
        with right:
            figure = px.line(
                monthly,
                x="month",
                y="ebitda_margin",
                markers=True,
                title="EBITDA margin (actual)",
            )
            figure.update_layout(
                yaxis_tickformat=".0%", yaxis_title="EBITDA margin", xaxis_title="Month"
            )
            st.plotly_chart(figure, width="stretch")

        st.markdown("**Synergy realization** — *modelled* (Phase 3)")
        synergies = synergy_realization_view(result)
        st.dataframe(
            synergies.style.format(
                {
                    "Platform EBITDA ($M)": "{:,.2f}",
                    "Add-on EBITDA ($M)": "{:,.2f}",
                    "Realized Synergies ($M)": "{:,.2f}",
                    "Total EBITDA ($M)": "{:,.2f}",
                    "Synergy % of EBITDA": "{:.1%}",
                    "Add-on % of EBITDA": "{:.1%}",
                }
            ),
            width="stretch",
            hide_index=True,
        )

    # ---------------- Service line & region ----------------
    with tabs[2]:
        left, right = st.columns(2)
        with left:
            service = dimension_rollup(filtered, "service_line")
            figure = px.bar(
                service.sort_values("gross_margin"),
                x="gross_margin",
                y="service_line",
                orientation="h",
                text_auto=".1%",
                title="Gross margin by service line (actual)",
            )
            figure.update_layout(xaxis_tickformat=".0%", xaxis_title="Gross margin", yaxis_title="")
            st.plotly_chart(figure, width="stretch")
        with right:
            region = dimension_rollup(filtered, "region")
            figure = px.scatter(
                region,
                x="utilization",
                y="ebitda_margin",
                size="revenue",
                text="region",
                title="Branch productivity versus EBITDA margin (actual)",
            )
            figure.update_traces(textposition="top center")
            figure.update_layout(
                xaxis_tickformat=".0%",
                yaxis_tickformat=".0%",
                xaxis_title="Billable utilization",
                yaxis_title="EBITDA margin",
            )
            st.plotly_chart(figure, width="stretch")

        st.dataframe(
            _money_millions(region, ["revenue", "ebitda"]).style.format(
                {
                    "revenue": "{:,.2f}",
                    "ebitda": "{:,.2f}",
                    "gross_margin": "{:.1%}",
                    "ebitda_margin": "{:.1%}",
                    "recurring_mix": "{:.1%}",
                    "utilization": "{:.1%}",
                    "route_density": "{:.2f}",
                    "revenue_share": "{:.1%}",
                }
            ),
            width="stretch",
            hide_index=True,
        )

    # ---------------- Platform vs add-on ----------------
    with tabs[3]:
        if "business_unit" not in dimensions:
            st.info(
                "This dataset has no `business_unit` column, so platform and add-on "
                "performance cannot be separated. Add the column to enable this view."
            )
        else:
            units_table = dimension_rollup(filtered, "business_unit")
            left, right = st.columns((1, 1.2))
            with left:
                figure = px.pie(
                    units_table,
                    names="business_unit",
                    values="revenue",
                    title="Revenue mix by business unit (actual)",
                    hole=0.45,
                )
                st.plotly_chart(figure, width="stretch")
            with right:
                figure = px.bar(
                    units_table,
                    x="business_unit",
                    y=["gross_margin", "ebitda_margin", "utilization"],
                    barmode="group",
                    title="Margin and utilization by business unit (actual)",
                )
                figure.update_layout(
                    yaxis_tickformat=".0%",
                    yaxis_title="Rate",
                    xaxis_title="",
                    legend_title_text="",
                )
                st.plotly_chart(figure, width="stretch")
            st.dataframe(
                _money_millions(units_table, ["revenue", "ebitda"]).style.format(
                    {
                        "revenue": "{:,.2f}",
                        "ebitda": "{:,.2f}",
                        "gross_margin": "{:.1%}",
                        "ebitda_margin": "{:.1%}",
                        "recurring_mix": "{:.1%}",
                        "utilization": "{:.1%}",
                        "route_density": "{:.2f}",
                        "revenue_share": "{:.1%}",
                    }
                ),
                width="stretch",
                hide_index=True,
            )

    # ---------------- Capital structure ----------------
    with tabs[4]:
        st.markdown(
            "Every figure on this tab is **modelled** from the Phase 3 "
            f"`{scenario_name}` scenario. None of it is an operating actual."
        )
        capital = capital_structure_view(result, SCENARIOS[scenario_name])
        figure = go.Figure()
        figure.add_trace(
            go.Bar(x=capital["Model Year"], y=capital["Ending Debt ($M)"], name="Ending debt ($M)")
        )
        figure.add_trace(
            go.Scatter(
                x=capital["Model Year"],
                y=capital["Net Leverage (x)"],
                name="Net leverage (x)",
                yaxis="y2",
                mode="lines+markers",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=capital["Model Year"],
                y=capital["Covenant Ceiling (x)"],
                name="Covenant ceiling (x)",
                yaxis="y2",
                mode="lines",
                line={"dash": "dot"},
            )
        )
        figure.update_layout(
            title="Modelled debt, leverage, and covenant headroom",
            xaxis_title="Model year",
            yaxis={"title": "Ending debt ($M)"},
            yaxis2={"title": "Leverage (x)", "overlaying": "y", "side": "right"},
            legend={"orientation": "h"},
        )
        st.plotly_chart(figure, width="stretch")
        st.dataframe(
            capital.style.format(
                {
                    "EBITDA ($M)": "{:,.2f}",
                    "Ending Debt ($M)": "{:,.2f}",
                    "Ending Cash ($M)": "{:,.2f}",
                    "Gross Leverage (x)": "{:,.2f}",
                    "Net Leverage (x)": "{:,.2f}",
                    "Covenant Ceiling (x)": "{:,.2f}",
                    "Headroom (turns)": "{:,.2f}",
                    "Incremental Debt Capacity ($M)": "{:,.2f}",
                    "Liquidity ($M)": "{:,.2f}",
                }
            ),
            width="stretch",
            hide_index=True,
        )

    # ---------------- Exceptions ----------------
    with tabs[5]:
        st.subheader("Management exception report")
        exceptions = exception_report(monthly, thresholds, window=window)
        if exceptions.empty:
            st.success(
                "No exceptions: every governing KPI is at or better than target for the "
                "selected filters and window."
            )
        else:
            st.dataframe(
                exceptions.style.format(
                    {"Current": "{:,.4f}", "Target": "{:,.4f}", "Gap": "{:+,.4f}"}
                ),
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "Severity is the relative shortfall against target: High is 15% or worse. "
                "`Unavailable` means the window has too little data to compute the metric."
            )
        st.download_button(
            "Download exception actions (CSV)",
            exceptions.to_csv(index=False).encode("utf-8"),
            file_name="management_exceptions.csv",
            mime="text/csv",
        )

        st.divider()
        st.subheader("Full KPI summary")
        st.dataframe(
            kpi_summary(monthly, thresholds, window=window),
            width="stretch",
            hide_index=True,
        )

    # ---------------- Definitions ----------------
    with tabs[6]:
        st.subheader("Metric definitions and data lineage")
        st.dataframe(metric_definitions_table(thresholds), width="stretch", hide_index=True)
        st.caption(
            "Targets are author-defined illustrative thresholds. They are **not "
            "externally benchmarked and are not industry standards**, and are not "
            "specified in PROJECT_BLUEPRINT.md. Governing KPIs are computed only from "
            "operating data; plan, capital-structure, and synergy figures come from "
            "the Phase 3 model and are labelled Modelled."
        )
        st.warning(
            "Customer churn is computed from segment-level customer counts. A "
            "customer buying several service lines, or served from several regions, "
            "is counted more than once, which distorts the rate. The figure is **not "
            "de-duplicated** — doing so needs a customer-level identifier this schema "
            "does not carry."
        )

    # ---------------- Downloads ----------------
    st.divider()
    left, right = st.columns(2)
    with left:
        st.download_button(
            "Download filtered operating data (CSV)",
            filtered.to_csv(index=False).encode("utf-8"),
            file_name="filtered_operating_data.csv",
            mime="text/csv",
        )
    with right:
        st.download_button(
            "Download monthly KPI table (CSV)",
            monthly.to_csv(index=False).encode("utf-8"),
            file_name="monthly_kpis.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
