"""Render checks for the Streamlit layer.

`AppTest` executes the real script, so these catch the class of breakage that
unit tests on `operations_kpis` cannot: a bad widget call, a mislabelled tab, a
formatting crash, or a chart fed the wrong frame. They are deliberately few —
the arithmetic is asserted in `test_operations_kpis.py`.
"""

from __future__ import annotations

import pathlib

import pytest

from operations_kpis import METRIC_DEFINITIONS

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

# Absolute, derived from this file's own location. A relative path is resolved
# by AppTest against the *calling* file's directory, so it silently depends on
# where the suite is invoked from — which broke the moment the repository was
# moved to a different parent directory.
APP = str(pathlib.Path(__file__).resolve().parent.parent / "operations_dashboard.py")
TIMEOUT = 240


@pytest.fixture(scope="module")
def app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=TIMEOUT).run()


def _plan_table(app: AppTest):
    """The rendered plan-versus-actual frame, identified by its columns."""
    for element in app.dataframe:
        columns = getattr(element.value, "columns", [])
        if "Model Year" in columns and "Months Covered" in columns:
            return element.value
    raise AssertionError("plan-versus-actual table was not rendered")


def test_app_renders_without_exceptions(app: AppTest) -> None:
    assert not app.exception, [str(e.value) for e in app.exception]


def test_every_governing_kpi_has_a_card(app: AppTest) -> None:
    labels = [m.label for m in app.metric]
    assert labels == [m.label for m in METRIC_DEFINITIONS]
    assert len(labels) == 7


def test_cards_show_a_value_and_a_target_caption(app: AppTest) -> None:
    for card in app.metric:
        assert card.value not in ("", None)
    captions = " ".join(c.value for c in app.caption)
    assert "target" in captions.lower()


def test_all_seven_sections_are_present(app: AppTest) -> None:
    assert len(app.tabs) == 7


def test_provenance_banner_names_the_source_and_scenario(app: AppTest) -> None:
    banner = " ".join(i.value for i in app.info)
    assert "SAMPLE" in banner
    assert "synthetic" in banner
    assert "base" in banner


def test_no_unhandled_warning_state_on_the_default_view(app: AppTest) -> None:
    """The default view has data, so no *filter* warning may show.

    One warning is expected and required: the churn overlapping-customer
    caveat, which is deliberately persistent rather than tucked into a tooltip.
    """
    texts = [w.value for w in app.warning]
    assert not any("No rows match" in text for text in texts)
    assert not any("Select both a start and an end date" in text for text in texts)
    assert any("not** de-duplicated" in text or "de-duplicated" in text for text in texts), (
        "the churn caveat warning must remain visible"
    )


def test_plan_table_shows_all_five_model_years_by_default(app: AppTest) -> None:
    plan = _plan_table(app)
    assert list(plan["Model Year"]) == [1, 2, 3, 4, 5]
    assert (plan["Model Anchor"] == "2024-01").all()


def test_mid_horizon_date_filter_keeps_true_model_year_labels(app: AppTest) -> None:
    """Narrowing the reporting period must not relabel Year 3 as Year 1."""
    import datetime as dt

    reporting = [d for d in app.date_input if d.label == "Reporting period"][0]
    filtered = reporting.set_value((dt.date(2026, 7, 1), dt.date(2028, 12, 1))).run()

    assert not filtered.exception, [str(e.value) for e in filtered.exception]
    plan = _plan_table(filtered)
    assert list(plan["Model Year"]) == [3, 4, 5]
    assert list(plan["Months Covered"]) == [6, 12, 12]
    assert (plan["Model Anchor"] == "2024-01").all()


def test_anchor_input_is_exposed_and_labelled(app: AppTest) -> None:
    labels = [d.label for d in app.date_input]
    assert "Model Year 1 begins" in labels
    captions = " ".join(c.value for c in app.caption)
    assert "Model Year 1 begins Jan 2024" in captions


def test_churn_caveat_is_visible_in_the_lineage_table(app: AppTest) -> None:
    # kpi_summary also carries a "Key" column, so match on "Definition".
    definitions = [
        df.value for df in app.dataframe if "Definition" in getattr(df.value, "columns", [])
    ]
    assert definitions, "metric definitions table not rendered"
    text = " ".join(definitions[0]["Definition"].astype(str))
    assert "customer-level identifier" in text


def test_downloads_are_offered(app: AppTest) -> None:
    labels = [b.label for b in app.button] + [
        getattr(b, "label", "") for b in app.get("download_button")
    ]
    joined = " ".join(labels).lower()
    assert "download" in joined
