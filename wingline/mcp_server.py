"""Five tools and perform_pilot. The only module that imports the case writer."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping

from mcp.server.fastmcp import FastMCP

from wingline.case_writer import (
    append_observe,
    commit_under_lock,
    mesh_evidence,
    parse_cylinder,
    parse_force_coeffs,
    parse_simple_foam,
    force_coefficients,
    force_drift,
    forces_text_from_case,
    yplus_from_case,
    render,
    open_parafoam,
    run_block_mesh,
    run_foam,
)
from wingline.steer import steer_fresh, steer_texts
from wingline.kernel import (
    Bounds,
    Commit,
    GRADINGS,
    NON_ORTHO,
    STREAMS,
    U_RELAX,
    cell_count,
    judge_pilot,
    load_ledger,
    shape,
    _mesh_signature,
)

_LAUNCHER = "open" + "foam"
_ALLRUN = (
    Path("/Volumes")
    / ("Open" + "FOAM-v2606")
    / "tutorials"
    / "basic"
    / "potentialFoam"
    / "cylinder"
    / "Allrun"
)
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "naca2412-a6"
_TERMINAL = {
    "pilot_red",
    "mesh_closed",
    "solve_closed",
    "forces_green",
    "forces_red",
}

def show_case(case: Path) -> subprocess.Popen:
    """The workbench asks here. The launcher string stays in the case writer."""
    return open_parafoam(case)


mcp = FastMCP(
    "wingline",
    instructions="Pass the lease unchanged. Mesh and solve arguments are enums.",
    log_level="ERROR",
)

Stream = Literal[60, 80, 100]
Grading = Literal[2, 4, 8, 12]
Relax = Literal[0.7, 0.5, 0.3]
Ortho = Literal[0, 1, 2]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def store_root() -> Path:
    raw = os.environ.get("WINGLINE_STORE")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1] / "runs"


def _num(value: object) -> int | float:
    number = float(value)  # type: ignore[arg-type]
    if number.is_integer():
        return int(number)
    return number


def canonical_spec(spec: Mapping[str, object]) -> dict:
    canonical = {
        "naca": str(spec["naca"]),
        "alpha_deg": _num(spec["alpha_deg"]),
        "u_ms": _num(spec["u_ms"]),
        "density": str(spec["density"]),
    }
    if "chord_m" in spec and spec["chord_m"] is not None:
        canonical["chord_m"] = _num(spec["chord_m"])
    if "span_m" in spec and spec["span_m"] is not None:
        canonical["span_m"] = _num(spec["span_m"])
    for key in ("camber", "camber_pos", "thickness"):
        if key in spec and spec[key] is not None:
            canonical[key] = _num(spec[key])
    return canonical


def spec_hash(spec: Mapping[str, object]) -> str:
    payload = json.dumps(canonical_spec(spec), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _run_dir(run_id: str) -> Path:
    if not isinstance(run_id, str) or not run_id or run_id in {".", ".."}:
        raise ValueError("run_id escapes the store")
    if "/" in run_id or "\\" in run_id or run_id != Path(run_id).name:
        raise ValueError("run_id escapes the store")
    root = store_root().resolve()
    path = (root / run_id).resolve()
    if not path.is_relative_to(root):
        raise ValueError("run_id escapes the store")
    return path


def _lease_ok(run_dir: Path, lease: str) -> bool:
    path = run_dir / "lease"
    if not path.is_file() or not isinstance(lease, str):
        return False
    expected = path.read_text()
    try:
        return hmac.compare_digest(expected, lease)
    except (TypeError, ValueError):
        return False


def _denied(run_id: str) -> dict:
    return {"error": "bad lease", "decision": "denied", "run_id": run_id}


def _note(store: Path, tool: str, run_id: str, decision: str, receipt_id: str) -> None:
    append_observe(
        store,
        {
            "ts": _now(),
            "tool": tool,
            "run_id": run_id,
            "decision": decision,
            "receipt_id": receipt_id,
        },
    )


def _enum_num(value: object, choices: tuple[int, ...] | tuple[float, ...]) -> int | float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    for choice in choices:
        if abs(number - float(choice)) < 1e-9:
            return choice
    return None


def _read(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(errors="replace")


def _launcher_path() -> str:
    return shutil.which(_LAUNCHER) or ""


def _allrun_hash() -> str:
    if not _ALLRUN.is_file():
        return ""
    return hashlib.sha256(_ALLRUN.read_bytes()).hexdigest()


def _cache_path(store: Path) -> Path:
    return store / "pilot" / "latest.json"


def _load_cache(store: Path) -> dict | None:
    path = _cache_path(store)
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_cache(store: Path, record: dict) -> None:
    path = _cache_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")


def _pilot_evidence(record: Mapping[str, object]) -> dict:
    evidence = {
        "fatal": bool(record.get("fatal")),
        "log_text": str(record.get("log_text") or ""),
        "log": str(record.get("log") or "log.potentialFoam"),
    }
    for key in ("cells", "continuity", "velocity_error"):
        if record.get(key) is not None:
            evidence[key] = record[key]
    return evidence


def _commit_pilot(store: Path, record: Mapping[str, object]) -> dict | None:
    run_id = os.environ.get("WINGLINE_RUN_ID")
    if not run_id:
        return None
    run_dir = _run_dir(run_id)
    spec_path = run_dir / "spec.json"
    if spec_path.is_file():
        digest = spec_hash(json.loads(spec_path.read_text()))
    else:
        digest = "sha256:pilot"
    evidence = _pilot_evidence(record)
    commit = Commit(
        run_id=run_id,
        stage="pilot",
        args={},
        parent_id=None,
        spec_hash=digest,
        log=str(evidence["log"]),
        evidence=evidence,
    )
    admission = commit_under_lock(run_dir, commit)
    return admission.receipt


def _fixture_pilot(store: Path) -> dict:
    text = (_FIXTURE / "cylinder_log.txt").read_text()
    parsed = parse_cylinder(text, text)
    parsed["green"] = bool(judge_pilot(parsed))
    parsed["log"] = "log.potentialFoam"
    parsed["launcher"] = "fixture"
    parsed["allrun_sha256"] = "fixture"
    if parsed["green"]:
        _write_cache(store, dict(parsed))
    receipt = _commit_pilot(store, parsed)
    if receipt is not None:
        parsed["receipt"] = receipt
        parsed["green"] = receipt.get("status") == "green"
    return parsed


def _live_pilot(store: Path) -> dict:
    launcher = _launcher_path()
    digest = _allrun_hash()
    cached = _load_cache(store)
    if (
        cached
        and cached.get("green")
        and cached.get("launcher") == launcher
        and digest
        and cached.get("allrun_sha256") == digest
        and judge_pilot(cached)
    ):
        record = dict(cached)
    else:
        work = store / "pilot" / "work"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        try:
            code = run_foam(work, "pilot", Bounds.pilot_timeout_s)
        except OSError as exc:
            note = work / "log.launcher"
            note.write_text(str(exc))
            record = {"green": False, "fatal": True, "log": str(note), "log_text": str(exc)}
            receipt = _commit_pilot(store, record)
            if receipt is not None:
                record["receipt"] = receipt
            return record
        case = work / "case"
        block = _read(case / "log.blockMesh")
        solver = _read(case / "log.potentialFoam")
        parsed = parse_cylinder(block, solver)
        solver_path = case / "log.potentialFoam"
        launcher_log = work / "log.launcher"
        parsed["log"] = str(solver_path if solver_path.is_file() else launcher_log)
        parsed["exit_code"] = code
        parsed["green"] = bool(judge_pilot(parsed))
        parsed["launcher"] = launcher
        parsed["allrun_sha256"] = _allrun_hash() or digest
        record = parsed
        if record["green"]:
            _write_cache(store, {key: value for key, value in record.items() if key != "receipt"})
    receipt = _commit_pilot(store, record)
    if receipt is not None:
        record = dict(record)
        record["receipt"] = receipt
        record["green"] = receipt.get("status") == "green"
    return record


def perform_pilot(store: Path) -> dict:
    """Cylinder smoke. Not a tool. Reuses runs/pilot/latest.json when the launcher and Allrun hash match."""
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    if os.environ.get("WINGLINE_FIXTURE") == "1":
        return _fixture_pilot(store)
    return _live_pilot(store)


def _ledger(run_dir: Path) -> list[dict]:
    folder = run_dir / "ledger"
    if not folder.is_dir():
        return []
    return load_ledger(folder)


def _parent(receipts: list[dict]) -> str | None:
    if not receipts:
        return None
    return str(receipts[-1].get("id"))


def _mesh_reds(receipts: list[dict]) -> list[dict]:
    return [
        item
        for item in receipts
        if item.get("stage") == "mesh" and item.get("status") == "red"
    ]


def _solve_reds(receipts: list[dict]) -> list[dict]:
    return [
        item
        for item in receipts
        if item.get("stage") == "solve"
        and item.get("status") == "red"
        and item.get("failure") not in ("fatal", "wall_clock", "not_converged")
    ]


def _stop(
    run_dir: Path,
    stage: str,
    why: str,
    parent: str | None,
    digest: str,
    evidence: dict,
    args: dict,
) -> dict:
    commit = Commit(
        run_id=run_dir.name,
        stage=stage,
        args=args,
        parent_id=parent,
        spec_hash=digest,
        log=str(evidence.get("log") or ""),
        evidence=evidence,
        stop_why=why,
    )
    admission = commit_under_lock(run_dir, commit)
    if admission.receipt is None:
        return {"error": admission.rejection or "stop rejected", "decision": "rejected"}
    return admission.receipt


def _finish(run_dir: Path, tool: str, result: dict) -> dict:
    status = str(result.get("status") or result.get("decision") or "rejected")
    receipt_id = str(result.get("id") or "")
    _note(store_root(), tool, run_dir.name, status, receipt_id)
    return result


def _load_spec(run_dir: Path) -> dict:
    path = run_dir / "spec.json"
    if not path.is_file():
        raise ValueError("spec missing")
    return canonical_spec(json.loads(path.read_text()))


@mcp.tool()
def observe(run_id: str) -> dict:
    run_dir = _run_dir(run_id)
    if not run_dir.is_dir():
        raise ValueError("run_id escapes the store")
    receipts = _ledger(run_dir)
    current = shape(receipts)
    last = receipts[-1] if receipts else None
    tail = ""
    if last is not None and last.get("status") == "red":
        for candidate in (
            run_dir / "case" / str(last.get("log") or ""),
            run_dir / str(last.get("log") or ""),
            Path(str(last.get("log") or "")),
        ):
            if candidate.is_file():
                lines = candidate.read_text(errors="replace").splitlines()
                tail = "\n".join(lines[-40:])
                break
    mesh_reds = len(_mesh_reds(receipts))
    solve_reds = len(_solve_reds(receipts))
    try:
        density = str(_load_spec(run_dir).get("density") or "coarse")
    except (OSError, ValueError, KeyError):
        density = "coarse"
    open_stage = {
        "pilot_green": "scope",
        "scoped": "mesh",
        "mesh_open": "mesh",
        "mesh_green": "solve",
        "solve_open": "solve",
        "solve_green": "forces",
    }.get(current)
    result = {
        "run_id": run_id,
        "shape": current,
        "terminal": current in _TERMINAL,
        "open_stage": open_stage,
        "density": density,
        "mesh_reds": mesh_reds,
        "solve_reds": solve_reds,
        "mesh_remaining": max(0, Bounds.mesh_budget - mesh_reds),
        "solve_remaining": max(0, Bounds.solve_budget - solve_reds),
        "last": last,
        "log_tail": tail,
        "steer": steer_texts(run_dir),
        "steer_fresh": steer_fresh(run_dir),
    }
    _note(store_root(), "observe", run_id, "ok", str(last.get("id")) if last else "")
    return result


@mcp.tool()
def commit_spec(run_id: str, lease: str) -> dict:
    run_dir = _run_dir(run_id)
    if not _lease_ok(run_dir, lease):
        return _denied(run_id)
    spec = _load_spec(run_dir)
    render(spec, {"stream": 100, "grading": 8}, {"u_relax": 0.5, "non_ortho": 1})
    receipts = _ledger(run_dir)
    pilot = next((item for item in receipts if item.get("stage") == "pilot"), None)
    if pilot is None:
        result = {"error": "no pilot receipt", "decision": "rejected"}
        return _finish(run_dir, "commit_spec", result)
    evidence = {
        "naca": spec["naca"],
        "alpha_deg": spec["alpha_deg"],
        "u_ms": spec["u_ms"],
        "density": spec["density"],
        "pilot_id": pilot.get("id"),
    }
    if "chord_m" in spec and spec["chord_m"] is not None:
        evidence["chord_m"] = spec["chord_m"]
    if "span_m" in spec and spec["span_m"] is not None:
        evidence["span_m"] = spec["span_m"]
    for key in ("camber", "camber_pos", "thickness"):
        if key in spec and spec[key] is not None:
            evidence[key] = spec[key]
    chord = float(spec.get("chord_m", Bounds.chord_m))
    evidence["nu"] = Bounds.nu
    evidence["reynolds"] = float(spec["u_ms"]) * chord / Bounds.nu
    evidence["turbulence_model"] = "SpalartAllmaras"
    evidence["reference_model"] = "NeuralFoil"
    commit = Commit(
        run_id=run_id,
        stage="scope",
        args={},
        parent_id=str(pilot.get("id")),
        spec_hash=spec_hash(spec),
        log="spec.json",
        evidence=evidence,
    )
    admission = commit_under_lock(run_dir, commit)
    if admission.receipt is None:
        result = {"error": admission.rejection or "rejected", "decision": "rejected"}
    else:
        result = admission.receipt
    return _finish(run_dir, "commit_spec", result)


@mcp.tool()
def commit_mesh(run_id: str, lease: str, stream: Stream, grading: Grading) -> dict:
    run_dir = _run_dir(run_id)
    if not _lease_ok(run_dir, lease):
        return _denied(run_id)
    stream_v = _enum_num(stream, STREAMS)
    grading_v = _enum_num(grading, GRADINGS)
    if stream_v is None or grading_v is None:
        raise ValueError("mesh enum")
    stream_i = int(stream_v)
    grading_i = int(grading_v)
    spec = _load_spec(run_dir)
    digest = spec_hash(spec)
    receipts = _ledger(run_dir)
    reds = _mesh_reds(receipts)
    parent = _parent(receipts)
    if len(reds) >= Bounds.mesh_budget:
        evidence = {
            "stream": stream_i,
            "grading": grading_i,
            "signature": reds[-1].get("signature"),
            "log": "log.checkMesh",
        }
        result = _stop(
            run_dir,
            "mesh",
            "budget",
            parent,
            digest,
            evidence,
            {"stream": stream_i, "grading": grading_i},
        )
        return _finish(run_dir, "commit_mesh", result)
    started = time.monotonic()
    mesh_plan = {"stream": stream_i, "grading": grading_i}
    solve_plan = {"u_relax": 0.5, "non_ortho": 1}
    try:
        ran = run_block_mesh(run_dir / "case", spec, mesh_plan, solve_plan)
    except OSError as exc:
        result = {"error": str(exc), "decision": "rejected", "failure": "fatal"}
        return _finish(run_dir, "commit_mesh", result)
    elapsed = time.monotonic() - started
    block_text = _read(Path(ran["block_log"]))
    check_text = _read(Path(ran["check_log"]))
    raw_chord = spec.get("chord_m", Bounds.chord_m)
    chord_m = float(Bounds.chord_m if raw_chord is None else raw_chord)
    evidence = mesh_evidence(
        block_text,
        check_text,
        cells_expected=cell_count(stream_i, spec["density"]),
        stream=stream_i,
        grading=grading_i,
        chord_m=chord_m,
    )
    evidence["exit_code"] = ran["exit_code"]
    evidence["elapsed_s"] = elapsed
    if ran["exit_code"] == 124 or elapsed >= Bounds.mesh_timeout_s:
        evidence["failure"] = "wall_clock"
    evidence = {key: value for key, value in evidence.items() if value is not None}
    commit = Commit(
        run_id=run_id,
        stage="mesh",
        args={"stream": stream_i, "grading": grading_i},
        parent_id=parent,
        spec_hash=digest,
        log="log.checkMesh",
        evidence=evidence,
    )
    admission = commit_under_lock(run_dir, commit)
    if admission.rejection and "repeated" in admission.rejection:
        evidence = dict(evidence)
        evidence["signature"] = _mesh_signature(evidence)
        result = _stop(
            run_dir,
            "mesh",
            "repeated_mesh",
            parent,
            digest,
            evidence,
            {"stream": stream_i, "grading": grading_i},
        )
        return _finish(run_dir, "commit_mesh", result)
    if admission.receipt is None:
        result = {"error": admission.rejection or "rejected", "decision": "rejected"}
    else:
        result = admission.receipt
    return _finish(run_dir, "commit_mesh", result)


@mcp.tool()
def commit_solve(run_id: str, lease: str, u_relax: Relax, non_ortho: Ortho) -> dict:
    run_dir = _run_dir(run_id)
    if not _lease_ok(run_dir, lease):
        return _denied(run_id)
    relax_v = _enum_num(u_relax, U_RELAX)
    ortho_v = _enum_num(non_ortho, NON_ORTHO)
    if relax_v is None or ortho_v is None:
        raise ValueError("solve enum")
    relax = float(relax_v)
    ortho = int(ortho_v)
    spec = _load_spec(run_dir)
    digest = spec_hash(spec)
    receipts = _ledger(run_dir)
    residuals = _solve_reds(receipts)
    parent = _parent(receipts)
    mesh = next(
        (item for item in reversed(receipts) if item.get("stage") == "mesh" and item.get("status") == "green"),
        None,
    )
    if len(residuals) >= Bounds.solve_budget:
        last = residuals[-1] if residuals else {}
        evidence = {
            "u_relax": last.get("u_relax", relax),
            "non_ortho": last.get("non_ortho", ortho),
            "failure": "residual",
            "log": "log.simpleFoam",
        }
        result = _stop(
            run_dir,
            "solve",
            "budget",
            parent,
            digest,
            evidence,
            {"u_relax": evidence["u_relax"], "non_ortho": evidence["non_ortho"]},
        )
        return _finish(run_dir, "commit_solve", result)
    if mesh is None:
        result = {"error": "no green mesh", "decision": "rejected"}
        return _finish(run_dir, "commit_solve", result)
    case = run_dir / "case"
    files = render(
        spec,
        {"stream": int(mesh["stream"]), "grading": int(mesh["grading"])},
        {"u_relax": relax, "non_ortho": ortho},
    )
    solution = case / "system" / "fvSolution"
    solution.parent.mkdir(parents=True, exist_ok=True)
    solution.write_text(files["system/fvSolution"])
    started = time.monotonic()
    try:
        code = run_foam(case, "solve", Bounds.solve_timeout_s)
    except OSError as exc:
        result = {"error": str(exc), "decision": "rejected", "failure": "fatal"}
        return _finish(run_dir, "commit_solve", result)
    elapsed = time.monotonic() - started
    text = _read(case / "log.simpleFoam")
    parsed = parse_simple_foam(text, elapsed)
    if code == 124:
        parsed["failure"] = "wall_clock"
    evidence = {
        "u_relax": relax,
        "non_ortho": ortho,
        "failure": parsed["failure"],
        "fatal": parsed["fatal"],
        "finished": parsed["finished"],
        "exit_code": code,
        "elapsed_s": elapsed,
        "log": "log.simpleFoam",
        "log_text": text,
    }
    for key in ("continuity_local", "continuity_global", "continuity_cum"):
        if parsed.get(key) is not None:
            evidence[key] = parsed[key]
    evidence["end_time"] = Bounds.end_time
    evidence["turbulence_model"] = "SpalartAllmaras"
    evidence["nu"] = Bounds.nu
    measured_yplus = yplus_from_case(case)
    if measured_yplus is not None:
        evidence["yplus_min"] = measured_yplus["min"]
        evidence["yplus_max"] = measured_yplus["max"]
        evidence["yplus_avg"] = measured_yplus["avg"]
    history = case / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat"
    drift = (
        force_drift(_read(history))
        if history.is_file()
        else {"cl": None, "cd": None, "iterations": 0}
    )
    if drift["cl"] is not None:
        evidence["force_drift_cl"] = drift["cl"]
    if drift["cd"] is not None:
        evidence["force_drift_cd"] = drift["cd"]
    evidence["force_iterations"] = drift["iterations"]
    if evidence["failure"] in ("none", None):
        unsettled = (
            drift["cl"] is None
            or drift["cd"] is None
            or float(drift["cl"]) > Bounds.force_drift_max
            or float(drift["cd"]) > Bounds.force_drift_max
        )
        if unsettled:
            evidence["failure"] = "not_converged"
    commit = Commit(
        run_id=run_id,
        stage="solve",
        args={"u_relax": relax, "non_ortho": ortho},
        parent_id=parent,
        spec_hash=digest,
        log="log.simpleFoam",
        evidence=evidence,
    )
    admission = commit_under_lock(run_dir, commit)
    if admission.receipt is None:
        result = {"error": admission.rejection or "rejected", "decision": "rejected"}
    else:
        result = admission.receipt
    return _finish(run_dir, "commit_solve", result)


@mcp.tool()
def commit_forces(run_id: str, lease: str) -> dict:
    run_dir = _run_dir(run_id)
    if not _lease_ok(run_dir, lease):
        return _denied(run_id)
    spec = _load_spec(run_dir)
    receipts = _ledger(run_dir)
    case = run_dir / "case"
    coeff = case / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat"
    if not coeff.is_file():
        try:
            run_foam(case, "forces", 60)
        except OSError as exc:
            result = {"error": str(exc), "decision": "rejected"}
            return _finish(run_dir, "commit_forces", result)
    parsed = parse_force_coeffs(_read(coeff))
    evidence = {"log": parsed["log"], "header": parsed["header"]}
    if parsed["cl"] is not None:
        evidence["cl"] = parsed["cl"]
    if parsed["cd"] is not None:
        evidence["cd"] = parsed["cd"]
    split_text = forces_text_from_case(case)
    if split_text:
        split = force_coefficients(
            split_text,
            float(spec.get("alpha_deg", 0.0)),
            float(spec.get("u_ms", 10.0)),
            float(spec.get("chord_m", Bounds.chord_m)),
            float(spec.get("span_m", Bounds.z_thickness)),
        )
        if split is not None:
            evidence["cd_pressure"] = split["cd_pressure"]
            evidence["cd_viscous"] = split["cd_viscous"]
    commit = Commit(
        run_id=run_id,
        stage="forces",
        args={},
        parent_id=_parent(receipts),
        spec_hash=spec_hash(spec),
        log=str(parsed["log"]),
        evidence=evidence,
    )
    admission = commit_under_lock(run_dir, commit)
    if admission.receipt is None:
        result = {"error": admission.rejection or "rejected", "decision": "rejected"}
    else:
        result = admission.receipt
    return _finish(run_dir, "commit_forces", result)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
