"""Parsers for checkMesh, blockMesh, simpleFoam, forceCoeffs, and the cylinder smoke."""

import re
from pathlib import Path

import pytest

from wingline.case_writer import (
    mesh_evidence,
    parse_check_mesh,
    parse_cylinder,
    parse_force_coeffs,
    parse_simple_foam,
    parse_yplus,
    render,
    yplus_from_case,
)
from wingline.kernel import (
    Bounds,
    cell_count,
    first_cell_height,
    judge_mesh,
    judge_pilot,
    judge_solve,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "naca2412-a6"
SPEC = {"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"}
MESH = {"stream": 100, "grading": 8}
SOLVE = {"u_relax": 0.7, "non_ortho": 0}


def test_force_coeffs_scrambled_cl_before_cd() -> None:
    text = """\
# Time          Cl              Cd              Cl(f)           Cd(f)
100             0.10            0.50            0.05            0.40
200             0.90            0.02            0.40            0.01
"""
    parsed = parse_force_coeffs(text)
    assert parsed["cl"] == pytest.approx(0.90)
    assert parsed["cd"] == pytest.approx(0.02)


def test_cylinder_snippet() -> None:
    text = (FIX / "cylinder_log.txt").read_text()
    parsed = parse_cylinder(text, text)
    assert parsed["cells"] == 2000
    assert parsed["continuity"] == pytest.approx(0.000135471)
    assert parsed["velocity_error"] == pytest.approx(1.18729e-05)
    assert judge_pilot(parsed) is True


def test_check_mesh_without_mesh_ok_is_not_ok() -> None:
    text = (
        "Mesh non-orthogonality Max: 20 average: 5\n"
        "Max skewness = 0.5 OK.\n"
        "Failed 1 mesh checks.\n"
    )
    parsed = parse_check_mesh(text)
    assert parsed["mesh_ok"] is False
    assert parsed["fatal"] is False
    assert parsed["max_non_ortho"] == pytest.approx(20)
    assert parsed["max_skew"] == pytest.approx(0.5)
    evidence = {
        "cells": 9600,
        "cells_expected": 9600,
        "max_non_ortho": parsed["max_non_ortho"],
        "max_skew": parsed["max_skew"],
        "mesh_ok": parsed["mesh_ok"],
        "fatal": parsed["fatal"],
    }
    assert judge_mesh(evidence) is False


def test_simple_foam_last_cumulative_and_execution_time() -> None:
    text = (
        "time step continuity errors : sum local = 1e-2, global = 1e-3, cumulative = 0.01\n"
        "time step continuity errors : sum local = 1e-5, global = 1e-6, cumulative = 1.5e-4\n"
        "ExecutionTime = 3.2 s  ClockTime = 4 s\n"
    )
    parsed = parse_simple_foam(text, 3.2)
    assert parsed["continuity_local"] == pytest.approx(1e-5)
    assert parsed["continuity_global"] == pytest.approx(1e-6)
    assert parsed["continuity_cum"] == pytest.approx(1.5e-4)
    assert parsed["finished"] is True
    assert parsed["failure"] == "none"


def test_simple_foam_local_residual_fails_before_the_cumulative_sum() -> None:
    text = (
        "time step continuity errors : sum local = 1e-3, global = 1e-4, cumulative = 1e-6\n"
        "ExecutionTime = 3.2 s  ClockTime = 4 s\n"
    )
    parsed = parse_simple_foam(text, 3.2)
    assert parsed["continuity_local"] == pytest.approx(1e-3)
    assert parsed["failure"] == "residual"


def test_simple_foam_cumulative_is_only_a_fallback() -> None:
    text = (
        "time step continuity errors : cumulative = 1.5e-4\n"
        "ExecutionTime = 3.2 s  ClockTime = 4 s\n"
    )
    parsed = parse_simple_foam(text, 3.2)
    assert parsed["continuity_local"] is None
    assert parsed["continuity_cum"] == pytest.approx(1.5e-4)
    assert parsed["failure"] == "none"


def test_block_mesh_keeps_cell_formula_and_unique_edges() -> None:
    text = render(SPEC, MESH, SOLVE)["system/blockMeshDict"]
    hexes = re.findall(r"hex\s+\([^)]+\)\s+\((\d+)\s+(\d+)\s+(\d+)\)", text)
    total = sum(int(nx) * int(ny) * int(nz) for nx, ny, nz in hexes)
    assert len(hexes) == 6
    assert total == cell_count(100, "coarse") == 64_000
    assert cell_count(100, "refined") == 128_000
    child = render({**SPEC, "density": "refined"}, {"stream": 60, "grading": 8}, SOLVE)
    child_mesh = child["system/blockMeshDict"]
    child_hexes = re.findall(r"hex\s+\([^)]+\)\s+\((\d+)\s+(\d+)\s+(\d+)\)", child_mesh)
    child_total = sum(int(nx) * int(ny) * int(nz) for nx, ny, nz in child_hexes)
    assert child_total == cell_count(60, "refined") == 76_800
    assert "simpleGrading (0.5 1000 1)" in text
    pairs = re.findall(r"polyLine\s+(\d+)\s+(\d+)", text)
    unique = {frozenset(pair) for pair in pairs}
    assert len(pairs) == len(unique) == 8
    assert frozenset(("7", "8")) not in unique
    assert frozenset(("19", "20")) not in unique
    assert "spline" not in text
    assert "walls" in text
    flat = render({**SPEC, "alpha_deg": 0}, MESH, SOLVE)["system/blockMeshDict"]
    assert flat == text
    files = render(SPEC, MESH, SOLVE)
    assert "nu              1.5e-05;" in files["constant/transportProperties"]
    assert "simulationType  RAS;" in files["constant/turbulenceProperties"]
    assert "RASModel        SpalartAllmaras;" in files["constant/turbulenceProperties"]
    assert "div(phi,nuTilda)" in files["system/fvSchemes"]
    assert "wallDist" in files["system/fvSchemes"]
    assert "nuTilda" in files["system/fvSolution"]
    assert "nutUSpaldingWallFunction" in files["0/nut"]
    assert "fixedValue" in files["0/nuTilda"]
    evidence = mesh_evidence(
        files["log.blockMesh"] if "log.blockMesh" in files else "nCells: 64000\n",
        "Mesh OK\nMesh non-orthogonality Max: 63.50\nMax skewness = 1.44\n",
        cells_expected=64000,
        stream=100,
        grading=8,
    )
    assert evidence["n_normal"] == 160
    assert evidence["wall_grading"] == 1000
    unit = first_cell_height(Bounds.chord_m * Bounds.far_y, Bounds.n_normal, 1000)
    assert evidence["first_cell_m"] == pytest.approx(unit)
    half = mesh_evidence(
        "nCells: 64000\n",
        "Mesh OK\nMesh non-orthogonality Max: 63.50\nMax skewness = 1.44\n",
        cells_expected=64000,
        stream=100,
        grading=8,
        chord_m=0.5,
    )
    wide = mesh_evidence(
        "nCells: 64000\n",
        "Mesh OK\nMesh non-orthogonality Max: 63.50\nMax skewness = 1.44\n",
        cells_expected=64000,
        stream=100,
        grading=8,
        chord_m=2.0,
    )
    assert half["first_cell_m"] == pytest.approx(unit / 2)
    assert wide["first_cell_m"] == pytest.approx(unit * 2)
    assert half["first_cell_m"] == pytest.approx(0.000170222, abs=1e-9)
    assert wide["first_cell_m"] == pytest.approx(0.000680889, abs=1e-9)


def test_wall_clock_mesh_fails_even_when_the_log_is_clean() -> None:
    evidence = {
        "cells": 64000,
        "cells_expected": 64000,
        "max_non_ortho": 63.5,
        "max_skew": 1.44,
        "mesh_ok": True,
        "fatal": False,
        "exit_code": 0,
        "failure": "wall_clock",
    }
    assert judge_mesh(evidence) is False
    evidence["failure"] = "fatal"
    assert judge_mesh(evidence) is False
    evidence["failure"] = "none"
    assert judge_mesh(evidence) is True


def test_yplus_parser_keeps_the_positive_max_at_the_latest_time() -> None:
    text = """\
# y+ ()
# Time    patch    min    max    average
0    walls    0    0    0
600    walls    1.45286110    12.6313041    4.91648736
600    walls    0    0    0
"""
    parsed = parse_yplus(text)
    assert parsed is not None
    assert parsed["min"] == pytest.approx(1.45286110)
    assert parsed["max"] == pytest.approx(12.6313041)
    assert parsed["avg"] == pytest.approx(4.91648736)


def test_yplus_from_case_prefers_the_measured_file(tmp_path: Path) -> None:
    zero = tmp_path / "postProcessing" / "yPlus" / "0"
    real = tmp_path / "postProcessing" / "yPlus" / "0"
    real.mkdir(parents=True)
    (real / "yPlus.dat").write_text(
        "# Time patch min max average\n600 walls 0 0 0\n"
    )
    (zero / "yPlus_0.dat").write_text(
        "# Time patch min max average\n"
        "600 walls 1.02530529 12.6473762 4.89616753\n"
    )
    parsed = yplus_from_case(tmp_path)
    assert parsed is not None
    assert parsed["max"] == pytest.approx(12.6473762)
    assert yplus_from_case(tmp_path / "missing") is None


def test_recorded_yplus_does_not_fail_the_solve() -> None:
    evidence = {
        "continuity_local": 1.4e-7,
        "failure": "none",
        "finished": True,
        "exit_code": 0,
        "log_text": "ExecutionTime = 3.2 s\n",
        "yplus_min": 1.45,
        "yplus_max": 12.63,
        "yplus_avg": 4.92,
    }
    assert judge_solve(evidence) is True
    del evidence["yplus_min"]
    del evidence["yplus_max"]
    del evidence["yplus_avg"]
    assert judge_solve(evidence) is True
