"""Chord and span scaling, Reynolds check, steer language, and mesh diagnosis. No OpenFOAM."""

import os
import re
import shutil
from pathlib import Path

import pytest

from wingline.case_writer import render
from wingline.__main__ import panel_lines
from wingline.engineer import parse
from wingline.kernel import Bounds, Refusal, judge_physics
from wingline.meta import Study, resolve
from wingline.outer import evaluate, explore, find_lean
from wingline.planner import diagnose_mesh, diagnose_solve, drive
from wingline import planner
from wingline.steer import interpret_steer

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "naca2412-a6"

MESH = {"stream": 60, "grading": 8}
SOLVE = {"u_relax": 0.7, "non_ortho": 0}
SPEC = {"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"}


def _files(spec: dict) -> dict:
    return render(spec, MESH, SOLVE)


def _z_values(mesh: str) -> set[str]:
    return set(re.findall(r"^\s+\([-0-9.]+ [-0-9.]+ ([-0-9.]+)\) // \d+$", mesh, re.MULTILINE))


def _edge_z(mesh: str) -> set[str]:
    return set(re.findall(r"^\s+\([-0-9.]+ [-0-9.]+ ([-0-9.]+)\)$", mesh, re.MULTILINE))


def test_default_wing_keeps_the_unit_chord_references() -> None:
    files = _files(SPEC)
    control = files["system/controlDict"]
    mesh = files["system/blockMeshDict"]
    assert "lRef            1;" in control
    assert "Aref            0.1;" in control
    assert "CofR            (0.25 0 0);" in control
    assert "writeInterval   20;" in control
    assert "purgeWrite      10;" in control
    assert control.count("writeInterval   1;") == 1
    assert control.count("writeInterval   1500;") == 2
    assert "type            forces;" in control
    assert "rhoName" not in control
    assert control.count("rho             rhoInf;") == 2
    assert "type            yPlus;" in control
    assert "libs            (fieldFunctionObjects);" in control
    assert "writeFields     false;" in control
    assert "endTime         1500;" in control
    assert "(-8 0 0) // 0" in mesh
    assert "(-8 0 0.1) // 12" in mesh
    assert _z_values(mesh) == {"0", "0.1"}
    assert _edge_z(mesh) == {"0", "0.1"}


def test_chord_and_span_scale_the_mesh_and_the_force_reference() -> None:
    spec = {**SPEC, "naca": "0012", "alpha_deg": 4, "u_ms": 15, "chord_m": 0.5, "span_m": 0.2}
    files = _files(spec)
    mesh = files["system/blockMeshDict"]
    control = files["system/controlDict"]
    assert "(-4 0 0) // 0" in mesh
    assert "(-4 0 0.2) // 12" in mesh
    assert _z_values(mesh) == {"0", "0.2"}
    assert _edge_z(mesh) == {"0", "0.2"}
    assert "lRef            0.5;" in control
    assert "Aref            0.1;" in control
    assert "CofR            (0.125 0 0);" in control
    wide = _files({**SPEC, "chord_m": 2})
    assert "lRef            2;" in wide["system/controlDict"]
    assert "Aref            0.2;" in wide["system/controlDict"]
    assert "CofR            (0.5 0 0);" in wide["system/controlDict"]
    assert "(-16 0 0) // 0" in wide["system/blockMeshDict"]


def test_parser_accepts_chord_span_and_camber_words(tmp_path: Path) -> None:
    spec = parse("NACA 0012 with chord 0.5m at 4 degrees and 15 m/s", tmp_path)
    assert spec["naca"] == "0012"
    assert spec["alpha_deg"] == 4
    assert spec["u_ms"] == 15
    assert spec["chord_m"] == 0.5
    assert "span_m" not in spec
    both = parse("NACA 4412 at 6 degrees chord 2m span 0.2m", tmp_path)
    assert both["chord_m"] == 2
    assert both["span_m"] == 0.2
    equals = parse("NACA 2412 at 6 degrees c=1.5 depth 0.05m", tmp_path)
    assert equals["chord_m"] == 1.5
    assert equals["span_m"] == 0.05
    shaped = parse("camber 2 at 40% thickness 12 at 6 degrees", tmp_path)
    assert shaped["naca"] == "2412"
    assert shaped["camber"] == 2
    assert shaped["camber_pos"] == 4
    assert shaped["thickness"] == 12
    for code in ("0006", "1408", "2412", "4412", "6409", "0024"):
        parsed = parse(f"NACA {code} at 6 degrees", tmp_path)
        assert parsed["naca"] == code
    refused = parse("NACA 0025 at 6 degrees", tmp_path)
    assert isinstance(refused, Refusal)
    assert refused.gate == "parse"


def test_classic_sentence_still_omits_lengths(tmp_path: Path) -> None:
    spec = parse("NACA 2412 at 6 degrees. Is this worth a finer run?", tmp_path)
    assert spec == SPEC
    study = resolve("NACA 0012 with chord 0.5m span 0.2m at 4 degrees and 15 m/s", tmp_path, lambda _text: "no")
    assert isinstance(study, Study)
    assert study.spec["chord_m"] == 0.5
    assert study.spec["span_m"] == 0.2
    assert "chord 0.5m" in study.sentence
    assert "span 0.2m" in study.sentence
    again = parse(study.sentence, tmp_path)
    assert again["chord_m"] == 0.5
    assert again["span_m"] == 0.2
    direct = resolve("NACA 0012 with chord 0.5m at 4 degrees and 15 m/s", tmp_path, lambda _text: "no")
    assert isinstance(direct, Study)
    replay = parse(direct.sentence, tmp_path)
    assert replay["naca"] == "0012"
    assert replay["chord_m"] == 0.5
    assert replay["u_ms"] == 15


def test_same_wing_keeps_the_lengths(tmp_path: Path) -> None:
    (tmp_path / "last_spec.json").write_text(
        '{"naca":"0012","alpha_deg":4,"u_ms":15,"density":"coarse","chord_m":0.5,"span_m":0.2}\n'
    )
    spec = parse("Same wing at 8 degrees", tmp_path)
    assert spec["naca"] == "0012"
    assert spec["alpha_deg"] == 8
    assert spec["chord_m"] == 0.5
    assert spec["span_m"] == 0.2


def test_a_later_angle_keeps_the_same_wing(tmp_path: Path) -> None:
    (tmp_path / "last_spec.json").write_text(
        '{"naca":"2412","alpha_deg":3,"u_ms":10,"density":"coarse"}\n'
    )
    spec = parse("do 10 degrees now", tmp_path)
    assert spec["naca"] == "2412"
    assert spec["alpha_deg"] == 10
    assert spec["u_ms"] == 10
    study = resolve("do 10 degrees now", tmp_path, lambda _text: "no")
    assert isinstance(study, Study)
    assert study.spec["alpha_deg"] == 10
    assert study.sentence.startswith("NACA 2412 at 10 degrees")
    refused = parse("do 12 degrees now", tmp_path)
    assert isinstance(refused, Refusal)
    assert refused.reason == "alpha outside bounds"
    assert "-2" in refused.panels[0].text and "10" in refused.panels[0].text
    plain = parse("what is the weather", tmp_path)
    assert isinstance(plain, Refusal)
    assert plain.reason == "unrecognized request"
    shown = " ".join(panel_lines(plain))
    assert "unrecognized request" not in shown
    assert "Log:" not in shown
    assert "2412" in shown
    fresh = parse("a wing with low drag at 10 degrees", tmp_path)
    assert isinstance(fresh, Refusal)
    assert fresh.reason == "unrecognized request"


def test_one_physics_model_rejects_the_low_re_lift() -> None:
    pytest.importorskip("neuralfoil")
    assert judge_physics(0.2313, "2412", 6, 10, 1.0) is False
    assert judge_physics(0.856, "2412", 6, 10, 1.0) is True
    assert judge_physics(0.882459, "2412", 6, 10, 1.0) is True
    assert judge_physics(0.0, "2412", 6, 10, 1.0) is False
    assert judge_physics(2.0, "2412", 6, 10, 1.0) is False
    assert Bounds.nu == 1.5e-5
    assert Bounds.n_normal == 160
    assert Bounds.end_time == 1500
    assert Bounds.force_drift_window == 100
    assert Bounds.force_drift_max == 1e-4
    assert Bounds.continuity_local == 1e-4
    assert Bounds.reference_abs == 0.03
    assert Bounds.reference_rel == 0.05
    assert Bounds.reference_min_confidence == 0.90
    assert Bounds.delta_cl == 0.05
    assert Bounds.max_skew == 4.0


def test_natural_language_steering() -> None:
    mild = interpret_steer(["use mild grading"], "mesh")
    assert mild["grading"] == 4
    assert interpret_steer(["gentle clustering please"], "mesh")["grading"] == 4
    assert interpret_steer(["reduce skew"], "mesh")["grading"] == 4
    assert interpret_steer(["make a fine mesh"], "mesh")["stream"] == 100
    assert interpret_steer(["dense stream"], "mesh")["stream"] == 100
    assert interpret_steer(["relax more"], "solve")["u_relax"] == 0.5
    assert interpret_steer(["dampen velocity"], "solve")["u_relax"] == 0.5
    assert interpret_steer(["slow down solve"], "solve")["u_relax"] == 0.5
    assert interpret_steer(["correct non-ortho"], "solve")["non_ortho"] == 1
    assert interpret_steer(["add corrector"], "solve")["non_ortho"] == 1
    assert interpret_steer(["abort this"], "solve")["action"] == "stop"


def test_diagnosis_follows_the_worse_fault() -> None:
    skew = diagnose_mesh({"max_skew": 4.4, "max_non_ortho": 20})
    assert skew is not None
    assert skew[0] == 100 and skew[1] == 4
    severe = diagnose_mesh({"max_skew": 6.2, "max_non_ortho": 20})
    assert severe is not None and severe[1] == 2
    ortho = diagnose_mesh({"max_skew": 1.0, "max_non_ortho": 80})
    assert ortho is not None and ortho[0] == 80 and ortho[1] == 8
    assert diagnose_mesh({"max_skew": 1.0, "max_non_ortho": 20}) is None
    stepped = diagnose_mesh({"max_skew": 4.4, "max_non_ortho": 20, "grading": 4, "stream": 60})
    assert stepped is not None and stepped[1] == 2
    finer = diagnose_mesh({"max_skew": 4.4, "max_non_ortho": 20, "grading": 2, "stream": 60})
    assert finer is not None and finer[0] == 80
    mild_solve = diagnose_solve({"continuity_cum": 0.02})
    assert mild_solve is not None and mild_solve[0] == 0.5 and mild_solve[1] == 0
    hard_solve = diagnose_solve({"continuity_cum": 0.2})
    assert hard_solve is not None and hard_solve[0] == 0.3 and hard_solve[1] == 1
    assert diagnose_solve({"continuity_cum": 0.001}) is None
    assert diagnose_solve({"continuity_local": 1e-5}) is None
    local = diagnose_solve({"continuity_local": 1e-3})
    assert local is not None and local[0] == 0.5 and local[1] == 0
    far = diagnose_solve({"continuity_local": 2e-3, "continuity_cum": 0.001})
    assert far is not None and far[0] == 0.3 and far[1] == 1


def test_planner_records_a_skew_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGLINE_STORE", str(tmp_path))
    calls: list[tuple[int, int]] = []
    seen = {"n": 0}

    def observe(_run_id: str) -> dict:
        seen["n"] += 1
        if seen["n"] > 1:
            return {"terminal": True}
        return {
            "terminal": False,
            "open_stage": "mesh",
            "mesh_reds": 1,
            "steer_fresh": [],
            "last": {"max_skew": 4.4, "max_non_ortho": 20, "status": "red"},
        }

    def commit_mesh(_run_id: str, _lease: str, stream: int, grading: int) -> dict:
        calls.append((stream, grading))
        return {"status": "green"}

    monkeypatch.setattr(planner, "observe", observe)
    monkeypatch.setattr(planner, "commit_mesh", commit_mesh)
    drive("run-x", "lease")
    assert calls == [(100, 4)]
    note = (tmp_path / "run-x" / "diagnosis.txt").read_text()
    assert "Skew" in note


def test_planner_opens_on_the_production_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGLINE_STORE", str(tmp_path))
    mesh_calls: list[tuple[int, int]] = []
    solve_calls: list[tuple[float, int]] = []

    def observe_for(density: str, stage: str):
        seen = {"n": 0}

        def observe(_run_id: str) -> dict:
            seen["n"] += 1
            if seen["n"] > 1:
                return {"terminal": True}
            return {
                "terminal": False,
                "open_stage": stage,
                "density": density,
                "mesh_reds": 0,
                "solve_reds": 0,
                "steer_fresh": [],
                "last": {},
            }

        return observe

    def commit_mesh(_run_id: str, _lease: str, stream: int, grading: int) -> dict:
        mesh_calls.append((stream, grading))
        return {"status": "green"}

    def commit_solve(_run_id: str, _lease: str, relax: float, ortho: int) -> dict:
        solve_calls.append((relax, ortho))
        return {"status": "green"}

    monkeypatch.setattr(planner, "commit_mesh", commit_mesh)
    monkeypatch.setattr(planner, "commit_solve", commit_solve)
    monkeypatch.setattr(planner, "observe", observe_for("coarse", "mesh"))
    drive("run-parent", "lease")
    monkeypatch.setattr(planner, "observe", observe_for("refined", "mesh"))
    drive("run-child", "lease")
    monkeypatch.setattr(planner, "observe", observe_for("coarse", "solve"))
    drive("run-solve", "lease")
    assert mesh_calls == [(100, 8), (60, 8)]
    assert solve_calls == [(0.5, 1)]


def test_low_reynolds_chord_is_refused_at_parse(tmp_path: Path) -> None:
    refused = parse("NACA 2412 with chord 0.01m at 6 degrees", tmp_path)
    assert isinstance(refused, Refusal)
    assert refused.reason == "Reynolds number outside the reference range"


def test_explore_keeps_the_agent_and_the_chord(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGLINE_AGENT", "1")
    study = Study(
        {"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse", "chord_m": 0.5, "span_m": 0.2},
        "max_ld",
        True,
        "NACA 2412 at 6 degrees and 10 m/s chord 0.5m span 0.2m.",
        (),
    )
    sentences: list[str] = []

    def run_case(sentence: str) -> Refusal:
        sentences.append(sentence)
        assert os.environ.get("WINGLINE_AGENT") == "1"
        return Refusal(gate="parse", log="", reason="stopped for the test", panels=())

    explore(tmp_path, study, Refusal(gate="physics", log="", reason="direct", panels=()), run_case)
    assert sentences
    assert all("chord 0.5m" in sentence and "span 0.2m" in sentence for sentence in sentences)
    assert os.environ.get("WINGLINE_AGENT") == "1"


def test_find_lean_names_the_binary() -> None:
    assert find_lean().name == "lean"


def test_lean_check_can_be_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGLINE_VERIFY_LEAN", "0")
    monkeypatch.delenv("WINGLINE_AGENT", raising=False)
    run = tmp_path / "run-fixture"
    shutil.copytree(FIX / "ledger_green", run / "ledger")
    result = evaluate(run)
    assert not (isinstance(result, Refusal) and result.gate == "lean")
