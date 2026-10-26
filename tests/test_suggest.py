"""The closing note is the model's sentence. The receipt stays Python. No OpenFOAM."""

import json
from pathlib import Path

import pytest

from wingline.brief import engineer_brief
from wingline.kernel import Bounds, Panel, Refusal, Screening
from wingline.prompts import INNER_SYSTEM, SUGGEST_SYSTEM
from wingline.suggest import close_paragraph


def _solve_case(tmp_path: Path) -> Path:
    run = tmp_path / "run-demo"
    (run / "ledger").mkdir(parents=True)
    (run / "case" / "postProcessing" / "forceCoeffs" / "0").mkdir(parents=True)
    (run / "spec.json").write_text(json.dumps({
        "naca": "2412",
        "alpha_deg": 4,
        "u_ms": 10,
        "density": "coarse",
    }))
    (run / "ledger" / "rcpt-solve-1.json").write_text(json.dumps({
        "stage": "solve",
        "status": "red",
        "failure": "not_converged",
        "force_drift_cl": 0.00039,
        "force_drift_cd": 0.000021,
        "force_iterations": 1500,
        "end_time": 1500,
    }))
    coeff = run / "case" / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat"
    coeff.write_text("# Time Cd Cl\n1500 0.0224356435 0.699344388\n")
    return run


def _refusal() -> Refusal:
    return Refusal(gate="solve", log="log.simpleFoam", reason="solve red", panels=())


def test_the_closing_prompt_is_a_seat_and_the_inner_loop_stays_on_the_spec() -> None:
    assert "You are the closing seat." in SUGGEST_SYSTEM
    assert "not a gate" in SUGGEST_SYSTEM
    assert "NACA 4-digit" in SUGGEST_SYSTEM
    assert "Do not propose another wing." in INNER_SYSTEM


def test_a_stubbed_model_sentence_follows_the_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WINGLINE_AGENT", raising=False)
    seen: dict = {}

    def fake(system, prompt, **kwargs):
        seen["system"] = system
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return {"text": "I would try NACA 2410 next. Less thickness usually cuts pressure drag. A third sentence stays off the page."}

    monkeypatch.setattr("wingline.suggest.run_grok", fake)
    monkeypatch.setattr("wingline.suggest.grok_available", lambda: True)
    run = _solve_case(tmp_path)
    outcome = _refusal()
    receipt = engineer_brief(outcome, run, showing=True)
    text = close_paragraph(receipt, outcome, run)
    assert "refusing the solve" in text
    assert "I would try NACA 2410 next." in text
    assert "Less thickness usually cuts pressure drag." in text
    assert "third sentence" not in text
    assert text.count("\n") == 0
    assert seen["system"] == SUGGEST_SYSTEM
    assert "0.0224356435" in seen["prompt"] or "0.0224" in seen["prompt"]
    assert seen["kwargs"]["allow_tools"] is False
    assert seen["kwargs"]["max_turns"] == 1
    assert Path(seen["kwargs"]["cwd"]) != Path(__file__).resolve().parents[1]
    assert "WINGLINE_RUN_ID" not in seen["kwargs"]["env"]


def _drop(monkeypatch: pytest.MonkeyPatch, text: str) -> dict:
    monkeypatch.delenv("WINGLINE_AGENT", raising=False)
    monkeypatch.setattr("wingline.suggest.grok_available", lambda: True)
    seen = {"called": False}

    def fake(*_args, **_kwargs):
        seen["called"] = True
        return {"text": text}

    monkeypatch.setattr("wingline.suggest.run_grok", fake)
    return seen


def test_an_illegal_section_is_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _drop(monkeypatch, "I would try NACA 2404 next.")
    run = _solve_case(tmp_path)
    outcome = _refusal()
    receipt = engineer_brief(outcome, run)
    assert close_paragraph(receipt, outcome, run) == receipt
    assert seen["called"] is True


def test_a_five_digit_section_is_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _drop(monkeypatch, "I would try NACA 23012 next.")
    run = _solve_case(tmp_path)
    outcome = _refusal()
    receipt = engineer_brief(outcome, run)
    assert close_paragraph(receipt, outcome, run) == receipt
    assert seen["called"] is True


def test_an_angle_outside_the_bounds_is_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _drop(monkeypatch, "I would run the same wing at 15 degrees.")
    run = _solve_case(tmp_path)
    outcome = _refusal()
    receipt = engineer_brief(outcome, run)
    assert close_paragraph(receipt, outcome, run) == receipt
    assert seen["called"] is True
    assert Bounds.alpha_max == 10


def test_the_planner_does_not_ask_for_advice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGLINE_AGENT", "0")

    def boom(*_args, **_kwargs):
        raise AssertionError("grok was called")

    monkeypatch.setattr("wingline.suggest.run_grok", boom)
    run = _solve_case(tmp_path)
    outcome = _refusal()
    receipt = engineer_brief(outcome, run)
    assert close_paragraph(receipt, outcome, run) == receipt


def test_a_parse_refusal_does_not_ask_for_advice(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("grok was called")

    monkeypatch.setattr("wingline.suggest.run_grok", boom)
    outcome = Refusal(
        gate="parse",
        log="",
        reason="unrecognized request",
        panels=(Panel("goal", "Name a section and an angle, such as NACA 2412 at 6 degrees."),),
    )
    receipt = engineer_brief(outcome)
    assert close_paragraph(receipt, outcome, None) == receipt


def test_a_commission_refusal_does_not_ask_even_when_the_parent_has_forces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("grok was called")

    monkeypatch.setattr("wingline.suggest.run_grok", boom)
    run = _solve_case(tmp_path)
    outcome = Refusal(gate="commission", log="", reason="not cleared", panels=())
    receipt = engineer_brief(outcome, run)
    assert "Refused at gate commission" in receipt
    assert close_paragraph(receipt, outcome, run) == receipt


def test_a_failed_call_leaves_the_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WINGLINE_AGENT", raising=False)
    monkeypatch.setattr("wingline.suggest.grok_available", lambda: True)

    def down(*_args, **_kwargs):
        raise RuntimeError("grok exited 1")

    monkeypatch.setattr("wingline.suggest.run_grok", down)
    run = _solve_case(tmp_path)
    outcome = _refusal()
    receipt = engineer_brief(outcome, run)
    assert close_paragraph(receipt, outcome, run) == receipt


def test_a_missing_grok_leaves_the_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WINGLINE_AGENT", raising=False)
    monkeypatch.setattr("wingline.suggest.grok_available", lambda: False)

    def boom(*_args, **_kwargs):
        raise AssertionError("grok was called")

    monkeypatch.setattr("wingline.suggest.run_grok", boom)
    outcome = Screening(
        run_id="run-demo",
        cl=0.699,
        cd=0.0224,
        sentence=Bounds.screening_sentence,
        worth_finer=False,
    )
    receipt = engineer_brief(outcome)
    assert close_paragraph(receipt, outcome, None) == receipt
