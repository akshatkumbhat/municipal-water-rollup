from __future__ import annotations

import math

import pytest

from buy_and_build_model import AddOn, Assumptions, build_model


def test_base_case_reproduces_documented_returns() -> None:
    model, returns = build_model()

    assert len(model) == 5
    assert returns["gross_moic"] == pytest.approx(4.5388573508, rel=1e-8)
    assert returns["gross_irr"] == pytest.approx(0.3532851206, rel=1e-8)
    assert model.iloc[-1]["Ending Debt"] == pytest.approx(1.5602039747, rel=1e-8)


def test_debt_never_becomes_negative() -> None:
    model, _ = build_model(Assumptions(annual_organic_growth=0.15, terminal_multiple=6.0))
    assert (model["Ending Debt"] >= 0).all()


def test_no_addons_still_builds_valid_schedule() -> None:
    model, returns = build_model(add_ons=())
    assert (model["Acquisition Debt Draw"] == 0).all()
    assert returns["total_add_on_enterprise_value"] == 0
    assert math.isfinite(returns["gross_irr"])


def test_invalid_addon_year_is_rejected() -> None:
    with pytest.raises(ValueError, match="close_year outside forecast"):
        build_model(add_ons=(AddOn("Late", 6, 2.0, 0.20),))
