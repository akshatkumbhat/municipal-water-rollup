"""Tests for the Phase 5 integrated candidate deliverable.

These assert the properties a reviewer depends on: that the package builds from
nothing, that every artifact is described and checksummed, that reruns are
byte-identical, that the narrative reconciles to the generated model outputs,
and that Phase 3 and Phase 4 results are unchanged by the integration.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

import pandas as pd
import pytest

from buy_and_build_model import (
    DEFAULT_ASSUMPTIONS,
    SCENARIOS,
    build_model,
    load_scenarios,
    return_bridge,
    run_scenario,
)
from candidate_package import (
    MANIFEST_NAME,
    PROVENANCE,
    SCENARIO_ORDER,
    STRESS_DIR,
    STRESS_ORDER,
    Artifact,
    PackageError,
    build_package,
    main,
    order_targets,
    score_distribution,
    select_candidate,
    verify_package,
)
from operations_kpis import (
    Thresholds,
    generate_sample_data,
    kpi_summary,
    monthly_rollup,
    validate_operating_data,
)

AS_OF = "2026-01-31"


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    """One clean build, reused across assertions."""
    target = tmp_path_factory.mktemp("package")
    return build_package(target, as_of=AS_OF)


@pytest.fixture(scope="module")
def manifest(package) -> dict:
    return json.loads(package.manifest_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ic_summary(package) -> str:
    return (package.output_dir / "IC_SUMMARY.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Clean build.
# ---------------------------------------------------------------------------


def test_clean_build_produces_a_complete_package(package) -> None:
    assert package.manifest_path.exists()
    assert (package.output_dir / "IC_SUMMARY.md").exists()
    assert (package.output_dir / "DEMO_WALKTHROUGH.md").exists()
    for directory in ("01_sourcing", "02_model", "03_operating", "04_reference"):
        assert (package.output_dir / directory).is_dir(), directory
    assert len(package.artifacts) >= 40


def test_build_creates_missing_output_directories(tmp_path) -> None:
    nested = tmp_path / "does" / "not" / "exist" / "yet"
    assert not nested.exists()
    result = build_package(nested, as_of=AS_OF)
    assert result.manifest_path.exists()


def test_build_does_not_depend_on_pre_existing_outputs(tmp_path) -> None:
    """A stale artifact from a previous run must not survive or be reused."""
    first = build_package(tmp_path / "pkg", as_of=AS_OF)
    stale = first.output_dir / "02_model" / "base" / "five_year_pro_forma.csv"
    stale.write_text("corrupted,garbage\n1,2\n", encoding="utf-8")
    orphan = first.output_dir / "01_sourcing" / "orphan_from_old_run.csv"
    orphan.write_text("stale\n", encoding="utf-8")

    second = build_package(tmp_path / "pkg", as_of=AS_OF)

    assert not orphan.exists(), "stale artifact survived a rebuild"
    assert "corrupted" not in stale.read_text(encoding="utf-8")
    assert not verify_package(second.manifest_path)


def test_build_works_in_a_path_containing_spaces(tmp_path) -> None:
    spaced = tmp_path / "candidate package with spaces"
    result = build_package(spaced, as_of=AS_OF)
    assert not verify_package(result.manifest_path)
    assert " " in str(result.output_dir)


def test_build_refuses_to_write_into_a_repository_root(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(PackageError, match="repository root"):
        build_package(tmp_path, as_of=AS_OF)


def test_invalid_as_of_fails_cleanly(tmp_path) -> None:
    with pytest.raises(PackageError, match="Invalid --as-of"):
        build_package(tmp_path / "pkg", as_of="not-a-date")


# ---------------------------------------------------------------------------
# Manifest and checksums.
# ---------------------------------------------------------------------------


def test_manifest_lists_every_generated_file(package, manifest) -> None:
    on_disk = {
        str(p.relative_to(package.output_dir)).replace("\\", "/")
        for p in package.output_dir.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    }
    listed = {entry["path"] for entry in manifest["artifacts"]}
    assert listed == on_disk
    assert manifest["artifact_count"] == len(on_disk)


def test_every_artifact_carries_provenance_and_a_description(manifest) -> None:
    valid = set(PROVENANCE.values())
    for entry in manifest["artifacts"]:
        assert entry["provenance"] in valid, entry["path"]
        assert len(entry["description"]) > 10, entry["path"]
        assert entry["bytes"] > 0, entry["path"]
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry["path"]


def test_checksums_verify_against_the_written_files(package) -> None:
    assert verify_package(package.manifest_path) == []


def test_verification_detects_a_tampered_artifact(tmp_path) -> None:
    result = build_package(tmp_path / "pkg", as_of=AS_OF)
    victim = result.output_dir / "02_model" / "scenario_comparison.csv"
    victim.write_text(victim.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    problems = verify_package(result.manifest_path)
    assert any("CHECKSUM MISMATCH" in p and "scenario_comparison" in p for p in problems)


def test_verification_detects_a_missing_artifact(tmp_path) -> None:
    result = build_package(tmp_path / "pkg", as_of=AS_OF)
    (result.output_dir / "01_sourcing" / "funnel_summary.csv").unlink()

    problems = verify_package(result.manifest_path)
    assert any("MISSING" in p and "funnel_summary" in p for p in problems)


def test_verification_detects_an_unlisted_file(tmp_path) -> None:
    result = build_package(tmp_path / "pkg", as_of=AS_OF)
    (result.output_dir / "04_reference" / "sneaked_in.csv").write_text("x\n", encoding="utf-8")

    problems = verify_package(result.manifest_path)
    assert any("UNLISTED FILE" in p for p in problems)


def test_verify_reports_a_missing_manifest_cleanly(tmp_path) -> None:
    with pytest.raises(PackageError, match="Manifest not found"):
        verify_package(tmp_path / "nope" / MANIFEST_NAME)


def test_verify_reports_malformed_manifest_cleanly(tmp_path) -> None:
    bad = tmp_path / MANIFEST_NAME
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(PackageError, match="not valid JSON"):
        verify_package(bad)


def test_manifest_isolates_the_only_volatile_field(manifest) -> None:
    assert manifest["determinism"]["artifacts_are_byte_identical_across_runs"] is True
    assert manifest["determinism"]["volatile_fields"] == ["as_of.date"]
    assert manifest["as_of"]["date"] == AS_OF
    assert manifest["as_of"]["is_volatile"] is True


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_two_clean_builds_are_byte_identical(tmp_path) -> None:
    first = build_package(tmp_path / "a", as_of=AS_OF)
    second = build_package(tmp_path / "b", as_of=AS_OF)

    first_files = sorted(
        p.relative_to(first.output_dir) for p in first.output_dir.rglob("*") if p.is_file()
    )
    second_files = sorted(
        p.relative_to(second.output_dir) for p in second.output_dir.rglob("*") if p.is_file()
    )
    assert first_files == second_files

    for relative in first_files:
        assert (first.output_dir / relative).read_bytes() == (
            second.output_dir / relative
        ).read_bytes(), relative


def test_target_ordering_is_total_and_stable() -> None:
    """The pipeline's own sort leaves score ties unordered; ours must not."""
    frame = pd.DataFrame(
        {
            "priority_score": [100.0, 100.0, 100.0, 90.0],
            "data_confidence": [100, 100, 100, 100],
            "company_name": ["Charlie", "Alpha", "Bravo", "Delta"],
        }
    )
    ordered = order_targets(frame)
    assert list(ordered["company_name"]) == ["Alpha", "Bravo", "Charlie", "Delta"]
    # Shuffling the input cannot change the output.
    reshuffled = order_targets(frame.iloc[::-1].reset_index(drop=True))
    pd.testing.assert_frame_equal(ordered, reshuffled)


# ---------------------------------------------------------------------------
# Candidate selection and identity consistency.
# ---------------------------------------------------------------------------


def test_selection_discloses_the_tie_rather_than_choosing_silently(package) -> None:
    selection = package.selection
    assert selection.is_ambiguous, "fixture set ties at the top score"
    assert len(selection.tied) > 1
    assert "did not select a candidate" in selection.rationale
    assert "no investment meaning" in selection.rationale


def test_selection_prefers_the_blueprint_anchor_band() -> None:
    targets = pd.DataFrame(
        {
            "priority_score": [100.0, 100.0],
            "data_confidence": [100, 100],
            "company_name": ["Aardvark Too Small", "Zebra In Band"],
            "technician_count_est": [3.0, 25.0],
        }
    )
    selection = select_candidate(targets)
    # Alphabetically first, but outside the band, so the band wins.
    assert selection.name == "Zebra In Band"
    assert selection.is_ambiguous


def test_selection_is_unambiguous_when_one_candidate_leads() -> None:
    targets = pd.DataFrame(
        {
            "priority_score": [100.0, 90.0],
            "data_confidence": [100, 100],
            "company_name": ["Leader", "Runner Up"],
            "technician_count_est": [20.0, 20.0],
        }
    )
    selection = select_candidate(targets)
    assert not selection.is_ambiguous
    assert "no tiebreak was required" in selection.rationale


def test_empty_universe_fails_cleanly() -> None:
    with pytest.raises(PackageError, match="target universe is empty"):
        select_candidate(pd.DataFrame())


def test_candidate_identity_is_consistent_across_every_artifact(
    package, manifest, ic_summary
) -> None:
    """The same company must appear in sourcing, manifest, and the summary."""
    name = package.selection.name
    out = package.output_dir

    selected = pd.read_csv(out / "01_sourcing" / "selected_candidate.csv")
    assert len(selected) == 1
    assert selected.iloc[0]["company_name"] == name

    universe = pd.read_csv(out / "01_sourcing" / "target_universe.csv")
    assert name in set(universe["company_name"])

    top15 = pd.read_csv(out / "01_sourcing" / "top_15_targets.csv")
    assert name in set(top15["company_name"])

    tied = pd.read_csv(out / "01_sourcing" / "selection_tie_disclosure.csv")
    assert name in set(tied["company_name"])

    assert manifest["candidate"]["selected"] == name
    assert manifest["candidate"]["tied_at_top_score"] == len(tied)
    assert f"**Selected candidate: {name}**" in ic_summary


def test_selected_candidate_is_the_top_of_the_packaged_universe(package) -> None:
    universe = pd.read_csv(package.output_dir / "01_sourcing" / "target_universe.csv")
    top_score = universe["priority_score"].max()
    selected = pd.read_csv(package.output_dir / "01_sourcing" / "selected_candidate.csv")
    assert float(selected.iloc[0]["priority_score"]) == pytest.approx(top_score)


def test_funnel_stages_reconcile_to_the_universe(package) -> None:
    """A funnel a reviewer reads must add up."""
    funnel = pd.read_csv(package.output_dir / "01_sourcing" / "funnel_summary.csv")
    universe = pd.read_csv(package.output_dir / "01_sourcing" / "target_universe.csv")
    counts = dict(zip(funnel["Stage"], funnel["Count"], strict=True))

    assert counts["Unique companies after deduplication"] == len(universe)
    assert counts["Directory records collected"] == int(universe["duplicate_count"].fillna(1).sum())
    assert counts["Directory records collected"] >= counts["Unique companies after deduplication"]
    # Clean enrichment plus limited-evidence records must equal the scored total,
    # so blocked and errored fetches are visible rather than folded into "success".
    assert (
        counts["Enriched without error"] + counts["Scored on limited evidence"]
        == counts["Total scored"]
    )
    assert counts["Enriched without error"] == int((universe["website_status"] == "ok").sum())
    assert counts["Selected anchor candidate"] == 1
    assert counts["Tied at maximum score"] == len(package.selection.tied)


def test_selected_candidate_rests_on_full_evidence(package) -> None:
    """Selecting a robots-blocked or errored record would be indefensible."""
    selected = pd.read_csv(package.output_dir / "01_sourcing" / "selected_candidate.csv").iloc[0]
    assert selected["website_status"] == "ok"
    assert pd.isna(selected["enrichment_error"])
    assert float(selected["data_confidence"]) >= 80


def test_tie_disclosure_shows_the_discriminating_flag(package) -> None:
    tied = pd.read_csv(package.output_dir / "01_sourcing" / "selection_tie_disclosure.csv")
    assert "in_anchor_band" in tied.columns
    assert tied["priority_score"].nunique() == 1, "ties must share one score"
    assert bool(tied.iloc[0]["in_anchor_band"]), "the selected row must lead the ranking"


def test_packaged_universe_excludes_the_volatile_timestamp(package) -> None:
    universe = pd.read_csv(package.output_dir / "01_sourcing" / "target_universe.csv")
    assert "scraped_at_utc" not in universe.columns
    # Provenance columns that are NOT volatile must survive.
    for column in ("company_url", "evidence_summary", "data_confidence", "merge_reason"):
        assert column in universe.columns, column


# ---------------------------------------------------------------------------
# IC summary reconciliation.
# ---------------------------------------------------------------------------


def test_summary_returns_reconcile_to_the_model(package, ic_summary) -> None:
    for name in SCENARIO_ORDER:
        returns = package.results[name].returns
        row = (
            f"| {name} | {returns['gross_moic']:.2f}x | {returns['gross_irr']:.1%} | "
            f"${returns['terminal_equity_value']:,.2f}M | "
            f"${returns['terminal_debt']:,.2f}M | "
            f"{returns['gross_leverage_at_close']:.2f}x | "
            f"{returns['maximum_year_end_gross_leverage']:.2f}x | "
            f"{'YES' if returns['leverage_limit_exceeded'] else 'no'} |"
        )
        assert row in ic_summary, f"{name} row missing or not reconciling"


def test_summary_transaction_values_reconcile_to_the_model(package, ic_summary) -> None:
    returns = package.results["base"].returns
    for value in (
        returns["platform_enterprise_value"],
        returns["initial_debt"],
        returns["initial_sponsor_equity"],
        returns["total_sponsor_equity_invested"],
    ):
        assert f"${value:,.2f}M" in ic_summary


def test_summary_matches_the_generated_scenario_comparison(package, ic_summary) -> None:
    comparison = pd.read_csv(package.output_dir / "02_model" / "scenario_comparison.csv")
    for _, row in comparison.iterrows():
        assert f"| {row['Scenario']} | {row['Gross MOIC']:.2f}x |" in ic_summary


def test_summary_distinguishes_close_year_end_and_exit_leverage(package, ic_summary) -> None:
    """One number cannot describe leverage at close, peak, and exit."""
    returns = package.results["base"].returns
    schedule = package.results["base"].schedule

    assert returns["gross_leverage_at_close"] == pytest.approx(3.0, abs=1e-9)
    assert returns["maximum_year_end_gross_leverage"] == pytest.approx(
        float(schedule["Gross Leverage"].max()), abs=1e-12
    )
    assert returns["exit_net_leverage"] == pytest.approx(
        float(schedule.iloc[-1]["Net Leverage"]), abs=1e-12
    )
    # The retained alias must stay equal to the year-end maximum.
    assert returns["peak_gross_leverage"] == returns["maximum_year_end_gross_leverage"]

    for value, field in (
        (returns["gross_leverage_at_close"], "`gross_leverage_at_close`"),
        (returns["maximum_year_end_gross_leverage"], "`maximum_year_end_gross_leverage`"),
        (returns["exit_net_leverage"], "`exit_net_leverage`"),
    ):
        assert f"{value:.2f}x" in ic_summary
        assert field in ic_summary
    assert "does not see the" in ic_summary


def test_summary_reports_the_leverage_limit_state(package, ic_summary) -> None:
    returns = package.results["base"].returns
    assert returns["leverage_limit_exceeded"] is False
    assert "No year exceeds the modelled 4.00x governor" in ic_summary
    assert "not a covenant" in ic_summary.lower()


def test_summary_labels_the_synergy_addback_as_author_defined(package, ic_summary) -> None:
    assert "leverage_synergy_addback_fraction" in ic_summary
    assert "not a covenant term" in ic_summary
    assert "documentation, caps, timing" in ic_summary


def test_summary_bridge_matches_the_generated_bridge(package, ic_summary) -> None:
    bridge = pd.read_csv(package.output_dir / "02_model" / "base" / "return_bridge.csv")
    for _, row in bridge.iterrows():
        assert f"| {row['Component']} | {row['Value']:,.2f} |" in ic_summary


def test_summary_labels_all_three_scenarios(ic_summary) -> None:
    assert "## 5. Returns" in ic_summary
    for name in SCENARIO_ORDER:
        assert f"| {name} |" in ic_summary
    assert "3% organic growth, half synergy capture" in ic_summary


def test_summary_does_not_overstate_the_downside(ic_summary) -> None:
    assert "not the floor" in ic_summary
    assert "stresses only four" in ic_summary
    assert "sensitivity, not a stress test" in ic_summary
    # The caveat is no longer the end of the story: the summary must also point
    # at the case that does stress the return-critical drivers.
    assert "02_model/stress/" in ic_summary


def test_summary_discloses_synthetic_and_fixture_data(ic_summary) -> None:
    assert "synthetic fixture company" in ic_summary
    assert "generated, not observed" in ic_summary
    assert "not evidence about any real business" in ic_summary
    assert "`synthetic`" in ic_summary
    assert "`fixture`" in ic_summary


def test_summary_states_targets_are_not_benchmarked(ic_summary) -> None:
    assert "not externally benchmarked" in ic_summary


def test_summary_states_the_model_is_not_derived_from_the_candidate(ic_summary) -> None:
    assert "financial\n> model is not derived from it" in ic_summary
    assert "no revenue or EBITDA" in ic_summary


def test_summary_covers_every_required_section(ic_summary) -> None:
    for heading in (
        "## 1. Transaction overview",
        "## 2. Investment thesis",
        "## 3. Target funnel and selected candidate",
        "## 4. Operating case",
        "## 5. Returns",
        "## 6. Value-creation bridge",
        "## 7. Leverage and liquidity",
        "## 8. Downside",
        "## 9. Material risks",
        "## 10. Next diligence steps",
        "## 11. First 100 days",
        "## 12. Limitations",
    ):
        assert heading in ic_summary, heading


def test_operating_kpis_in_summary_match_the_generated_table(package, ic_summary) -> None:
    summary_table = pd.read_csv(package.output_dir / "03_operating" / "kpi_summary.csv")
    for _, row in summary_table.iterrows():
        assert str(row["Metric"]) in ic_summary


# ---------------------------------------------------------------------------
# Reference material and demo.
# ---------------------------------------------------------------------------


def test_limitations_document_discloses_known_gaps(package) -> None:
    text = (package.output_dir / "04_reference" / "limitations.md").read_text(encoding="utf-8")
    for phrase in (
        "fixture, not a real company",
        "Operating data is synthetic",
        "not derived from the candidate",
        "blueprint downside is not severe",
        "modelling input, not a covenant",
        "Lender credit for synergies is assumed, not agreed",
        "Leverage is not one number",
        "not externally benchmarked",
        "overlapping customers",
    ):
        assert phrase in text, phrase

    # Items remediated in Phase 6 must no longer be listed as limitations.
    for stale in (
        "Sensitivity grids are centred on the base case",
        "Negative organic growth cannot be modelled",
        "Synergies count toward covenant capacity",
    ):
        assert stale not in text, f"stale limitation still disclosed: {stale}"


def test_assumption_table_labels_every_source(package) -> None:
    table = pd.read_csv(package.output_dir / "04_reference" / "assumptions_and_provenance.csv")
    assert len(table) >= 15
    assert set(table["Provenance"]) <= {PROVENANCE["blueprint"], PROVENANCE["author"]}
    governor = table[table["Assumption"].str.contains("Max pro-forma leverage")]
    assert governor.iloc[0]["Provenance"] == PROVENANCE["author"]


def test_demo_walkthrough_covers_the_required_path(package) -> None:
    text = (package.output_dir / "DEMO_WALKTHROUGH.md").read_text(encoding="utf-8")
    assert "Sourcing funnel" in text
    assert "Underwriting" in text
    assert "Operating control" in text
    assert "Exception to operating action" in text
    assert package.selection.name in text
    assert "synthetic" in text


# ---------------------------------------------------------------------------
# Phase 3 and Phase 4 regression.
# ---------------------------------------------------------------------------


def test_phase3_golden_economics_are_unchanged(package) -> None:
    """Integration must not perturb the documented base case."""
    returns = build_model().returns
    assert returns["gross_moic"] == pytest.approx(4.5388573508, rel=1e-8)
    assert returns["gross_irr"] == pytest.approx(0.3532851206, rel=1e-8)
    assert returns["terminal_debt"] == pytest.approx(1.5602039747, rel=1e-8)

    packaged = json.loads(
        (package.output_dir / "02_model" / "base" / "return_summary.json").read_text()
    )
    assert packaged["gross_moic"] == pytest.approx(returns["gross_moic"], rel=1e-12)
    assert packaged["gross_irr"] == pytest.approx(returns["gross_irr"], rel=1e-12)


def test_packaged_model_matches_a_direct_scenario_run(package) -> None:
    for name in SCENARIO_ORDER:
        direct = run_scenario(SCENARIOS[name]).returns
        packaged = json.loads(
            (package.output_dir / "02_model" / name / "return_summary.json").read_text()
        )
        for key in ("gross_moic", "gross_irr", "terminal_debt", "peak_gross_leverage"):
            assert packaged[key] == pytest.approx(direct[key], rel=1e-12), f"{name}.{key}"


def test_phase4_default_kpis_are_unchanged(package) -> None:
    """The package must reproduce Phase 4's default KPI results exactly."""
    direct = kpi_summary(
        monthly_rollup(validate_operating_data(generate_sample_data())), Thresholds()
    )
    packaged = pd.read_csv(package.output_dir / "03_operating" / "kpi_summary.csv")

    assert list(packaged["Metric"]) == list(direct["Metric"])
    for column in ("Current", "Prior", "Target"):
        assert packaged[column].tolist() == pytest.approx(direct[column].tolist(), rel=1e-12)
    assert list(packaged["Status"]) == list(direct["Status"])


def test_packaged_operating_data_matches_the_generator(package) -> None:
    packaged = pd.read_csv(package.output_dir / "03_operating" / "operating_data.csv")
    direct = validate_operating_data(generate_sample_data())
    assert len(packaged) == len(direct)
    assert packaged["revenue"].sum() == pytest.approx(direct["revenue"].sum(), rel=1e-12)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def test_cli_builds_and_then_verifies(tmp_path, capsys) -> None:
    out = tmp_path / "cli_pkg"
    main(["--output-dir", str(out), "--as-of", AS_OF])
    built = capsys.readouterr().out
    assert "Integrated candidate package written" in built
    assert "AMBIGUOUS" in built

    main(["--verify", str(out / MANIFEST_NAME)])
    verified = capsys.readouterr().out
    assert "Verification passed" in verified


def test_cli_verify_exits_nonzero_on_tampering(tmp_path, capsys) -> None:
    out = tmp_path / "cli_pkg"
    main(["--output-dir", str(out), "--as-of", AS_OF])
    capsys.readouterr()

    victim = out / "IC_SUMMARY.md"
    victim.write_text(victim.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main(["--verify", str(out / MANIFEST_NAME)])
    assert exit_info.value.code == 1
    assert "Verification FAILED" in capsys.readouterr().out


def test_cli_rejects_a_bad_as_of(tmp_path) -> None:
    with pytest.raises(SystemExit, match="Invalid --as-of"):
        main(["--output-dir", str(tmp_path / "pkg"), "--as-of", "13/13/2026"])


def test_artifact_serializes_completely() -> None:
    artifact = Artifact(
        path="a/b.csv", sha256="0" * 64, bytes=10, kind="model", provenance="x", description="y"
    )
    assert set(artifact.to_dict()) == {
        "path",
        "sha256",
        "bytes",
        "kind",
        "provenance",
        "description",
    }


def test_missing_expected_artifact_is_detected_at_build_time(tmp_path, monkeypatch) -> None:
    """If a writer silently stops emitting a file, the build must fail."""
    import candidate_package as cp

    real_write = cp.write_operating_outputs

    def partial_write(frame, output_dir, **kwargs):
        written = real_write(frame, output_dir, **kwargs)
        (output_dir / "exceptions.csv").unlink()
        return written

    monkeypatch.setattr(cp, "write_operating_outputs", partial_write)
    with pytest.raises(PackageError, match="were not generated"):
        build_package(tmp_path / "pkg", as_of=AS_OF)


def test_undescribed_artifact_is_detected_at_build_time(tmp_path, monkeypatch) -> None:
    """A new output with no manifest description must not slip through."""
    import candidate_package as cp

    real_write = cp.write_operating_outputs

    def extra_write(frame, output_dir, **kwargs):
        written = real_write(frame, output_dir, **kwargs)
        (output_dir / "undocumented_extra.csv").write_text("x\n", encoding="utf-8")
        return written

    monkeypatch.setattr(cp, "write_operating_outputs", extra_write)
    with pytest.raises(PackageError, match="missing a manifest description"):
        build_package(tmp_path / "pkg", as_of=AS_OF)


def test_corrupt_fixture_source_fails_cleanly(tmp_path, monkeypatch) -> None:
    import candidate_package as cp

    monkeypatch.setattr(cp, "build_offline_dataset", lambda: ([], {}, set(), set()))
    with pytest.raises(PackageError, match="produced no records"):
        build_package(tmp_path / "pkg", as_of=AS_OF)


def test_shutil_is_not_used_to_remove_unmanaged_content(tmp_path) -> None:
    """Rebuilds must not delete files the package did not create."""
    out = tmp_path / "pkg"
    build_package(out, as_of=AS_OF)
    bystander = out / "reviewer_notes.txt"
    bystander.write_text("keep me\n", encoding="utf-8")

    build_package(out, as_of=AS_OF)
    assert bystander.exists(), "rebuild deleted a file it did not create"
    assert bystander.read_text(encoding="utf-8") == "keep me\n"
    # ...but it is then reported as unlisted, not silently included.
    assert any("UNLISTED FILE" in p for p in verify_package(out / MANIFEST_NAME))
    shutil.rmtree(out)


# ---------------------------------------------------------------------------
# Phase 6: the shipped custom-scenario example (M3).
# ---------------------------------------------------------------------------

EXAMPLE_SCENARIOS = pathlib.Path(__file__).resolve().parent.parent / "examples" / "scenarios.json"


def test_example_scenario_file_is_tracked_and_valid() -> None:
    """The README points at this path; it must exist and load."""
    assert EXAMPLE_SCENARIOS.exists(), "README references examples/scenarios.json"
    registry = load_scenarios(EXAMPLE_SCENARIOS)
    assert registry, "example file defines no scenarios"
    for name, scenario in registry.items():
        assert scenario.description, name
        assert "Author-defined" in scenario.source, name


def test_example_includes_a_negative_growth_case() -> None:
    registry = load_scenarios(EXAMPLE_SCENARIOS)
    declining = [s for s in registry.values() if s.assumptions.annual_organic_growth < 0]
    assert declining, "the example must demonstrate negative organic growth"
    result = run_scenario(declining[0])
    assert result.returns["gross_moic"] > 0
    assert (result.schedule["Ending Debt"] >= 0).all()


def test_example_scenarios_are_override_only() -> None:
    """Omitted keys must keep their base value, proving override-only loading."""
    registry = load_scenarios(EXAMPLE_SCENARIOS)
    for scenario in registry.values():
        assert scenario.assumptions.platform_revenue == DEFAULT_ASSUMPTIONS.platform_revenue
        assert scenario.assumptions.tax_rate == DEFAULT_ASSUMPTIONS.tax_rate


def test_example_file_still_rejects_unknown_keys(tmp_path) -> None:
    payload = json.loads(EXAMPLE_SCENARIOS.read_text(encoding="utf-8"))
    payload["scenarios"][0]["assumptions"]["intrest_rate"] = 0.1
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown assumption"):
        load_scenarios(broken)


def test_readme_custom_scenario_command_runs_from_a_clean_checkout(tmp_path) -> None:
    """Execute the exact command the README prints, as a reviewer would."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "--scenario-file examples/scenarios.json" in readme, "README command drifted"
    assert "--scenario-file scenarios.json" not in readme, "stale path still documented"

    completed = subprocess.run(
        [
            sys.executable,
            "buy_and_build_model.py",
            "--scenario-file",
            "examples/scenarios.json",
            "--output-dir",
            str(tmp_path / "out"),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert (tmp_path / "out" / "revenue-decline" / "return_summary.json").exists()
    assert (tmp_path / "out" / "scenario_comparison.csv").exists()


def test_example_includes_a_severe_downside_worse_than_the_blueprint_case() -> None:
    """The blueprint guardrail case explores a narrow band; this one is severe."""
    registry = load_scenarios(EXAMPLE_SCENARIOS)
    assert "severe-downside" in registry

    severe = run_scenario(registry["severe-downside"]).returns
    blueprint = run_scenario(SCENARIOS["downside"]).returns

    assert severe["gross_moic"] < blueprint["gross_moic"]
    assert severe["gross_irr"] < blueprint["gross_irr"]
    # A modeled capital-impairment case: the sponsor does not recover its
    # investment. This is a severe downside, not a guaranteed lower bound.
    assert severe["gross_moic"] < 1.05
    assert severe["gross_irr"] < 0.01


def test_severe_downside_stresses_the_three_required_drivers() -> None:
    """Entry multiple, platform margin, and add-on integration must all move."""
    scenario = load_scenarios(EXAMPLE_SCENARIOS)["severe-downside"]
    a = scenario.assumptions

    assert a.platform_entry_multiple > DEFAULT_ASSUMPTIONS.platform_entry_multiple
    assert a.platform_ebitda_margin < DEFAULT_ASSUMPTIONS.platform_ebitda_margin
    assert a.platform_margin_expansion_bps_per_year == 0
    assert a.annual_organic_growth < 0
    assert a.terminal_multiple < DEFAULT_ASSUMPTIONS.terminal_multiple
    assert a.interest_rate > DEFAULT_ASSUMPTIONS.interest_rate

    # Integration failure: a smaller programme, delayed, at worse margins.
    assert len(scenario.add_ons) < len(SCENARIOS["downside"].add_ons)
    assert max(x.close_year for x in scenario.add_ons) > 3
    assert min(x.ebitda_margin for x in scenario.add_ons) < 0.15


def test_severe_downside_remains_internally_coherent() -> None:
    """Severe must not mean broken: debt still amortises and identities hold."""
    scenario = load_scenarios(EXAMPLE_SCENARIOS)["severe-downside"]
    result = run_scenario(scenario)
    schedule = result.schedule

    assert (schedule["Ending Debt"] >= 0).all()
    assert (schedule["Ending Cash"] >= 0).all()
    assert (schedule["Free Cash Flow"] > 0).all(), "a stress case, not a liquidity crisis"
    assert result.returns["leverage_limit_exceeded"] is False
    assert result.returns["terminal_debt"] < result.returns["initial_debt"]

    bridge = return_bridge(result, scenario.add_ons)
    walk = bridge[bridge["Component"] != "Exit equity value"]["Value"].sum()
    assert walk == pytest.approx(result.returns["terminal_equity_value"], abs=1e-9)
    # The equity story is carried by deleveraging, not by operations.
    paydown = bridge.loc[bridge["Component"] == "Net debt paydown", "Value"].item()
    multiple = bridge.loc[bridge["Component"] == "Multiple change", "Value"].item()
    assert paydown > 0 and multiple < 0


def test_integration_failure_isolates_the_add_on_driver() -> None:
    """Platform assumptions untouched, so the delta is attributable to tuck-ins."""
    scenario = load_scenarios(EXAMPLE_SCENARIOS)["integration-failure"]
    a = scenario.assumptions

    assert a.platform_entry_multiple == DEFAULT_ASSUMPTIONS.platform_entry_multiple
    assert a.platform_ebitda_margin == DEFAULT_ASSUMPTIONS.platform_ebitda_margin
    assert a.annual_organic_growth == DEFAULT_ASSUMPTIONS.annual_organic_growth
    assert a.terminal_multiple == DEFAULT_ASSUMPTIONS.terminal_multiple

    result = run_scenario(scenario).returns
    base = build_model().returns
    assert result["gross_moic"] < base["gross_moic"]


# ---------------------------------------------------------------------------
# Research-grounded content: sourced thesis, self-criticism, benchmark table.
# ---------------------------------------------------------------------------


def test_summary_cites_the_demand_gap_rather_than_asserting_it(ic_summary) -> None:
    assert "$630.1 billion" in ic_summary
    assert "73% increase" in ic_summary
    assert "Clean Watersheds Needs Survey" in ic_summary
    assert "RESEARCH_BENCHMARKS.md" in ic_summary


def test_summary_states_the_verified_literature_finding(ic_summary) -> None:
    """The peer-reviewed evidence supports buy-and-build; say so accurately."""
    assert "outperform" in ic_summary
    assert "3,399 buyouts" in ic_summary
    assert "above-average equity returns" in ic_summary


def test_summary_shifts_the_burden_to_entry_pricing(ic_summary) -> None:
    """If the strategy is supported, the exposure is what was paid for it."""
    assert "the exposure is not the strategy" in ic_summary.lower()
    assert "0.41x to" in ic_summary
    assert "sensitivity_add_on_entry.csv" in ic_summary
    assert "not on a sourcing edge" in ic_summary


def test_summary_names_the_residual_literature_risks(ic_summary) -> None:
    assert "Late-entrant disadvantage" in ic_summary
    assert "Limited attention" in ic_summary
    assert "integration-failure" in ic_summary


def test_summary_contains_no_falsified_add_on_statistic(ic_summary) -> None:
    """Regression guard: this claim was shipped once and must never return."""
    assert "19.9" not in ic_summary
    assert "23.1" not in ic_summary


def test_summary_records_the_correction_rather_than_hiding_it(ic_summary) -> None:
    assert "A correction is recorded here deliberately" in ic_summary
    assert "falsified" in ic_summary
    assert "provenance rule" in ic_summary


def test_benchmark_table_scores_every_material_assumption(package) -> None:
    table = pd.read_csv(package.output_dir / "04_reference" / "assumption_benchmarks.csv")
    assert len(table) >= 8
    for column in ("Assumption", "Model value", "Published range", "Tier", "Verdict"):
        assert column in table.columns
    assert table["Comment"].str.len().gt(20).all()


def test_benchmark_table_shows_aggression_and_conservatism_both(package) -> None:
    """A table that only flattered the model would be worthless."""
    table = pd.read_csv(package.output_dir / "04_reference" / "assumption_benchmarks.csv")
    verdicts = " ".join(table["Verdict"])
    assert "ABOVE" in verdicts, "must flag inputs above the evidence"
    assert "BELOW range" in verdicts, "must flag inputs below the evidence"
    assert "conservative" in verdicts, "must credit genuine conservatism"


def test_practitioner_sourced_verdicts_are_marked_indicative(package) -> None:
    """The provenance rule, enforced: [D] sources may not be load-bearing."""
    table = pd.read_csv(package.output_dir / "04_reference" / "assumption_benchmarks.csv")
    practitioner = table[table["Tier"].str.startswith("[D]")]
    assert not practitioner.empty
    assert practitioner["Verdict"].str.contains("indicative only").all()

    load_bearing = table[table["Tier"] == "[C]"]
    assert not load_bearing.empty
    assert not load_bearing["Verdict"].str.contains("indicative").any()


def test_benchmark_table_marks_unbenchmarked_inputs_honestly(package) -> None:
    table = pd.read_csv(package.output_dir / "04_reference" / "assumption_benchmarks.csv")
    unsourced = table[table["Tier"] == "none"]
    assert not unsourced.empty
    assert unsourced["Verdict"].eq("Unbenchmarked").all()


def test_summary_points_at_the_benchmark_table(ic_summary) -> None:
    assert "assumption_benchmarks.csv" in ic_summary
    assert "below current market" in ic_summary


# ---------------------------------------------------------------------------
# Author-defined stress cases.
#
# The blueprint downside stresses four variables and still returns 3.58x. These
# cases vary the entry multiple, platform margin, and add-on execution. They
# ship alongside the blueprint scenarios and must never be presented as
# blueprint authority, so provenance is asserted as tightly as the numbers.
# ---------------------------------------------------------------------------


def test_stress_cases_are_shipped_in_the_package(package) -> None:
    stress_dir = package.output_dir / "02_model" / "stress"
    assert stress_dir.is_dir()
    for name in STRESS_ORDER:
        assert (stress_dir / name / "return_summary.json").exists()
    assert (stress_dir / "stress_comparison.csv").exists()


def test_stress_artifacts_are_labelled_author_defined(manifest) -> None:
    entries = [a for a in manifest["artifacts"] if a["path"].startswith(f"{STRESS_DIR}/")]
    assert entries, "no stress artifacts recorded in the manifest"
    assert {a["provenance"] for a in entries} == {PROVENANCE["author"]}


def test_stress_cases_do_not_displace_the_blueprint_scenarios(package, manifest) -> None:
    # The shipped `downside` is the blueprint's IC guardrail case. Adding a
    # harsher author-defined case must not quietly replace it.
    assert SCENARIO_ORDER == ("base", "downside", "upside")
    for name in SCENARIO_ORDER:
        assert (package.output_dir / "02_model" / name / "return_summary.json").exists()
    blueprint = [
        a
        for a in manifest["artifacts"]
        if a["path"].startswith("02_model/downside/") and not a["path"].startswith(STRESS_DIR)
    ]
    assert blueprint
    assert {a["provenance"] for a in blueprint} == {PROVENANCE["modelled"]}


def test_severe_downside_prices_capital_impairment(package) -> None:
    summary = json.loads(
        (
            package.output_dir / "02_model" / "stress" / "severe-downside" / "return_summary.json"
        ).read_text(encoding="utf-8")
    )
    # The point of the case: equity is impaired, not merely disappointing.
    assert summary["gross_moic"] < 1.0
    assert summary["gross_irr"] < 0.0


def test_summary_quotes_the_severe_case_from_the_model(package, ic_summary) -> None:
    """The narrative figure must be the generated one, not a typed constant."""
    summary = json.loads(
        (
            package.output_dir / "02_model" / "stress" / "severe-downside" / "return_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert f"{summary['gross_moic']:.2f}x" in ic_summary
    assert "02_model/stress/" in ic_summary


def test_summary_no_longer_asks_for_a_stress_that_already_exists(ic_summary) -> None:
    # Recommendation 7 used to read "Build a downside that stresses entry
    # multiple, margin, and integration failure" — which by then existed.
    assert "Build a downside that stresses" not in ic_summary


def test_limitations_records_the_stress_and_its_limits(package) -> None:
    text = (package.output_dir / "04_reference" / "limitations.md").read_text(encoding="utf-8")
    assert "02_model/stress/" in text
    # Still honest in both directions: the stress exists, and it is not a floor.
    assert "author-defined" in text
    assert "not a guaranteed lower bound" in text
    assert "This remains the most" not in text


# ---------------------------------------------------------------------------
# Evidence position.
#
# The summary states, in the reader's first screen, how much of the model rests
# on real evidence. Those counts are computed from the benchmark table, so the
# tests below assert the narrative and the table cannot disagree — and that the
# authored interpretation still matches the verdicts it describes.
# ---------------------------------------------------------------------------


def test_summary_states_the_evidence_position_from_the_table(package, ic_summary) -> None:
    table = pd.read_csv(package.output_dir / "04_reference" / "assumption_benchmarks.csv")
    strong = int(table["Tier"].str.startswith(("[A]", "[B]")).sum())
    industry = int(table["Tier"].str.startswith("[C]").sum())
    indicative = int(table["Tier"].str.startswith("[D]").sum())
    unbenchmarked = int(table["Tier"].eq("none").sum())

    assert f"Of the {len(table)} material model inputs" in ic_summary
    assert f"**{strong} are supported by" in ic_summary
    assert f"{industry} rest on industry-research" in ic_summary
    assert f"{indicative} on practitioner" in ic_summary
    assert f"and {unbenchmarked} had no usable" in ic_summary


def test_summary_states_no_model_input_has_strong_evidence(package) -> None:
    """If this ever stops being true, the narrative claim must be revisited."""
    table = pd.read_csv(package.output_dir / "04_reference" / "assumption_benchmarks.csv")
    assert not table["Tier"].str.startswith(("[A]", "[B]")).any()


def test_summary_says_blueprint_provenance_is_not_evidence(ic_summary) -> None:
    assert "not evidence that the number is right" in ic_summary
    assert "written by the same author" in ic_summary


def test_return_flattering_inputs_still_carry_the_verdicts_described(package, ic_summary) -> None:
    """Guards the authored reading of the table against the table changing.

    The summary asserts that three inputs sit outside their cited range in the
    direction that helps returns. Each must still be outside it.
    """
    table = pd.read_csv(package.output_dir / "04_reference" / "assumption_benchmarks.csv")
    verdicts = dict(zip(table["Assumption"], table["Verdict"], strict=True))

    assert "ABOVE" in verdicts["Platform EBITDA margin"]
    assert "BELOW" in verdicts["Add-on entry multiple"]
    assert "BELOW" in verdicts["Interest rate"]
    # And the one that cuts the other way is still flagged conservative.
    assert "conservative" in verdicts["Year-5 exit mark"]
    assert "Three of those cut in favour of the returns" in ic_summary


# ---------------------------------------------------------------------------
# Score discrimination.
#
# The tie disclosure shows the symptom; score_distribution.csv shows the cause.
# These assert the diagnostic ships, that its bands account for the whole
# universe, and that the narrative counts are the frame's own.
# ---------------------------------------------------------------------------


def test_score_distribution_is_shipped_and_recorded(package, manifest) -> None:
    path = package.output_dir / "01_sourcing" / "score_distribution.csv"
    assert path.exists()
    listed = {a["path"] for a in manifest["artifacts"]}
    assert "01_sourcing/score_distribution.csv" in listed


def test_score_bands_account_for_every_company(package) -> None:
    frame = pd.read_csv(package.output_dir / "01_sourcing" / "score_distribution.csv")
    total = int(frame.loc[frame["Band"] == "TOTAL", "Companies"].iloc[0])
    banded = int(frame.loc[frame["Band"] != "TOTAL", "Companies"].sum())
    assert banded == total, "bands must partition the universe, not overlap or drop"


def test_summary_quotes_the_distribution_it_ships(package, ic_summary) -> None:
    universe = pd.read_csv(package.output_dir / "01_sourcing" / "target_universe.csv")
    scores = pd.to_numeric(universe["priority_score"], errors="coerce").dropna()
    top = float(scores.max())
    at_top = int(scores.eq(top).sum())
    within_five = int(scores.ge(top - 5).sum())

    assert f"{at_top} companies score exactly {top:.0f}" in ic_summary
    assert f"and {within_five} of" in ic_summary
    assert "01_sourcing/score_distribution.csv" in ic_summary


def test_saturation_is_named_as_an_instrument_limitation(package, ic_summary) -> None:
    """It is a property of the score, not of the fixture data. Say so."""
    assert "instrument-design limitation, not a data artifact" in ic_summary
    limitations = (package.output_dir / "04_reference" / "limitations.md").read_text(
        encoding="utf-8"
    )
    assert "does not discriminate at the top" in limitations
    assert "score_distribution.csv" in limitations


def test_score_distribution_rejects_an_unscored_universe() -> None:
    with pytest.raises(PackageError, match="no scores present"):
        score_distribution(pd.DataFrame({"priority_score": [None, None]}))


def test_no_attribution_is_inferred_by_subtracting_bundled_scenarios(ic_summary) -> None:
    """`severe-downside` minus `integration-failure` is not an attribution.

    The two differ by more than the add-on programme: severe also degrades
    synergy capture and first-year realization, and its residual carries organic
    growth and the interest rate. An earlier version of section 8 subtracted one
    from the other and ranked risks with the result. Guard the retraction.
    """
    for banned in (
        "not the fragile part",
        "entire tuck-in programme",
        "inverts the usual",
        "isolates that last driver",
    ):
        assert banned not in ic_summary, f"invalid attribution phrasing returned: {banned}"
    assert "Retraction" in ic_summary
    # The retraction is now backed by a real decomposition, not a holding note.
    assert "Shapley decomposition" in ic_summary
    assert "driver_attribution.csv" in ic_summary


def test_attribution_artifacts_ship_and_reconcile(package, manifest) -> None:
    stress = package.output_dir / "02_model" / "stress"
    shapley = pd.read_csv(stress / "driver_attribution.csv")
    oat = pd.read_csv(stress / "driver_attribution_oat.csv")

    base = json.loads(
        (package.output_dir / "02_model" / "base" / "return_summary.json").read_text()
    )
    severe = json.loads((stress / "severe-downside" / "return_summary.json").read_text())
    endpoint = base["gross_moic"] - severe["gross_moic"]

    assert shapley["Contribution"].sum() == pytest.approx(endpoint, abs=1e-9)
    assert oat["Isolated loss"].sum() > endpoint, "diagnostic must show non-additivity"

    listed = {a["path"] for a in manifest["artifacts"]}
    assert "02_model/stress/driver_attribution.csv" in listed
    assert "02_model/stress/driver_attribution_oat.csv" in listed


def test_margin_risk_is_separated_from_repricing(package, ic_summary) -> None:
    """The severe case must price a diligence miss, not a cheaper acquisition.

    Previously `platform_ebitda_margin` set both the earnings and the purchase
    price, so a margin stress silently repriced the deal and the decomposition
    scored margin risk at a fraction of its real weight.
    """
    severe = json.loads(
        (
            package.output_dir / "02_model" / "stress" / "severe-downside" / "return_summary.json"
        ).read_text(encoding="utf-8")
    )
    # Price struck on the believed margin, earnings delivered on the real one.
    assert severe["entry_ebitda_shortfall"] > 0
    assert (
        severe["effective_platform_entry_multiple"] > severe["underwritten_platform_entry_multiple"]
    )
    assert severe["gross_leverage_at_close"] > 3.0, "true opening leverage must exceed headline"

    body = ic_summary.split("### What actually destroys the value")[1].split("## 9.")[0]
    assert "underwritten_ebitda_margin" in body
    assert "diligence-miss" in body and "operating-miss" in body


def test_both_margin_failure_modes_ship_separately(package) -> None:
    """A price struck on absent EBITDA is not the same risk as later erosion."""
    stress = package.output_dir / "02_model" / "stress"
    diligence = json.loads((stress / "diligence-miss" / "return_summary.json").read_text())
    operating = json.loads((stress / "operating-miss" / "return_summary.json").read_text())

    # The diligence miss leaves the dollars paid untouched and raises the
    # effective multiple; the operating miss leaves entry economics honest.
    assert diligence["effective_platform_entry_multiple"] > 6.0
    assert operating["effective_platform_entry_multiple"] == pytest.approx(6.0, abs=1e-9)
    assert operating["entry_ebitda_shortfall"] == pytest.approx(0.0, abs=1e-9)
