"""Turns hand-maintained documentation claims into enforced invariants.

The README states counts and lists that nothing checked: the module list omitted
three files, and the KPI count contradicted both `GOVERNING_KEYS` and the
README's own later section. Each test here fails when a documented claim stops
matching the code, so the drift surfaces in CI rather than in front of a reader.

Only claims a reader acts on are asserted here. A precise test count is
deliberately not one of them: it changes on every commit that touches tests and
tells a reader nothing that "no network access anywhere in the suite" does not
already tell them, so asserting it would tax every future change to protect a
fact with no informational value.
"""

from __future__ import annotations

import re
from pathlib import Path

from operations_kpis import GOVERNING_KEYS, METRIC_DEFINITIONS

REPO_ROOT = Path(__file__).resolve().parent.parent
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
MAKEFILE = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def test_readme_states_the_suite_is_hermetic() -> None:
    # The load-bearing claim about the suite is that it never touches the
    # network, which conftest.py enforces for real. Assert the claim is present.
    assert "no network access anywhere in the suite" in README.lower()


def test_readme_governing_kpi_count_matches_the_module() -> None:
    claims = re.findall(r"(\w+) governing\s+KPIs", README)
    assert claims, "README no longer states a governing-KPI count"
    for claim in claims:
        stated = _NUMBER_WORDS.get(claim.lower())
        assert stated is not None, f"unparseable KPI count in README: {claim!r}"
        assert stated == len(GOVERNING_KEYS)


def test_dso_is_never_described_as_governing() -> None:
    # DSO is a supporting cash metric. It has a dashboard card but is not in
    # GOVERNING_KEYS, so a card count is not a governing-KPI count.
    assert len(METRIC_DEFINITIONS) == len(GOVERNING_KEYS) + 1
    assert "dso" not in GOVERNING_KEYS
    for path in REPO_ROOT.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "seven governing" not in text.lower(), f"{path.name} miscounts governing KPIs"


def test_readme_lists_every_top_level_module() -> None:
    modules = {path.name for path in REPO_ROOT.glob("*.py")}
    missing = {name for name in modules if f"`{name}`" not in README}
    assert not missing, f"README does not document: {sorted(missing)}"


def test_every_documented_make_target_exists() -> None:
    targets = set(re.findall(r"^([a-z][a-z-]*):", MAKEFILE, re.MULTILINE))
    documented: set[str] = set()
    for doc in ("README.md", "METHODOLOGY.md", "CLAUDE.md"):
        text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        documented |= set(re.findall(r"`make ([a-z][a-z-]*)`", text))
        documented |= set(re.findall(r"^\s*make ([a-z][a-z-]*)\s*$", text, re.MULTILINE))
    assert documented, "no make targets found in the documentation"
    assert documented <= targets, (
        f"documented but absent from the Makefile: {sorted(documented - targets)}"
    )
