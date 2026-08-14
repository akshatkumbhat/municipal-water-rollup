Implement Phase 2 — Sourcing Engine.

Build a robust, auditable target pipeline for municipal water and wastewater asset-integrity service companies.

Required capabilities:
- Adapter-friendly directory ingestion using configured CSS selectors plus JSON-LD/generic fallback.
- robots.txt check, rate limiting, retries, timeout handling, and explicit source-term warning.
- Normalization of company name, domain, URL, address, phone, and service keywords.
- Website enrichment for founding year, workforce clues, contactability, service mix, and digital footprint.
- Deterministic deduplication with a retained merge/audit trail.
- 0–100 scoring with separate age, workforce, and digital-whitespace components.
- Separate data-confidence score; missing evidence must never be presented as fact.
- CSV output usable as a CRM import.
- Offline fixtures producing at least 50 records and comprehensive tests without network access.

Add an optional `--offline-demo` path so a reviewer can generate a realistic target file without scraping live sites.

Do not purchase data, bypass controls, or invent real-company facts. State the plan first, implement only this phase, verify, update docs/backlog, and stop.
