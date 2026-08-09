"""Entity-resolution accuracy measurement for the sourcing pipeline.

The pipeline deduplicates with a *deterministic* union-find over exact matches
on registrable domain, canonical phone, normalized name, and normalized
address. It has always reported an audit trail of what it merged. It has never
reported whether those merges were **right**.

This module closes that gap. Because `sourcing_fixtures` generates its
duplicates deliberately, the correct clustering is known by construction, so
precision and recall can be measured rather than assumed.

Why it matters, from the literature
-----------------------------------
Binette & Steorts, *(Almost) All of Entity Resolution* (Science Advances),
establishes pairwise precision and recall as the standard evaluation metrics
for record linkage, and notes that **deterministic matching suffers low recall
when data quality is poor**, because exact-match rules cannot tolerate typos,
transpositions, or missing values. `measure_noise_sensitivity` demonstrates
that ceiling on this pipeline directly: it injects typographic noise into the
matching fields and reports how recall decays.

This is a measurement module, not a replacement matcher. It does not implement
Fellegi-Sunter probabilistic linkage; it quantifies what the current
deterministic approach achieves, which is the necessary first step before
arguing for anything more elaborate.

Run:
    python sourcing_evaluation.py
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd

import sourcing_pipeline as sourcing
from sourcing_fixtures import build_offline_dataset, ground_truth_clusters

#: Fields a deterministic matcher relies on, and therefore the fields whose
#: corruption exposes its recall ceiling.
NOISE_TARGET_FIELDS = ("company_name", "address", "company_url")


@dataclass(frozen=True)
class ResolutionMetrics:
    """Pairwise entity-resolution accuracy.

    A "pair" is any two source records. A pair is *positive* when both records
    describe the same real entity. Pairwise metrics are used rather than
    cluster-level accuracy because they degrade gracefully: merging two of
    three duplicates is partially correct, and pairwise scoring says so.
    """

    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    predicted_clusters: int
    true_clusters: int
    records: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pairs(clusters: Mapping[int, int]) -> set[tuple[int, int]]:
    """Every unordered same-entity pair implied by a clustering."""
    members: dict[int, list[int]] = {}
    for record, entity in clusters.items():
        members.setdefault(entity, []).append(record)
    pairs: set[tuple[int, int]] = set()
    for group in members.values():
        for a, b in combinations(sorted(group), 2):
            pairs.add((a, b))
    return pairs


def pairwise_metrics(
    truth: Mapping[int, int], predicted: Mapping[int, int]
) -> ResolutionMetrics:
    """Score a predicted clustering against a known-correct one.

    Precision answers "of the merges we made, how many were real?" — a low
    value means the pipeline is destroying distinct companies. Recall answers
    "of the real duplicates, how many did we catch?" — a low value means the
    target list is inflated with the same company counted twice.

    For this use case the two errors are not symmetric. A false merge silently
    deletes a target from the funnel; a missed merge merely double-counts one.
    Precision is the more costly metric to lose.
    """
    if set(truth) != set(predicted):
        missing = sorted(set(truth) - set(predicted))
        extra = sorted(set(predicted) - set(truth))
        raise ValueError(
            "Predicted clustering does not cover the same records as the truth. "
            f"Missing: {missing[:5]}; unexpected: {extra[:5]}."
        )

    truth_pairs = _pairs(truth)
    predicted_pairs = _pairs(predicted)

    true_positives = len(truth_pairs & predicted_pairs)
    false_positives = len(predicted_pairs - truth_pairs)
    false_negatives = len(truth_pairs - predicted_pairs)

    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives)
        else 1.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives)
        else 1.0
    )
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    )

    return ResolutionMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1,
        predicted_clusters=len(set(predicted.values())),
        true_clusters=len(set(truth.values())),
        records=len(truth),
    )


def predicted_clusters(deduplicated: pd.DataFrame) -> dict[int, int]:
    """Recover the pipeline's clustering from its own audit trail.

    Uses `merged_source_rows`, which records input positions. `merged_from`
    cannot be used for this: it stores names, and two records that normalize to
    the same name are indistinguishable in that field — which is exactly the
    case for one of the fixture duplicate pairs.
    """
    if "merged_source_rows" not in deduplicated.columns:
        raise ValueError(
            "Deduplicated frame has no 'merged_source_rows' column; it was not "
            "produced by sourcing_pipeline.clean_and_deduplicate."
        )
    clusters: dict[int, int] = {}
    for _, row in deduplicated.iterrows():
        members = [int(part) for part in str(row["merged_source_rows"]).split(";") if part.strip()]
        if not members:
            continue
        entity = min(members)
        for member in members:
            clusters[member] = entity
    return clusters


def evaluate_fixture_deduplication(
    records: Sequence[dict[str, Any]] | None = None,
    truth: Mapping[int, int] | None = None,
) -> ResolutionMetrics:
    """Run the real pipeline over the fixtures and score the result."""
    if records is None:
        records, _pages, _blocked, _errors = build_offline_dataset()
    if truth is None:
        truth = ground_truth_clusters()

    deduplicated = sourcing.clean_and_deduplicate(list(records))
    predicted = predicted_clusters(deduplicated)

    # Records the cleaning stage legitimately drops (name-length or exclusion
    # filters) cannot be scored, so they are excluded from both sides rather
    # than counted as misses.
    scored = {r: e for r, e in truth.items() if r in predicted}
    return pairwise_metrics(scored, {r: predicted[r] for r in scored})


def inject_typographic_noise(
    records: Sequence[dict[str, Any]],
    *,
    rate: float,
    seed: int = 11,
) -> list[dict[str, Any]]:
    """Corrupt a fraction of matching-field characters, deterministically.

    Models the realistic condition the literature warns about: directory data
    with transcription errors. Only fields the deterministic matcher relies on
    are corrupted, because those are what determine whether it still matches.
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"noise rate must be between 0 and 1, got {rate}")
    rng = random.Random(seed)
    noisy: list[dict[str, Any]] = []
    for record in records:
        corrupted = dict(record)
        for field in NOISE_TARGET_FIELDS:
            value = str(corrupted.get(field, ""))
            if not value or rng.random() > rate:
                continue
            position = rng.randrange(len(value))
            character = value[position]
            if character.isalnum():
                # Transpose with a neighbour, or drop a character: the two most
                # common real transcription errors.
                if position + 1 < len(value) and rng.random() < 0.5:
                    value = (
                        value[:position]
                        + value[position + 1]
                        + character
                        + value[position + 2 :]
                    )
                else:
                    value = value[:position] + value[position + 1 :]
                corrupted[field] = value
        noisy.append(corrupted)
    return noisy


def measure_noise_sensitivity(
    rates: Sequence[float] = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0),
    *,
    seeds: Sequence[int] = tuple(range(20)),
) -> pd.DataFrame:
    """Recall decay curve for the deterministic matcher under input noise.

    This is the empirical demonstration of the ceiling Binette & Steorts
    describe. It is a property of exact-match rules, not a defect introduced
    here — but it is a property this repository should be able to quantify
    rather than merely acknowledge.

    Results are averaged over `seeds` noise draws. A single draw produces a
    non-monotonic curve that looks like a bug and is really just sampling
    noise: whether a given corruption happens to break a matching field is a
    coin flip, and with only two true duplicate pairs the per-draw variance
    swamps the trend. Averaging makes the decay legible and reports the spread.
    """
    base_records, _pages, _blocked, _errors = build_offline_dataset()
    truth = ground_truth_clusters()

    rows: list[dict[str, Any]] = []
    for rate in rates:
        draws = [
            evaluate_fixture_deduplication(
                list(base_records)
                if rate == 0.0
                else inject_typographic_noise(base_records, rate=rate, seed=seed),
                truth,
            )
            for seed in seeds
        ]
        frame = pd.DataFrame([d.to_dict() for d in draws])
        rows.append(
            {
                "Noise Rate": rate,
                "Precision": frame["precision"].mean(),
                "Recall": frame["recall"].mean(),
                "Recall StdDev": frame["recall"].std(ddof=0),
                "F1": frame["f1"].mean(),
                "Mean False Merges": frame["false_positives"].mean(),
                "Mean Missed Merges": frame["false_negatives"].mean(),
                "Draws": len(draws),
            }
        )
    return pd.DataFrame(rows)


def scored_fixture_targets(*, workers: int = 8) -> pd.DataFrame:
    """Run the fixtures through the real enrichment and scoring path."""
    records, pages, blocked, errors = build_offline_dataset()
    fetcher = sourcing.OfflineFetcher(pages, blocked, errors)
    cleaned = sourcing.clean_and_deduplicate(records)
    return sourcing.enrich_and_score(fetcher, cleaned, workers, "2026-01-31")


def write_evaluation_outputs(output_dir: Path | str) -> dict[str, Path]:
    """Write the accuracy report and the noise-sensitivity curve."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics = evaluate_fixture_deduplication()
    written: dict[str, Path] = {}

    metrics_path = output_dir / "deduplication_metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "method": "deterministic union-find over domain / phone / name / address",
                "evaluation": "pairwise precision and recall against fixture ground truth",
                "ground_truth_source": "sourcing_fixtures.ground_truth_clusters",
                "metrics": metrics.to_dict(),
                "note": (
                    "Measured on synthetic fixtures whose duplicates are known by "
                    "construction. Real directory data is noisier; see "
                    "noise_sensitivity.csv for the recall decay this matcher exhibits "
                    "as input quality falls."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    written["deduplication_metrics.json"] = metrics_path

    curve_path = output_dir / "noise_sensitivity.csv"
    measure_noise_sensitivity().to_csv(curve_path, index=False)
    written["noise_sensitivity.csv"] = curve_path

    targets = scored_fixture_targets()
    validation = validate_lead_score(targets)
    score_path = output_dir / "lead_score_validation.json"
    score_path.write_text(
        json.dumps(
            {
                "metric": "precision@k against the blueprint anchor technician band",
                "proxy_limitation": (
                    "Technician count is the only hard anchor criterion present in "
                    "sourcing data. Revenue, EBITDA margin, municipal mix, recurring "
                    "mix, and customer concentration are all part of the anchor "
                    "profile and none are observable here, so this measures workforce "
                    "fit only, not overall fit."
                ),
                "no_auc_reason": (
                    "AUC requires outcome labels — which targets converted to a deal — "
                    "which this repository does not have. Precision against a stated "
                    "proxy is the strongest honest claim available."
                ),
                "result": asdict(validation),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    written["lead_score_validation.json"] = score_path

    curve = output_dir / "workforce_curve_vs_anchor_band.csv"
    workforce_curve_vs_anchor_band().to_csv(curve, index=False)
    written["workforce_curve_vs_anchor_band.csv"] = curve
    return written


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="outputs/sourcing_evaluation",
        help="Directory for the evaluation outputs",
    )
    args = parser.parse_args(argv)

    metrics = evaluate_fixture_deduplication()
    print("DEDUPLICATION ACCURACY (deterministic union-find, fixture ground truth)\n")
    print(f"  records scored     : {metrics.records}")
    print(f"  true entities      : {metrics.true_clusters}")
    print(f"  predicted clusters : {metrics.predicted_clusters}")
    print(f"  true positives     : {metrics.true_positives}")
    print(f"  false positives    : {metrics.false_positives}")
    print(f"  false negatives    : {metrics.false_negatives}")
    print(f"  precision          : {metrics.precision:.4f}")
    print(f"  recall             : {metrics.recall:.4f}")
    print(f"  F1                 : {metrics.f1:.4f}")

    print("\nRECALL UNDER INPUT NOISE (exact-match ceiling)\n")
    print(
        measure_noise_sensitivity().to_string(
            index=False, float_format=lambda x: f"{x:,.4f}"
        )
    )

    validation = validate_lead_score(scored_fixture_targets())
    print("\nLEAD SCORE: PRECISION@15 AGAINST THE ANCHOR TECHNICIAN BAND\n")
    print(f"  universe                 : {validation.universe}")
    print(f"  meet anchor band         : {validation.fit_in_universe}")
    print(f"  unknown technician count : {validation.unknown_technicians}")
    print(f"  in top {validation.k:<2d}                : {validation.fit_in_top_k}")
    print(f"  precision@{validation.k:<2d}            : {validation.precision_at_k:.4f}")
    print(f"  base rate                : {validation.base_rate:.4f}")
    print(f"  lift over random         : {validation.lift:.2f}x")
    if validation.lift < 1.5:
        print(
            "\n  WARNING: the score barely outperforms random selection on this\n"
            "  criterion. See workforce_curve_vs_anchor_band.csv — the blueprint's\n"
            "  workforce curve peaks at 8-35 technicians while its anchor criterion\n"
            "  is 15-60, so the two disagree by construction."
        )

    written = write_evaluation_outputs(Path(args.output_dir))
    print(f"\n{len(written)} file(s) written to {args.output_dir}/")



# ---------------------------------------------------------------------------
# Lead-score validation.
# ---------------------------------------------------------------------------

#: PROJECT_BLUEPRINT.md anchor-platform criterion: 15-60 technicians/operators.
ANCHOR_TECHNICIAN_BAND = (15, 60)


@dataclass(frozen=True)
class ScoreValidation:
    """How well the 0-100 priority score selects blueprint-fit targets.

    `precision_at_k` is the share of the top-k list that meets the anchor
    profile. `base_rate` is the share of the whole universe that does. `lift`
    is their ratio: 1.0 means the score is no better than picking at random.
    """

    k: int
    precision_at_k: float
    base_rate: float
    lift: float
    universe: int
    fit_in_universe: int
    fit_in_top_k: int
    unknown_technicians: int


def anchor_fit(targets: pd.DataFrame) -> pd.Series:
    """Whether each target meets the one anchor criterion the data can test.

    **This is a partial proxy and must not be read as overall fit.** The
    blueprint's anchor profile also requires $7-15M revenue, an 18%-25%
    normalized EBITDA margin, at least 60% municipal customers, at least 50%
    recurring revenue, and customer concentration limits. None of those appear
    in directory or website data, so none can be evaluated here. Technician
    count is the single hard, quantitative anchor criterion the sourcing
    pipeline actually observes.

    Records with an unknown technician count are counted as not-fit, which is
    the conservative treatment: an unverified target has not been shown to
    qualify.
    """
    technicians = pd.to_numeric(
        targets.get("technician_count_est", pd.Series(dtype=float)), errors="coerce"
    )
    low, high = ANCHOR_TECHNICIAN_BAND
    return technicians.between(low, high).fillna(False)


def validate_lead_score(targets: pd.DataFrame, *, k: int = 15) -> ScoreValidation:
    """Measure whether the priority score actually surfaces anchor-profile targets.

    The repository hands the top 15 to hand research, so precision@15 is the
    decision that matters: of the fifteen names a person will spend time on,
    how many meet the profile the blueprint says we are looking for?

    Full AUC-style validation is not possible here and is not attempted. That
    would need outcome labels — which targets actually converted to a deal —
    and this repository has none. Reporting precision against a stated proxy is
    the strongest honest claim available.
    """
    if targets.empty:
        raise ValueError("Cannot validate a lead score on an empty target universe.")
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")

    ordered = sourcing.order_scored_targets(targets)
    fit = anchor_fit(ordered)
    top_k = min(k, len(ordered))

    fit_in_top_k = int(fit.iloc[:top_k].sum())
    fit_in_universe = int(fit.sum())
    precision = fit_in_top_k / top_k
    base_rate = fit_in_universe / len(ordered)
    technicians = pd.to_numeric(
        ordered.get("technician_count_est", pd.Series(dtype=float)), errors="coerce"
    )

    return ScoreValidation(
        k=top_k,
        precision_at_k=precision,
        base_rate=base_rate,
        lift=precision / base_rate if base_rate else float("nan"),
        universe=len(ordered),
        fit_in_universe=fit_in_universe,
        fit_in_top_k=fit_in_top_k,
        unknown_technicians=int(technicians.isna().sum()),
    )


def workforce_curve_vs_anchor_band() -> pd.DataFrame:
    """Diagnose *why* the score discriminates weakly, band by band.

    The blueprint states two things that do not agree. Its anchor-platform
    criterion is 15-60 technicians. Its workforce score awards the full 40
    points to 8-35 technicians and then *declines* across 36-60 — penalising
    part of the very band the acquisition criteria target, while awarding full
    marks to 8-14 technician businesses that fall below it.

    This table makes that visible. It is a finding about the blueprint, not a
    proposed change to it: the scoring weights are blueprint-defined and locked
    by golden tests, and changing them silently would be the wrong response to
    a documentation-versus-implementation conflict.
    """
    low, high = ANCHOR_TECHNICIAN_BAND
    rows = []
    for technicians in (2, 5, 8, 12, 14, 15, 20, 35, 40, 50, 60, 70, 120):
        rows.append(
            {
                "Technicians": technicians,
                "Workforce Score (of 40)": sourcing.workforce_score(float(technicians), None),
                "In Anchor Band": low <= technicians <= high,
            }
        )
    frame = pd.DataFrame(rows)
    frame["Scored As Well As Best In Band"] = frame["Workforce Score (of 40)"] >= 40.0
    return frame


if __name__ == "__main__":
    main()
