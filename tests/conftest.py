"""The closing seat stays quiet in unit tests.

pytest on this machine can see grok. A finished design grade would otherwise
start a real model call. Tests that want the seat turn grok_available back on
and stub run_grok.
"""

import pytest


@pytest.fixture(autouse=True)
def _closing_seat_stays_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wingline.suggest.grok_available", lambda: False)
