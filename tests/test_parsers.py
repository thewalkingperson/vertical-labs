"""Parsers for checkMesh, blockMesh, simpleFoam, forceCoeffs, and the cylinder smoke."""

import re
from pathlib import Path

import pytest

from wingline.case_writer import (
    parse_check_mesh,
    parse_cylinder,
    parse_force_coeffs,
    parse_simple_foam,
    render,
)
from wingline.kernel import cell_count, judge_mesh, judge_pilot

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "naca2412-a6"
SPEC = {"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"}
MESH = {"stream": 60, "grading": 8}
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
    assert parsed["continuity_cum"] == pytest.approx(1.5e-4)
    assert parsed["finished"] is True
    assert parsed["failure"] == "none"


def test_block_mesh_keeps_cell_formula_and_unique_edges() -> None:
    text = render(SPEC, MESH, SOLVE)["system/blockMeshDict"]
    hexes = re.findall(r"hex\s+\([^)]+\)\s+\((\d+)\s+(\d+)\s+(\d+)\)", text)
    total = sum(int(nx) * int(ny) * int(nz) for nx, ny, nz in hexes)
    assert len(hexes) == 6
    assert total == cell_count(60, "coarse") == 9600
    refined = render({**SPEC, "density": "refined"}, MESH, SOLVE)["system/blockMeshDict"]
    refined_hexes = re.findall(r"hex\s+\([^)]+\)\s+\((\d+)\s+(\d+)\s+(\d+)\)", refined)
    refined_total = sum(int(nx) * int(ny) * int(nz) for nx, ny, nz in refined_hexes)
    assert refined_total == cell_count(60, "refined") == 19200
    pairs = re.findall(r"polyLine\s+(\d+)\s+(\d+)", text)
    unique = {frozenset(pair) for pair in pairs}
    assert len(pairs) == len(unique) == 8
    assert frozenset(("7", "8")) not in unique
    assert frozenset(("19", "20")) not in unique
    assert "spline" not in text
    assert "walls" in text
    flat = render({**SPEC, "alpha_deg": 0}, MESH, SOLVE)["system/blockMeshDict"]
    assert flat == text
