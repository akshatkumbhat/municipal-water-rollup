"""Shared test guards.

The sourcing pipeline must be testable with zero live-network access. This
autouse fixture makes any accidental HTTP call fail loudly, so a test can only
pass by going through the deterministic offline fixtures / OfflineFetcher.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests


@pytest.fixture(autouse=True)
def _disable_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Live network access is disabled in the test suite.")

    monkeypatch.setattr(requests.Session, "get", _blocked)
    monkeypatch.setattr(requests.Session, "request", _blocked)
    monkeypatch.setattr(requests, "get", _blocked, raising=False)
