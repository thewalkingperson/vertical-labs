"""Offline ask replays the NACA 2412 fixture and does not screen hardware."""

from pathlib import Path

import pytest

from wingline import Engineer, Screening, hardware_decision

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "naca2412-a6"


def test_fixture_ask_screens_and_blocks_hardware(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGLINE_FIXTURE", "1")
    monkeypatch.delenv("WINGLINE_AGENT", raising=False)
    engineer = Engineer.open(tmp_path)
    view = engineer.ask((FIX / "ask.txt").read_text().strip())
    assert isinstance(view.outcome, Screening)
    assert view.outcome.sentence == (FIX / "sentence.txt").read_text().strip()
    with pytest.raises(TypeError):
        hardware_decision(view.outcome)
