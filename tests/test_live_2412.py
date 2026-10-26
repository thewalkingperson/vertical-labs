"""One planner screening against OpenFOAM. Skipped unless WINGLINE_LIVE=1."""

import os
from pathlib import Path

import pytest

from wingline import Refusal, Screening
from wingline.engineer import Engineer

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "naca2412-a6"
_GATES = {"mesh", "solve", "forces", "physics", "pilot"}


def _log_is_under_run(run: Path, log: str) -> bool:
    if not log:
        return False
    named = Path(log)
    if named.is_file() and run in named.resolve().parents:
        return True
    return (run / log).is_file() or (run / "case" / log).is_file()


@pytest.mark.skipif(os.environ.get("WINGLINE_LIVE") != "1", reason="set WINGLINE_LIVE=1")
def test_planner_screens_the_fixture_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGLINE_AGENT", "0")
    monkeypatch.setenv("WINGLINE_EXPLORE", "0")
    monkeypatch.delenv("WINGLINE_FIXTURE", raising=False)
    engineer = Engineer.open(tmp_path)
    try:
        view = engineer.ask((FIX / "ask.txt").read_text().strip())
    finally:
        engineer.close()
    outcome = view.outcome
    if isinstance(outcome, Screening):
        assert view.run_id
        return
    assert isinstance(outcome, Refusal)
    assert outcome.gate in _GATES
    assert _log_is_under_run(tmp_path / view.run_id, outcome.log)
