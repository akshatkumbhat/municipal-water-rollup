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
import os
import re
import time
import urllib.robotparser
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse, urlunparse

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger("water_rollup_sourcing")
CONTACT_ENV_VAR = "COPPERLINE_SCRAPER_CONTACT"
PLACEHOLDER_CONTACT = "compliance-contact@example.invalid"
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
    re.compile(
        r"(?:founded|established|since|serving .* since)\D{0,18}(18\d{2}|19\d{2}|20[0-2]\d)", re.I
    ),
    re.compile(r"\b(18\d{2}|19\d{2}|20[0-2]\d)\b.{0,20}(?:founded|established)", re.I),
)
EMPLOYEE_PATTERNS = (
    re.compile(
        r"\b(?:team of|more than|over|approximately|about)?\s*(\d{1,4})\+?\s+(?:employees|people|staff members)\b",
        re.I,
    ),
)
TECHNICIAN_PATTERNS = (
    re.compile(
        r"\b(?:team of|more than|over|approximately|about)?\s*(\d{1,3})\+?\s+(?:technicians|field technicians|operators|crews)\b",
        re.I,
    ),
)
FLEET_PATTERNS = (
    re.compile(
        r"\b(?:fleet of|more than|over|approximately|about)?\s*(\d{1,3})\+?\s+(?:trucks|service vehicles|vans)\b",
        re.I,
    ),
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


def scraper_contact() -> str:
    """Contact address advertised to the sites this pipeline visits.

    Site operators use it to reach whoever is running the crawler, so it must be
    a monitored address. It is read from the environment rather than hardcoded;
    the placeholder resolves to a reserved `.invalid` domain that cannot receive
    mail, and using it is a configuration error rather than a silent default.
    """
    return os.environ.get(CONTACT_ENV_VAR, "").strip() or PLACEHOLDER_CONTACT


def user_agent() -> str:
    return f"LongDurationHoldCoResearch/1.0 (+{scraper_contact()})"


def build_session() -> requests.Session:
    if scraper_contact() == PLACEHOLDER_CONTACT:
        LOGGER.warning(
            "No scraper contact configured: requests will advertise %s, which cannot "
            "receive mail. Set %s to a monitored address before visiting any site you "
            "do not control. Offline paths (--offline-demo, fixtures) do not need this.",
            PLACEHOLDER_CONTACT,
            CONTACT_ENV_VAR,
        )
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
            "User-Agent": user_agent(),
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
    # Collapse whitespace and trim orphaned punctuation left by suffix removal
    # (e.g. "Acme Services, Inc." -> the trailing ", ." is stripped).
    value = re.sub(r"\s+", " ", value).strip(" -|,;:.")
    return value.title()


def name_slug(name: str) -> str:
    """Alphanumeric-only lowercase key used for name-collision dedup."""
    return re.sub(r"\W+", "", normalize_name(name).lower())


def attr_str(tag: Tag, name: str) -> str:
    """Read a BeautifulSoup attribute as a plain string.

    bs4 types multi-valued attributes (e.g. ``class``) as lists, so ``tag.get``
    returns ``str | list[str] | None``. Collapse every shape to a stripped str.
    """
    value = tag.get(name)
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return " ".join(str(part) for part in value).strip()


def canonical_phone(value: str | None) -> str:
    """Return a NANP phone as ``+1XXXXXXXXXX``; empty string if not parseable.

    US/North-American numbering only (matches ``PHONE_RE``); international
    numbers are intentionally out of scope and return "".
    """
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return "+1" + digits


def normalize_phone(value: str | None) -> str:
    """Human-readable canonical form ``(XXX) XXX-XXXX``; "" if not parseable."""
    canonical = canonical_phone(value)
    if not canonical:
        return ""
    d = canonical[2:]
    return f"({d[0:3]}) {d[3:6]}-{d[6:10]}"


_STREET_ABBREVIATIONS = {
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "drive": "dr",
    "road": "rd",
    "lane": "ln",
    "suite": "ste",
    "highway": "hwy",
    "parkway": "pkwy",
}


def normalize_address(value: str | None) -> str:
    """Collapse whitespace/punctuation and standardize common street suffixes.

    Produces a stable lowercase key so the same physical address written in
    different formats compares equal. Deterministic; no geocoding.
    """
    text = re.sub(r"[.,;]+", " ", (value or "").lower())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    tokens = [_STREET_ABBREVIATIONS.get(token, token) for token in text.split(" ")]
    return " ".join(tokens)


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
        return parser.can_fetch(user_agent(), url)
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


class HtmlFetcher(Protocol):
    """Seam that returns page HTML for a URL.

    Returns "" when a page is unavailable for compliance reasons (robots.txt
    denial, non-HTML content); raises ``requests.RequestException`` on transport
    failure. This lets scraping, enrichment, and tests share one code path while
    keeping all live-network behavior (and its offline substitute) behind a
    single interface.
    """

    def fetch(self, url: str) -> str: ...


class NetworkFetcher:
    """Live fetcher: preserves robots.txt enforcement, rate-limit delay, retries."""

    def __init__(self, session: requests.Session, delay_seconds: float) -> None:
        self._session = session
        self._delay_seconds = delay_seconds

    def fetch(self, url: str) -> str:
        return get_html(self._session, url, self._delay_seconds)


class OfflineFetcher:
    """Deterministic fetcher backed by in-memory fixtures — no network at all.

    * ``pages``: canonical URL -> HTML, served directly.
    * ``blocked``: URLs treated as robots-denied / non-HTML (return "").
    * ``errors``: URLs that raise ``requests.RequestException`` (transport failure).
    """

    def __init__(
        self,
        pages: dict[str, str],
        blocked: Iterable[str] = (),
        errors: Iterable[str] = (),
    ) -> None:
        self._pages = {canonicalize_url(url): html for url, html in pages.items()}
        self._blocked = {canonicalize_url(url) for url in blocked}
        self._errors = {canonicalize_url(url) for url in errors}

    def fetch(self, url: str) -> str:
        key = canonicalize_url(url)
        if key in self._errors:
            raise requests.RequestException(f"synthetic fetch failure for {key}")
        if key in self._blocked:
            return ""
        return self._pages.get(key, "")


def first_text(node: Tag, selector: str) -> str:
    if not selector:
        return ""
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else ""


def first_link(node: Tag, selector: str, base_url: str) -> str:
    for found in node.select(selector or "a[href]"):
        href = attr_str(found, "href")
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme in {"http", "https"}:
            return canonicalize_url(absolute)
    return ""


def parse_json_ld(
    soup: BeautifulSoup, source: DirectorySource, page_url: str
) -> list[dict[str, str]]:
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
            if not types.intersection(
                {"Organization", "LocalBusiness", "ProfessionalService", "Corporation"}
            ):
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
                    "directory_text": " ".join(
                        str(item.get(k, "")) for k in ("description", "telephone")
                    ),
                }
            )
    return records


def generic_item_nodes(soup: BeautifulSoup) -> list[Tag]:
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
        nodes = list(soup.select(selector))
        if len(nodes) >= 3:
            return nodes
    return []


def parse_directory_page(
    html: str,
    source: DirectorySource,
    page_url: str,
) -> tuple[list[dict[str, str]], str]:
    soup = BeautifulSoup(html, "html.parser")
    records = parse_json_ld(soup, source, page_url)
    nodes: list[Tag] = list(soup.select(source.item_selector)) if source.item_selector else []
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
        href = attr_str(next_link, "href")
        if href:
            next_url = canonicalize_url(urljoin(page_url, href))
            break
    return records, next_url


def scrape_source(fetcher: HtmlFetcher, source: DirectorySource) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen_pages: set[str] = set()
    page_url = canonicalize_url(source.url)

    for _ in range(source.max_pages):
        if not page_url or page_url in seen_pages:
            break
        seen_pages.add(page_url)
        LOGGER.info("Scraping %s: %s", source.name, page_url)
        try:
            html = fetcher.fetch(page_url)
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
        href = urljoin(base_url, attr_str(anchor, "href"))
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if registrable_domain(href) != base_domain:
            continue
        if parsed.path.lower().endswith((".pdf", ".jpg", ".png", ".zip")):
            continue
        paths.add(parsed.path.rstrip("/") or "/")
    return len(paths)


def enrich_company(fetcher: HtmlFetcher, row: dict[str, Any], verified_on: str) -> dict[str, Any]:
    """Visit the company website and attach enrichment + provenance fields.

    Provenance is preserved end to end: directory fields from ``row`` are kept,
    and every returned record carries ``verification_date`` (when the site was
    checked) plus ``address_normalized``. Extracted figures are accompanied by an
    ``evidence_summary`` so no estimate is presented without a trace.
    """
    result: dict[str, Any] = dict(row)
    result["verification_date"] = verified_on
    result["address_normalized"] = normalize_address(str(row.get("address", "")))
    url = canonicalize_url(str(row.get("company_url", "")))
    if not url:
        result.update(
            {
                "website_status": "missing",
                "enrichment_error": "No company URL",
                "evidence_summary": "",
            }
        )
        return result
    try:
        html = fetcher.fetch(url)
        if not html:
            result.update(
                {
                    "website_status": "blocked",
                    "enrichment_error": "robots or non-HTML",
                    "evidence_summary": "",
                }
            )
            return result
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)[:MAX_SITE_TEXT_CHARS]
        lower_text = text.lower()
        service_hits = sorted(keyword for keyword in SERVICE_KEYWORDS if keyword in lower_text)
        social_domains = {
            registrable_domain(attr_str(anchor, "href"))
            for anchor in soup.select("a[href]")
            if any(
                social in attr_str(anchor, "href").lower()
                for social in (
                    "linkedin.com",
                    "facebook.com",
                    "instagram.com",
                    "youtube.com",
                    "x.com",
                    "twitter.com",
                )
            )
        }
        emails = sorted(set(EMAIL_RE.findall(text)))
        phones = sorted(set(PHONE_RE.findall(text)))
        founding_year = extract_founding_year(text)
        employees = extract_first_int(EMPLOYEE_PATTERNS, text)
        technicians = extract_first_int(TECHNICIAN_PATTERNS, text)
        fleet = extract_first_int(FLEET_PATTERNS, text)
        workforce_evidence = "reported"
        if technicians is None and fleet is not None:
            technicians = max(1, round(fleet * 1.1))
            workforce_evidence = "inferred from fleet"
        if employees is None and technicians is not None:
            employees = max(technicians, round(technicians * 1.5))

        evidence_parts: list[str] = []
        if founding_year is not None:
            evidence_parts.append(f"founding_year={founding_year} (site text)")
        if technicians is not None:
            evidence_parts.append(f"technicians={technicians} ({workforce_evidence})")
        if employees is not None:
            evidence_parts.append(f"employees={employees}")
        if service_hits:
            evidence_parts.append("services=" + ",".join(service_hits[:6]))

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
                    token in lower_text
                    for token in ("contact us", "request a quote", "get in touch")
                ),
                "careers_page_found": "careers" in lower_text or "join our team" in lower_text,
                "primary_email": emails[0] if emails else "",
                "primary_phone": normalize_phone(phones[0]) if phones else "",
                "phone_canonical": canonical_phone(phones[0]) if phones else "",
                "evidence_summary": "; ".join(evidence_parts),
                "enrichment_error": "",
            }
        )
    except requests.RequestException as exc:
        result.update(
            {"website_status": "error", "enrichment_error": str(exc)[:300], "evidence_summary": ""}
        )
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
    scored["age_score"] = scored.get("company_age", pd.Series(index=scored.index, dtype=float)).map(
        age_score
    )
    scored["workforce_score"] = scored.apply(
        lambda r: workforce_score(r.get("technician_count_est"), r.get("employee_count_est")),
        axis=1,
    )
    scored["digital_whitespace_score"] = scored.apply(digital_whitespace_score, axis=1)
    scored["priority_score"] = (
        scored["age_score"] + scored["workforce_score"] + scored["digital_whitespace_score"]
    ).round(1)
    # data_confidence weights (20 per present figure / 25 website / 15 services)
    # are unchanged; this rebuild only guards against missing columns, which
    # previously raised AttributeError on a directory-only frame.
    evidence_cols = ["founding_year", "employee_count_est", "technician_count_est"]
    present = scored.reindex(columns=evidence_cols).notna().sum(axis=1)
    if "website_status" in scored.columns:
        website_ok = scored["website_status"].astype(str).eq("ok")
    else:
        website_ok = pd.Series(False, index=scored.index)
    if "service_keyword_count" in scored.columns:
        keyword_positive = (
            pd.to_numeric(scored["service_keyword_count"], errors="coerce").fillna(0).gt(0)
        )
    else:
        keyword_positive = pd.Series(False, index=scored.index)
    scored["data_confidence"] = (
        present.mul(20) + website_ok.mul(25) + keyword_positive.mul(15)
    ).clip(upper=100)
    return order_scored_targets(scored)


# The documented total ordering for scored targets. Score and confidence alone
# leave ties unresolved, so their relative order fell out of thread-pool
# completion order and changed between runs. Normalized name and then a stable
# source identifier make the order total, so repeated runs — concurrent or not
# — produce byte-identical output. No score is recomputed by ordering.
TARGET_ORDER_COLUMNS = ("priority_score", "data_confidence", "_name_key", "_source_key")
TARGET_ORDER_ASCENDING = (False, False, True, True)


def order_scored_targets(scored: pd.DataFrame) -> pd.DataFrame:
    """Impose the documented total order on scored targets.

    Ordering is, in sequence:

    1. `priority_score` descending — the scoring methodology decides first;
    2. `data_confidence` descending — better-evidenced records rank higher;
    3. normalized company name ascending — a deterministic, human-meaningful
       tiebreak once the methodology has stopped discriminating;
    4. registrable domain then source URL ascending — a stable identifier that
       separates records which normalize to the same name.

    Blueprint technician-band preference is deliberately *not* applied here:
    it is candidate-selection logic, not a property of the scored universe.
    `candidate_package.select_candidate` layers it on top of this order.
    """
    if scored.empty:
        return scored.copy()

    working = scored.copy()
    names = working.get("company_name", pd.Series("", index=working.index, dtype=object))
    working["_name_key"] = names.fillna("").astype(str).map(normalize_name)

    domains = working.get("domain", pd.Series("", index=working.index, dtype=object))
    urls = working.get("company_url", pd.Series("", index=working.index, dtype=object))
    working["_source_key"] = domains.fillna("").astype(str) + "|" + urls.fillna("").astype(str)

    ordered = working.sort_values(
        list(TARGET_ORDER_COLUMNS), ascending=list(TARGET_ORDER_ASCENDING), kind="mergesort"
    )
    return ordered.drop(columns=["_name_key", "_source_key"]).reset_index(drop=True)


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


def clean_and_deduplicate(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize, filter, and deduplicate directory records with an audit trail.

    Two records merge when they share any non-empty signal: registrable domain,
    canonical phone, name slug, or normalized address. Merges are deterministic
    (stable union-find over the input order) and every surviving row records
    ``duplicate_count``, ``merged_from`` (the names it absorbed), and
    ``merge_reason`` (which signals matched). Nothing is dropped silently.
    """
    cleaned: list[dict[str, Any]] = []
    for source_row, raw in enumerate(records):
        row: dict[str, Any] = dict(raw)
        # Position in the caller's input list, carried through so a merge can be
        # traced to the exact source rows it absorbed. `merged_from` records
        # names, which collide: two records that normalize to the same name are
        # indistinguishable in that field. Positions never collide.
        row["source_row"] = source_row
        name = normalize_name(str(row.get("company_name", "")))
        if not 3 <= len(name) <= 120:
            continue
        combined = " ".join(
            [name, str(row.get("directory_text", "")), str(row.get("address", ""))]
        ).lower()
        if any(keyword in combined for keyword in EXCLUDE_KEYWORDS):
            continue
        url = canonicalize_url(str(row.get("company_url", "")))
        row["company_name"] = name
        row["company_url"] = url
        row["domain"] = registrable_domain(url)
        cleaned.append(row)

    if not cleaned:
        return pd.DataFrame()

    n = len(cleaned)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    key_specs: list[tuple[str, list[str]]] = [
        ("domain", [str(r.get("domain", "")) for r in cleaned]),
        (
            "phone",
            [
                canonical_phone(str(r.get("phone_canonical") or r.get("primary_phone", "")))
                for r in cleaned
            ],
        ),
        ("name", [name_slug(str(r.get("company_name", ""))) for r in cleaned]),
        ("address", [normalize_address(str(r.get("address", ""))) for r in cleaned]),
    ]

    edges: list[tuple[int, int, str]] = []
    for key_type, values in key_specs:
        first_seen: dict[str, int] = {}
        for idx, value in enumerate(values):
            if not value:
                continue
            if value in first_seen:
                edges.append((first_seen[value], idx, key_type))
            else:
                first_seen[value] = idx

    for a, b, _reason in edges:
        union(a, b)

    root_reasons: dict[int, set[str]] = defaultdict(set)
    for a, _b, reason in edges:
        root_reasons[find(a)].add(reason)

    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        groups[find(idx)].append(idx)

    survivors: list[tuple[int, dict[str, Any]]] = []
    for root, members in groups.items():
        representative = min(
            members,
            key=lambda i: (
                cleaned[i].get("company_url", "") == "",
                cleaned[i].get("domain", "") == "",
                i,
            ),
        )
        rep_row = dict(cleaned[representative])
        absorbed = sorted(str(cleaned[i]["company_name"]) for i in members if i != representative)
        rep_row["duplicate_count"] = len(members)
        rep_row["merged_from"] = "; ".join(absorbed)
        rep_row["merge_reason"] = ", ".join(sorted(root_reasons[root])) if len(members) > 1 else ""
        rep_row["merged_source_rows"] = "; ".join(
            str(cleaned[i]["source_row"])
            for i in sorted(members, key=lambda i: cleaned[i]["source_row"])
        )
        survivors.append((representative, rep_row))

    survivors.sort(key=lambda pair: pair[0])
    return pd.DataFrame([row for _index, row in survivors])


def enrich_and_score(
    fetcher: HtmlFetcher,
    cleaned: pd.DataFrame,
    workers: int,
    verified_on: str,
) -> pd.DataFrame:
    """Enrich each deduplicated record through ``fetcher`` and score the result.

    A single enrichment failure is isolated to its own row (marked ``error``) and
    never aborts the run. Shared by the live and offline paths so both exercise
    the identical enrichment/scoring code.
    """
    rows: list[dict[str, Any]] = [
        {str(key): value for key, value in record.items()}
        for record in cleaned.to_dict(orient="records")
    ]
    enriched: list[dict[str, Any]] = []
    with futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {
            executor.submit(enrich_company, fetcher, row, verified_on): row for row in rows
        }
        # Collect in SUBMISSION order, not completion order. Enrichment still
        # runs concurrently, but results are assembled deterministically. With
        # `as_completed` both the row order and the DataFrame's column order
        # (taken from whichever record happened to finish first, and blocked
        # records carry fewer keys) varied between runs and worker counts.
        for future in future_map:
            try:
                enriched.append(future.result())
            except Exception as exc:  # last-resort isolation; one target must not kill the run
                failed = dict(future_map[future])
                failed.update(
                    {
                        "website_status": "error",
                        "enrichment_error": str(exc)[:300],
                        "evidence_summary": "",
                        "verification_date": verified_on,
                    }
                )
                enriched.append(failed)
    return apply_scoring(pd.DataFrame(enriched))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="targets.csv")
    parser.add_argument("--source-config", default=None)
    parser.add_argument("--min-targets", type=int, default=50)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--offline-demo",
        action="store_true",
        help="Generate a target file from bundled synthetic fixtures with no network access.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    verified_on = date.today().isoformat()
    fetcher: HtmlFetcher
    if args.offline_demo:
        from sourcing_fixtures import build_offline_dataset

        raw_records, pages, blocked, errors = build_offline_dataset()
        fetcher = OfflineFetcher(pages, blocked, errors)
        LOGGER.info("Offline demo: %s synthetic directory records (no network)", len(raw_records))
    else:
        session = build_session()
        fetcher = NetworkFetcher(session, args.delay_seconds)
        sources = load_sources(args.source_config)
        raw_records = []
        for source in sources:
            raw_records.extend(scrape_source(fetcher, source))

    cleaned = clean_and_deduplicate(raw_records)
    if cleaned.empty:
        raise RuntimeError(
            "No records were collected. Check robots.txt, selectors, directory terms, and network access."
        )

    LOGGER.info("Collected %s unique directory records; enriching websites", len(cleaned))
    scored = enrich_and_score(fetcher, cleaned, args.workers, verified_on)
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
