"""Bounds, legal prefix, judges, admit, project, and policy emit.

Screening and DesignGrade are built only by project. hardware_decision
accepts only a DesignGrade.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from wingline.reference import ReferenceError, reference_polar

SHAPES: tuple[str, ...] = (
    "empty",
    "pilot_red",
    "pilot_green",
    "scoped",
    "mesh_open",
    "mesh_green",
    "mesh_closed",
    "solve_open",
    "solve_green",
    "solve_closed",
    "forces_green",
    "forces_red",
)

# (shape, stage, status, destination). NEXT and the Lean step are this table.
_EDGES: tuple[tuple[str, str, str, str], ...] = (
    ("empty", "pilot", "green", "pilot_green"),
    ("empty", "pilot", "red", "pilot_red"),
    ("pilot_green", "scope", "green", "scoped"),
    ("scoped", "mesh", "green", "mesh_green"),
    ("scoped", "mesh", "red", "mesh_open"),
    ("mesh_open", "mesh", "green", "mesh_green"),
    ("mesh_open", "mesh", "red", "mesh_open"),
    ("mesh_open", "mesh", "stopped", "mesh_closed"),
    ("mesh_green", "solve", "green", "solve_green"),
    ("mesh_green", "solve", "red", "solve_open"),
    ("solve_open", "solve", "green", "solve_green"),
    ("solve_open", "solve", "red", "solve_open"),
    ("solve_open", "solve", "stopped", "solve_closed"),
    ("solve_green", "forces", "green", "forces_green"),
    ("solve_green", "forces", "red", "forces_red"),
)

TRANSITION: dict[tuple[str, str, str], str] = {
    (shape, stage, status): dest for shape, stage, status, dest in _EDGES
}

def _build_next() -> dict[str, tuple[tuple[str, str], ...]]:
    table: dict[str, list[tuple[str, str]]] = {name: [] for name in SHAPES}
    for shape, stage, status, _dest in _EDGES:
        table[shape].append((stage, status))
    return {name: tuple(edges) for name, edges in table.items()}


NEXT: dict[str, tuple[tuple[str, str], ...]] = _build_next()

STREAMS: tuple[int, ...] = (60, 80, 100)
GRADINGS: tuple[int, ...] = (2, 4, 8, 12)
U_RELAX: tuple[float, ...] = (0.7, 0.5, 0.3)
NON_ORTHO: tuple[int, ...] = (0, 1, 2)

_STAGE_TYPE = {
    "pilot": "PilotReceipt",
    "scope": "ScopeReceipt",
    "mesh": "MeshReceipt",
    "solve": "SolveReceipt",
    "forces": "ForcesReceipt",
}

_MINT = 0


class Bounds:
    """Single numeric source. Emit copies these into SHACL and Lean."""

    alpha_min = -2.0
    alpha_max = 10.0
    u_min = 5.0
    u_max = 25.0
    u_default = 10.0
    chord_m = 1.0
    chord_min = 0.01
    chord_max = 100.0
    span_min = 0.001
    span_max = 50.0
    nu = 1.5e-5
    rho = 1.0
    aref = 0.1
    t_min = 6
    t_max = 24
    n_normal = 160
    wall_grading_scale = 125.0
    far_x_in = -8.0
    far_x_out = 12.0
    far_y = 8.0
    z_thickness = 0.1
    end_time = 1500
    force_drift_window = 100
    force_drift_max = 1e-4
    mesh_budget = 3
    solve_budget = 2
    max_agent_turns = 12
    max_non_ortho = 65.0
    max_skew = 4.0
    continuity_local = 1e-4
    continuity_cum = 5e-3
    reynolds_min = 1.0e5
    reynolds_max = 5.0e6
    reference_abs = 0.03
    reference_rel = 0.05
    reference_min_confidence = 0.90
    cylinder_cells = 2000
    cylinder_continuity = 5e-4
    cylinder_velocity = 5e-5
    delta_cl = 0.05
    delta_cd = 0.01
    mesh_timeout_s = 90
    solve_timeout_s = 1800
    pilot_timeout_s = 180
    screening_sentence = (
        "This mesh is not a design answer until the refinement gate passes."
    )
    design_sentence = (
        "Design grade is force agreement between the parent mesh and a 20% "
        "chordwise refinement on this wall-modeled Spalart-Allmaras setup. "
        "It is not mesh independence and it is not a flight-hardware release."
    )


class _Mint:
    def __enter__(self) -> "_Mint":
        global _MINT
        _MINT += 1
        return self

    def __exit__(self, *_exc: object) -> bool:
        global _MINT
        _MINT -= 1
        return False


@dataclass(frozen=True, slots=True)
class Panel:
    kind: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class Naca4:
    m: int
    p: int
    t: int

    def __post_init__(self) -> None:
        if not (0 <= self.m <= 9 and 0 <= self.p <= 9):
            raise ValueError("naca digit out of range")
        if not (Bounds.t_min <= self.t <= Bounds.t_max):
            raise ValueError("thickness digit out of range")
        if self.m > 0 and self.p == 0:
            raise ValueError("cambered section needs a position digit")

    def code(self) -> str:
        return f"{self.m}{self.p}{self.t:02d}"


@dataclass(frozen=True, slots=True)
class Refusal:
    gate: str
    log: str
    reason: str
    panels: tuple[Panel, ...] = ()


@dataclass(frozen=True, slots=True)
class Screening:
    run_id: str
    cl: float
    cd: float
    sentence: str
    worth_finer: bool
    panels: tuple[Panel, ...] = ()
    grade: str = "screening"

    def __post_init__(self) -> None:
        if self.sentence != Bounds.screening_sentence:
            raise ValueError("screening sentence")
        if self.grade != "screening":
            raise ValueError("grade")
        if not math.isfinite(self.cl) or not math.isfinite(self.cd) or self.cd <= 0:
            raise ValueError("coefficients")
        if self.worth_finer and _MINT <= 0:
            raise ValueError("worth_finer requires a commission from project")


@dataclass(frozen=True, slots=True)
class DesignGrade:
    run_id: str
    parent_run_id: str
    cl: float
    cd: float
    delta_cl: float
    delta_cd: float
    sentence: str
    panels: tuple[Panel, ...] = ()
    grade: str = "design"

    def __post_init__(self) -> None:
        if self.sentence != Bounds.design_sentence:
            raise ValueError("design sentence")
        if self.grade != "design":
            raise ValueError("grade")
        if not math.isfinite(self.delta_cl) or not math.isfinite(self.delta_cd):
            raise ValueError("delta")
        if abs(self.delta_cl) > Bounds.delta_cl or abs(self.delta_cd) > Bounds.delta_cd:
            raise ValueError("delta outside bound")
        if not math.isfinite(self.cl) or not math.isfinite(self.cd) or self.cd <= 0:
            raise ValueError("coefficients")


@dataclass(frozen=True, slots=True)
class HardwareDecision:
    run_id: str
    parent_run_id: str
    cl: float
    cd: float
    delta_cl: float
    delta_cd: float
    sentence: str


@dataclass(frozen=True, slots=True)
class Commit:
    run_id: str
    stage: str
    args: Mapping[str, object]
    parent_id: str | None
    spec_hash: str
    log: str
    evidence: Mapping[str, object]
    stop_why: str | None = None


@dataclass(frozen=True, slots=True)
class Admission:
    receipt: dict | None
    rejection: str | None
    appended: bool

    @property
    def ok(self) -> bool:
        return self.rejection is None


def cell_count(stream: int, density: str) -> int:
    if stream not in STREAMS:
        raise ValueError("stream")
    if density not in ("coarse", "refined"):
        raise ValueError("density")
    n_chord = stream * (2 if density == "refined" else 1)
    return 4 * n_chord * Bounds.n_normal


def parse_naca(value: object) -> Naca4:
    if isinstance(value, Naca4):
        return value
    if isinstance(value, dict):
        return Naca4(int(value["m"]), int(value["p"]), int(value["t"]))
    text = str(value)
    if len(text) != 4 or not text.isdigit():
        raise ValueError("naca")
    return Naca4(int(text[0]), int(text[1]), int(text[2:]))


def wall_grading_ratio(grading: float) -> float:
    """Map the agent's small grading knob to the actual blockMesh ratio."""
    return float(grading) * float(Bounds.wall_grading_scale)


def first_cell_height(length: float, cells: int, ratio: float) -> float:
    """First-cell height for a one-sided geometric grading over `length`."""
    if cells < 1 or length <= 0 or ratio <= 0:
        raise ValueError("bad grading inputs")
    if cells == 1:
        return float(length)
    growth = float(ratio) ** (1.0 / (cells - 1))
    return float(length) * (growth - 1.0) / (growth**cells - 1.0)


def _log_text(ev: Mapping[str, object]) -> str:
    return str(ev.get("log_text") or "")


def judge_mesh(ev: Mapping[str, object], expected_cells: int | None = None) -> bool:
    if ev.get("failure") in ("wall_clock", "fatal"):
        return False
    if ev.get("fatal") or ev.get("exit_code", 0) not in (0, None):
        return False
    text = _log_text(ev)
    if "FOAM FATAL" in text:
        return False
    if "mesh_ok" in ev:
        if not ev["mesh_ok"]:
            return False
    elif "Mesh OK" not in text:
        return False
    try:
        cells = int(ev["cells"])  # type: ignore[arg-type]
        non_ortho = float(ev["max_non_ortho"])  # type: ignore[arg-type]
        skew = float(ev["max_skew"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return False
    expected = expected_cells if expected_cells is not None else ev.get("cells_expected")
    if expected is None or cells != int(expected):
        return False
    if non_ortho > Bounds.max_non_ortho or skew > Bounds.max_skew:
        return False
    return True


def judge_solve(ev: Mapping[str, object]) -> bool:
    if ev.get("fatal") or ev.get("exit_code", 0) not in (0, None):
        return False
    text = _log_text(ev)
    if "FOAM FATAL" in text:
        return False
    if text and "ExecutionTime" not in text:
        return False
    failure = ev.get("failure", "none")
    if failure not in ("none", None):
        return False
    if "finished" in ev and not ev["finished"]:
        return False
    local = ev.get("continuity_local")
    if local is not None:
        try:
            if float(local) > Bounds.continuity_local:  # type: ignore[arg-type]
                return False
        except (TypeError, ValueError):
            return False
    else:
        try:
            continuity = float(ev["continuity_cum"])  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            return False
        if continuity > Bounds.continuity_cum:
            return False
    elapsed = ev.get("elapsed_s")
    if elapsed is not None and float(elapsed) >= Bounds.solve_timeout_s:
        return False
    for key in ("force_drift_cl", "force_drift_cd"):
        if key not in ev or ev.get(key) is None:
            continue
        try:
            if float(ev[key]) > Bounds.force_drift_max:  # type: ignore[arg-type]
                return False
        except (TypeError, ValueError):
            return False
    return True


def judge_forces(ev: Mapping[str, object]) -> bool:
    header = ev.get("header")
    if header is not None:
        tokens = str(header).split()
        if "Cl" not in tokens or "Cd" not in tokens:
            return False
    try:
        cl = float(ev["cl"])  # type: ignore[arg-type]
        cd = float(ev["cd"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(cl) and math.isfinite(cd) and cd > 0.0


def judge_pilot(ev: Mapping[str, object]) -> bool:
    if ev.get("fatal"):
        return False
    if "FOAM FATAL" in _log_text(ev):
        return False
    try:
        cells = int(ev["cells"])  # type: ignore[arg-type]
        continuity = float(ev["continuity"])  # type: ignore[arg-type]
        velocity = float(ev["velocity_error"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return False
    return (
        cells == Bounds.cylinder_cells
        and continuity <= Bounds.cylinder_continuity
        and velocity <= Bounds.cylinder_velocity
    )


def physics_check(
    cl: float,
    naca: Naca4 | str | dict,
    alpha_deg: float,
    u_ms: float = 10.0,
    chord_m: float = 1.0,
) -> tuple[bool, dict[str, object], str]:
    """Check one measured Cl against the NeuralFoil polar.

    Returns (ok, evidence, reason). This is the only lift model for the
    supported Reynolds range. A missing reference, an out-of-range Reynolds
    number, a low-confidence analysis, or a lift outside tolerance fails closed.
    Drag is reported and checked by the refinement delta, not by this band.
    Reference Cd is recorded and is not a pass or fail. A forced-transition
    polar is recorded after the free-transition reference clears the confidence
    floor, and that polar is not a pass or fail. y+ is not part of this check.
    """
    try:
        measured = float(cl)
        speed = float(u_ms)
        chord = float(chord_m)
    except (TypeError, ValueError):
        evidence = {"passes": False, "model": "NeuralFoil", "error": "bad numeric input"}
        return False, evidence, "reference polar: bad numeric input"

    reynolds = speed * chord / float(Bounds.nu)
    base: dict[str, object] = {
        "model": "NeuralFoil",
        "reynolds": reynolds,
        "measured_cl": measured if math.isfinite(measured) else None,
        "passes": False,
    }
    if not math.isfinite(measured):
        return False, base, "reference polar: measured Cl is not finite"
    if not (Bounds.reynolds_min <= reynolds <= Bounds.reynolds_max):
        reason = (
            f"Reynolds number {reynolds:.0f} is outside the validated "
            f"range {Bounds.reynolds_min:.0f}..{Bounds.reynolds_max:.0f}"
        )
        base["error"] = reason
        return False, base, reason

    try:
        reference = reference_polar(parse_naca(naca).code(), float(alpha_deg), reynolds)
    except (ReferenceError, ValueError) as exc:
        reason = f"reference polar unavailable: {exc}"
        base["error"] = reason
        return False, base, reason

    if reference.confidence < Bounds.reference_min_confidence:
        reason = (
            f"{reference.model} confidence {reference.confidence:.3f} is below "
            f"{Bounds.reference_min_confidence:.2f}"
        )
        evidence = {
            "model": reference.model,
            "source": reference.source,
            "reynolds": reynolds,
            "reference_cl": reference.cl,
            "reference_cd": reference.cd,
            "reference_cm": reference.cm,
            "confidence": reference.confidence,
            "measured_cl": measured,
            "passes": False,
            "error": reason,
        }
        return False, evidence, reason

    tolerance = max(
        float(Bounds.reference_abs),
        float(Bounds.reference_rel) * abs(reference.cl),
    )
    difference = measured - reference.cl
    passes = abs(difference) <= tolerance
    evidence = {
        "model": reference.model,
        "source": reference.source,
        "reynolds": reynolds,
        "reference_xtr": reference.xtr,
        "reference_cl": reference.cl,
        "reference_cd": reference.cd,
        "reference_cm": reference.cm,
        "confidence": reference.confidence,
        "measured_cl": measured,
        "difference": difference,
        "tolerance": tolerance,
        "passes": passes,
    }
    try:
        forced = reference_polar(reference.naca, reference.alpha_deg, reference.reynolds, 0.0)
    except (ReferenceError, ValueError) as exc:
        evidence["forced_error"] = str(exc)
    else:
        evidence["forced_xtr"] = forced.xtr
        evidence["forced_cl"] = forced.cl
        evidence["forced_cd"] = forced.cd
        evidence["forced_cm"] = forced.cm
        evidence["forced_confidence"] = forced.confidence
    reason = (
        f"{reference.model} reference: measured Cl {measured:.4f}; "
        f"reference Cl {reference.cl:.4f}; difference {difference:+.4f}; "
        f"tolerance {tolerance:.4f}; Re {reynolds:.0f}; "
        f"confidence {reference.confidence:.3f}"
    )
    return passes, evidence, reason


def judge_physics(
    cl: float,
    naca: Naca4 | str | dict,
    alpha_deg: float,
    u_ms: float = 10.0,
    chord_m: float = 1.0,
) -> bool:
    return physics_check(cl, naca, alpha_deg, u_ms, chord_m)[0]


def _physics_reason(
    cl: float,
    naca: Naca4 | str | dict,
    alpha_deg: float,
    u_ms: float = 10.0,
    chord_m: float = 1.0,
) -> str:
    return physics_check(cl, naca, alpha_deg, u_ms, chord_m)[2]


def judge_delta(delta_cl: float, delta_cd: float) -> bool:
    return abs(delta_cl) <= Bounds.delta_cl and abs(delta_cd) <= Bounds.delta_cd


def shape(ledger: list[dict]) -> str:
    current = "empty"
    for receipt in ledger:
        stage = receipt.get("stage")
        status = receipt.get("status")
        edge = (current, stage, status)
        if edge not in TRANSITION:
            return current
        dest = TRANSITION[edge]
        if (
            stage == "solve"
            and status == "red"
            and receipt.get("failure") in ("fatal", "wall_clock", "not_converged")
        ):
            dest = "solve_closed"
        current = dest
    return current


def _mesh_signature(ev: Mapping[str, object]) -> str:
    supplied = ev.get("signature")
    if supplied:
        return str(supplied)
    ok = "OK" if ev.get("mesh_ok") else "Failed"
    try:
        non_ortho = round(float(ev.get("max_non_ortho", 0)), 1)  # type: ignore[arg-type]
        skew = round(float(ev.get("max_skew", 0)), 2)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        non_ortho, skew = 0.0, 0.0
    raw = f"{ev.get('cells')}|{non_ortho}|{skew}|{ok}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _match_choice(value: object, choices: tuple[float, ...] | tuple[int, ...]) -> bool:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return any(abs(number - float(choice)) < 1e-9 for choice in choices)


def _scope_ok(ev: Mapping[str, object]) -> bool:
    try:
        parse_naca(ev["naca"])
        alpha = float(ev["alpha_deg"])  # type: ignore[arg-type]
        speed = float(ev["u_ms"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return False
    if not (Bounds.alpha_min <= alpha <= Bounds.alpha_max):
        return False
    if not (Bounds.u_min <= speed <= Bounds.u_max):
        return False
    if "chord_m" in ev and ev["chord_m"] is not None:
        try:
            chord = float(ev["chord_m"])  # type: ignore[arg-type]
            if not (Bounds.chord_min <= chord <= Bounds.chord_max):
                return False
        except (TypeError, ValueError):
            return False
    if "span_m" in ev and ev["span_m"] is not None:
        try:
            span = float(ev["span_m"])  # type: ignore[arg-type]
            if not (Bounds.span_min <= span <= Bounds.span_max):
                return False
        except (TypeError, ValueError):
            return False
    try:
        chord = float(ev["chord_m"]) if ev.get("chord_m") is not None else float(Bounds.chord_m)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    reynolds = speed * chord / float(Bounds.nu)
    if not (Bounds.reynolds_min <= reynolds <= Bounds.reynolds_max):
        return False
    if "nu" in ev and ev["nu"] is not None:
        try:
            if abs(float(ev["nu"]) - float(Bounds.nu)) > 1e-12:  # type: ignore[arg-type]
                return False
        except (TypeError, ValueError):
            return False
    if "turbulence_model" in ev and ev["turbulence_model"] not in (None, "SpalartAllmaras"):
        return False
    return ev.get("density") in ("coarse", "refined")


def _status_of(commit: Commit) -> str | None:
    if commit.stop_why:
        return "stopped"
    ev = commit.evidence
    stage = commit.stage
    if stage == "pilot":
        return "green" if judge_pilot(ev) else "red"
    if stage == "scope":
        return "green" if _scope_ok(ev) else None
    if stage == "mesh":
        if not _match_choice(ev.get("stream"), STREAMS):
            return None
        if not _match_choice(ev.get("grading"), GRADINGS):
            return None
        return "green" if judge_mesh(ev) else "red"
    if stage == "solve":
        if not _match_choice(ev.get("u_relax"), U_RELAX):
            return None
        if not _match_choice(ev.get("non_ortho"), NON_ORTHO):
            return None
        return "green" if judge_solve(ev) else "red"
    if stage == "forces":
        return "green" if judge_forces(ev) else "red"
    return None


def idempotency_key(
    run_id: str,
    stage: str,
    args: Mapping[str, object] | None,
    parent_id: str | None,
) -> str:
    payload = json.dumps(
        {
            "run": run_id,
            "stage": stage,
            "args": args or {},
            "parent": parent_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _key_args(commit: Commit) -> dict:
    args = dict(commit.args or {})
    if commit.stop_why is not None and "stop_why" not in args:
        args["stop_why"] = commit.stop_why
    return args


def _edge_allowed(ledger: list[dict], stage: str, status: str, commit: Commit) -> str | None:
    current = shape(ledger)
    if (stage, status) not in NEXT.get(current, ()):
        return f"no edge from {current} to ({stage}, {status})"
    if stage == "forces" and status == "green":
        if any(item.get("status") in ("red", "stopped") for item in ledger):
            return "forces green after a red or stopped receipt"
    if current == "mesh_open" and stage == "mesh":
        reds = [
            item
            for item in ledger
            if item.get("stage") == "mesh" and item.get("status") == "red"
        ]
        new_sig = _mesh_signature(commit.evidence)
        last_sig = str(reds[-1].get("signature")) if reds else None
        repeated = last_sig is not None and new_sig == last_sig
        if status in ("green", "red"):
            if len(reds) >= Bounds.mesh_budget:
                return "mesh budget exhausted"
            if repeated:
                return "repeated mesh signature"
        if status == "stopped":
            why = commit.stop_why
            if repeated and why == "repeated_mesh":
                return None
            if len(reds) >= Bounds.mesh_budget and why == "budget":
                return None
            if why in ("fatal", "wall_clock"):
                return None
            return "mesh stop is not the legal close"
    if stage == "solve":
        failure = commit.evidence.get("failure") or commit.stop_why
        residuals = [
            item
            for item in ledger
            if item.get("stage") == "solve"
            and item.get("status") == "red"
            and item.get("failure") not in ("fatal", "wall_clock", "not_converged")
        ]
        if status == "red":
            if failure in ("fatal", "wall_clock", "not_converged"):
                return None
            if len(residuals) >= Bounds.solve_budget:
                return "solve budget exhausted"
        if status == "stopped":
            why = commit.stop_why
            if why in ("fatal", "wall_clock"):
                return None
            if why == "budget" and len(residuals) >= Bounds.solve_budget:
                return None
            return "solve stop is not the legal close"
    return None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def admit(ledger: list[dict], commit: Commit) -> Admission:
    """Append one legal receipt, replay an identical key, or reject with no write.

    Forces green is rejected when any earlier receipt is red or stopped.
    """
    key = idempotency_key(commit.run_id, commit.stage, _key_args(commit), commit.parent_id)
    for existing in ledger:
        if existing.get("idempotency_key") == key:
            return Admission(existing, None, False)
    status = _status_of(commit)
    if status is None:
        return Admission(None, "commit is not judgeable on this edge", False)
    rejection = _edge_allowed(ledger, commit.stage, status, commit)
    if rejection is not None:
        return Admission(None, rejection, False)
    fields = {
        key: value
        for key, value in dict(commit.evidence).items()
        if key
        not in {
            "status",
            "grade",
            "id",
            "type",
            "writer",
            "stage",
            "run_id",
            "parent_id",
            "spec_hash",
            "log",
            "written_at",
            "idempotency_key",
            "header",
            "log_text",
            "exit_code",
            "fatal",
            "mesh_ok",
            "finished",
            "elapsed_s",
        }
    }
    if commit.stage == "mesh":
        fields["signature"] = _mesh_signature(commit.evidence)
    receipt_type = "StopReceipt" if status == "stopped" else _STAGE_TYPE[commit.stage]
    receipt = {
        "id": f"rcpt-{commit.stage}-{key[:12]}",
        "type": receipt_type,
        "run_id": commit.run_id,
        "stage": commit.stage,
        "status": status,
        "parent_id": commit.parent_id,
        "spec_hash": commit.spec_hash,
        "log": commit.log,
        "writer": "case-writer",
        "written_at": _now(),
        "idempotency_key": key,
    }
    receipt.update(fields)
    if status == "stopped":
        receipt["why"] = commit.stop_why
    ledger.append(receipt)
    return Admission(receipt, None, True)


def load_ledger(path: Path | str) -> list[dict]:
    folder = Path(path)
    index = folder / "index.jsonl"
    receipts: list[dict] = []
    if index.is_file():
        for line in index.read_text().splitlines():
            if not line.strip():
                continue
            pointer = json.loads(line)
            name = pointer.get("file") or pointer.get("path")
            target = folder / str(name)
            if not target.is_file():
                continue
            receipts.append(json.loads(target.read_text()))
        return receipts
    for target in sorted(folder.glob("*.json")):
        receipts.append(json.loads(target.read_text()))
    return receipts


def _as_ledger(ledger: object) -> list[dict]:
    if ledger is None:
        return []
    if isinstance(ledger, list):
        return ledger
    if isinstance(ledger, (str, Path)):
        return load_ledger(ledger)
    raise TypeError("ledger must be a list or a directory")


def _as_outer(outer: object) -> dict | None:
    if outer is None:
        return None
    if isinstance(outer, (str, Path)):
        path = Path(outer)
        if not path.is_file():
            return None
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            raise TypeError("outer verdict must be an object")
        return loaded
    if isinstance(outer, dict):
        return outer
    raise TypeError("outer verdict must be a dict or a path")


def _split_outer(outer: object, child_present: bool) -> tuple[dict | None, dict | None]:
    if not child_present:
        return _as_outer(outer), None
    if isinstance(outer, (tuple, list)):
        parent = _as_outer(outer[0]) if len(outer) > 0 else None
        child = _as_outer(outer[1]) if len(outer) > 1 else None
        return parent, child
    if isinstance(outer, dict) and "parent" in outer and "child" in outer:
        return _as_outer(outer["parent"]), _as_outer(outer["child"])
    return None, _as_outer(outer)


def _find(receipts: list[dict], stage: str, status: str | None = None) -> dict | None:
    found = None
    for receipt in receipts:
        if receipt.get("stage") != stage:
            continue
        if status is not None and receipt.get("status") != status:
            continue
        found = receipt
    return found


def _first_bad(receipts: list[dict]) -> dict | None:
    for receipt in receipts:
        if receipt.get("status") in ("red", "stopped"):
            return receipt
    return None


def _goal_text(scope: dict | None) -> str:
    if not scope:
        return ""
    return (
        f"NACA {scope.get('naca')} at {scope.get('alpha_deg')} degrees, "
        f"{scope.get('u_ms')} m/s, {scope.get('density')}."
    )


def _physics_scope_reason(cl: float, scope: dict) -> str:
    """Why the reference gate refused. The view still has no coefficient field."""
    return _physics_reason(
        cl,
        scope["naca"],
        float(scope["alpha_deg"]),
        float(scope.get("u_ms", 10.0)),
        float(scope.get("chord_m", 1.0)),
    )


def _refuse(gate: str, log: str, reason: str, panels: tuple[Panel, ...]) -> Refusal:
    return Refusal(gate=gate, log=log, reason=reason, panels=panels)


def _other_panels(scope: dict | None, gate: str) -> tuple[Panel, ...]:
    return (Panel("goal", _goal_text(scope)), Panel("refused", gate))


def _panels_for_bad(receipts: list[dict], bad: dict) -> tuple[Panel, ...]:
    scope = _find(receipts, "scope")
    mesh = _find(receipts, "mesh")
    goal = Panel("goal", _goal_text(scope))
    stage = str(bad.get("stage"))
    if stage == "mesh":
        meshing = Panel(
            "meshing",
            f"stream {bad.get('stream', mesh.get('stream') if mesh else '')}, "
            f"grading {bad.get('grading', mesh.get('grading') if mesh else '')}.",
        )
        return (goal, meshing, Panel("mesh_refused", str(bad.get("log") or "")))
    if stage == "solve":
        panels = [goal]
        if mesh:
            panels.append(
                Panel("meshing", f"stream {mesh.get('stream')}, grading {mesh.get('grading')}.")
            )
            panels.append(Panel("mesh_accepted", str(mesh.get("cells", ""))))
        panels.append(Panel("solving", ""))
        panels.append(Panel("solve_refused", str(bad.get("log") or "")))
        return tuple(panels)
    return _other_panels(scope, stage)


def _panels_through_solve(receipts: list[dict]) -> tuple[Panel, ...]:
    """Gates that already passed, with no coefficient panel."""
    scope = _find(receipts, "scope")
    mesh = _find(receipts, "mesh", "green") or _find(receipts, "mesh")
    return (
        Panel("goal", _goal_text(scope)),
        Panel(
            "meshing",
            f"stream {mesh.get('stream') if mesh else ''}, "
            f"grading {mesh.get('grading') if mesh else ''}.",
        ),
        Panel("mesh_accepted", str(mesh.get("cells") if mesh else "")),
        Panel("solving", ""),
    )


def _screening_panels(receipts: list[dict], cl: float, cd: float) -> tuple[Panel, ...]:
    scope = _find(receipts, "scope")
    mesh = _find(receipts, "mesh", "green") or _find(receipts, "mesh")
    return (
        Panel("goal", _goal_text(scope)),
        Panel(
            "meshing",
            f"stream {mesh.get('stream') if mesh else ''}, grading {mesh.get('grading') if mesh else ''}.",
        ),
        Panel("mesh_accepted", str(mesh.get("cells") if mesh else "")),
        Panel("solving", ""),
        Panel("screening", f"Cl={cl:.4f} Cd={cd:.4f}. {Bounds.screening_sentence}"),
    )


def _flight_length(scope: dict, key: str, default: float) -> float:
    """Round so a missing length and the same JSON default still match."""
    raw = scope.get(key)
    value = default if raw is None else float(raw)
    return round(value, 9)


def _flight_key(scope: dict) -> tuple[str, float, float, float, float]:
    return (
        parse_naca(scope["naca"]).code(),
        float(scope["alpha_deg"]),
        float(scope["u_ms"]),
        _flight_length(scope, "chord_m", Bounds.chord_m),
        _flight_length(scope, "span_m", Bounds.z_thickness),
    )


def _project_run(receipts: list[dict], outer: dict | None) -> Screening | Refusal:
    bad = _first_bad(receipts)
    if bad is not None:
        return _refuse(
            str(bad.get("stage")),
            str(bad.get("log") or ""),
            f"{bad.get('stage')} {bad.get('status')}",
            _panels_for_bad(receipts, bad),
        )
    scope = _find(receipts, "scope", "green")
    forces = _find(receipts, "forces", "green")
    if shape(receipts) != "forces_green" or scope is None or forces is None:
        return _refuse("incomplete", "", "ledger is not a green forces chain", _other_panels(scope, "incomplete"))
    if outer is None:
        return _refuse("incomplete", "", "no outer verdict", _other_panels(scope, "incomplete"))
    cl = float(forces["cl"])
    cd = float(forces["cd"])
    physics_name = str(outer.get("physics", "")).strip().lower()
    physics_ok = physics_name == "green" and judge_physics(
        cl,
        scope["naca"],
        float(scope["alpha_deg"]),
        float(scope.get("u_ms", 10.0)),
        float(scope.get("chord_m", 1.0)),
    )
    if not physics_ok:
        return _refuse(
            "physics",
            str(forces.get("log") or ""),
            _physics_scope_reason(cl, scope),
            _panels_through_solve(receipts) + (Panel("refused", "physics"),),
        )
    review = str(outer.get("review", "")).strip().lower()
    if review not in ("affirm", "challenge"):
        review = "challenge"
    worth = review == "affirm"
    panels = _screening_panels(receipts, cl, cd)
    with _Mint():
        return Screening(
            run_id=str(forces.get("run_id") or scope.get("run_id")),
            cl=cl,
            cd=cd,
            sentence=Bounds.screening_sentence,
            worth_finer=worth,
            panels=panels,
        )


def project(
    parent_ledger: object,
    child_ledger: object = None,
    outer: object = None,
) -> Screening | DesignGrade | Refusal:
    """Only builder of Screening and DesignGrade."""
    parent_receipts = _as_ledger(parent_ledger)
    child_present = child_ledger is not None
    parent_outer, child_outer = _split_outer(outer, child_present)
    parent_view = _project_run(parent_receipts, parent_outer)
    if not child_present:
        return parent_view
    if isinstance(parent_view, Refusal):
        return parent_view
    if not parent_view.worth_finer:
        scope = _find(parent_receipts, "scope")
        return _refuse(
            "refinement",
            "",
            "parent screening was not affirmed",
            _other_panels(scope, "refinement"),
        )
    child_receipts = _as_ledger(child_ledger)
    child_scope = _find(child_receipts, "scope")
    if child_outer is None:
        return _refuse(
            "incomplete",
            "",
            "no outer verdict",
            _other_panels(child_scope or _find(parent_receipts, "scope"), "incomplete"),
        )
    bad = _first_bad(child_receipts)
    if bad is not None:
        return _refuse(
            str(bad.get("stage")),
            str(bad.get("log") or ""),
            f"{bad.get('stage')} {bad.get('status')}",
            _panels_for_bad(child_receipts, bad),
        )
    parent_scope = _find(parent_receipts, "scope", "green")
    child_forces = _find(child_receipts, "forces", "green")
    parent_forces = _find(parent_receipts, "forces", "green")
    if (
        shape(child_receipts) != "forces_green"
        or child_scope is None
        or child_forces is None
        or parent_scope is None
        or parent_forces is None
    ):
        return _refuse(
            "incomplete",
            "",
            "child ledger is not a green forces chain",
            _other_panels(child_scope, "incomplete"),
        )
    if child_scope.get("density") != "refined":
        return _refuse("refinement", "", "child density is not refined", _other_panels(child_scope, "refinement"))
    if _flight_key(parent_scope) != _flight_key(child_scope):
        return _refuse(
            "refinement",
            "",
            "naca, alpha, U, chord, or span differ",
            _other_panels(child_scope, "refinement"),
        )
    delta_cl = float(child_forces["cl"]) - float(parent_forces["cl"])
    delta_cd = float(child_forces["cd"]) - float(parent_forces["cd"])
    if not judge_delta(delta_cl, delta_cd):
        return _refuse(
            "refinement",
            str(child_forces.get("log") or ""),
            "force delta outside bound",
            _other_panels(child_scope, "refinement"),
        )
    child_cl = float(child_forces["cl"])
    physics_name = str(child_outer.get("physics", "")).strip().lower()
    physics_ok = physics_name == "green" and judge_physics(
        child_cl,
        child_scope["naca"],
        float(child_scope["alpha_deg"]),
        float(child_scope.get("u_ms", 10.0)),
        float(child_scope.get("chord_m", 1.0)),
    )
    if not physics_ok:
        return _refuse(
            "physics",
            str(child_forces.get("log") or ""),
            _physics_scope_reason(child_cl, child_scope),
            _other_panels(child_scope, "physics"),
        )
    if str(child_outer.get("review", "")).strip().lower() != "affirm":
        return _refuse(
            "refinement",
            "",
            "child review did not affirm",
            _other_panels(child_scope, "refinement"),
        )
    design_panel = Panel(
        "design",
        f"Cl={child_cl:.4f} Cd={float(child_forces['cd']):.4f} "
        f"dCl={delta_cl:.4f} dCd={delta_cd:.4f}. {Bounds.design_sentence}",
    )
    return DesignGrade(
        run_id=str(child_forces.get("run_id")),
        parent_run_id=parent_view.run_id,
        cl=child_cl,
        cd=float(child_forces["cd"]),
        delta_cl=delta_cl,
        delta_cd=delta_cd,
        sentence=Bounds.design_sentence,
        panels=parent_view.panels + (design_panel,),
    )


def hardware_decision(view: object) -> HardwareDecision:
    if type(view) is not DesignGrade:
        raise TypeError("hardware_decision requires a DesignGrade")
    return HardwareDecision(
        run_id=view.run_id,
        parent_run_id=view.parent_run_id,
        cl=view.cl,
        cd=view.cd,
        delta_cl=view.delta_cl,
        delta_cd=view.delta_cd,
        sentence=view.sentence,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _bounds_comment() -> str:
    counts = [cell_count(stream, density) for density in ("coarse", "refined") for stream in STREAMS]
    return "\n".join(
        [
            "# BEGIN BOUNDS",
            f"# nu {Bounds.nu}",
            f"# rho {Bounds.rho}",
            f"# aref {Bounds.aref}",
            f"# chord_m {Bounds.chord_m}",
            f"# chord [{Bounds.chord_min}, {Bounds.chord_max}]",
            f"# span [{Bounds.span_min}, {Bounds.span_max}]",
            f"# alpha [{Bounds.alpha_min}, {Bounds.alpha_max}]",
            f"# U [{Bounds.u_min}, {Bounds.u_max}] default {Bounds.u_default}",
            f"# thickness [{Bounds.t_min}, {Bounds.t_max}]",
            f"# n_normal {Bounds.n_normal}",
            f"# farfield x [{Bounds.far_x_in}, {Bounds.far_x_out}] y [{-Bounds.far_y}, {Bounds.far_y}] z [0, {Bounds.z_thickness}]",
            f"# end_time {Bounds.end_time}",
            f"# force_drift_window {Bounds.force_drift_window}",
            f"# force_drift_max {Bounds.force_drift_max}",
            f"# mesh_budget {Bounds.mesh_budget}",
            f"# solve_budget {Bounds.solve_budget}",
            f"# max_agent_turns {Bounds.max_agent_turns}",
            f"# max_non_ortho {Bounds.max_non_ortho}",
            f"# max_skew {Bounds.max_skew}",
            f"# continuity_local {Bounds.continuity_local}",
            f"# continuity_cum {Bounds.continuity_cum}",
            f"# reynolds [{Bounds.reynolds_min}, {Bounds.reynolds_max}]",
            f"# reference_abs {Bounds.reference_abs}",
            f"# reference_rel {Bounds.reference_rel}",
            f"# reference_min_confidence {Bounds.reference_min_confidence}",
            f"# wall_grading_scale {Bounds.wall_grading_scale}",
            f"# cylinder_cells {Bounds.cylinder_cells}",
            f"# cylinder_continuity {Bounds.cylinder_continuity}",
            f"# cylinder_velocity {Bounds.cylinder_velocity}",
            f"# delta_cl {Bounds.delta_cl}",
            f"# delta_cd {Bounds.delta_cd}",
            f"# timeouts mesh {Bounds.mesh_timeout_s} solve {Bounds.solve_timeout_s} pilot {Bounds.pilot_timeout_s}",
            f"# cell_counts {' '.join(str(count) for count in counts)}",
            f"# streams {' '.join(str(item) for item in STREAMS)}",
            f"# gradings {' '.join(str(item) for item in GRADINGS)}",
            f"# screening_sentence {Bounds.screening_sentence}",
            f"# design_sentence {Bounds.design_sentence}",
            "# END BOUNDS",
            "",
        ]
    )


def render_shacl() -> str:
    stream_in = " ".join(str(item) for item in STREAMS)
    grading_in = " ".join(str(item) for item in GRADINGS)
    delta_cl = format(Bounds.delta_cl, ".12g")
    delta_cd = format(Bounds.delta_cd, ".12g")
    return (
        _bounds_comment()
        + f"""@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix wl: <https://vertical-labs.local/wingline#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

wl:ReceiptShape a sh:NodeShape ;
  sh:targetClass wl:PilotReceipt, wl:ScopeReceipt, wl:MeshReceipt,
                 wl:SolveReceipt, wl:ForcesReceipt, wl:StopReceipt ;
  sh:closed true ;
  sh:ignoredProperties (rdf:type) ;
  sh:property [ sh:path wl:writer ; sh:hasValue "case-writer" ; sh:minCount 1 ; sh:maxCount 1 ] ;
  sh:property [ sh:path wl:status ; sh:in ("green" "red" "stopped") ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:log ; sh:minCount 1 ; sh:datatype xsd:string ] ;
  sh:property [ sh:path wl:id ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:type ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:run_id ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:stage ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:parent_id ] ;
  sh:property [ sh:path wl:spec_hash ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:written_at ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:cells ] ;
  sh:property [ sh:path wl:continuity ] ;
  sh:property [ sh:path wl:velocity_error ] ;
  sh:property [ sh:path wl:naca ] ;
  sh:property [ sh:path wl:alpha_deg ] ;
  sh:property [ sh:path wl:u_ms ] ;
  sh:property [ sh:path wl:density ] ;
  sh:property [ sh:path wl:chord_m ] ;
  sh:property [ sh:path wl:span_m ] ;
  sh:property [ sh:path wl:camber ] ;
  sh:property [ sh:path wl:camber_pos ] ;
  sh:property [ sh:path wl:thickness ] ;
  sh:property [ sh:path wl:pilot_id ] ;
  sh:property [ sh:path wl:stream ] ;
  sh:property [ sh:path wl:grading ] ;
  sh:property [ sh:path wl:cells_expected ] ;
  sh:property [ sh:path wl:max_non_ortho ] ;
  sh:property [ sh:path wl:max_skew ] ;
  sh:property [ sh:path wl:signature ] ;
  sh:property [ sh:path wl:u_relax ] ;
  sh:property [ sh:path wl:non_ortho ] ;
  sh:property [ sh:path wl:continuity_cum ] ;
  sh:property [ sh:path wl:continuity_local ] ;
  sh:property [ sh:path wl:continuity_global ] ;
  sh:property [ sh:path wl:n_normal ] ;
  sh:property [ sh:path wl:wall_grading ] ;
  sh:property [ sh:path wl:first_cell_m ] ;
  sh:property [ sh:path wl:nu ] ;
  sh:property [ sh:path wl:reynolds ] ;
  sh:property [ sh:path wl:turbulence_model ] ;
  sh:property [ sh:path wl:reference_model ] ;
  sh:property [ sh:path wl:end_time ] ;
  sh:property [ sh:path wl:yplus_min ] ;
  sh:property [ sh:path wl:yplus_max ] ;
  sh:property [ sh:path wl:yplus_avg ] ;
  sh:property [ sh:path wl:force_drift_cl ] ;
  sh:property [ sh:path wl:force_drift_cd ] ;
  sh:property [ sh:path wl:force_iterations ] ;
  sh:property [ sh:path wl:cd_pressure ] ;
  sh:property [ sh:path wl:cd_viscous ] ;
  sh:property [ sh:path wl:failure ] ;
  sh:property [ sh:path wl:cl ] ;
  sh:property [ sh:path wl:cd ] ;
  sh:property [ sh:path wl:why ] ;
  sh:property [ sh:path wl:idempotency_key ] .

wl:MeshShape a sh:NodeShape ;
  sh:targetClass wl:MeshReceipt ;
  sh:property [ sh:path wl:stream ; sh:in ({stream_in}) ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:grading ; sh:in ({grading_in}) ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:cells ; sh:equals wl:cells_expected ; sh:minCount 1 ] .

wl:ForcesShape a sh:NodeShape ;
  sh:targetClass wl:ForcesReceipt ;
  sh:property [ sh:path wl:cd ; sh:minExclusive 0 ; sh:minCount 1 ] ;
  sh:property [ sh:path wl:cl ; sh:minCount 1 ] ;
  sh:sparql [
    a sh:SPARQLConstraint ;
    sh:message "green forces receipt shares a run with a red or stopped receipt" ;
    sh:select \"""
      PREFIX wl: <https://vertical-labs.local/wingline#>
      SELECT $this ?other ?st
      WHERE {{
        $this wl:status "green" .
        $this wl:run_id ?run .
        ?other wl:run_id ?run .
        ?other wl:status ?st .
        FILTER (?st IN ("red", "stopped"))
        FILTER (?other != $this)
      }}
    \""" ;
  ] .

wl:DeltaShape a sh:NodeShape ;
  sh:targetClass wl:DeltaRecord ;
  sh:sparql [
    a sh:SPARQLConstraint ;
    sh:message "force delta outside bound" ;
    sh:select \"""
      PREFIX wl: <https://vertical-labs.local/wingline#>
      SELECT $this ?dcl ?dcd
      WHERE {{
        $this wl:delta_cl ?dcl .
        $this wl:delta_cd ?dcd .
        FILTER (ABS(?dcl) > {delta_cl} || ABS(?dcd) > {delta_cd})
      }}
    \""" ;
  ] .
"""
    )


def _lean_arms(kind: str) -> str:
    lines: list[str] = []
    if kind == "next":
        for shape, stage, status, _dest in _EDGES:
            lines.append(f"  | .{shape}, .{stage}, .{status} => true")
        lines.append("  | _, _, _ => false")
    else:
        for shape, stage, status, dest in _EDGES:
            lines.append(f"    | .{shape}, .{stage}, .{status} => .{dest}")
        lines.append("    | _, _, _ => s")
    return "\n".join(lines)


def render_lean() -> str:
    delta_cl = int(round(Bounds.delta_cl * 1000))
    delta_cd = int(round(Bounds.delta_cd * 1000))
    return f"""-- Emitted from wingline.kernel Bounds and NEXT. Do not edit.
-- nu = {Bounds.nu}
-- delta_cl = {Bounds.delta_cl}
-- delta_cd = {Bounds.delta_cd}
-- screening: {Bounds.screening_sentence}
-- design: {Bounds.design_sentence}

inductive Shape where
  | empty
  | pilot_red
  | pilot_green
  | scoped
  | mesh_open
  | mesh_green
  | mesh_closed
  | solve_open
  | solve_green
  | solve_closed
  | forces_green
  | forces_red
  deriving DecidableEq, Repr

inductive Stage where
  | pilot | scope | mesh | solve | forces
  deriving DecidableEq, Repr

inductive Status where
  | green | red | stopped
  deriving DecidableEq, Repr

inductive Review where
  | affirm | challenge
  deriving DecidableEq, Repr

inductive Refine where
  | absent | green | red | stopped
  deriving DecidableEq, Repr

inductive Label where
  | screening | design | refusal
  deriving DecidableEq, Repr

def deltaClBound : Nat := {delta_cl}
def deltaCdBound : Nat := {delta_cd}

def deltaInside (dcl dcd : Int) : Bool :=
  decide (Int.natAbs dcl ≤ deltaClBound ∧ Int.natAbs dcd ≤ deltaCdBound)

def nextOk : Shape → Stage → Status → Bool
{_lean_arms("next")}

def step (s : Shape) (g : Stage) (st : Status) : Shape :=
  if nextOk s g st then
    match s, g, st with
{_lean_arms("step")}
  else
    s

def taintedOf : Status → Bool
  | .green => false
  | .red => true
  | .stopped => true

/-- Report label. Design is one row: untainted forces-green, physics green,
    review affirm, refinement green, and delta inside the emitted bound. -/
def label (shape : Shape) (tainted physicsGreen : Bool) (review : Review)
    (refine : Refine) (inside : Bool) : Label :=
  match tainted, shape, physicsGreen, review, refine, inside with
  | false, .forces_green, true, .affirm, .green, true => .design
  | false, .forces_green, true, _, _, _ => .screening
  | _, _, _, _, _, _ => .refusal

theorem delta_inside_bound_endpoints :
    deltaInside {delta_cl} {delta_cd} = true ∧
    deltaInside ({delta_cl} + 1) 0 = false ∧
    deltaInside (-{delta_cl}) (-{delta_cd}) = true := by
  decide

theorem report_unreachable_after_failure
    (shape : Shape) (prior : Status) (physicsGreen : Bool) (review : Review)
    (refine : Refine) (inside : Bool)
    (h : prior = .red ∨ prior = .stopped) :
    label shape (taintedOf prior) physicsGreen review refine inside ≠ .screening ∧
    label shape (taintedOf prior) physicsGreen review refine inside ≠ .design := by
  cases h <;> cases prior <;> cases shape <;> cases physicsGreen <;> cases review <;>
    cases refine <;> cases inside <;> simp [label, taintedOf] at *

theorem design_needs_refinement
    (shape : Shape) (tainted physicsGreen : Bool) (review : Review)
    (refine : Refine) (inside : Bool)
    (h : label shape tainted physicsGreen review refine inside = .design) :
    refine = .green ∧ inside = true ∧ review = .affirm := by
  cases tainted <;> cases shape <;> cases physicsGreen <;> cases review <;>
    cases refine <;> cases inside <;> simp [label] at h
  all_goals try cases h
  exact ⟨rfl, rfl, rfl⟩

theorem review_cannot_mint_design
    (shape : Shape) (tainted physicsGreen : Bool) (refine : Refine) (inside : Bool) :
    label shape tainted physicsGreen .challenge refine inside ≠ .design := by
  cases shape <;> cases tainted <;> cases physicsGreen <;> cases refine <;> cases inside <;>
    simp [label]
  all_goals (intro contra; cases contra)

theorem bad_step_does_not_enter_report
    (s : Shape) (g : Stage) (st : Status)
    (hbad : st = .red ∨ st = .stopped) (hfrom : s ≠ .forces_green) :
    step s g st ≠ .forces_green := by
  cases hbad <;> cases s <;> cases g <;> cases st <;> simp [step, nextOk] at *
"""


def emit(root: Path | None = None) -> None:
    repo = root or _repo_root()
    shape_path = repo / "harness" / "shapes" / "receipt.shacl.ttl"
    lean_path = repo / "harness" / "gate" / "Gate.lean"
    shape_path.parent.mkdir(parents=True, exist_ok=True)
    lean_path.parent.mkdir(parents=True, exist_ok=True)
    shape_path.write_text(render_shacl())
    lean_path.write_text(render_lean())


if __name__ == "__main__":
    import sys

    if "--emit" in sys.argv:
        emit()
    else:
        raise SystemExit("usage: python -m wingline.kernel --emit")
