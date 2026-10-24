"""A missing checker is that gate's refusal. Do not skip it."""

import shutil
from pathlib import Path

from wingline import Refusal
from wingline.outer import evaluate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "naca2412-a6"


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "run-fixture"
    shutil.copytree(FIX / "ledger_green", run / "ledger")
    return run


def test_missing_pyshacl_is_shacl(tmp_path: Path) -> None:
    refusal = evaluate(_run(tmp_path), pyshacl=Path("/nonexistent/pyshacl"))
    assert isinstance(refusal, Refusal)
    assert refusal.gate == "shacl"


def test_missing_lean_is_lean(tmp_path: Path) -> None:
    refusal = evaluate(_run(tmp_path), lean=Path("/nonexistent/lean"))
    assert isinstance(refusal, Refusal)
    assert refusal.gate == "lean"
