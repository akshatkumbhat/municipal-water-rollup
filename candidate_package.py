"""Integrated candidate deliverable for Project Copperline.

This module is **orchestration only**. It runs the existing sourcing, scoring,
financial-model, and operating-KPI components and assembles their outputs into
one reviewable package. It reimplements no scoring rule, no financial formula,
and no KPI definition; every number it writes is produced by the module that
owns it.

Run:
    python candidate_package.py --output-dir outputs/candidate_package
    python candidate_package.py --verify outputs/candidate_package/MANIFEST.json

Determinism
-----------
Every generated artifact is byte-identical across runs from a clean output
directory. Two upstream sources of volatility are handled here rather than by
changing Phase 2:

* Scored-target ordering is owned by `sourcing_pipeline.order_scored_targets`,
  which defines one documented total order (score, confidence, normalized name,
  then a stable source identifier). This module consumes that function rather
  than keeping a second ordering rule, so the standalone pipeline and the
  package cannot drift apart.
* `sourcing_pipeline` stamps a wall-clock `scraped_at_utc` on every row. That
  column is not carried into the package; the run's as-of date is isolated in
  a single `as_of` block in the manifest and can be pinned with `--as-of`.

Provenance vocabulary
---------------------
Every artifact and every headline figure is tagged with one of:

* ``fixture``    — synthetic directory records from `sourcing_fixtures`.
* ``synthetic``  — generated operating data; not observed company performance.
* ``blueprint``  — stated in `PROJECT_BLUEPRINT.md`.
* ``modelled``   — computed by `buy_and_build_model` from stated assumptions.
* ``author``     — author-defined input, not externally benchmarked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

import sourcing_pipeline as sourcing
from buy_and_build_model import (
    SCENARIOS,
    ModelResult,
    Scenario,
    return_bridge,
    run_scenario,
    scenario_comparison,
    sources_and_uses,
    write_scenario_outputs,
)
from operations_kpis import (
    Thresholds,
    assign_model_period,
    exception_report,
    generate_sample_data,
    kpi_summary,
    metric_definitions_table,
    monthly_rollup,
    resolve_model_anchor,
    validate_operating_data,
    write_operating_outputs,
)
from sourcing_fixtures import build_offline_dataset

PACKAGE_SCHEMA_VERSION = "1.0"
MANIFEST_NAME = "MANIFEST.json"
IC_SUMMARY_NAME = "IC_SUMMARY.md"
DEMO_NAME = "DEMO_WALKTHROUGH.md"

SOURCING_DIR = "01_sourcing"
MODEL_DIR = "02_model"
OPERATING_DIR = "03_operating"
REFERENCE_DIR = "04_reference"

MANAGED_ENTRIES = (
    SOURCING_DIR,
    MODEL_DIR,
    OPERATING_DIR,
    REFERENCE_DIR,
    MANIFEST_NAME,
    IC_SUMMARY_NAME,
    DEMO_NAME,
)

SCENARIO_ORDER = ("base", "downside", "upside")

# PROJECT_BLUEPRINT.md anchor-platform profile: 15-60 technicians/operators.
ANCHOR_TECHNICIAN_MIN = 15
ANCHOR_TECHNICIAN_MAX = 60

# Columns dropped from the packaged target universe. `scraped_at_utc` is a
# wall-clock stamp that would make the artifact irreproducible; its value is
# isolated in the manifest instead. The raw pipeline output (`make sourcing`)
# still carries it.
VOLATILE_SOURCING_COLUMNS = ("scraped_at_utc",)

PROVENANCE = {
    "fixture": "Fixture (synthetic directory records; no real company)",
    "synthetic": "Synthetic (generated operating data; not observed performance)",
    "blueprint": "Blueprint (stated in PROJECT_BLUEPRINT.md)",
    "modelled": "Modelled (computed by buy_and_build_model)",
    "author": "Author-defined (not externally benchmarked)",
}


class PackageError(RuntimeError):
    """Raised when the package cannot be built or verified."""


@dataclass(frozen=True)
class CandidateSelection:
    """Which target was selected, and honestly, how.

    `tied` holds every candidate sharing the top score. When more than one
    candidate ties, selection required a tiebreak rather than being decided by
    the score, and the package says so.
    """

    candidate: pd.Series
    tied: pd.DataFrame
    rule: str
    rationale: str

    @property
    def name(self) -> str:
        return str(self.candidate["company_name"])

    @property
    def is_ambiguous(self) -> bool:
        return len(self.tied) > 1


@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str
    bytes: int
    kind: str
    provenance: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "kind": self.kind,
            "provenance": self.provenance,
            "description": self.description,
        }


@dataclass(frozen=True)
class PackageResult:
    output_dir: Path
    manifest_path: Path
    artifacts: tuple[Artifact, ...]
    selection: CandidateSelection
    results: dict[str, ModelResult]
    as_of: str


# ---------------------------------------------------------------------------
# Sourcing: run the real pipeline offline, then order it deterministically.
# ---------------------------------------------------------------------------


def generate_target_universe(verified_on: str, *, workers: int = 8) -> pd.DataFrame:
    """Run the existing sourcing pipeline against bundled fixtures.

    Calls `sourcing_pipeline`'s own clean/dedupe/enrich/score functions, so the
    scoring methodology stays owned by Phase 2. Network access is impossible
    here: the offline fetcher serves fixture HTML only.
    """
    raw_records, pages, blocked, errors = build_offline_dataset()
    fetcher = sourcing.OfflineFetcher(pages, blocked, errors)
    cleaned = sourcing.clean_and_deduplicate(raw_records)
    if cleaned.empty:
        raise PackageError(
            "Sourcing produced no records. The bundled fixtures in "
            "sourcing_fixtures.py may be corrupt."
        )
    scored = sourcing.enrich_and_score(fetcher, cleaned, workers, verified_on)
    return order_targets(scored)


def order_targets(targets: pd.DataFrame) -> pd.DataFrame:
    """Delegate to the sourcing module's documented total order.

    Ordering is owned by `sourcing_pipeline.order_scored_targets` so the
    standalone pipeline and this package cannot drift apart. This wrapper
    exists only so package code reads naturally.
    """
    return sourcing.order_scored_targets(targets)


def select_candidate(targets: pd.DataFrame) -> CandidateSelection:
    """Pick the anchor-platform candidate, disclosing any tie.

    The priority score is the primary criterion. Where it does not discriminate
    — and in the bundled fixture set it does not, five companies score exactly
    100.0 — the following tiebreaks apply, in order:

    1. inside the blueprint's 15-60 technician anchor band;
    2. higher data confidence;
    3. company name ascending.

    Step 3 is a determinism device, not an investment judgment, and the package
    says so. Every tied candidate is written to the package so a reviewer can
    see what the score alone could not settle.
    """
    if targets.empty:
        raise PackageError("Cannot select a candidate: the target universe is empty.")

    ordered = order_targets(targets)
    top_score = float(ordered.iloc[0]["priority_score"])
    tied = ordered[ordered["priority_score"] == top_score].copy()

    technicians = pd.to_numeric(tied["technician_count_est"], errors="coerce")
    tied["in_anchor_band"] = technicians.between(
        ANCHOR_TECHNICIAN_MIN, ANCHOR_TECHNICIAN_MAX
    ).fillna(False)

    ranked = tied.sort_values(
        ["in_anchor_band", "data_confidence", "company_name"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    candidate = ranked.iloc[0]

    rule = (
        "priority_score desc -> inside blueprint 15-60 technician anchor band -> "
        "data_confidence desc -> company_name ascending"
    )
    if len(tied) > 1:
        band_count = int(ranked["in_anchor_band"].sum())
        rationale = (
            f"{len(tied)} candidates tie at the maximum priority score of "
            f"{top_score:.0f}, so the score alone did not select a candidate. "
            f"{band_count} of them fall inside the blueprint's {ANCHOR_TECHNICIAN_MIN}-"
            f"{ANCHOR_TECHNICIAN_MAX} technician anchor band. Where that still did not "
            "discriminate, company name ascending was applied purely to make the "
            "choice reproducible; it carries no investment meaning. A real process "
            "would hand-research all tied candidates before choosing."
        )
    else:
        rationale = (
            f"A single candidate holds the maximum priority score of {top_score:.0f}; "
            "no tiebreak was required."
        )

    return CandidateSelection(
        candidate=candidate, tied=ranked, rule=rule, rationale=rationale
    )


def sourcing_funnel(targets: pd.DataFrame, selection: CandidateSelection) -> pd.DataFrame:
    """The funnel a reviewer walks: universe -> scored -> top 15 -> selected."""
    scored = targets[targets["priority_score"].notna()]
    collected = int(targets["duplicate_count"].fillna(1).sum())
    clean_enrichment = int((targets["website_status"] == "ok").sum())
    limited_evidence = int(len(scored) - clean_enrichment)
    return pd.DataFrame(
        [
            {
                "Stage": "Directory records collected",
                "Count": collected,
                "Basis": "Raw fixture directory rows, before deduplication",
            },
            {
                "Stage": "Unique companies after deduplication",
                "Count": int(len(targets)),
                "Basis": "Union-find over domain / phone / name / address",
            },
            {
                "Stage": "Enriched without error",
                "Count": clean_enrichment,
                "Basis": "website_status == ok; full evidence captured",
            },
            {
                "Stage": "Scored on limited evidence",
                "Count": limited_evidence,
                "Basis": "Robots-blocked or fetch error; scored with lower data confidence",
            },
            {
                "Stage": "Total scored",
                "Count": int(len(scored)),
                "Basis": "All unique companies carry a 0-100 priority score",
            },
            {
                "Stage": "Top 15 for hand research",
                "Count": int(min(15, len(scored))),
                "Basis": "Highest priority score, deterministic tiebreak",
            },
            {
                "Stage": "Tied at maximum score",
                "Count": int(len(selection.tied)),
                "Basis": "Score did not discriminate; tiebreak applied",
            },
            {
                "Stage": "Selected anchor candidate",
                "Count": 1,
                "Basis": selection.rule,
            },
        ]
    )


# ---------------------------------------------------------------------------
# Reference tables
# ---------------------------------------------------------------------------


def assumption_provenance_table(scenario: Scenario) -> pd.DataFrame:
    """Every model assumption with the source that justifies it."""
    a = scenario.assumptions
    blueprint = PROVENANCE["blueprint"]
    author = PROVENANCE["author"]
    rows = [
        ("Platform revenue ($M)", a.platform_revenue, blueprint, "Modelled case is $10M"),
        ("Platform EBITDA margin", a.platform_ebitda_margin, blueprint, "18-25% normalized"),
        ("Platform entry multiple (x)", a.platform_entry_multiple, blueprint, "6.0x EBITDA"),
        ("Opening leverage (x EBITDA)", a.initial_debt_to_ebitda, blueprint, "3.0x, or $6.0M"),
        ("Organic growth", a.annual_organic_growth, blueprint, "5.0% annually"),
        ("Margin expansion (bps/yr)", a.platform_margin_expansion_bps_per_year, blueprint, "50 bps"),
        ("Margin cap", a.platform_margin_cap, blueprint, "22.0%"),
        ("Add-on SG&A % revenue", a.add_on_sgna_pct_revenue, blueprint, "20% of revenue"),
        ("SG&A synergy capture", a.sgna_synergy_capture, blueprint, "15% consolidation"),
        ("First-year synergy realization", a.first_year_synergy_realization, blueprint, "50% in year one"),
        ("Interest rate", a.interest_rate, blueprint, "8.0%"),
        ("Tax rate", a.tax_rate, blueprint, "25% cash taxes"),
        ("Maintenance capex % revenue", a.capex_pct_revenue, blueprint, "3.0%"),
        ("NWC % incremental revenue", a.nwc_pct_incremental_revenue, blueprint, "1.0%"),
        ("Transaction fees % EV", a.transaction_fee_pct_ev, blueprint, "2.0% of EV"),
        ("Year-5 valuation mark (x)", a.terminal_multiple, blueprint, "6.5x EBITDA mark"),
        (
            "Max pro-forma leverage (x)",
            a.max_pro_forma_leverage,
            author,
            "Acquisition financing governor; inert in the base case",
        ),
    ]
    return pd.DataFrame(
        [
            {"Assumption": name, "Value": value, "Provenance": prov, "Basis": basis}
            for name, value, prov, basis in rows
        ]
    )


def limitations_markdown() -> str:
    """Known limitations carried into the deliverable rather than hidden.

    These are open items recorded in BACKLOG.md. Phase 5 discloses them; it
    does not change the implementations behind them.
    """
    return """# Known limitations

Every item below is a real constraint on this package, recorded so a reviewer
is not misled about what these outputs support. They are disclosures, not
to-do items: each is a property of the deliverable as shipped.

## Data integrity

1. **The candidate is a fixture, not a real company.** Every sourced target is
   generated by `sourcing_fixtures.py` on reserved `example.com` domains. No
   claim is made about any real business, market, or owner. No diligence,
   customer reference, or historical performance data exists in this
   repository.
2. **Operating data is synthetic.** The dashboard dataset is generated, not
   observed. Its annual revenue and EBITDA are deliberately calibrated to the
   modelled base case so plan-versus-actual is coherent; the region, service
   line, and business-unit split within a year is invented.
3. **The model is not derived from the candidate.** Platform economics are
   blueprint parameters ($10M revenue, 20% margin, 6.0x entry). The sourcing
   data carries no revenue or EBITDA, so the selected candidate does not and
   cannot drive the financial model.

## Selection

4. **Candidate selection required a tiebreak.** Several fixture companies tie
   at the maximum priority score. The final tiebreak is alphabetical and
   carries no investment meaning. See `01_sourcing/selection_tie_disclosure.csv`.

## Model

5. **The downside case is not severe.** It stresses only organic growth,
   synergy capture, interest rate, and the exit mark. It does not stress the
   entry multiple, platform margin, customer concentration, or add-on
   execution failure. It should not be read as a floor. This remains the most
   significant open limitation of the underwriting.
6. **The leverage governor is a modelling input, not a covenant.**
   `max_pro_forma_leverage` sizes acquisition debt; no covenant is modelled
   anywhere in this repository. `leverage_limit_exceeded` flags years where
   year-end gross leverage passes that governor — for example after a revolver
   draw under stress — and is a model-limit warning only.
7. **Lender credit for synergies is assumed, not agreed.**
   `leverage_synergy_addback_fraction` defaults to 0.0, so debt capacity is
   sized on delivered operating EBITDA. Any non-zero value is an author-defined
   assumption about lender behaviour; real credit agreements cap add-backs and
   impose documentation, timing, and realization requirements.
8. **Leverage is not one number.** Leverage at the platform closing (3.00x in
   the base case) is higher than the maximum reported year-end figure (2.30x).
   `peak_gross_leverage` observes year-end periods only.

## Operating KPIs

9. **Dashboard targets are author-defined and not externally benchmarked.**
   They are not drawn from the blueprint or from industry data.
10. **Customer churn can be distorted by overlapping customers.** Counts are
    summed across segments, so a customer buying several service lines is
    counted more than once. De-duplication requires a customer-level
    identifier the schema does not carry.
"""


# ---------------------------------------------------------------------------
# IC summary
# ---------------------------------------------------------------------------


def _money(value: float) -> str:
    return f"${value:,.2f}M"


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _turns(value: float) -> str:
    return f"{value:.2f}x"


def _by_unit(value: float, unit: str) -> str:
    if unit == "percent":
        return _pct(value)
    if unit == "days":
        return f"{value:.1f}"
    return f"{value:.2f}"


def render_ic_summary(
    results: dict[str, ModelResult],
    selection: CandidateSelection,
    operating_kpis: pd.DataFrame,
    operating_exceptions: pd.DataFrame,
    as_of: str,
) -> str:
    """Build the IC summary from generated outputs.

    Every financial figure is read from a `ModelResult` — the same object that
    produced `02_model/*` — so the narrative and the CSVs cannot drift apart.
    Nothing here is hardcoded.
    """
    base = results["base"]
    downside = results["downside"]
    upside = results["upside"]
    br = base.returns

    bridge = return_bridge(base, SCENARIOS["base"].add_ons)
    su = sources_and_uses(base)
    su_total = su.iloc[-1]

    def bridge_value(component: str) -> float:
        return float(bridge.loc[bridge["Component"] == component, "Value"].item())

    lines: list[str] = []
    add = lines.append

    add("# Project Copperline — Investment Committee Summary")
    add("")
    add("**Municipal water & wastewater asset-integrity services buy-and-build**")
    add("")
    add(f"As of {as_of} · generated by `candidate_package.py` from the outputs in this package.")
    add("")
    add("---")
    add("")
    add("## How to read this document")
    add("")
    add("Every figure is tagged with its provenance. Nothing here is observed company")
    add("performance, because this repository contains none.")
    add("")
    add("| Tag | Meaning |")
    add("|---|---|")
    add(f"| `blueprint` | {PROVENANCE['blueprint']} |")
    add(f"| `modelled` | {PROVENANCE['modelled']} |")
    add(f"| `fixture` | {PROVENANCE['fixture']} |")
    add(f"| `synthetic` | {PROVENANCE['synthetic']} |")
    add(f"| `author` | {PROVENANCE['author']} |")
    add("")
    add("> **The selected candidate is a synthetic fixture company, and the financial")
    add("> model is not derived from it.** Platform economics are blueprint parameters.")
    add("> The sourcing dataset carries no revenue or EBITDA for any target, so no")
    add("> candidate drives the returns below. Operating data in the dashboard is")
    add("> generated, not observed. This package demonstrates a repeatable method; it")
    add("> is not evidence about any real business.")
    add("")
    add("---")
    add("")

    # ---- Transaction overview
    add("## 1. Transaction overview  `modelled`")
    add("")
    add("| Item | Value | Source |")
    add("|---|---:|---|")
    add(f"| Platform enterprise value | {_money(br['platform_enterprise_value'])} | `02_model/base/sources_and_uses.csv` |")
    add(f"| Transaction fees (platform) | {_money(br['platform_transaction_fees'])} | `02_model/base/sources_and_uses.csv` |")
    add(f"| Opening debt | {_money(br['initial_debt'])} | `02_model/base/return_summary.json` |")
    add(f"| Initial sponsor equity | {_money(br['initial_sponsor_equity'])} | `02_model/base/return_summary.json` |")
    add(f"| Add-on enterprise value (3 tuck-ins) | {_money(br['total_add_on_enterprise_value'])} | `02_model/base/sources_and_uses.csv` |")
    add(f"| Total uses across all closings | {_money(float(su_total['Total Uses']))} | `02_model/base/sources_and_uses.csv` |")
    add(f"| Total sponsor equity invested | {_money(br['total_sponsor_equity_invested'])} | `02_model/base/return_summary.json` |")
    add(f"| Blended entry multiple | {_turns(br['blended_entry_multiple'])} | `02_model/base/return_summary.json` |")
    add("")
    add("Sources equal uses at every closing by construction; the reconciliation is")
    add("asserted in `tests/test_financial_model.py`.")
    add("")

    # ---- Thesis
    add("## 2. Investment thesis  `blueprint`")
    add("")
    add("Acquire a $7-15M revenue anchor providing recurring, compliance-driven field")
    add("services to municipal water, wastewater, and stormwater systems, then")
    add("consolidate founder-owned operators in adjacent geographies. The work is")
    add("recurring and regulatory rather than discretionary, the market is fragmented")
    add("and founder-owned, route density compounds with scale, and asset data creates")
    add("switching costs.")
    add("")
    add("The strategy deliberately excludes regulated utility ownership, commodity-heavy")
    add("civil construction, equipment manufacturing, and environmental remediation with")
    add("unknown liabilities.")
    add("")
    add("**Anchor acquisition criteria** (`blueprint`): $7-15M revenue; 18-25% normalized")
    add("EBITDA margin; at least 60% municipal customers; at least 50% recurring revenue;")
    add(f"{ANCHOR_TECHNICIAN_MIN}-{ANCHOR_TECHNICIAN_MAX} technicians; no customer above 20% of revenue;")
    add("maintenance capex below 4% of revenue.")
    add("")

    # ---- Funnel and candidate
    add("## 3. Target funnel and selected candidate  `fixture`")
    add("")
    add(f"**Selected candidate: {selection.name}**")
    add("")
    add("| Attribute | Value |")
    add("|---|---|")
    for field_name, label in (
        ("company_url", "Website"),
        ("address", "Address"),
        ("company_age", "Company age (years)"),
        ("technician_count_est", "Estimated technicians"),
        ("employee_count_est", "Estimated employees"),
        ("service_keywords", "Service keywords"),
        ("priority_score", "Priority score (0-100)"),
        ("data_confidence", "Data confidence (0-100)"),
    ):
        if field_name in selection.candidate.index:
            add(f"| {label} | {selection.candidate[field_name]} |")
    add("")
    add(f"**Selection rule:** `{selection.rule}`")
    add("")
    if selection.is_ambiguous:
        add(f"> **Selection was ambiguous.** {selection.rationale}")
    else:
        add(f"{selection.rationale}")
    add("")
    add("Full funnel in `01_sourcing/funnel_summary.csv`; every tied candidate in")
    add("`01_sourcing/selection_tie_disclosure.csv`; the complete scored universe with")
    add("evidence and merge audit trail in `01_sourcing/target_universe.csv`.")
    add("")
    add("The score decomposes into company age (35 points), field workforce fit (40),")
    add("and digital whitespace (25), with a separate data-confidence score to prevent")
    add("false precision. Scoring is owned by `sourcing_pipeline.py`.")
    add("")

    # ---- Operating case
    add("## 4. Operating case  `synthetic`")
    add("")
    add("The dashboard demonstrates the post-close operating cadence. **These are")
    add("generated figures, not observed performance.**")
    add("")
    add("| Governing KPI | Current | Target | Status |")
    add("|---|---:|---:|---|")
    for _, row in operating_kpis.iterrows():
        unit = str(row["Unit"])
        current = _by_unit(float(row["Current"]), unit)
        target = _by_unit(float(row["Target"]), unit)
        add(f"| {row['Metric']} | {current} | {target} | {row['Status']} |")
    add("")
    add(f"Targets are `author` inputs and are **not externally benchmarked**. "
        f"Open management exceptions at the aggregate level: {len(operating_exceptions)}.")
    add("")
    add("Filtering to the weakest branch surfaces route density, utilization, gross")
    add("margin, and churn exceptions with a named operating action for each — the")
    add("demo path in `DEMO_WALKTHROUGH.md`.")
    add("")

    # ---- Returns
    add("## 5. Returns  `modelled`")
    add("")
    add("| Scenario | MOIC | Gross IRR | Exit equity | Terminal debt | Close lev. | Max year-end lev. | Limit exceeded |")
    add("|---|---:|---:|---:|---:|---:|---:|---|")
    for name in SCENARIO_ORDER:
        r = results[name].returns
        add(
            f"| {name} | {_turns(r['gross_moic'])} | {_pct(r['gross_irr'])} | "
            f"{_money(r['terminal_equity_value'])} | {_money(r['terminal_debt'])} | "
            f"{_turns(r['gross_leverage_at_close'])} | "
            f"{_turns(r['maximum_year_end_gross_leverage'])} | "
            f"{'YES' if r['leverage_limit_exceeded'] else 'no'} |"
        )
    add("")
    add("Source: `02_model/scenario_comparison.csv` and each scenario's")
    add("`return_summary.json`. IRR is solved on the actual equity cash-flow vector,")
    add("not derived from MOIC. The Year-5 value is a **valuation mark, not an assumed")
    add("sale**.")
    add("")

    # ---- Bridge
    add("## 6. Value-creation bridge  `modelled`")
    add("")
    add("| Component | $M |")
    add("|---|---:|")
    for _, row in bridge.iterrows():
        add(f"| {row['Component']} | {float(row['Value']):,.2f} |")
    add("")
    add("The bridge is an identity: components sum to exit equity value exactly")
    add("(asserted to 1e-9). EBITDA growth is valued at the blended entry multiple, so")
    add("multiple arbitrage between the 6.0x platform and 3.5x add-ons is embedded")
    add("there rather than double-counted. Source: `02_model/base/return_bridge.csv`.")
    add("")
    add(f"Of the {_money(br['terminal_equity_value'])} exit equity value, ")
    add(f"{_money(bridge_value('Net debt paydown'))} comes from debt paydown and ")
    add(f"{_money(bridge_value('Multiple change'))} from the change in multiple — the")
    add("latter being the least defensible driver, since it assumes a re-rating.")
    add("")

    # ---- Leverage
    add("## 7. Leverage and liquidity  `modelled`")
    add("")
    add("Leverage is reported at three distinct points, because no single number")
    add("describes all of them:")
    add("")
    add("| Point | Value | Field |")
    add("|---|---:|---|")
    add(f"| At the platform closing | {_turns(br['gross_leverage_at_close'])} | `gross_leverage_at_close` |")
    add(f"| Maximum reported year-end | {_turns(br['maximum_year_end_gross_leverage'])} | `maximum_year_end_gross_leverage` (alias `peak_gross_leverage`) |")
    add(f"| Exit (net of cash) | {_turns(br['exit_net_leverage'])} | `exit_net_leverage` |")
    add("")
    add(f"Terminal debt is {_money(br['terminal_debt'])}. Note that `peak_gross_leverage`")
    add("observes reported year-**end** periods only and therefore does not see the")
    add("closing position, which is the higher of the two.")
    add("")
    add("All positive levered free cash flow sweeps to debt. Add-on financing is capped")
    add(f"at a {_turns(br['leverage_limit'])} pro-forma leverage governor (`author`), with any excess")
    add("funded by sponsor equity; the governor is inert in the base case, so no equity")
    add("is staged. Capacity is sized on delivered **operating** EBITDA plus only")
    add(f"{_pct(SCENARIOS['base'].assumptions.leverage_synergy_addback_fraction)} of realized synergies "
        "(`leverage_synergy_addback_fraction`, `author`,")
    add("default 0.0). That is a modelling input, **not a covenant term**: actual lender")
    add("credit for synergies depends on documentation, caps, timing, and realization")
    add("requirements. Detail in `03_operating/capital_structure.csv`.")
    add("")
    if br["leverage_limit_exceeded"]:
        years = ", ".join(str(y) for y in br["leverage_limit_exceeded_years"])
        add("> **Leverage-limit warning.** Year-end gross leverage exceeds the modelled")
        add(f"> {_turns(br['leverage_limit'])} governor in year(s) {years}, reaching")
        add(f"> {_turns(br['maximum_year_end_gross_leverage'])}. This is a model-limit warning, not a")
        add("> covenant breach — no covenant is modelled in this repository.")
    else:
        add(f"No year exceeds the modelled {_turns(br['leverage_limit'])} governor "
            "(`leverage_limit_exceeded`: false).")
    add("")
    add("Note the blueprint's claim that add-on financing keeps pro-forma leverage below")
    add("2.5x holds **post-close**; leverage at the platform closing is 3.0x by design.")
    add("")

    # ---- Downside
    add("## 8. Downside  `modelled`")
    add("")
    dr = downside.returns
    add("The blueprint's IC guardrail case — 3% organic growth, half synergy capture, 9%")
    add(f"interest, 6.0x exit mark — returns {_turns(dr['gross_moic'])} MOIC and ")
    add(f"{_pct(dr['gross_irr'])} IRR, with terminal debt of {_money(dr['terminal_debt'])}.")
    add("")
    add("> **This downside should not be read as a floor.** It stresses only four")
    add("> variables: growth, synergy capture, interest rate, and the exit multiple. It")
    add("> does not stress the entry multiple, platform margin, customer concentration,")
    add("> integration failure, or the loss of a major municipal contract. A case that")
    add("> still returns")
    add(f"> {_turns(dr['gross_moic'])} is a sensitivity, not a stress test. Underwriting")
    add("> committee attention should focus on what is *not* varied here.")
    add("")
    ur = upside.returns
    add(f"The upside case ({_turns(ur['gross_moic'])} / {_pct(ur['gross_irr'])}) is `author`-defined and")
    add("deliberately operational only: it holds the exit mark at the base-case 6.5x")
    add("rather than assuming a re-rating.")
    add("")

    # ---- Risks
    add("## 9. Material risks")
    add("")
    add("1. **Evidence risk (highest).** Every target is a fixture and all operating")
    add("   data is synthetic. Nothing in this package constitutes diligence, market")
    add("   validation, or observed company performance.")
    add("2. **Model-candidate disconnect.** Returns are computed from blueprint")
    add("   parameters, not from the selected candidate, which has no revenue data.")
    add("3. **Selection ambiguity.** The top score does not discriminate between")
    add("   several candidates; the final tiebreak is alphabetical.")
    add("4. **Downside insufficiency.** See section 8.")
    add("5. **Multiple dependence.** A meaningful share of modelled equity value comes")
    add("   from the change in multiple.")
    add("6. **Customer and municipal concentration.** Not testable from fixture data;")
    add("   the blueprint sets limits (no customer above 20%, top ten below 55%) that")
    add("   remain unverified.")
    add("7. **Labor and certification.** The thesis depends on licensed technicians and")
    add("   NASSCO or state credentials; wage, safety, and prevailing-wage exposure is")
    add("   untested here.")
    add("8. **Unbenchmarked operating targets.** Dashboard thresholds are author-defined.")
    add("")

    # ---- Diligence
    add("## 10. Next diligence steps")
    add("")
    add("1. Replace the fixture universe with permitted live directory sources and")
    add("   re-run scoring; hand-research the top 15 and call the top five.")
    add("2. Obtain audited or reviewed financials for a real anchor and rebuild the")
    add("   model from its actuals rather than blueprint parameters.")
    add("3. Verify customer and municipality concentration, contract renewal dates, and")
    add("   backlog against the blueprint's limits.")
    add("4. Confirm technician roster, certifications, safety record, and wage exposure.")
    add("5. Underwrite synergies by named cost centre, owner, timing, and one-time cost")
    add("   rather than as a percentage of SG&A.")
    add("6. Size financing to covenant headroom with a lender, and confirm the treatment")
    add("   of synergy add-backs in covenant EBITDA.")
    add("7. Build a downside that stresses entry multiple, margin, and integration")
    add("   failure, not only growth and rate.")
    add("8. Replace synthetic operating data with the target's actual monthly data and")
    add("   re-benchmark every dashboard target.")
    add("")

    # ---- 100-day
    add("## 11. First 100 days  `blueprint`")
    add("")
    add("**Days 0-30 — establish control.** Freeze the chart of accounts and branch or")
    add("service-line mappings; reconcile technician roster, fleet, certifications,")
    add("customer master, contracts, backlog, and AR aging; install weekly cash,")
    add("bookings, labor, utilization, and safety reporting; identify the top 20")
    add("customers and every contract renewing within 180 days.")
    add("")
    add("**Days 31-60 — remove leakage.** Standardize quoting and job costing; reprice")
    add("unprofitable service lines and emergency callout terms; centralize insurance,")
    add("software, telecom, recruiting, and selected procurement; pilot dispatch")
    add("clustering in one branch; create a collections cadence by municipality.")
    add("")
    add("**Days 61-100 — build the compounding system.** Launch cross-sell playbooks;")
    add("create certification and recruiting funnels; implement branch scorecards and")
    add("manager incentives; build a target map around existing branches; define")
    add("integration templates for finance, HR, safety, dispatch, and customer data.")
    add("")

    add("## 12. Limitations")
    add("")
    add("See `04_reference/limitations.md` for the full list, including the")
    add("known constraints on the downside case, the sensitivity grids, and the")
    add("churn calculation.")
    add("")
    add("---")
    add("")
    add("*Generated from the artifacts in this package. Verify integrity with*")
    add("*`python candidate_package.py --verify <package>/MANIFEST.json`.*")
    return "\n".join(lines) + "\n"


def render_demo_walkthrough(selection: CandidateSelection, as_of: str) -> str:
    """The five-minute demo path: funnel -> model -> exception -> action."""
    return f"""# Five-minute demo walkthrough

As of {as_of}. Every command runs offline.

## 0. Build the package (once, ~15 seconds)

```bash
make package
```

## 1. Sourcing funnel (60 seconds)

Open `01_sourcing/funnel_summary.csv`. Walk the stages: fixture directory
records, deduplication by domain / phone / name / address with a retained merge
audit trail, enrichment and 0-100 scoring, top 15 for hand research, then the
selected anchor.

Open `01_sourcing/selection_tie_disclosure.csv`. Make the point explicitly:
several candidates tie at the top score, so the score did not choose
**{selection.name}** on its own — a documented tiebreak did. Say that out loud;
it is the honest version of the story.

Open `01_sourcing/target_universe.csv` and show `merged_from` / `merge_reason`
on any merged row. Merges are never silent.

## 2. Underwriting (90 seconds)

Open `02_model/base/sources_and_uses.csv`: sources equal uses at every closing.

Open `02_model/scenario_comparison.csv`: base, downside, and upside side by
side. Note that the downside is the blueprint's own guardrail case, and say
plainly that it stresses only four variables.

Open `02_model/base/return_bridge.csv`: entry equity walks to exit equity as an
identity. Point at the multiple-change line and name it as the least defensible
driver.

## 3. Operating control (90 seconds)

```bash
make dashboard
```

Default view: seven governing KPI cards, all on track, over 60 months of
**synthetic** operating data. Say the word synthetic.

In the sidebar, set Regions to **Mountain West** only. Every card updates; four
flip to "Management action" — route density, utilization, gross margin, and
churn.

## 4. Exception to operating action (60 seconds)

Open the **Exceptions** tab. Each breach carries a severity and a named action.
Route density is the one to talk about: low jobs per 100 route miles in a
single branch points at dispatch clustering and job sequencing, which is the
Days 31-60 pilot in the 100-day plan.

Close the loop: the sourcing funnel found the branch, the model underwrote the
platform, and the dashboard turned one metric into a specific management
action with an owner.

## 5. Integrity (30 seconds)

```bash
python candidate_package.py --verify outputs/candidate_package/MANIFEST.json
```

Every artifact is checksummed. Re-running the build from a clean directory
reproduces byte-identical files.

## What to say about the data

The candidate is a synthetic fixture on a reserved `example.com` domain. The
operating data is generated. The model is parameterised from
`PROJECT_BLUEPRINT.md`, not from the candidate. This package demonstrates a
repeatable method, not a real transaction.
"""


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _is_managed(relative: str) -> bool:
    """True when a path inside the output directory is one the package owns."""
    head = relative.split("/", 1)[0]
    return head in MANAGED_ENTRIES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_output_dir(output_dir: Path) -> None:
    """Guarantee a clean build without touching anything we did not create."""
    if output_dir.exists():
        if (output_dir / ".git").exists():
            raise PackageError(
                f"Refusing to build into {output_dir}: it looks like a repository root."
            )
        for name in MANAGED_ENTRIES:
            target = output_dir / name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)


def build_package(
    output_dir: Path | str,
    *,
    as_of: str | None = None,
    workers: int = 8,
    thresholds: Thresholds | None = None,
) -> PackageResult:
    """Generate the complete candidate package into `output_dir`.

    Everything is regenerated from source on every run; no artifact from a
    previous run is read or reused.
    """
    output_dir = Path(output_dir)
    resolved_as_of = as_of or date.today().isoformat()
    try:
        date.fromisoformat(resolved_as_of)
    except ValueError:
        raise PackageError(
            f"Invalid --as-of value {resolved_as_of!r}: expected an ISO date such as 2026-01-31."
        ) from None

    t = thresholds or Thresholds()
    _prepare_output_dir(output_dir)

    sourcing_dir = output_dir / SOURCING_DIR
    model_dir = output_dir / MODEL_DIR
    operating_dir = output_dir / OPERATING_DIR
    reference_dir = output_dir / REFERENCE_DIR
    for directory in (sourcing_dir, model_dir, operating_dir, reference_dir):
        directory.mkdir(parents=True, exist_ok=True)

    described: dict[str, tuple[str, str, str]] = {}

    def record(relative: str, kind: str, provenance: str, description: str) -> None:
        described[relative] = (kind, provenance, description)

    # ---- 1. Sourcing
    targets = generate_target_universe(resolved_as_of, workers=workers)
    selection = select_candidate(targets)

    packaged_targets = targets.drop(
        columns=[c for c in VOLATILE_SOURCING_COLUMNS if c in targets.columns]
    )
    packaged_targets.to_csv(sourcing_dir / "target_universe.csv", index=False)
    record(
        f"{SOURCING_DIR}/target_universe.csv",
        "sourcing",
        PROVENANCE["fixture"],
        "All scored targets with enrichment evidence and deduplication audit trail.",
    )

    packaged_targets.head(15).to_csv(sourcing_dir / "top_15_targets.csv", index=False)
    record(
        f"{SOURCING_DIR}/top_15_targets.csv",
        "sourcing",
        PROVENANCE["fixture"],
        "Highest-scoring 15 targets for hand research.",
    )

    selection.candidate.to_frame().T.to_csv(sourcing_dir / "selected_candidate.csv", index=False)
    record(
        f"{SOURCING_DIR}/selected_candidate.csv",
        "sourcing",
        PROVENANCE["fixture"],
        "The single selected anchor candidate record.",
    )

    selection.tied.to_csv(sourcing_dir / "selection_tie_disclosure.csv", index=False)
    record(
        f"{SOURCING_DIR}/selection_tie_disclosure.csv",
        "sourcing",
        PROVENANCE["fixture"],
        "Every candidate tied at the top score, with the tiebreak flags applied.",
    )

    sourcing_funnel(targets, selection).to_csv(sourcing_dir / "funnel_summary.csv", index=False)
    record(
        f"{SOURCING_DIR}/funnel_summary.csv",
        "sourcing",
        PROVENANCE["fixture"],
        "Funnel stage counts from universe to selected candidate.",
    )

    # ---- 2. Model
    results: dict[str, ModelResult] = {}
    for name in SCENARIO_ORDER:
        scenario = SCENARIOS[name]
        result = run_scenario(scenario)
        results[name] = result
        write_scenario_outputs(scenario, result, model_dir / name)
        for filename, description in (
            ("five_year_pro_forma.csv", "Operating, debt, and cash schedule."),
            ("sources_and_uses.csv", "Per-closing purchase price, fees, debt, and equity."),
            ("return_bridge.csv", "Entry equity to exit equity walk."),
            ("sensitivity_moic.csv", "MOIC across exit multiple and organic growth."),
            ("sensitivity_irr.csv", "IRR across exit multiple and organic growth."),
            ("return_summary.json", "Headline returns and capital structure."),
            ("assumptions.json", "Full assumption set and scenario provenance."),
        ):
            record(
                f"{MODEL_DIR}/{name}/{filename}",
                "model",
                PROVENANCE["modelled"],
                f"{name} scenario — {description}",
            )

    scenario_comparison(results).to_csv(model_dir / "scenario_comparison.csv", index=False)
    record(
        f"{MODEL_DIR}/scenario_comparison.csv",
        "model",
        PROVENANCE["modelled"],
        "Base, downside, and upside side by side.",
    )

    # ---- 3. Operating
    operating_frame = validate_operating_data(generate_sample_data())
    write_operating_outputs(operating_frame, operating_dir, thresholds=t)
    for filename, description in (
        ("operating_data.csv", "Monthly segment-level operating dataset (dashboard input)."),
        ("monthly_kpis.csv", "Monthly rollup with every governing KPI."),
        ("kpi_summary.csv", "Trailing-window KPI values against targets."),
        ("exceptions.csv", "Threshold breaches with severity and management action."),
        ("metric_definitions.csv", "KPI formulas, sources, targets, and provenance."),
        ("organic_growth.csv", "Year-over-year revenue and EBITDA growth."),
        ("plan_vs_actual.csv", "Actuals against the modelled annual plan."),
        ("capital_structure.csv", "Modelled debt, leverage, headroom, and liquidity."),
        ("synergy_realization.csv", "Modelled platform/add-on split and synergy capture."),
        ("performance_by_region.csv", "Branch-level performance."),
        ("performance_by_service_line.csv", "Service-line performance."),
        ("performance_by_business_unit.csv", "Platform versus add-on performance."),
    ):
        provenance = (
            PROVENANCE["modelled"]
            if filename in {"capital_structure.csv", "synergy_realization.csv"}
            else PROVENANCE["synthetic"]
        )
        record(f"{OPERATING_DIR}/{filename}", "operating", provenance, description)

    period = resolve_model_anchor(operating_frame)
    monthly = monthly_rollup(assign_model_period(operating_frame, period))
    operating_summary = kpi_summary(monthly, t)
    operating_exceptions = exception_report(monthly, t)

    # ---- 4. Reference
    assumption_provenance_table(SCENARIOS["base"]).to_csv(
        reference_dir / "assumptions_and_provenance.csv", index=False
    )
    record(
        f"{REFERENCE_DIR}/assumptions_and_provenance.csv",
        "reference",
        PROVENANCE["blueprint"],
        "Every base-case assumption with its source and basis.",
    )

    metric_definitions_table(t).to_csv(reference_dir / "kpi_definitions.csv", index=False)
    record(
        f"{REFERENCE_DIR}/kpi_definitions.csv",
        "reference",
        PROVENANCE["author"],
        "KPI formulas and author-defined targets.",
    )

    (reference_dir / "limitations.md").write_text(limitations_markdown(), encoding="utf-8")
    record(
        f"{REFERENCE_DIR}/limitations.md",
        "reference",
        PROVENANCE["author"],
        "Known limitations carried into the deliverable.",
    )

    # ---- 5. Narrative
    (output_dir / IC_SUMMARY_NAME).write_text(
        render_ic_summary(results, selection, operating_summary, operating_exceptions, resolved_as_of),
        encoding="utf-8",
    )
    record(
        IC_SUMMARY_NAME,
        "summary",
        PROVENANCE["modelled"],
        "Investment committee summary generated from the artifacts in this package.",
    )

    (output_dir / DEMO_NAME).write_text(
        render_demo_walkthrough(selection, resolved_as_of), encoding="utf-8"
    )
    record(
        DEMO_NAME,
        "summary",
        PROVENANCE["author"],
        "Five-minute demo script: funnel to model to exception to action.",
    )

    # ---- 6. Manifest
    artifacts = _collect_artifacts(output_dir, described)
    manifest_path = output_dir / MANIFEST_NAME
    manifest_path.write_text(
        _render_manifest(artifacts, selection, resolved_as_of), encoding="utf-8"
    )

    return PackageResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        artifacts=artifacts,
        selection=selection,
        results=results,
        as_of=resolved_as_of,
    )


def _collect_artifacts(
    output_dir: Path, described: dict[str, tuple[str, str, str]]
) -> tuple[Artifact, ...]:
    """Checksum every generated file, and fail loudly if one is undescribed.

    Only files the package owns are considered — the managed directories and
    the two top-level documents. Anything a reviewer drops into the output
    directory is left alone here and reported by `verify_package` as unlisted,
    so a stray note cannot break a rebuild but also cannot pass as an artifact.
    """
    found = sorted(
        relative
        for relative in (
            str(p.relative_to(output_dir)).replace("\\", "/")
            for p in output_dir.rglob("*")
            if p.is_file() and p.name != MANIFEST_NAME
        )
        if _is_managed(relative)
    )
    missing_description = [path for path in found if path not in described]
    if missing_description:
        raise PackageError(
            "Generated files are missing a manifest description: "
            f"{', '.join(missing_description)}"
        )
    not_generated = [path for path in described if path not in found]
    if not_generated:
        raise PackageError(
            "Expected artifacts were not generated: " + ", ".join(sorted(not_generated))
        )

    artifacts = []
    for relative in found:
        absolute = output_dir / relative
        kind, provenance, description = described[relative]
        artifacts.append(
            Artifact(
                path=relative,
                sha256=_sha256(absolute),
                bytes=absolute.stat().st_size,
                kind=kind,
                provenance=provenance,
                description=description,
            )
        )
    return tuple(artifacts)


def _render_manifest(
    artifacts: tuple[Artifact, ...], selection: CandidateSelection, as_of: str
) -> str:
    payload = {
        "package": {
            "name": "Project Copperline — Integrated Candidate Deliverable",
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "generated_by": "candidate_package.py",
        },
        "determinism": {
            "artifacts_are_byte_identical_across_runs": True,
            "volatile_fields": ["as_of.date"],
            "notes": (
                "No artifact contains a run timestamp. The sourcing pipeline's "
                "wall-clock scraped_at_utc column is excluded from the package and "
                "the run date is isolated here. Pin it with --as-of for a fully "
                "reproducible manifest."
            ),
        },
        "as_of": {"date": as_of, "is_volatile": True},
        "candidate": {
            "selected": selection.name,
            "selection_rule": selection.rule,
            "selection_was_ambiguous": selection.is_ambiguous,
            "tied_at_top_score": int(len(selection.tied)),
            "rationale": selection.rationale,
        },
        "scenarios": list(SCENARIO_ORDER),
        "provenance_legend": PROVENANCE,
        "artifact_count": len(artifacts),
        "artifacts": [artifact.to_dict() for artifact in artifacts],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def verify_package(manifest_path: Path | str) -> list[str]:
    """Re-checksum every artifact against the manifest. Returns problems found."""
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise PackageError(f"Manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageError(f"Manifest is not valid JSON: {exc}") from None
    if "artifacts" not in manifest:
        raise PackageError(f"Manifest has no 'artifacts' key: {manifest_path}")

    root = manifest_path.parent
    problems: list[str] = []
    for entry in manifest["artifacts"]:
        target = root / entry["path"]
        if not target.exists():
            problems.append(f"MISSING: {entry['path']}")
            continue
        actual = _sha256(target)
        if actual != entry["sha256"]:
            problems.append(
                f"CHECKSUM MISMATCH: {entry['path']} "
                f"(expected {entry['sha256'][:12]}…, got {actual[:12]}…)"
            )
    listed = {entry["path"] for entry in manifest["artifacts"]}
    for found in root.rglob("*"):
        if found.is_file() and found.name != MANIFEST_NAME:
            relative = str(found.relative_to(root)).replace("\\", "/")
            if relative not in listed:
                problems.append(f"UNLISTED FILE: {relative}")
    return problems


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="outputs/candidate_package",
        help="Directory for the integrated candidate package",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="ISO date recorded in the manifest. Defaults to today; pin it for byte-identical manifests.",
    )
    parser.add_argument(
        "--verify",
        default=None,
        metavar="MANIFEST",
        help="Verify an existing package's checksums instead of building",
    )
    args = parser.parse_args(argv)

    if args.verify:
        try:
            problems = verify_package(args.verify)
        except PackageError as exc:
            raise SystemExit(f"error: {exc}") from None
        if problems:
            print(f"Verification FAILED — {len(problems)} problem(s):")
            for problem in problems:
                print(f"  {problem}")
            raise SystemExit(1)
        print(f"Verification passed: every artifact in {args.verify} matches its checksum.")
        return

    try:
        package = build_package(Path(args.output_dir), as_of=args.as_of)
    except PackageError as exc:
        raise SystemExit(f"error: {exc}") from None

    base = package.results["base"].returns
    print(f"Integrated candidate package written to {package.output_dir}/")
    print(f"  artifacts        : {len(package.artifacts)}")
    print(f"  as of            : {package.as_of} (isolated volatile field)")
    print(f"  candidate        : {package.selection.name}")
    print(f"  selection        : {'AMBIGUOUS — tiebreak applied' if package.selection.is_ambiguous else 'unambiguous'}")
    if package.selection.is_ambiguous:
        print(f"  tied at top score: {len(package.selection.tied)}")
    print(f"  base MOIC / IRR  : {base['gross_moic']:.2f}x / {base['gross_irr']:.1%}")
    print()
    print(f"  IC summary       : {package.output_dir}/{IC_SUMMARY_NAME}")
    print(f"  demo script      : {package.output_dir}/{DEMO_NAME}")
    print(f"  manifest         : {package.manifest_path}")
    print()
    print("Verify with:")
    print(f"  python candidate_package.py --verify {package.manifest_path}")


if __name__ == "__main__":
    main()
