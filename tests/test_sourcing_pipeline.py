"""Network-free tests for the sourcing engine.

Every test drives the pipeline through the in-memory OfflineFetcher or pure
helpers; the autouse guard in conftest.py fails any accidental live request.
"""

from __future__ import annotations

import pandas as pd
import pytest

from sourcing_fixtures import DIRECTORY_SOURCE, build_offline_dataset
from sourcing_pipeline import (
    CONTACT_ENV_VAR,
    EMPLOYEE_PATTERNS,
    FLEET_PATTERNS,
    PLACEHOLDER_CONTACT,
    SERVICE_KEYWORDS,
    TECHNICIAN_PATTERNS,
    DirectorySource,
    OfflineFetcher,
    apply_scoring,
    canonical_phone,
    canonicalize_url,
    clean_and_deduplicate,
    enrich_and_score,
    enrich_company,
    extract_first_int,
    extract_founding_year,
    name_slug,
    normalize_address,
    normalize_name,
    normalize_phone,
    scrape_source,
    scraper_contact,
    user_agent,
)

# --------------------------------------------------------------------------- #
# Scraper identity
# --------------------------------------------------------------------------- #


def test_scraper_contact_falls_back_to_unroutable_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONTACT_ENV_VAR, raising=False)
    assert scraper_contact() == PLACEHOLDER_CONTACT
    # A reserved .invalid domain can never resolve, so an unconfigured run cannot
    # point site operators at somebody else's mailbox.
    assert PLACEHOLDER_CONTACT.endswith(".invalid")


def test_scraper_contact_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTACT_ENV_VAR, "  ops@example.org  ")
    assert scraper_contact() == "ops@example.org"
    assert user_agent() == "LongDurationHoldCoResearch/1.0 (+ops@example.org)"


def test_blank_contact_env_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTACT_ENV_VAR, "   ")
    assert scraper_contact() == PLACEHOLDER_CONTACT


def test_build_session_warns_when_contact_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from sourcing_pipeline import build_session

    monkeypatch.delenv(CONTACT_ENV_VAR, raising=False)
    with caplog.at_level("WARNING", logger="water_rollup_sourcing"):
        session = build_session()
    assert CONTACT_ENV_VAR in caplog.text
    assert session.headers["User-Agent"].endswith(f"(+{PLACEHOLDER_CONTACT})")


def test_build_session_is_quiet_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from sourcing_pipeline import build_session

    monkeypatch.setenv(CONTACT_ENV_VAR, "ops@example.org")
    with caplog.at_level("WARNING", logger="water_rollup_sourcing"):
        build_session()
    assert CONTACT_ENV_VAR not in caplog.text


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Water Services, Inc.", "Acme Water Services"),
        ("  ACME   WATER  ", "Acme Water"),
        ("Beta Sewer Co", "Beta Sewer"),
        ("Gamma Pipeline LLC", "Gamma Pipeline"),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_name_slug_collapses_variants() -> None:
    assert name_slug("BETA SEWER, LLC") == name_slug("Beta Sewer Co")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example.com/about/", "https://example.com/about"),
        ("https://www.example.com//x//y/", "https://example.com/x/y"),
        ("http://Example.com", "http://example.com/"),
    ],
)
def test_canonicalize_url(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["(206) 555-0134", "206.555.0134", "206-555-0134", "+1 206 555 0134", "1-206-555-0134"],
)
def test_phone_normalization_is_format_agnostic(raw: str) -> None:
    assert canonical_phone(raw) == "+12065550134"
    assert normalize_phone(raw) == "(206) 555-0134"


def test_phone_rejects_unparseable() -> None:
    assert canonical_phone("call soon") == ""
    assert normalize_phone("") == ""


def test_normalize_address_is_format_agnostic() -> None:
    a = normalize_address("500 Main Street, Reno, NV 98501")
    b = normalize_address("500 Main St   Reno NV 98501")
    assert a == b == "500 main st reno nv 98501"


def test_founding_year_extraction_and_range_clamp() -> None:
    assert extract_founding_year("Founded in 1998 by two engineers.") == 1998
    assert extract_founding_year("established 2015") == 2015
    assert extract_founding_year("founded in 2029") is None  # future year clamped out


def test_workforce_extraction() -> None:
    assert extract_first_int(EMPLOYEE_PATTERNS, "our team of 40 employees") == 40
    assert extract_first_int(TECHNICIAN_PATTERNS, "over 18 field technicians") == 18
    assert extract_first_int(FLEET_PATTERNS, "a fleet of 12 service vehicles") == 12
    assert extract_first_int(EMPLOYEE_PATTERNS, "no numbers here") is None


def test_service_keyword_detection() -> None:
    text = "We offer CCTV inspection, sewer cleaning, and leak detection.".lower()
    hits = {keyword for keyword in SERVICE_KEYWORDS if keyword in text}
    assert {"cctv", "sewer cleaning", "leak detection"} <= hits


# --------------------------------------------------------------------------- #
# Deduplication across every signal, with audit trail
# --------------------------------------------------------------------------- #


def _rec(name: str, url: str = "", address: str = "", phone: str = "") -> dict[str, str]:
    return {
        "company_name": name,
        "company_url": url,
        "address": address,
        "primary_phone": phone,
        "directory_source": "t",
        "directory_page": "p",
        "directory_text": "",
    }


def test_dedup_on_domain() -> None:
    out = clean_and_deduplicate(
        [
            _rec("Alpha Water Services", "https://alpha.example.com/", "1 A St"),
            _rec("Alpha Utility Group", "https://www.alpha.example.com/contact", "2 B St"),
        ]
    )
    assert len(out) == 1
    row = out.iloc[0]
    assert row["duplicate_count"] == 2
    assert "domain" in row["merge_reason"]


def test_dedup_on_name() -> None:
    out = clean_and_deduplicate([_rec("Beta Sewer Co"), _rec("BETA SEWER, LLC")])
    assert len(out) == 1
    assert out.iloc[0]["merge_reason"] == "name"


def test_dedup_on_phone() -> None:
    out = clean_and_deduplicate(
        [
            _rec("Gamma Pipeline", phone="(206) 555-0100"),
            _rec("Delta Drainage", phone="206.555.0100"),
        ]
    )
    assert len(out) == 1
    assert "phone" in out.iloc[0]["merge_reason"]


def test_dedup_on_address() -> None:
    out = clean_and_deduplicate(
        [
            _rec("Epsilon Water", address="500 Main Street, Reno, NV 89501"),
            _rec("Zeta Utility", address="500 Main St Reno NV 89501"),
        ]
    )
    assert len(out) == 1
    assert "address" in out.iloc[0]["merge_reason"]


def test_distinct_records_do_not_merge() -> None:
    out = clean_and_deduplicate(
        [
            _rec("Northgate Water", "https://northgate.example.com/", "10 First Ave"),
            _rec("Harbor Sewer", "https://harbor.example.com/", "20 Second Ave"),
        ]
    )
    assert len(out) == 2
    assert set(out["duplicate_count"]) == {1}
    assert set(out["merge_reason"]) == {""}


def test_dedup_keeps_url_bearing_representative() -> None:
    out = clean_and_deduplicate(
        [
            _rec("Theta Water", url=""),
            _rec("Theta Water", url="https://theta.example.com/"),
        ]
    )
    assert len(out) == 1
    row = out.iloc[0]
    assert row["company_url"] == "https://theta.example.com/"
    assert row["merged_from"] == "Theta Water"


def test_exclude_keywords_filter_out_public_entities() -> None:
    out = clean_and_deduplicate(
        [
            _rec("Riverside Water District", "https://rwd.example.com/"),
            _rec("Ashford Field Services", "https://ashford.example.com/"),
        ]
    )
    assert list(out["company_name"]) == ["Ashford Field Services"]


# --------------------------------------------------------------------------- #
# Enrichment resilience — nothing crashes the run
# --------------------------------------------------------------------------- #


def _enrich(fetcher: OfflineFetcher, url: str, name: str = "Co", address: str = "") -> dict[str, object]:
    row = {"company_name": name, "company_url": url, "address": address}
    return enrich_company(fetcher, row, "2026-01-01")


def test_blocked_missing_and_error_paths() -> None:
    fetcher = OfflineFetcher(
        pages={"https://ok.example.com/": "<html><body><p>cctv</p></body></html>"},
        blocked={"https://blocked.example.com/"},
        errors={"https://err.example.com/"},
    )
    assert _enrich(fetcher, "https://blocked.example.com/")["website_status"] == "blocked"
    assert _enrich(fetcher, "https://err.example.com/")["website_status"] == "error"
    assert _enrich(fetcher, "")["website_status"] == "missing"
    ok = _enrich(fetcher, "https://ok.example.com/")
    assert ok["website_status"] == "ok"
    # provenance is attached on every path
    for url in ("https://blocked.example.com/", "https://err.example.com/", ""):
        assert _enrich(fetcher, url)["verification_date"] == "2026-01-01"


def test_malformed_html_still_parses() -> None:
    malformed = "<html><body><h1>Mal</h1><p>Founded in 1990<ul><li>cctv</body>"
    fetcher = OfflineFetcher({"https://mal.example.com/": malformed})
    row = _enrich(fetcher, "https://mal.example.com/")
    assert row["website_status"] == "ok"
    assert row["founding_year"] == 1990


def test_missing_fields_do_not_crash() -> None:
    fetcher = OfflineFetcher({"https://bare.example.com/": "<html><body><p>hello</p></body></html>"})
    row = _enrich(fetcher, "https://bare.example.com/")
    assert row["website_status"] == "ok"
    assert row["founding_year"] is None
    assert row["technician_count_est"] is None


def test_partial_enrichment_failure_isolated() -> None:
    cleaned = pd.DataFrame(
        [
            {"company_name": "Ok Co", "company_url": "https://ok.example.com/", "address": ""},
            {"company_name": "Err Co", "company_url": "https://err.example.com/", "address": ""},
        ]
    )
    fetcher = OfflineFetcher(
        pages={"https://ok.example.com/": "<html><body><p>sewer cleaning</p></body></html>"},
        errors={"https://err.example.com/"},
    )
    scored = enrich_and_score(fetcher, cleaned, workers=2, verified_on="2026-01-01")
    assert len(scored) == 2
    assert set(scored["website_status"]) == {"ok", "error"}


# --------------------------------------------------------------------------- #
# Directory parsing + pagination
# --------------------------------------------------------------------------- #


def _dir_page(companies: list[tuple[str, str]], next_href: str | None = None) -> str:
    cards = "".join(
        f"<article class='company'><h3>{name}</h3><a href='{url}'>site</a></article>"
        for name, url in companies
    )
    nxt = f"<a class='next' href='{next_href}'>Next</a>" if next_href else ""
    return f"<html><body>{cards}{nxt}</body></html>"


def test_scrape_follows_pagination() -> None:
    page1 = _dir_page(
        [
            ("Alpha Water Services", "https://alpha.example.com/"),
            ("Beta Sewer Group", "https://beta.example.com/"),
            ("Gamma Pipeline Co", "https://gamma.example.com/"),
        ],
        next_href="https://dir.example.com/page2",
    )
    page2 = _dir_page(
        [
            ("Delta Drainage", "https://delta.example.com/"),
            ("Epsilon Utility", "https://epsilon.example.com/"),
            ("Zeta Hydro", "https://zeta.example.com/"),
        ]
    )
    fetcher = OfflineFetcher(
        {"https://dir.example.com/page1": page1, "https://dir.example.com/page2": page2}
    )
    source = DirectorySource(
        name="Test Dir", url="https://dir.example.com/page1", item_selector="article.company"
    )
    records = scrape_source(fetcher, source)
    assert len(records) == 6
    assert {"Alpha Water Services", "Zeta Hydro"} <= {r["company_name"] for r in records}


# --------------------------------------------------------------------------- #
# Offline end-to-end + provenance retention
# --------------------------------------------------------------------------- #


def test_offline_end_to_end_processes_fifty_plus() -> None:
    records, pages, blocked, errors = build_offline_dataset()
    cleaned = clean_and_deduplicate(records)
    assert len(cleaned) >= 50

    scored = enrich_and_score(
        OfflineFetcher(pages, blocked, errors), cleaned, workers=4, verified_on="2026-07-30"
    )

    assert len(scored) >= 50
    assert scored["priority_score"].between(0, 100).all()
    assert scored["data_confidence"].between(0, 100).all()

    # Provenance and evidence retained through every transformation.
    for column in ("directory_source", "directory_page", "verification_date", "address_normalized"):
        assert column in scored.columns
    assert (scored["verification_date"] == "2026-07-30").all()
    assert (scored["directory_source"] == DIRECTORY_SOURCE).all()

    # Blocked/error targets survive as rows (never silently dropped).
    assert set(scored["website_status"]) >= {"ok", "blocked", "error"}
    # At least one merge was recorded with an audit reason.
    assert (scored["duplicate_count"] > 1).any()
    merged = scored[scored["duplicate_count"] > 1].iloc[0]
    assert merged["merge_reason"] != ""


def test_apply_scoring_survives_directory_only_frame() -> None:
    """The data_confidence defect fix: no website_status/service columns present."""
    frame = pd.DataFrame(
        [{"company_age": 30, "technician_count_est": 10, "employee_count_est": 15, "founding_year": 1990}]
    )
    scored = apply_scoring(frame)  # must not raise
    assert scored.iloc[0]["data_confidence"] == 60  # 3 present figures * 20
