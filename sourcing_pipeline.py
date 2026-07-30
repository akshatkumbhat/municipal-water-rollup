"""Compliant target-sourcing pipeline for water/wastewater field-service companies.

The script discovers company records from public directories, normalizes and
filters them, visits company websites for lightweight enrichment, and assigns a
0-100 proprietary outreach priority score.

It deliberately does NOT bypass logins, CAPTCHAs, access controls, or robots.txt.
Review each directory's terms before commercial use.

Run:
    python sourcing_pipeline.py --output targets.csv --min-targets 50

Optional:
    python sourcing_pipeline.py --source-config sources.csv --output targets.csv

A custom source CSV may contain:
    name,url,item_selector,name_selector,url_selector,address_selector,next_selector
Selectors may be blank; the parser then uses generic card/JSON-LD heuristics.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import logging
import re
import time
import urllib.robotparser
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger("water_rollup_sourcing")
USER_AGENT = "LongDurationHoldCoResearch/1.0 (+compliance-contact@example.com)"
REQUEST_TIMEOUT = 20
MAX_SITE_TEXT_CHARS = 250_000

SERVICE_KEYWORDS = {
    "asset management",
    "cctv",
    "closed circuit television",
    "condition assessment",
    "cross connection",
    "hydrant",
    "inflow and infiltration",
    "i&i",
    "jetting",
    "leak detection",
    "manhole",
    "meter testing",
    "pipeline inspection",
    "preventive maintenance",
    "sewer cleaning",
    "smoke testing",
    "stormwater inspection",
    "trenchless",
    "utility locating",
    "valve exercising",
    "water sampling",
    "wastewater compliance",
}

EXCLUDE_KEYWORDS = {
    "city of ",
    "county of ",
    "department of public works",
    "municipal utility",
    "water authority",
    "water district",
    "university",
}

FOUNDING_PATTERNS = (
    re.compile(r"(?:founded|established|since|serving .* since)\D{0,18}(18\d{2}|19\d{2}|20[0-2]\d)", re.I),
    re.compile(r"\b(18\d{2}|19\d{2}|20[0-2]\d)\b.{0,20}(?:founded|established)", re.I),
)
EMPLOYEE_PATTERNS = (
    re.compile(r"\b(?:team of|more than|over|approximately|about)?\s*(\d{1,4})\+?\s+(?:employees|people|staff members)\b", re.I),
)
TECHNICIAN_PATTERNS = (
    re.compile(r"\b(?:team of|more than|over|approximately|about)?\s*(\d{1,3})\+?\s+(?:technicians|field technicians|operators|crews)\b", re.I),
)
FLEET_PATTERNS = (
    re.compile(r"\b(?:fleet of|more than|over|approximately|about)?\s*(\d{1,3})\+?\s+(?:trucks|service vehicles|vans)\b", re.I),
)
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
YEAR_NOW = datetime.now(UTC).year


@dataclass(frozen=True)
class DirectorySource:
    name: str
    url: str
    item_selector: str = ""
    name_selector: str = ""
    url_selector: str = "a[href]"
    address_selector: str = ""
    next_selector: str = "a[rel='next'], a.next, .pagination a.next"
    max_pages: int = 8


DEFAULT_SOURCES = (
    DirectorySource(
        name="AWWA Sourcebook - wastewater suppliers",
        url="https://sourcebook.awwa.org/wastewater-supplier/",
        item_selector="article, .listing, .directory-item, .company-listing",
    ),
    DirectorySource(
        name="WWEMA member directory",
        url="https://wwema.org/companies/",
        item_selector="article, .member, .company, .directory-item",
    ),
    DirectorySource(
        name="Water & Wastewater News company directory",
        url="https://www.waterwastewaterdirectory.com/company",
        item_selector="article, .company, .listing, .directory-item",
    ),
    DirectorySource(
        name="ACWA associate directory",
        url="https://www.acwa.com/about/directory/associates/",
        item_selector="article, .directory-item, .member, .card",
    ),
    DirectorySource(
        name="MMSD approved contractors",
        url="https://www.mmsd.com/government-business/rules-regulations/private-property-i-i/approved-contractors",
        item_selector="tr, article, .contractor, .card",
    ),
    DirectorySource(
        name="Kitsap County sewer contractors",
        url="https://www.kitsap.gov/pw/Pages/Sewer-Contractors.aspx",
        item_selector="tr, article, .contractor, .card",
    ),
)


def build_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse("https://" + url.strip())
    host = parsed.netloc.lower().split("@")[(-1)]
    host = host.removeprefix("www.")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunparse((parsed.scheme or "https", host, path.rstrip("/") or "/", "", "", ""))


def registrable_domain(url: str) -> str:
    host = urlparse(canonicalize_url(url)).netloc
    return host.removeprefix("www.")


def normalize_name(name: str) -> str:
    value = re.sub(r"\s+", " ", (name or "")).strip(" -|,;:")
    value = re.sub(r"\b(?:incorporated|corporation|company|limited)\b", "", value, flags=re.I)
    value = re.sub(r"\b(?:inc|corp|co|llc|ltd)\.?\b", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" -|,;:")
    return value.title()


def robots_allows(session: requests.Session, url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = session.get(robots_url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            return True
        response.raise_for_status()
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except requests.RequestException as exc:
        LOGGER.warning("robots.txt check failed for %s: %s; skipping for safety", url, exc)
        return False


def get_html(session: requests.Session, url: str, delay_seconds: float) -> str:
    if not robots_allows(session, url):
        LOGGER.warning("robots.txt does not permit or could not verify access: %s", url)
        return ""
    time.sleep(max(delay_seconds, 0.0))
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "html" not in content_type and "xhtml" not in content_type:
        LOGGER.warning("Skipping non-HTML response %s (%s)", url, content_type)
        return ""
    return response.text


def first_text(node: BeautifulSoup, selector: str) -> str:
    if not selector:
        return ""
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else ""


def first_link(node: BeautifulSoup, selector: str, base_url: str) -> str:
    for found in node.select(selector or "a[href]"):
        href = found.get("href", "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme in {"http", "https"}:
            return canonicalize_url(absolute)
    return ""


def parse_json_ld(soup: BeautifulSoup, source: DirectorySource, page_url: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for tag in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(tag.string or tag.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        expanded: list[dict] = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                expanded.extend(x for x in item["@graph"] if isinstance(x, dict))
            elif isinstance(item, dict):
                expanded.append(item)
        for item in expanded:
            item_type = item.get("@type", "")
            types = {item_type} if isinstance(item_type, str) else set(item_type or [])
            if not types.intersection({"Organization", "LocalBusiness", "ProfessionalService", "Corporation"}):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            address = item.get("address", "")
            if isinstance(address, dict):
                address = ", ".join(
                    str(address.get(key, "")).strip()
                    for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode")
                    if address.get(key)
                )
            records.append(
                {
                    "company_name": name,
                    "company_url": canonicalize_url(str(item.get("url", ""))),
                    "address": str(address),
                    "directory_source": source.name,
                    "directory_page": page_url,
                    "directory_text": " ".join(str(item.get(k, "")) for k in ("description", "telephone")),
                }
            )
    return records


def generic_item_nodes(soup: BeautifulSoup) -> list[BeautifulSoup]:
    selectors = (
        "article",
        ".directory-item",
        ".company-listing",
        ".member-listing",
        ".listing-item",
        ".card",
        "main table tr",
    )
    for selector in selectors:
        nodes = soup.select(selector)
        if len(nodes) >= 3:
            return nodes
    return []


def parse_directory_page(
    html: str,
    source: DirectorySource,
    page_url: str,
) -> tuple[list[dict[str, str]], str]:
    soup = BeautifulSoup(html, "lxml")
    records = parse_json_ld(soup, source, page_url)
    nodes = soup.select(source.item_selector) if source.item_selector else []
    if len(nodes) < 3:
        nodes = generic_item_nodes(soup)

    for node in nodes:
        text = node.get_text(" ", strip=True)
        if len(text) < 3:
            continue
        name = first_text(node, source.name_selector) if source.name_selector else ""
        link = first_link(node, source.url_selector, page_url)
        if not name:
            heading = node.select_one("h2, h3, h4, strong, .name, .title")
            if heading:
                name = heading.get_text(" ", strip=True)
            elif link:
                anchor = node.select_one(source.url_selector or "a[href]")
                name = anchor.get_text(" ", strip=True) if anchor else ""
            elif node.name == "tr":
                cells = node.find_all(["th", "td"])
                name = cells[0].get_text(" ", strip=True) if cells else ""
        address = first_text(node, source.address_selector) if source.address_selector else ""
        if not address:
            address_node = node.select_one("address, .address, [itemprop='address']")
            address = address_node.get_text(" ", strip=True) if address_node else ""

        clean_name = normalize_name(name)
        if len(clean_name) < 3 or len(clean_name) > 120:
            continue
        if clean_name.lower() in {"view profile", "learn more", "details", "website"}:
            continue
        records.append(
            {
                "company_name": clean_name,
                "company_url": link,
                "address": address,
                "directory_source": source.name,
                "directory_page": page_url,
                "directory_text": text[:2_000],
            }
        )

    next_url = ""
    for next_link in soup.select(source.next_selector):
        href = next_link.get("href", "")
        if href:
            next_url = canonicalize_url(urljoin(page_url, href))
            break
    return records, next_url


def scrape_source(
    session: requests.Session,
    source: DirectorySource,
    delay_seconds: float,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen_pages: set[str] = set()
    page_url = canonicalize_url(source.url)

    for _ in range(source.max_pages):
        if not page_url or page_url in seen_pages:
            break
        seen_pages.add(page_url)
        LOGGER.info("Scraping %s: %s", source.name, page_url)
        try:
            html = get_html(session, page_url, delay_seconds)
        except requests.RequestException as exc:
            LOGGER.warning("Source failed %s: %s", page_url, exc)
            break
        if not html:
            break
        page_records, next_url = parse_directory_page(html, source, page_url)
        records.extend(page_records)
        page_url = next_url
    return records


def extract_first_int(patterns: Iterable[re.Pattern[str]], text: str) -> int | None:
    values: list[int] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            try:
                values.append(int(match.group(1)))
            except (ValueError, IndexError):
                continue
    return min(values) if values else None


def extract_founding_year(text: str) -> int | None:
    years: list[int] = []
    for pattern in FOUNDING_PATTERNS:
        for match in pattern.finditer(text):
            year = int(match.group(1))
            if 1800 <= year <= YEAR_NOW:
                years.append(year)
    return min(years) if years else None


def internal_link_count(soup: BeautifulSoup, base_url: str) -> int:
    base_domain = registrable_domain(base_url)
    paths: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(base_url, anchor.get("href", ""))
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if registrable_domain(href) != base_domain:
            continue
        if parsed.path.lower().endswith((".pdf", ".jpg", ".png", ".zip")):
            continue
        paths.add(parsed.path.rstrip("/") or "/")
    return len(paths)


def enrich_company(session: requests.Session, row: dict[str, str], delay_seconds: float) -> dict[str, object]:
    result: dict[str, object] = dict(row)
    url = canonicalize_url(str(row.get("company_url", "")))
    if not url:
        result.update({"website_status": "missing", "enrichment_error": "No company URL"})
        return result
    try:
        html = get_html(session, url, delay_seconds)
        if not html:
            result.update({"website_status": "blocked", "enrichment_error": "robots or non-HTML"})
            return result
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)[:MAX_SITE_TEXT_CHARS]
        lower_text = text.lower()
        service_hits = sorted(keyword for keyword in SERVICE_KEYWORDS if keyword in lower_text)
        social_domains = {
            registrable_domain(anchor.get("href", ""))
            for anchor in soup.select("a[href]")
            if any(
                social in anchor.get("href", "").lower()
                for social in ("linkedin.com", "facebook.com", "instagram.com", "youtube.com", "x.com", "twitter.com")
            )
        }
        emails = sorted(set(EMAIL_RE.findall(text)))
        phones = sorted(set(PHONE_RE.findall(text)))
        founding_year = extract_founding_year(text)
        employees = extract_first_int(EMPLOYEE_PATTERNS, text)
        technicians = extract_first_int(TECHNICIAN_PATTERNS, text)
        fleet = extract_first_int(FLEET_PATTERNS, text)
        if technicians is None and fleet is not None:
            technicians = max(1, round(fleet * 1.1))
        if employees is None and technicians is not None:
            employees = max(technicians, round(technicians * 1.5))

        result.update(
            {
                "company_url": url,
                "domain": registrable_domain(url),
                "website_status": "ok",
                "https": urlparse(url).scheme == "https",
                "founding_year": founding_year,
                "company_age": YEAR_NOW - founding_year if founding_year else None,
                "employee_count_est": employees,
                "technician_count_est": technicians,
                "fleet_count_est": fleet,
                "service_keyword_count": len(service_hits),
                "service_keywords": "; ".join(service_hits),
                "internal_page_count": internal_link_count(soup, url),
                "social_profile_count": len({d for d in social_domains if d}),
                "email_found": bool(emails),
                "phone_found": bool(phones),
                "contact_page_found": any(
                    token in lower_text for token in ("contact us", "request a quote", "get in touch")
                ),
                "careers_page_found": "careers" in lower_text or "join our team" in lower_text,
                "primary_email": emails[0] if emails else "",
                "primary_phone": phones[0] if phones else "",
                "enrichment_error": "",
            }
        )
    except requests.RequestException as exc:
        result.update({"website_status": "error", "enrichment_error": str(exc)[:300]})
    return result


def age_score(age: float | int | None) -> float:
    if age is None or pd.isna(age):
        return 10.0
    age = float(age)
    if age < 5:
        return 4.0
    if age < 10:
        return 10.0
    if age < 20:
        return 22.0
    if age < 40:
        return 30.0
    return 35.0


def workforce_score(technicians: float | None, employees: float | None) -> float:
    value = technicians if technicians is not None and not pd.isna(technicians) else None
    if value is None and employees is not None and not pd.isna(employees):
        value = float(employees) * 0.65
    if value is None:
        return 12.0
    value = float(value)
    if value < 3:
        return 7.0
    if value < 8:
        return 20.0 + (value - 3) * 2.0
    if value <= 35:
        return 40.0
    if value <= 60:
        return 40.0 - (value - 35) * 0.4
    if value <= 100:
        return 30.0 - (value - 60) * 0.25
    return 15.0


def digital_whitespace_score(row: pd.Series) -> float:
    """Reward credible but under-optimized web presence, not maximum marketing polish."""
    if row.get("website_status") != "ok":
        return 2.0
    score = 5.0
    score += 2.0 if bool(row.get("https")) else 0.0
    score += 2.0 if bool(row.get("phone_found")) else 0.0
    score += 2.0 if bool(row.get("email_found")) else 0.0
    score += 2.0 if bool(row.get("contact_page_found")) else 0.0

    pages = float(row.get("internal_page_count") or 0)
    if 2 <= pages <= 15:
        score += 7.0
    elif pages <= 40:
        score += 5.0
    elif pages > 40:
        score += 2.0

    social = float(row.get("social_profile_count") or 0)
    if social <= 2:
        score += 5.0
    elif social <= 4:
        score += 3.0
    else:
        score += 1.0
    return min(score, 25.0)


def apply_scoring(df: pd.DataFrame) -> pd.DataFrame:
    scored = df.copy()
    scored["age_score"] = scored.get("company_age", pd.Series(index=scored.index, dtype=float)).map(age_score)
    scored["workforce_score"] = scored.apply(
        lambda r: workforce_score(r.get("technician_count_est"), r.get("employee_count_est")), axis=1
    )
    scored["digital_whitespace_score"] = scored.apply(digital_whitespace_score, axis=1)
    scored["priority_score"] = (
        scored["age_score"] + scored["workforce_score"] + scored["digital_whitespace_score"]
    ).round(1)
    scored["data_confidence"] = (
        scored[["founding_year", "employee_count_est", "technician_count_est"]]
        .notna()
        .sum(axis=1)
        .mul(20)
        + scored.get("website_status", "").eq("ok").mul(25)
        + scored.get("service_keyword_count", 0).gt(0).mul(15)
    ).clip(upper=100)
    return scored.sort_values(["priority_score", "data_confidence"], ascending=False)


def load_sources(path: str | None) -> tuple[DirectorySource, ...]:
    if not path:
        return DEFAULT_SOURCES
    frame = pd.read_csv(path).fillna("")
    required = {"name", "url"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"source config is missing columns: {sorted(missing)}")
    sources = []
    for row in frame.to_dict(orient="records"):
        sources.append(
            DirectorySource(
                name=row["name"],
                url=row["url"],
                item_selector=row.get("item_selector", ""),
                name_selector=row.get("name_selector", ""),
                url_selector=row.get("url_selector", "a[href]") or "a[href]",
                address_selector=row.get("address_selector", ""),
                next_selector=row.get("next_selector", "a[rel='next'], a.next, .pagination a.next")
                or "a[rel='next'], a.next, .pagination a.next",
                max_pages=int(row.get("max_pages") or 8),
            )
        )
    return tuple(sources)


def clean_and_deduplicate(records: list[dict[str, str]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records).fillna("")
    frame["company_name"] = frame["company_name"].map(normalize_name)
    frame["company_url"] = frame["company_url"].map(canonicalize_url)
    frame["domain"] = frame["company_url"].map(registrable_domain)
    frame["combined_text"] = (
        frame["company_name"] + " " + frame["directory_text"] + " " + frame["address"]
    ).str.lower()
    frame = frame[~frame["combined_text"].apply(lambda text: any(x in text for x in EXCLUDE_KEYWORDS))]
    frame = frame[frame["company_name"].str.len().between(3, 120)]
    frame["dedupe_key"] = frame.apply(
        lambda r: r["domain"] or re.sub(r"\W+", "", r["company_name"].lower()), axis=1
    )
    frame = frame.sort_values("company_url", key=lambda s: s.eq(""))
    frame = frame.drop_duplicates("dedupe_key", keep="first")
    return frame.drop(columns=["combined_text", "dedupe_key"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="targets.csv")
    parser.add_argument("--source-config", default=None)
    parser.add_argument("--min-targets", type=int, default=50)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    session = build_session()
    sources = load_sources(args.source_config)

    raw_records: list[dict[str, str]] = []
    for source in sources:
        raw_records.extend(scrape_source(session, source, args.delay_seconds))

    cleaned = clean_and_deduplicate(raw_records)
    if cleaned.empty:
        raise RuntimeError(
            "No records were collected. Check robots.txt, selectors, directory terms, and network access."
        )

    LOGGER.info("Collected %s unique directory records; enriching websites", len(cleaned))
    rows = cleaned.to_dict(orient="records")
    enriched: list[dict[str, object]] = []
    with futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(enrich_company, build_session(), row, args.delay_seconds): row
            for row in rows
        }
        for future in futures.as_completed(future_map):
            try:
                enriched.append(future.result())
            except Exception as exc:  # last-resort isolation; individual targets should not kill the run
                failed = dict(future_map[future])
                failed.update({"website_status": "error", "enrichment_error": str(exc)[:300]})
                enriched.append(failed)

    scored = apply_scoring(pd.DataFrame(enriched))
    scored["scraped_at_utc"] = datetime.now(UTC).isoformat()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output, index=False)

    print(f"Wrote {len(scored):,} unique targets to {output}")
    if len(scored) < args.min_targets:
        print(
            f"WARNING: target count is below {args.min_targets}. Add permitted regional directories "
            "through --source-config and re-run."
        )
    preview_cols = [
        "company_name",
        "company_url",
        "company_age",
        "technician_count_est",
        "employee_count_est",
        "priority_score",
        "data_confidence",
        "directory_source",
    ]
    print(scored[[c for c in preview_cols if c in scored.columns]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
