"""Outer gates. Reads the ledger and the case. Does not read an agent transcript."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from wingline.kernel import (
    Bounds,
    Panel,
    Refusal,
    judge_forces,
    judge_mesh,
    judge_pilot,
    judge_physics,
    judge_solve,
    load_ledger,
    parse_naca,
    physics_check,
    render_lean,
    render_shacl,
    _scope_ok,
)
from wingline.prompts import REVIEW_SYSTEM

_REPO = Path(__file__).resolve().parents[1]
_PYSHACL = _REPO / ".venv" / "bin" / "pyshacl"


def find_lean() -> Path:
    on_path = shutil.which("lean")
    if on_path:
        return Path(on_path)
    candidates = [
        Path.home() / ".elan" / "bin" / "lean",
        Path("/opt/homebrew/bin/lean"),
        Path("/usr/local/bin/lean"),
        Path("/Users/saarthakbhatia/.elan/bin/lean"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


_LEAN = find_lean()
_TERMINAL_REVIEW = ("affirm", "challenge")


def _goal(scope: dict | None) -> str:
    if not scope:
        return ""
    return (
        f"NACA {scope.get('naca')} at {scope.get('alpha_deg')} degrees, "
        f"{scope.get('u_ms')} m/s, {scope.get('density')}."
    )


def _find(receipts: list[dict], stage: str, status: str | None = None) -> dict | None:
    found = None
    for receipt in receipts:
        if receipt.get("stage") != stage:
            continue
        if status is not None and receipt.get("status") != status:
            continue
        found = receipt
    return found


def _refusal(gate: str, log: str, reason: str, receipts: list[dict]) -> Refusal:
    scope = _find(receipts, "scope")
    return Refusal(
        gate=gate,
        log=log,
        reason=reason,
        panels=(Panel("goal", _goal(scope)), Panel("refused", gate)),
    )


def _policy_differs() -> bool:
    shape = _REPO / "harness" / "shapes" / "receipt.shacl.ttl"
    lean = _REPO / "harness" / "gate" / "Gate.lean"
    if not shape.is_file() or not lean.is_file():
        return True
    return shape.read_text() != render_shacl() or lean.read_text() != render_lean()


def _graph(receipts: list[dict]) -> dict:
    nodes = []
    for receipt in receipts:
        node: dict = {
            "@id": f"https://vertical-labs.local/run/{receipt.get('id')}",
            "@type": receipt.get("type"),
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


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _log_text(run_dir: Path, receipt: dict) -> str:
    name = str(receipt.get("log") or "")
    for candidate in (run_dir / "case" / name, run_dir / name):
        if candidate.is_file():
            return candidate.read_text(errors="replace")
    return ""


def _judge_status(run_dir: Path, receipt: dict) -> str | None:
    """Return green or red from the same judges. None if the row is a stop."""
    status = receipt.get("status")
    if status == "stopped":
        why = receipt.get("why")
        if why in ("repeated_mesh", "budget", "fatal", "wall_clock"):
            return None
        return "red"
    stage = receipt.get("stage")
    evidence = dict(receipt)
    if stage == "pilot":
        ok = judge_pilot(evidence)
    elif stage == "scope":
        ok = _scope_ok(evidence)
    elif stage == "mesh":
        text = _log_text(run_dir, receipt)
        if text:
            evidence["log_text"] = text
            evidence["mesh_ok"] = "Mesh OK" in text
        elif "mesh_ok" not in evidence:
            return "red" if status == "green" else "red"
        ok = judge_mesh(evidence)
    elif stage == "solve":
        text = _log_text(run_dir, receipt)
        if text:
            evidence["log_text"] = text
        elif status == "green":
            return "red"
        ok = judge_solve(evidence)
    elif stage == "forces":
        ok = judge_forces(evidence)
    else:
        return "red"
    return "green" if ok else "red"


def _review_evidence(run_dir: Path, receipts: list[dict]) -> dict:
    """The reviewer sees the measurements, not just the requested spec."""
    scope = _find(receipts, "scope") or {}
    mesh = _find(receipts, "mesh", "green") or _find(receipts, "mesh") or {}
    solve = _find(receipts, "solve", "green") or _find(receipts, "solve") or {}
    forces = _find(receipts, "forces", "green") or _find(receipts, "forces") or {}
    evidence: dict = {
        "run_id": receipts[-1].get("run_id") if receipts else "",
        "spec": {
            "naca": scope.get("naca"),
            "alpha_deg": scope.get("alpha_deg"),
            "u_ms": scope.get("u_ms"),
            "density": scope.get("density"),
            "chord_m": scope.get("chord_m", Bounds.chord_m),
        },
        "mesh": {
            "stream": mesh.get("stream"),
            "grading": mesh.get("grading"),
            "cells": mesh.get("cells"),
            "cells_expected": mesh.get("cells_expected"),
            "max_non_ortho": mesh.get("max_non_ortho"),
            "max_skew": mesh.get("max_skew"),
            "n_normal": mesh.get("n_normal"),
            "wall_grading": mesh.get("wall_grading"),
            "first_cell_m": mesh.get("first_cell_m"),
            "status": mesh.get("status"),
        },
        "solve": {
            "continuity_local": solve.get("continuity_local"),
            "continuity_global": solve.get("continuity_global"),
            "continuity_cum": solve.get("continuity_cum"),
            "continuity_bound": Bounds.continuity_local,
            "end_time": solve.get("end_time"),
            "turbulence_model": solve.get("turbulence_model"),
            "yplus_min": solve.get("yplus_min"),
            "yplus_max": solve.get("yplus_max"),
            "yplus_avg": solve.get("yplus_avg"),
            "force_drift_cl": solve.get("force_drift_cl"),
            "force_drift_cd": solve.get("force_drift_cd"),
            "force_iterations": solve.get("force_iterations"),
            "failure": solve.get("failure"),
            "status": solve.get("status"),
        },
        "forces": {
            "cl": forces.get("cl"),
            "cd": forces.get("cd"),
            "cd_pressure": forces.get("cd_pressure"),
            "cd_viscous": forces.get("cd_viscous"),
            "status": forces.get("status"),
        },
    }
    try:
        measured_cl = float(forces["cl"])
        measured_cd = float(forces["cd"])
        passes, physics_evidence, reason = physics_check(
            measured_cl,
            scope["naca"],
            float(scope["alpha_deg"]),
            float(scope.get("u_ms", 10.0)),
            float(scope.get("chord_m", Bounds.chord_m)),
        )
        evidence["physics"] = {
            **physics_evidence,
            "measured_cd": measured_cd,
            "reason": reason,
            "passes": passes,
        }
    except (KeyError, TypeError, ValueError) as exc:
        evidence["physics"] = {
            "model": "NeuralFoil",
            "passes": False,
            "reason": f"physics evidence unavailable: {exc}",
        }
    last = receipts[-1] if receipts else {}
    if last.get("status") == "red":
        tail = _log_text(run_dir, last)
        if tail:
            evidence["last_log_tail"] = "\n".join(tail.splitlines()[-25:])
    return evidence


def _review_text(stdout: str) -> str:
    """Prefer the final assistant text over thoughts in a headless JSON result."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(payload, dict):
        for key in ("text", "result", "structured_output", "structuredOutput"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return stdout


def _review(run_dir: Path, receipts: list[dict]) -> str:
    # The CLI default is the agent, so an unset value means the reviewer runs.
    if os.environ.get("WINGLINE_AGENT", "1") == "0":
        return "challenge"
    grok = shutil.which("grok")
    if not grok:
        return "challenge"
    evidence = _review_evidence(run_dir, receipts)
    prompt = run_dir / "outer" / "review-prompt.txt"
    prompt.write_text(
        "\n".join(
            [
                "Reply with one JSON object only:",
                '{"verdict":"affirm"|"challenge","gate":"...","note":"..."}',
                "Evidence: " + json.dumps(evidence, sort_keys=True),
                "Affirm only when every receipt is green, the mesh cell count matches cells_expected,",
                "the solve local continuity is within continuity_bound, Cd is positive, and the",
                "measured Cl is inside the NeuralFoil reference tolerance in the physics block.",
                "If the physics block says passes=false or is missing, challenge.",
                "Reference Cd is context, not a pass or fail. A forced-transition Cd is context.",
                "cd_pressure and cd_viscous are context. A green solve has force_drift_cl and",
                "force_drift_cd at or below the bound when those fields are present.",
                "y+ is reported when present and is not required to be below 1.",
                "You cannot pass a run and you cannot edit a gate. You cannot read files; the evidence",
                "above is complete.",
                "",
            ]
        )
    )
    # Run in a scratch cwd. The project .grok hooks and skills belong to the
    # inner loop; loading them in the review seat makes its Stop hook fight
    # the reviewer for a receipt the reviewer is not allowed to produce.
    try:
        with tempfile.TemporaryDirectory(prefix="wingline-review-") as scratch:
            cmd = [
                grok,
                "--prompt-file",
                str(prompt.resolve()),
                "--cwd",
                scratch,
                "--system-prompt-override",
                REVIEW_SYSTEM,
                "-m",
                "grok-4.7",
                "--reasoning-effort",
                "xhigh",
                "--max-turns",
                "3",
                "--output-format",
                "json",
                "--no-subagents",
                "--disable-web-search",
                "--permission-mode",
                "dontAsk",
                "--tools",
                "",
                "--disallowed-tools",
                "run_terminal_command,write,search_replace,spawn_subagent,web_search,web_fetch,read_file,grep",
            ]
            proc = subprocess.run(
                cmd,
                cwd=scratch,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
                env=os.environ.copy(),
            )
    except (OSError, subprocess.TimeoutExpired):
        return "challenge"
    text = _review_text(proc.stdout or "")
    found = re.findall(r'"verdict"\s*:\s*"(affirm|challenge)"', text)
    verdict = found[-1] if found else "challenge"
    if verdict not in _TERMINAL_REVIEW:
        verdict = "challenge"
    (run_dir / "outer" / "review.json").write_text(
        json.dumps(
            {
                "evidence": evidence,
                "verdict": verdict,
                "text": text[-4000:],
                "stdout": (proc.stdout or "")[-4000:],
                "stderr": (proc.stderr or "")[-1000:],
            },
            indent=2,
        )
        + "\n"
    )
    return verdict


def _write_verdict(run_dir: Path, payload: dict) -> None:
    outer = run_dir / "outer"
    outer.mkdir(parents=True, exist_ok=True)
    (outer / "verdict.json").write_text(json.dumps(payload, indent=2) + "\n")


def evaluate(
    run_dir: Path,
    *,
    pyshacl: Path | None = None,
    lean: Path | None = None,
) -> Refusal | None:
    """First failure wins. None means the caller should project the verdict."""
    run_dir = Path(run_dir)
    receipts = load_ledger(run_dir / "ledger") if (run_dir / "ledger").is_dir() else []
    if _policy_differs():
        return _refusal("bounds", "harness/shapes/receipt.shacl.ttl", "emitted policy differs", receipts)

    outer = run_dir / "outer"
    outer.mkdir(parents=True, exist_ok=True)
    graph_path = outer / "graph.jsonld"
    graph_path.write_text(json.dumps(_graph(receipts)))

    shacl_bin = Path(pyshacl) if pyshacl is not None else _PYSHACL
    shape = _REPO / "harness" / "shapes" / "receipt.shacl.ttl"
    if not shacl_bin.is_file():
        refusal = _refusal("shacl", str(shacl_bin), "pyshacl missing", receipts)
        _write_verdict(run_dir, {"gate": "shacl", "physics": "red", "review": "challenge"})
        return refusal
    try:
        shacl_proc = _run(
            [str(shacl_bin), "-s", str(shape), "-df", "json-ld", str(graph_path)],
            120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        refusal = _refusal("shacl", str(graph_path), str(exc), receipts)
        _write_verdict(run_dir, {"gate": "shacl", "physics": "red", "review": "challenge"})
        return refusal
    if shacl_proc.returncode != 0:
        reason = (shacl_proc.stderr or shacl_proc.stdout or "pyshacl failed").strip()
        refusal = _refusal("shacl", str(graph_path), reason[-500:], receipts)
        _write_verdict(run_dir, {"gate": "shacl", "physics": "red", "review": "challenge"})
        return refusal

    skip_lean = lean is None and os.environ.get("WINGLINE_VERIFY_LEAN") == "0"
    if not skip_lean:
        lean_bin = Path(lean) if lean is not None else _LEAN
        lean_file = _REPO / "harness" / "gate" / "Gate.lean"
        if not lean_bin.is_file():
            refusal = _refusal("lean", str(lean_bin), "lean missing", receipts)
            _write_verdict(run_dir, {"gate": "lean", "physics": "red", "review": "challenge"})
            return refusal
        try:
            lean_proc = _run([str(lean_bin), str(lean_file)], 180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            refusal = _refusal("lean", str(lean_file), str(exc), receipts)
            _write_verdict(run_dir, {"gate": "lean", "physics": "red", "review": "challenge"})
            return refusal
        if lean_proc.returncode != 0:
            reason = (lean_proc.stderr or lean_proc.stdout or "lean failed").strip()
            refusal = _refusal("lean", str(lean_file), reason[-500:], receipts)
            _write_verdict(run_dir, {"gate": "lean", "physics": "red", "review": "challenge"})
            return refusal

    for receipt in receipts:
        judged = _judge_status(run_dir, receipt)
        if judged is None:
            continue
        if judged != receipt.get("status"):
            gate = str(receipt.get("stage") or "incomplete")
            refusal = _refusal(gate, str(receipt.get("log") or ""), "rejudge disagreed", receipts)
            _write_verdict(run_dir, {"gate": gate, "physics": "red", "review": "challenge"})
            return refusal

    bad = next((item for item in receipts if item.get("status") in ("red", "stopped")), None)
    if bad is not None:
        _write_verdict(
            run_dir,
            {"gate": bad.get("stage"), "physics": "red", "review": "challenge"},
        )
        return None

    scope = _find(receipts, "scope", "green")
    forces = _find(receipts, "forces", "green")
    physics = "red"
    if scope is not None and forces is not None:
        try:
            if judge_physics(
                float(forces["cl"]),
                scope["naca"],
                float(scope["alpha_deg"]),
                float(scope.get("u_ms", 10.0)),
                float(scope.get("chord_m", 1.0)),
            ):
                physics = "green"
        except (KeyError, TypeError, ValueError):
            physics = "red"
    if physics != "green":
        _write_verdict(run_dir, {"physics": "red", "review": "challenge"})
        return None
    review = _review(run_dir, receipts)
    if review not in _TERMINAL_REVIEW:
        review = "challenge"
    _write_verdict(run_dir, {"physics": physics, "review": review})
    return None


# Bounds is referenced so a policy edit cannot drift unnoticed in this module's import.
_ = Bounds.screening_sentence

# One finite-difference step, serial, after the direct case.
# The first four shifts are the original step. The command line stops there.
# The workbench switch runs the longer list and stops at ten.
EXPLORE_BUDGET = 4
EXPLORE_CAP = 10
_NEIGHBORS = (
    (0, -2),
    (0, 2),
    (1, 0),
    (-1, 0),
    (0, -4),
    (0, 4),
    (2, 0),
    (-2, 0),
    (1, -2),
    (-1, 2),
    (1, 2),
    (-1, -2),
)
_OBJECTIVES = {
    "min_cd": "lower drag",
    "max_cl": "higher lift",
    "max_ld": "higher lift over drag",
}


def _shift_naca(code: str, dm: int, dt: int) -> str | None:
    foil = parse_naca(code)
    camber = foil.m + dm
    thickness = foil.t + dt
    position = foil.p
    if not 0 <= camber <= 9:
        return None
    if camber == 0:
        position = 0
    elif position == 0:
        position = 4
    try:
        return parse_naca(f"{camber}{position}{thickness:02d}").code()
    except ValueError:
        return None


def probes(spec: dict, *, alpha_fixed: bool, budget: int = EXPLORE_BUDGET) -> list[dict]:
    """Nearby specs. Angle stays put when the engineer named it."""
    found: list[dict] = []
    seen = {(str(spec["naca"]), float(spec["alpha_deg"]))}
    for dm, dt in _NEIGHBORS:
        code = _shift_naca(str(spec["naca"]), dm, dt)
        if code is None:
            continue
        key = (code, float(spec["alpha_deg"]))
        if key in seen:
            continue
        seen.add(key)
        row = dict(spec)
        row["naca"] = code
        found.append(row)
        if len(found) >= budget:
            return found
    if alpha_fixed:
        return found
    for step in (-2.0, 2.0):
        alpha = float(spec["alpha_deg"]) + step
        if not (Bounds.alpha_min <= alpha <= Bounds.alpha_max):
            continue
        key = (str(spec["naca"]), alpha)
        if key in seen:
            continue
        seen.add(key)
        row = dict(spec)
        row["alpha_deg"] = int(alpha) if alpha.is_integer() else alpha
        found.append(row)
        if len(found) >= budget:
            break
    return found


def _score(cl: object, cd: object, objective: str) -> float | None:
    try:
        lift = float(cl)  # type: ignore[arg-type]
        drag = float(cd)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if drag <= 0:
        return None
    if objective == "min_cd":
        return -drag
    if objective == "max_cl":
        return lift
    return lift / drag


def better(direct: dict, rows: list[dict], objective: str) -> dict | None:
    """The neighbor with the best strict improvement, or None."""
    base = _score(direct.get("cl"), direct.get("cd"), objective)
    if base is None:
        return None
    winner = None
    winner_score = base
    for row in rows:
        value = _score(row.get("cl"), row.get("cd"), objective)
        if value is None:
            continue
        if value > winner_score + 1e-9:
            winner = row
            winner_score = value
    return winner


def _gate_name(outcome: object) -> str:
    if isinstance(outcome, Refusal):
        return outcome.gate
    name = type(outcome).__name__
    if name == "Screening":
        return "screening"
    if name == "DesignGrade":
        return "design"
    return "incomplete"


def measure(store: Path, view: object) -> dict:
    """Coefficients come from a green forces receipt. The gate comes from the projection."""
    outcome = getattr(view, "outcome", view)
    run_id = str(getattr(view, "run_id", "") or getattr(outcome, "run_id", "") or "")
    cl = None
    cd = None
    spec: dict = {}
    if run_id:
        folder = Path(store) / run_id
        spec_path = folder / "spec.json"
        if spec_path.is_file():
            try:
                spec = json.loads(spec_path.read_text())
            except json.JSONDecodeError:
                spec = {}
        ledger = folder / "ledger"
        if ledger.is_dir():
            for item in reversed(load_ledger(ledger)):
                if item.get("stage") == "forces" and item.get("status") == "green":
                    try:
                        cl = float(item["cl"])
                        cd = float(item["cd"])
                    except (KeyError, TypeError, ValueError):
                        cl = None
                        cd = None
                    break
    return {
        "run_id": run_id,
        "gate": _gate_name(outcome),
        "cl": cl,
        "cd": cd,
        "naca": spec.get("naca", ""),
        "alpha_deg": spec.get("alpha_deg", ""),
        "u_ms": spec.get("u_ms", ""),
    }


def _label(row: dict) -> str:
    return f"NACA {row.get('naca')} at {row.get('alpha_deg')} degrees"


def _fmt(kind: str, row: dict) -> str:
    label = _label(row)
    if row.get("cl") is None or row.get("cd") is None:
        return f"{kind}: {label}. Gate {row.get('gate')}. No coefficients."
    return (
        f"{kind}: {label}. Gate {row.get('gate')}. "
        f"Cl {float(row['cl']):.3f}. Cd {float(row['cd']):.3f}."
    )


def _same_number(left: object, right: object) -> bool:
    try:
        return float(left) == float(right)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def board_view(payload: dict) -> dict:
    """One row per searched section. The stored neighbor is the mark. No second ranking."""
    raw_rows = payload.get("rows") if isinstance(payload, dict) else None
    rows = raw_rows if isinstance(raw_rows, list) else []
    best_naca = payload.get("best_naca") if isinstance(payload, dict) else None
    best_alpha = payload.get("best_alpha_deg") if isinstance(payload, dict) else None
    rendered: list[dict] = []
    marked = False
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        best = False
        if (
            not marked
            and index > 0
            and best_naca not in (None, "")
            and str(row.get("naca") or "") == str(best_naca)
            and _same_number(row.get("alpha_deg"), best_alpha)
        ):
            best = True
            marked = True
        rendered.append(
            {
                "naca": row.get("naca") or "",
                "alpha_deg": row.get("alpha_deg", ""),
                "cl": row.get("cl"),
                "cd": row.get("cd"),
                "gate": row.get("gate") or "",
                "direct": index == 0,
                "best": best,
            }
        )
    asked = [row for row in rendered if row["direct"]]
    neighborhood = [row for row in rendered if not row["direct"]]
    origin = asked[0] if asked else {}
    neighborhood.sort(key=lambda row: _section_distance(origin, row), reverse=True)
    rendered = asked + neighborhood
    return {
        "ready": bool(rendered),
        "objective": str(payload.get("objective") or "") if isinstance(payload, dict) else "",
        "sentence": Bounds.design_sentence,
        "rows": rendered,
    }


def board_lines(objective: str, rows: list[dict], best: dict | None) -> list[str]:
    aim = _OBJECTIVES.get(objective, objective)
    lines = [f"outer: compared nearby wings on {aim}."]
    for index, row in enumerate(rows):
        lines.append(_fmt("direct" if index == 0 else "probed", row))
    if best is None:
        lines.append("outer: no nearby wing beat the direct case on this pass.")
        return lines
    lines.append(
        f"outer: {_label(best)} is the better neighbor on this coarse pass. "
        "The numbers are the log measurements. This is not a hardware decision."
    )
    return lines


def _write_search(
    store: Path,
    run_id: str,
    objective: str,
    rows: list[dict],
    best: dict | None,
    budget: int,
) -> None:
    folder = Path(store) / run_id / "outer" if run_id else Path(store)
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "objective": objective,
        "budget": budget,
        "rows": rows,
        "best_naca": None if best is None else best.get("naca"),
        "best_alpha_deg": None if best is None else best.get("alpha_deg"),
    }
    (folder / "search.json").write_text(json.dumps(payload, indent=2) + "\n")


def _probe_sentence(neighbor: dict) -> str:
    text = (
        f"NACA {neighbor['naca']} at {neighbor['alpha_deg']} degrees"
        f" and {neighbor['u_ms']} m/s"
    )
    if neighbor.get("chord_m") is not None:
        text += f" chord {neighbor['chord_m']}m"
    if neighbor.get("span_m") is not None:
        text += f" span {neighbor['span_m']}m"
    return text + "."


def mark_origin(store: Path, origin_id: str, run_id: str) -> None:
    """Tie a neighborhood run to the wing that was asked for. The asked run stays first."""
    if not origin_id or not run_id or origin_id == run_id:
        return
    if origin_id != Path(origin_id).name or run_id != Path(run_id).name:
        return
    if not origin_id.startswith("run-") or not run_id.startswith("run-"):
        return
    folder = Path(store) / run_id
    if not folder.is_dir():
        return
    (folder / "origin").write_text(origin_id + "\n")


def _section_distance(origin: dict, row: dict) -> tuple[float, float]:
    """Camber, position, and thickness steps, then angle. Larger means farther out."""
    try:
        asked = parse_naca(str(origin.get("naca") or ""))
        other = parse_naca(str(row.get("naca") or ""))
        section = float(abs(asked.m - other.m) + abs(asked.p - other.p) + abs(asked.t - other.t))
    except (TypeError, ValueError):
        section = 0.0
    try:
        angle = abs(float(origin.get("alpha_deg")) - float(row.get("alpha_deg")))
    except (TypeError, ValueError):
        angle = 0.0
    return (section, angle)


def explore(
    store: Path,
    study: object,
    origin: object,
    run_case,
    budget: int | None = None,
) -> list[str]:
    """Grade is already done. This is the search: nearby wings, one at a time."""
    cap = EXPLORE_BUDGET if budget is None else max(0, int(budget))
    direct = measure(store, origin)
    spec = getattr(study, "spec", {})
    if not direct["naca"]:
        direct["naca"] = spec.get("naca", "")
        direct["alpha_deg"] = spec.get("alpha_deg", "")
        direct["u_ms"] = spec.get("u_ms", "")
    rows = [direct]
    last = Path(store) / "last_spec.json"
    backup = last.read_text() if last.is_file() else None
    try:
        alpha_fixed = bool(getattr(study, "alpha_fixed", True))
        for neighbor in probes(spec, alpha_fixed=alpha_fixed, budget=cap):
            view = run_case(_probe_sentence(neighbor))
            row = measure(store, view)
            if not row["naca"]:
                row["naca"] = neighbor["naca"]
                row["alpha_deg"] = neighbor["alpha_deg"]
                row["u_ms"] = neighbor["u_ms"]
            rows.append(row)
            mark_origin(Path(store), str(direct.get("run_id") or ""), str(row.get("run_id") or ""))
    finally:
        if backup is None:
            if last.exists():
                last.unlink()
        else:
            last.write_text(backup)
    objective = str(getattr(study, "objective", "max_ld"))
    chosen = better(direct, rows[1:], objective)
    _write_search(store, str(direct.get("run_id") or ""), objective, rows, chosen, cap)
    return board_lines(objective, rows, chosen)
