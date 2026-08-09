# Research benchmarks and empirical challenges

Every assumption in this repository was, until now, either taken from
`PROJECT_BLUEPRINT.md` or author-defined. This document replaces that with
sourced benchmarks, and — more importantly — records where the published
evidence **contradicts** the case.

## How to read this

**Source tiers.** Claims are tagged so a reviewer can weigh them:

| Tier | Meaning |
|---|---|
| `[A]` | Peer-reviewed journal or established academic venue |
| `[B]` | Government, regulator, or standards body |
| `[C]` | Industry research, transaction advisory, or trade association survey |
| `[D]` | Practitioner, brokerage, or content-marketing material — **indicative only, not verified against a primary source** |

**Method and its limits.** This is a *screening-tier* review: ten systematic
searches across ten themes, roughly eighty sources surfaced, findings extracted
from abstracts, institutional summaries, and search synthesis. It is **not** a
full-text review of every cited work, and it should not be represented as one.
Where a number drives a model input, the tier tag and the specific figure are
given so the claim can be checked against the primary source before it is
relied on.

**Currency.** Compiled August 2026. Rate-sensitive figures (Section 8) decay
fastest and should be refreshed before any live use.

**Provenance rule — added after a failure.** *No `[D]` source may be
load-bearing for a shipped verdict or a model input.* A `[D]` figure may inform
a judgement, and the resulting number must then be labelled **author-defined**;
it may never be presented as a benchmark that validates a target.

This rule exists because the first draft of this document violated it. A
statistic sourced from brokerage content — "deals with more than two add-ons
returned 19.9% IRR versus 23.1% for standalone buyouts" — was placed at the
centre of the investment committee summary as the case's principal
self-criticism. Pulling the primary source falsified it: the figure does not
appear in the peer-reviewed literature, and the very paper cited alongside it
reports the opposite direction (Section 3). The claim has been removed. Any
number carrying argumentative weight must have its primary source retrieved and
read, not summarised from an aggregator.

---

## 1. The demand thesis is real, and citable  `[B]` — SUPPORTS

The blueprint asserts recurring, regulation-driven demand without a source. It
does not need to.

- EPA's 2022 Clean Watersheds Needs Survey identifies **$630.1 billion** in
  needed but unfunded clean-water infrastructure investment over 20 years — a
  **73% increase** over the 2012 survey. `[B]`
- AWWA's *Buried No Longer* estimates **over $1 trillion over 25 years** to
  replace aging drinking-water pipe alone, excluding sewer, stormwater, and
  treatment plants. `[C]`
- Sustained IIJA-level investment would close roughly **$125 billion** of the
  gap over 20 years — meaningful, but leaving the large majority unfunded. `[B]`

**Repo implication.** The IC summary's thesis section should cite these rather
than assert the driver. A demand gap growing 73% in a decade, against funding
that closes a fraction of it, is the strongest single fact supporting the case.

---

## 2. Buy-and-build returns: the evidence cuts both ways  `[A]`

- Hammer, Marcotty-Dehm, Schweizer & Schwetzler (*Journal of Corporate
  Finance*, 2022) study **3,399 buyouts, 1997–2020**, with proprietary
  performance data. PE firms pay **sizable premiums** for B&B platforms —
  multiples comparable to those strategic acquirers pay for matched targets —
  yet still generate above-average equity returns, driven by **both higher
  top-line growth and multiple expansion**. `[A]`
- A related body of work covering **9,548 buyouts and 4,937 add-ons across 86
  countries** finds add-ons are **detrimental** where the sponsor is a late
  entrant to the consolidation or suffers limited-attention problems. `[A]`
- Achleitner et al., on **1,980 buyouts 1986–2010**, find multiple expansion is
  a fundamental driver of equity return and reflects **skill rather than
  luck** — i.e. it is not purely market beta. `[A]`

**Repo implication.** Two-sided. The base case's reliance on multiple change
(+$7.05M of $28.32M exit equity) is *defensible* against this literature rather
than embarrassing — but only if the case argues sponsor skill or genuine
re-rating, which it currently does not. The "late buyer" finding is a real risk
to name: a fragmented-market thesis assumes you are early.

---

## 3. CORRECTED — the literature supports buy-and-build; the exposure is entry price  `[A]`

**An earlier draft of this section asserted the opposite, on a false citation.
The correction is recorded rather than quietly edited, because the error is the
instructive part.**

That draft claimed, on brokerage-content sourcing, that deals with more than two
add-ons returned 19.9% IRR against 23.1% for standalone buyouts, and built the
IC summary's self-critique around it. Retrieving the primary source falsified
the claim. Hammer, Marcotty-Dehm, Schweizer & Schwetzler (2022), surveying the
prior literature, states:

> "Nikoskelainen & Wright (2007), as well as Valkama et al. (2013), find that
> deals with **add-on acquisitions outperform those without** in terms of their
> internal rates of return (IRR)."  `[A]`

The `19.9` that appears in that paper is a coincidental match inside a
summary-statistics table on high-yield spreads; `23.1` does not appear at all.

**What the peer-reviewed evidence actually establishes:**

- Add-on deals **outperform** non-add-on deals on IRR. `[A]`
- Sponsors pay **sizable premiums** for buy-and-build platforms — multiples
  comparable to those strategic acquirers pay for matched targets — and still
  earn above-average equity returns, through both top-line growth and multiple
  expansion. `[A]`
- Add-ons are **detrimental where the sponsor is a late entrant** to a
  consolidation, or suffers limited-attention problems. `[A]`
- Add-on probability and productivity rise with sponsor experience, platform
  size, prior M&A experience, moderate industry fragmentation, and favourable
  financing conditions. `[A]`

**Repo implication — the burden shifts, it does not disappear.** The strategy is
not the exposure; **entry pricing is**. If the literature's central finding is
that sponsors pay premiums for these platforms, the question a committee should
press is not "does buy-and-build work?" but "did we pay the premium, and what
did we assume to justify it?" That lands directly on Section 6: the model buys
add-ons at 3.5x, below every cited range, and the add-on entry sensitivity
quantifies the cost of paying market at 0.41x to 0.68x of MOIC.

Two risks remain nameable and are named: **late-entrant disadvantage**, which
any fragmented-market thesis must argue against rather than assume away, and
**limited attention** across a three-add-on programme.

## 4. INDICATIVE — platform margin may sit above the sector range  `[D]`

- Water and wastewater service operators run **10%–16% EBITDA margins**. `[D]`
  *Brokerage-sourced; not verified against a primary industry study. Indicative
  only — this figure must not by itself condemn the modelled margin.*
- The blueprint specifies an 18%–25% normalized margin; the model uses **20%**.

**Repo implication.** The modelled platform margin is **above the top of the
cited operating range**. Either the target is genuinely premium (route density,
service mix, contract structure — all arguable), or the normalization is
optimistic. The severe-downside case already runs 16.5%, which lands inside the
cited range — that case is closer to sector-typical than the base case is.

---

## 5. CHALLENGE — the entry margin has a second-order effect the model hides

Section 4 matters more than it first appears. Because the entry multiple is
applied to entry EBITDA, an optimistic margin inflates enterprise value, debt
capacity, and exit value simultaneously. The model's own single-driver test
shows margin alone moves MOIC only −0.13x, which **understates** the issue:
that test holds entry price constant. Paying 6.0x an overstated EBITDA is a
different error from earning a lower margin on a correctly priced asset.

**Repo implication.** Add a paired sensitivity that flexes margin **and** holds
purchase price in dollars constant, isolating "we overpaid because diligence
overstated EBITDA" from "the business earns less than we thought."

---

## 6. INDICATIVE — add-on entry pricing looks aggressive  `[D]`

- The multiple-arbitrage pattern most commonly described is buying add-ons at
  **5–6x** and exiting a consolidated platform at **9–10x**. `[D]`
- Water/wastewater transactions cluster **6x–12x EBITDA**, with sub-$10M
  municipal O&M operators at the low end and $50M+ integrated platforms at the
  top; the reported median is **13.9x**. `[D]` *Brokerage valuation content;
  not verified against a transaction database. Indicative only.*

The model buys add-ons at a fixed **3.5x**.

**Repo implication.** 3.5x is below the bottom of every cited range. If add-ons
genuinely clear at 3.5x, that is a sourcing edge and should be argued as one.
Otherwise the modelled multiple arbitrage — embedded in the 4.97x blended entry
multiple — is overstated. A sensitivity on add-on entry multiple is missing and
should exist.

---

## 7. INDICATIVE STRENGTH — the exit mark looks conservative  `[D]`

Against an indicative sector median of **13.9x** and a 6x–12x transaction
range, the model's **6.5x** Year-5 mark sits at or below the bottom of that
distribution.

**Repo implication.** Worth stating, but as an indication rather than a proof:
both figures are `[D]`. The conservatism is real relative to the sector's own
reported pricing, and it partly offsets Section 4. The upside case holding the mark
at 6.5x rather than re-rating is stronger than it currently looks.

---

## 8. STRENGTH and CHALLENGE — leverage conservative, debt cost stale  `[C]`

- Companies under **$250M EV** carry average leverage near **3.2x** debt/EBITDA;
  lower-mid-market transactions generally run **3.5x–4.5x**. The model's **3.0x**
  opening leverage is conservative and well supported. `[C]` **STRENGTH**
- Senior debt pricing on $10M–$250M EV deals ran **10.4%** end-2023, **8.1%** in
  Q1 2025, and **8.6%** by Q3 2025. Ninety-day SOFR sat at **4.3%** in February
  2026, with first-lien spreads of **550–700bps** — implying roughly
  **9.8%–11.3%** all-in. `[C]` **CHALLENGE**
- Unitranche structures that previously carried light covenants now include
  **maintenance tests and amortization**. `[C]`

**Repo implication.** The base case's **8.0%** interest rate is below current
market. The severe case's 10.5% is right at it. More importantly: the repo
models *no covenant at all* and says so — but the evidence that maintenance
tests have returned means a covenant is now the realistic condition, and its
absence is a modelling gap worth naming rather than a neutral simplification.

---

## 9. CHALLENGE — receivables assumption is optimistic  `[C]` `[D]`

- Commercial B2B on Net-30 averages roughly **45 days** DSO; **government
  contracting regularly runs 60+ days**, and 60–90 day cycles are common. `[D]`

The dashboard targets **55 days** and the synthetic data runs **~42.6 days**.

**Repo implication.** Both are optimistic for a municipally-concentrated payer
base. This matters doubly because the thesis *depends* on municipal
concentration — the same feature that makes revenue sticky makes collection
slow. Re-benchmark the DSO target to 60–75 days and regenerate the sample data
so the demo does not quietly assume better-than-municipal collections.

---

## 10. CHALLENGE — utilization target is soft, and definitionally ambiguous  `[C]`

- Field services commonly report **75%–85%** billable utilization. `[C]`
- TSIA reports an industry average of **83%**, with pacesetters at **90.6%**. `[C]`
- Other sources put strong performance at **60%–80%**, and HVAC-type field
  businesses at **65%–85%**. `[C]`

The dashboard targets **72%**.

**Repo implication.** Two actions. Re-benchmark the target into the cited band
with the citation attached. And — more important — **state the denominator**.
The spread between these sources is largely definitional: billable ÷ *paid*
hours is a different metric from billable ÷ *available* hours. This repo uses
billable ÷ paid. Without that stated, the benchmark comparison is meaningless.

---

## 11. CHALLENGE — concentration limits are looser than PE practice  `[C]`

- PE firms typically flag any single customer **above 15%** of revenue, with a
  hard internal line commonly drawn there. `[C]`
- **Under 10%** is considered healthy with no diligence trigger; **10%–20%** is
  a caution zone; **above 30%** is severe, associated with a **20%–35%
  valuation discount**, and causes many institutional buyers to walk. `[C]`
- Top-five above **50%** is a common concern threshold. `[C]`
- US GAAP requires disclosure of any customer at **10%+** of revenue. `[C]`

The blueprint permits **no customer above 20%** and **top-ten below 55%**.

**Repo implication.** The blueprint's single-customer limit is **looser than
the 15% line PE practice draws**, and its top-ten test is weaker than the
top-five test commonly applied. Tighten the screen, or state explicitly that
the case accepts more concentration than institutional practice and why.

---

## 12. METHOD — deduplication has no measured accuracy  `[A]`

The sourcing pipeline deduplicates with a **deterministic** union-find over
exact matches on domain, phone, normalized name, and normalized address. It
reports a merge audit trail but **never measures whether the merges are right**.

- Binette & Steorts, *(Almost) All of Entity Resolution* (**Science Advances**)
  is the canonical survey of the field. `[A]`
- The Fellegi-Sunter framework estimates per-attribute match weights, including
  **frequency-based weights** — a match on a rare name is stronger evidence than
  a match on a common one — with weights estimable unsupervised via **EM**. `[A]`
- **Blocking** avoids O(n²) comparison. `[A]`
- **Precision and recall** are the standard pairwise evaluation metrics. `[A]`
- Critically: **deterministic matching suffers low recall when data quality is
  poor**, because it cannot tolerate typos, transpositions, or missing
  values. `[A]`

**Repo implication — the single highest-value technical upgrade available.**
The fixtures are synthetic, so **ground truth is known**. The pipeline can
therefore report precision, recall, and F1 on its own deduplication against a
labelled answer key, and can be stress-tested by injecting typographical noise
to demonstrate the recall ceiling the literature predicts. No other change in
this repository converts an assertion into a measurement so cheaply.

---

## 13. METHOD — the 0–100 lead score is never validated  `[A]`

The score allocates 35 points to company age, 40 to workforce fit, 25 to digital
whitespace. Those weights are asserted, locked by a golden test, and **never
tested for predictive power**.

- B2B lead-scoring research (*Frontiers in Artificial Intelligence*, 2025)
  evaluates fifteen classifiers on **AUC-ROC** and accuracy. `[A]`
- Recommended validation covers **AUC, precision at top-N, calibration error,
  and business lift**. `[D]`
- **Calibrated probabilities** outperform raw scores where a threshold drives
  routing decisions. `[D]`
- Time-based splits guard against optimistic bias. `[D]`

**Repo implication.** A defensible weaker claim is available immediately:
define "true fit" from the blueprint's own anchor criteria, then report
**precision@15** — of the fifteen targets handed to research, how many actually
meet the anchor profile? That is honest, computable today, and far stronger
than an unvalidated score. Full AUC work requires outcome labels the repository
does not have, and that limitation should be stated rather than papered over.

---

## Priority of work

Ordered by evidentiary value per unit of effort.

| # | Change | Section | Why first |
|---|---|---|---|
| 1 | Deduplication precision/recall/F1 against fixture ground truth, plus a noise-injection test | 12 | Converts the largest unmeasured claim into a measurement; ground truth already exists |
| 2 | Re-benchmark KPI targets (DSO, utilization, churn) with citations and stated denominators | 9, 10 | Closes the repo's own top open limitation — "targets are not externally benchmarked" |
| 3 | Cite the demand gap in the IC thesis | 1 | Highest-credibility single fact; currently asserted |
| 4 | Confront the >2 add-on finding in the IC summary | 3 | Self-criticism is the strongest signal a candidate can send |
| 5 | Benchmark table: every model assumption vs. cited range, flagged inside/outside | 4, 6, 7, 8 | Makes conservatism and aggression both visible at a glance |
| 6 | Add-on entry-multiple sensitivity | 6 | The one missing sensitivity axis that matters |
| 7 | precision@15 for the lead score | 13 | Honest partial validation of an unvalidated model |
| 8 | Tighten or justify the concentration screen | 11 | Cheap; aligns the screen with institutional practice |

---

## Sources

**Peer-reviewed / academic `[A]`**

- Hammer, B., Marcotty-Dehm, N., Schweizer, D., & Schwetzler, B. — *Pricing and
  Value Creation in Private Equity-backed Buy-and-Build Strategies*, Journal of
  Corporate Finance (2022). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3244189
- Hammer, B., et al. — *Inorganic growth strategies and the evolution of the
  private equity business model*, Journal of Corporate Finance.
  https://www.sciencedirect.com/science/article/abs/pii/S0929119917302122
- Achleitner, A.-K., et al. — *Value creation and pricing in buyouts: Empirical
  evidence from Europe and North America*.
  https://www.sciencedirect.com/science/article/abs/pii/S105833001100036X
- *Multiple Arbitrage in Private Equity Based Buy-and-Build Strategies*,
  Springer. https://link.springer.com/10.1007/978-3-030-38738-9_15-2
- Binette, O. & Steorts, R. C. — *(Almost) All of Entity Resolution*, Science
  Advances. https://www.science.org/doi/10.1126/sciadv.abi8021 · preprint
  https://arxiv.org/pdf/2008.04443
- Murray, J. — *Probabilistic Record Linkage and Deduplication after Indexing,
  Blocking, and Filtering*. https://arxiv.org/pdf/1603.07816
- *Multifile Partitioning for Record Linkage and Duplicate Detection*.
  https://arxiv.org/pdf/2110.03839
- *A Bayesian Approach to Graphical Record Linkage and De-duplication*.
  https://arxiv.org/pdf/1312.4645
- *The relevance of lead prioritization: a B2B lead scoring model based on
  machine learning*, Frontiers in Artificial Intelligence (2025).
  https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1554325/full
- *Buy-and-build strategy: Evidence from a survey of Private Equity General
  Partners*, Sinergie. https://ojs.sijm.it/index.php/sinergie/article/view/1671

**Government / institutional `[B]`**

- US EPA — *Clean Watersheds Needs Survey (CWNS) 2022 Report*.
  https://www.epa.gov/cwns/clean-watersheds-needs-survey-cwns-2022-report-and-data
  · summary https://www.epa.gov/system/files/documents/2024-05/2022-cwns-summary.pdf
- US EPA — press release on 2022 CWNS findings.
  https://epa.gov/newsreleases/new-epa-survey-highlights-wastewater-infrastructure-needs-protect-waterbodies
- NACWA — commentary on CWNS federal funding implications.
  https://www.nacwa.org/news-publications/news-detail/2024/05/14/epa-clean-watershed-needs-survey-report-reinforces-importance-of-additional-federal-funding-for-water-infrastructure
- Congressional Research Service — *EPA's Estimated Wastewater Infrastructure
  Needs*. https://www.congress.gov/crs_external_products/R/HTML/R48565.web.html
- Bipartisan Policy Center — *America's Aging Water Infrastructure*.
  https://bipartisanpolicy.org/wp-content/uploads/2019/03/BPC-Aging-Water-Infrastructure.pdf

**Industry research / advisory `[C]`**

- Capstone Partners — *Middle Market Leveraged Finance Report*.
  https://www.capstonepartners.com/insights/middle-market-leveraged-finance-report/
- Capstone Partners — *Middle Market M&A Valuations Index*.
  https://www.capstonepartners.com/insights/report-capstone-partners-middle-market-mergers-and-acquisitions-valuations-index/
- CIBC — *US Middle Market Monitor, Q1 2026*.
  https://cms.cibcusmmib.com/wp-content/uploads/2026/04/US-Middle-Market-Monitor_Q1-2026.pdf
- ABF Journal — *Leverage Limits: Stress-Testing Middle Market Debt Capacity*.
  https://www.abfjournal.com/leverage-limits-stress-testing-middle-market-debt-capacity-in-a-volatile-2025-economy/
- BCG — *The Power of Buy and Build*.
  https://www.bcg.com/publications/2016/private-equity-power-of-buy-build
- TSIA — field services benchmarks.
  https://www.tsia.com/blog/top-field-services-questions-answered
- Wall Street Prep — customer concentration risk methodology.
  https://www.wallstreetprep.com/knowledge/customer-concentration/
- Wall Street Prep — LBO returns attribution.
  https://www.wallstreetprep.com/knowledge/lbo-returns-attribution-value-creation/

**Practitioner / brokerage / content-marketing `[D]` — indicative only**

*These sources are not verified against primary research. Under the provenance
rule above, none may be load-bearing for a shipped verdict or a model input.
Where a figure below informed a target, that target is labelled author-defined.*

- ExitValue — water/wastewater services valuation ranges (brokerage content).
  https://exitvalue.ai/blog/how-to-value-water-wastewater
- Auxo Capital Advisors — water/wastewater engineering firm valuation.
  https://auxocapitaladvisors.com/water-wastewater-engineering-firm-valuation/
- Capital Pad — lower-middle-market PE statistics and roll-up statistics.
  **Source of the falsified 19.9%/23.1% claim; see Section 3.**
  https://capitalpad.com/lower-middle-market-ebitda-multiples/ ·
  https://capitalpad.com/private-equity-roll-up-statistics/
- Growth Shuttle — roll-up sequencing, integration, and the limits of multiple
  arbitrage. https://growthshuttle.com/private-equity-roll-up-strategy-sequencing-integration-and-the-limits-of-multiple-arbitrage/
- Growth Shuttle — entry pricing and where returns must come from.
  https://growthshuttle.com/private-equity-valuation-multiples-and-how-entry-pricing-shifts-where-returns-have-to-come-from/
- FieldEdge — technician utilization benchmarks.
  https://fieldedge.com/blog/technician-utilization-benchmarks/
- ServiceTitan — field service metrics.
  https://www.servicetitan.com/blog/field-service-metrics
- Level 3 Processing — government contractor payment cycles.
  https://level-3processing.com/government-contractor-cash-flow-the-hidden-cost-of-federal-payment-delays/
- AccountingTools — DSO calculation and interpretation.
  https://www.accountingtools.com/articles/days-sales-outstanding-calculation-and-usage.html
- Macabacus — LBO value creation through leverage and operations.
  https://macabacus.com/valuation/lbo-creating-value

---

*Nothing in this document changes a model input on its own. Each adopted
benchmark is applied in code with the citation attached, so a reviewer can trace
any number to its source and disagree with it specifically.*
