"""Deterministic offline fixtures for the sourcing pipeline.

Every company in this file is FICTIONAL. Domains live in the reserved
``example.com`` space and never resolve, and nothing here is a claim about any
real company. The dataset exists so reviewers and the automated test suite can
exercise the entire pipeline — directory parsing, normalization, deduplication,
enrichment, scoring, and export — with zero network access.

``build_offline_dataset()`` returns:
    records  : directory-level raw records (pre-dedup, pre-enrichment)
    pages    : canonical company URL -> synthetic HTML (served by OfflineFetcher)
    blocked  : URLs treated as robots-denied / non-HTML (enrichment -> "blocked")
    errors   : URLs that raise a transport error (enrichment -> "error")
"""

from __future__ import annotations

import re
from typing import Any

PRIMARY_COUNT = 54
DIRECTORY_SOURCE = "Offline Synthetic Directory"

_ADJ = [
    "Summit", "Cascade", "Ironwood", "Blue Heron", "Granite", "Riverbend",
    "Copper", "Sterling", "Meridian", "Harbor", "Prairie", "Cedar", "Vantage",
    "Keystone", "Anchor", "Pioneer", "Redwood", "Northgate", "Clearwater",
    "Trailhead",
]
_NOUN = [
    "Water", "Sewer", "Pipeline", "Utility", "Underground", "Aqua",
    "Drainage", "Hydro", "Wastewater", "Infrastructure",
]
_KIND = ["Services", "Solutions", "Contractors", "Group", "Partners", "Systems"]

_STREET = [
    "Main", "Oak", "Cedar", "Riverside", "Industrial", "Commerce", "Lakeview",
    "Millpond", "Foundry", "Harborview",
]
_STREET_TYPE = ["Street", "Avenue", "Road", "Boulevard", "Drive"]
_CITY = [
    "Ashford", "Brookline", "Cedar Falls", "Dryden", "Fairhaven", "Glenwood",
    "Hartley", "Kingsport", "Lakemont", "Millbrook", "Norwood", "Parkdale",
]
_STATE = ["WA", "OR", "ID", "MT", "CO", "UT"]

# Display phrases chosen so each embeds a SERVICE_KEYWORDS substring the
# enrichment regexes will match (e.g. "CCTV inspection" -> "cctv").
_SERVICE_DISPLAY = [
    "CCTV inspection",
    "sewer cleaning",
    "leak detection",
    "hydrant flushing",
    "manhole rehabilitation",
    "smoke testing",
    "utility locating",
    "valve exercising",
]

_PHONE_FORMATS = [
    "({a}) {b}-{c}",
    "{a}.{b}.{c}",
    "{a}-{b}-{c}",
    "+1 {a} {b} {c}",
]


def _names() -> list[str]:
    """Deterministic, guaranteed-unique company names (unique name slugs)."""
    names: list[str] = []
    seen: set[str] = set()
    for i in range(PRIMARY_COUNT):
        base = f"{_ADJ[i % len(_ADJ)]} {_NOUN[(i // 3) % len(_NOUN)]} {_KIND[(i // 5) % len(_KIND)]}"
        candidate = base
        counter = 2
        while re.sub(r"\W+", "", candidate.lower()) in seen:
            candidate = f"{base} {counter}"
            counter += 1
        seen.add(re.sub(r"\W+", "", candidate.lower()))
        names.append(candidate)
    return names


NAMES = _names()


def domain_for(i: int) -> str:
    return f"target{i:02d}.example.com"


def url_for(i: int, https: bool = True) -> str:
    scheme = "https" if https else "http"
    return f"{scheme}://{domain_for(i)}/"


def address_for(i: int) -> str:
    return (
        f"{100 + i * 3} {_STREET[i % len(_STREET)]} {_STREET_TYPE[i % len(_STREET_TYPE)]}, "
        f"{_CITY[i % len(_CITY)]}, {_STATE[i % len(_STATE)]} {90000 + i}"
    )


def phone_for(i: int) -> str:
    fmt = _PHONE_FORMATS[i % len(_PHONE_FORMATS)]
    return fmt.format(a="206", b="555", c=f"{100 + i:04d}")


def _company_spec(i: int) -> dict[str, Any]:
    """Deterministic per-company attributes exercising each enrichment path."""
    missing = i % 9 == 0            # everything-missing archetype (low confidence)
    fleet_only = (i % 3 == 0) and not missing  # forces fleet -> technician inference
    services = [] if missing else _SERVICE_DISPLAY[: (i % 6) + 1]
    return {
        "name": NAMES[i],
        "https": i % 11 != 0,
        "founded": None if missing else 2024 - ((i * 7) % 60),
        "employees": (30 + (i % 25)) if (i % 4 == 0 and not missing) else None,
        "technicians": None if (missing or fleet_only) else (3 + (i % 40)),
        "fleet": (5 + (i % 20)) if fleet_only else None,
        "services": services,
        "socials": i % 5,
        "has_contact": i % 2 == 0,
        "has_careers": i % 6 == 0,
        "phone": "" if missing else phone_for(i),
        "address": address_for(i),
    }


def render_company_html(spec: dict[str, Any]) -> str:
    name = str(spec["name"])
    parts: list[str] = [f"<html><head><title>{name}</title></head><body>", f"<h1>{name}</h1>"]

    services = list(spec["services"]) if spec["services"] else []
    if services:
        items = "".join(f"<li>{service}</li>" for service in services)
        parts.append(f"<section id='services'><h2>Field Services</h2><ul>{items}</ul></section>")

    if spec["founded"] is not None:
        parts.append(f"<p>Founded in {spec['founded']}, we serve regional utilities.</p>")
    if spec["employees"] is not None:
        parts.append(f"<p>Our team of {spec['employees']} employees supports every route.</p>")
    if spec["technicians"] is not None:
        parts.append(f"<p>We field over {spec['technicians']} field technicians daily.</p>")
    if spec["fleet"] is not None:
        parts.append(f"<p>We operate a fleet of {spec['fleet']} service vehicles.</p>")

    social_links = [
        "https://linkedin.com/company/example",
        "https://facebook.com/example",
        "https://instagram.com/example",
        "https://youtube.com/example",
    ]
    for link in social_links[: int(spec["socials"])]:
        parts.append(f"<a href='{link}'>social</a>")

    # A few internal pages so internal_link_count lands in the credible band.
    for slug in ("about", "services", "coverage", "safety"):
        parts.append(f"<a href='/{slug}'>{slug}</a>")

    if spec["phone"]:
        parts.append(f"<p>Call us: {spec['phone']}</p>")
    parts.append(f"<p>Email info@{name.split()[0].lower()}.example.com</p>")
    if spec["has_contact"]:
        parts.append("<p>Contact us for a quote.</p>")
    if spec["has_careers"]:
        parts.append("<p>Careers: join our team.</p>")

    parts.append("</body></html>")
    html = "".join(parts)
    # Deterministically emit a mildly malformed page for a slice of companies;
    # BeautifulSoup/lxml must parse it without raising.
    if int(str(spec.get("_index", 0))) % 13 == 0:
        html = html.replace("</section>", "").replace("</ul>", "")
    return html


def build_offline_dataset() -> tuple[list[dict[str, str]], dict[str, str], set[str], set[str]]:
    records: list[dict[str, str]] = []
    pages: dict[str, str] = {}
    blocked: set[str] = set()
    errors: set[str] = set()

    for i in range(PRIMARY_COUNT):
        spec = _company_spec(i)
        spec["_index"] = i
        url = url_for(i, https=bool(spec["https"]))
        service_text = ", ".join(str(s) for s in spec["services"]) or "field services"
        records.append(
            {
                "company_name": str(spec["name"]),
                "company_url": url,
                "address": str(spec["address"]),
                "directory_source": DIRECTORY_SOURCE,
                "directory_page": f"https://directory.example.com/page/{i // 20 + 1}",
                "directory_text": f"{spec['name']} — {service_text}.",
            }
        )

        if i != 0 and i % 17 == 0:
            blocked.add(url)          # robots-denied / non-HTML: no page served
        elif i != 0 and i % 19 == 0:
            errors.add(url)           # transport failure: no page served
        else:
            pages[url] = render_company_html(spec)

    # Intentional duplicates so the merge/audit trail is exercised end to end.
    # (a) same registrable domain as company 3, different display name -> domain merge.
    dup_three = dict(records[3])
    dup_three["company_name"] = "Summit Regional Sewer Partners"
    records.append(dup_three)
    # (b) same normalized name + reformatted address as company 7, no URL -> name/address merge.
    variant_seven = {
        "company_name": NAMES[7].upper() + ", LLC",
        "company_url": "",
        "address": address_for(7).replace("Street", "St").replace("Avenue", "Ave"),
        "directory_source": DIRECTORY_SOURCE,
        "directory_page": "https://directory.example.com/page/variants",
        "directory_text": f"{NAMES[7]} regional listing.",
    }
    records.append(variant_seven)

    return records, pages, blocked, errors
