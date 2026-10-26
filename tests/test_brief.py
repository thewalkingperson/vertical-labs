"""The workbench speaks one engineer paragraph. No OpenFOAM."""

import json
from pathlib import Path

from wingline.brief import engineer_brief
from wingline.kernel import Bounds, Panel, Refusal, Screening


def _solve_case(tmp_path: Path) -> Path:
    run = tmp_path / "run-demo"
    (run / "ledger").mkdir(parents=True)
    (run / "case" / "postProcessing" / "forceCoeffs" / "0").mkdir(parents=True)
    (run / "spec.json").write_text(json.dumps({
        "naca": "2412",
        "alpha_deg": 3,
        "u_ms": 10,
        "density": "coarse",
    }))
    (run / "ledger" / "rcpt-mesh-1.json").write_text(json.dumps({
        "stage": "mesh",
        "status": "green",
        "cells": 64000,
        "stream": 100,
        "grading": 8,
        "max_skew": 2.58,
    }))
    (run / "ledger" / "rcpt-solve-1.json").write_text(json.dumps({
        "stage": "solve",
        "status": "red",
        "failure": "not_converged",
        "force_drift_cl": 0.00016855,
        "force_drift_cd": 0.00001626,
        "force_iterations": 1500,
        "end_time": 1500,
    }))
    coeff = run / "case" / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat"
    coeff.write_text("# Time Cd Cl\n1500 0.0206800443 0.594380225\n")
    return run


def test_a_drifting_solve_is_one_paragraph(tmp_path: Path) -> None:
    outcome = Refusal(gate="solve", log="log.simpleFoam", reason="solve red", panels=())
    text = engineer_brief(outcome, _solve_case(tmp_path), showing=True)
    assert text.count("\n") == 0
    assert "goal:" not in text
    assert "Log:" not in text
    assert "NACA 2412 at 3 degrees and 10 m/s" in text
    assert "64,000 cells" in text
    assert "1,500 steps" in text
    assert "0.594" in text
    assert "0.0207" in text
    assert "refusing the solve" in text
    assert "paraFoam" in text
    assert "0.00017" in text
    assert "2410" not in text
    assert "less thickness" not in text
    assert f"{Bounds.force_drift_max:.5f}".rstrip("0") in text or "0.0001" in text


def test_a_screening_stays_one_paragraph() -> None:
    outcome = Screening(
        run_id="run-demo",
        cl=0.882459,
        cd=0.028414,
        sentence=Bounds.screening_sentence,
        worth_finer=False,
    )
    text = engineer_brief(outcome)
    assert "screening" in text
    assert "0.882" in text
    assert "0.0284" in text
    assert "goal:" not in text


def test_a_parse_refusal_uses_the_human_sentence() -> None:
    outcome = Refusal(
        gate="parse",
        log="",
        reason="unrecognized request",
        panels=(Panel("goal", "Name a section and an angle, such as NACA 2412 at 6 degrees."),),
    )
    text = engineer_brief(outcome)
    assert text == "Name a section and an angle, such as NACA 2412 at 6 degrees."
