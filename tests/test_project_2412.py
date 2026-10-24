"""Offline projection of the NACA 2412 fixture ledgers."""

import json
from pathlib import Path

import pytest
from pyshacl import validate

from wingline import DesignGrade, HardwareDecision, Refusal, Screening, hardware_decision
from wingline.kernel import (
    Bounds,
    Commit,
    admit,
    emit,
    judge_delta,
    load_ledger,
    project,
    shape,
)

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "fixtures" / "naca2412-a6"
SHAPE = REPO / "harness" / "shapes" / "receipt.shacl.ttl"


def _sentence(name: str) -> str:
    return (FIX / name).read_text().strip()


def _graph(receipts: list[dict]) -> dict:
    nodes = []
    for receipt in receipts:
        node = {
            "@id": f"https://vertical-labs.local/run/{receipt['id']}",
            "@type": receipt["type"],
        }
        for key, value in receipt.items():
            if value is None:
                continue
            node[key] = value
        nodes.append(node)
    return {
        "@context": {"@vocab": "https://vertical-labs.local/wingline#"},
        "@graph": nodes,
    }


def _conforms(document: dict) -> bool:
    conforms, _, _text = validate(
        json.dumps(document),
        shacl_graph=str(SHAPE),
        data_graph_format="json-ld",
        shacl_graph_format="turtle",
    )
    return bool(conforms)


@pytest.fixture(scope="module", autouse=True)
def emitted_policy() -> None:
    emit(REPO)


def test_green_affirm_screens_and_blocks_hardware() -> None:
    view = project(FIX / "ledger_green", None, FIX / "outer_affirm.json")
    assert isinstance(view, Screening)
    assert view.sentence == _sentence("sentence.txt")
    assert view.sentence == Bounds.screening_sentence
    assert view.worth_finer is True
    assert view.grade == "screening"
    with pytest.raises(TypeError):
        hardware_decision(view)


def test_green_challenge_is_not_worth_finer() -> None:
    view = project(FIX / "ledger_green", None, FIX / "outer_challenge.json")
    assert isinstance(view, Screening)
    assert view.worth_finer is False
    assert view.sentence == _sentence("sentence.txt")


def test_mesh_red_refuses_without_a_screening_panel() -> None:
    view = project(FIX / "ledger_mesh_red", None, None)
    assert isinstance(view, Refusal)
    assert view.gate == "mesh"
    assert not isinstance(view, Screening)
    assert all(panel.kind != "screening" for panel in view.panels)
    assert not hasattr(view, "cl")


def test_wide_delta_is_not_a_design_grade() -> None:
    outer = (FIX / "outer_affirm.json", FIX / "outer_affirm.json")
    view = project(FIX / "ledger_wide" / "parent", FIX / "ledger_wide" / "child", outer)
    assert isinstance(view, Refusal)
    assert view.gate == "refinement"
    assert not isinstance(view, DesignGrade)
    assert all(panel.kind != "screening" for panel in view.panels)


def test_design_ledger_is_a_design_grade() -> None:
    outer = (FIX / "outer_affirm.json", FIX / "outer_affirm.json")
    view = project(FIX / "ledger_design" / "parent", FIX / "ledger_design" / "child", outer)
    assert isinstance(view, DesignGrade)
    assert view.sentence == _sentence("design_sentence.txt")
    assert view.sentence == Bounds.design_sentence
    assert judge_delta(view.delta_cl, view.delta_cd)
    decision = hardware_decision(view)
    assert isinstance(decision, HardwareDecision)


def test_thin_red_keeps_the_mesh_and_solve_prefix() -> None:
    view = project(FIX / "ledger_green", None, {"thin": "red", "review": "challenge"})
    assert isinstance(view, Refusal)
    assert view.gate == "thin_airfoil"
    assert [panel.kind for panel in view.panels] == [
        "goal",
        "meshing",
        "mesh_accepted",
        "solving",
        "refused",
    ]
    assert all(panel.kind != "screening" for panel in view.panels)
    assert not hasattr(view, "cl")


def test_missing_outer_is_incomplete() -> None:
    missing = FIX / "outer_missing.json"
    assert not missing.exists()
    view = project(FIX / "ledger_green", None, missing)
    assert isinstance(view, Refusal)
    assert view.gate == "incomplete"
    assert not hasattr(view, "cl")


def test_judge_delta_bounds() -> None:
    assert judge_delta(0.05, 0.01) is True
    assert judge_delta(0.051, 0.0) is False


def test_design_grade_rejects_wide_delta() -> None:
    with pytest.raises(ValueError):
        DesignGrade(
            run_id="child",
            parent_run_id="parent",
            cl=0.89,
            cd=0.018,
            delta_cl=0.2,
            delta_cd=0.0,
            sentence=Bounds.design_sentence,
        )


def test_admit_rejects_forces_green_after_red_mesh() -> None:
    ledger = load_ledger(FIX / "ledger_mesh_red")
    assert shape(ledger) == "mesh_open"
    before = len(ledger)
    last = ledger[-1]
    commit = Commit(
        run_id=last["run_id"],
        stage="forces",
        args={},
        parent_id=last["id"],
        spec_hash=last["spec_hash"],
        log="postProcessing/forceCoeffs/0/coefficient.dat",
        evidence={"cl": 0.86, "cd": 0.02, "header": "Cl Cd"},
    )
    result = admit(ledger, commit)
    assert result.rejection
    assert result.receipt is None
    assert result.appended is False
    assert len(ledger) == before


def test_admit_idempotent_key_does_not_append_twice() -> None:
    ledger: list[dict] = []
    commit = Commit(
        run_id="run-2412-a6",
        stage="pilot",
        args={},
        parent_id=None,
        spec_hash="sha256:naca2412-a6",
        log="log.potentialFoam",
        evidence={
            "cells": 2000,
            "continuity": 0.000135471,
            "velocity_error": 1.18729e-05,
            "fatal": False,
        },
    )
    first = admit(ledger, commit)
    second = admit(ledger, commit)
    assert first.appended is True
    assert second.appended is False
    assert first.receipt is second.receipt
    assert first.receipt is not None
    assert first.receipt["status"] == "green"
    assert len(ledger) == 1


def test_pyshacl_rejects_green_forces_after_red_and_passes_green() -> None:
    green = _graph(load_ledger(FIX / "ledger_green"))
    assert _conforms(green) is True
    red = load_ledger(FIX / "ledger_mesh_red")
    forces = {
        "id": "rcpt-forces-illegal",
        "type": "ForcesReceipt",
        "run_id": red[-1]["run_id"],
        "stage": "forces",
        "status": "green",
        "parent_id": red[-1]["id"],
        "spec_hash": red[-1]["spec_hash"],
        "log": "postProcessing/forceCoeffs/0/coefficient.dat",
        "writer": "case-writer",
        "written_at": "2026-10-23T00:00:00Z",
        "cl": 0.86,
        "cd": 0.02,
    }
    assert _conforms(_graph(red + [forces])) is False
    wide = {
        "@context": {"@vocab": "https://vertical-labs.local/wingline#"},
        "@graph": [
            {
                "@id": "https://vertical-labs.local/run/delta-wide",
                "@type": "DeltaRecord",
                "delta_cl": 0.11,
                "delta_cd": 0.0,
            }
        ],
    }
    assert _conforms(wide) is False


def test_green_shape_is_forces_green() -> None:
    assert shape(load_ledger(FIX / "ledger_green")) == "forces_green"
