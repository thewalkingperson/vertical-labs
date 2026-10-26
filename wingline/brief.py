"""One spoken result for the workbench. The command line keeps its panel lines."""

from __future__ import annotations

import json
from pathlib import Path

from wingline.files import last_coefficients
from wingline.kernel import Bounds, DesignGrade, Refusal, Screening


def engineer_brief(
    outcome: object,
    run_dir: Path | None = None,
    *,
    showing: bool = False,
    viewer_error: str = "",
) -> str:
    """Explain one finished study in a single paragraph."""
    facts = _facts(Path(run_dir)) if run_dir is not None else {}
    text = _compose(outcome, facts)
    if showing:
        text += " I have the case open in paraFoam."
    elif viewer_error:
        text += " paraFoam did not open."
    return " ".join(text.split())


def _compose(outcome: object, facts: dict) -> str:
    if isinstance(outcome, DesignGrade):
        return _design(outcome, facts)
    if isinstance(outcome, Screening):
        return _screening(outcome, facts)
    if isinstance(outcome, Refusal):
        return _refusal(outcome, facts)
    goal = _panel(outcome, "goal")
    return goal or "I could not finish that request."


def _design(outcome: DesignGrade, facts: dict) -> str:
    wing = _wing(facts)
    opening = f"The finer mesh agrees with the parent on the {wing}." if wing else "The finer mesh agrees with the parent."
    return (
        f"{opening} Lift is {_lift(outcome.cl)} and drag is {_drag(outcome.cd)}. "
        f"The lift change is {outcome.delta_cl:+.4f} and the drag change is {outcome.delta_cd:+.4f}, "
        f"both inside the limits. This is a design grade for the wall-modeled Spalart-Allmaras setup: "
        f"force agreement on a 20% chordwise refinement. It is a screening of the mesh pair, "
        f"and it is not a release for flight hardware."
    )


def _screening(outcome: Screening, facts: dict) -> str:
    wing = _wing(facts)
    where = f" for the {wing}" if wing else ""
    mesh = _mesh_clause(facts)
    mesh_bit = f" {mesh}" if mesh else ""
    nxt = (
        " I would run the finer mesh on this one."
        if outcome.worth_finer
        else " I would wait for a review that accepts it before spending a finer mesh."
    )
    return (
        f"I have a coarse result{where}. Lift is {_lift(outcome.cl)} and drag is {_drag(outcome.cd)}."
        f"{mesh_bit} This is a screening. A design grade still needs the finer mesh to agree.{nxt}"
    )


def _refusal(outcome: Refusal, facts: dict) -> str:
    gate = outcome.gate
    if gate in {"parse", "meta"}:
        return _panel(outcome, "goal") or outcome.reason or "I need a NACA section and an angle before I can build this."
    if gate == "pilot":
        return "The OpenFOAM cylinder check failed, so I did not start a wing."
    if gate == "bounds":
        return "The policy files do not match the code, so I stopped before opening a case."
    if gate == "agent":
        return "The agent stopped with the case still unfinished, so there is no grade."
    if gate == "commission":
        return (
            "Refused at gate commission. This screening was not cleared for a finer mesh, "
            "so I did not start another solve."
        )
    if gate == "mesh" or facts.get("bad_stage") == "mesh":
        return _mesh_refusal(facts, outcome)
    if gate == "solve" or facts.get("bad_stage") == "solve":
        return _solve_refusal(facts)
    if gate == "physics":
        return _physics_refusal(facts, outcome)
    if gate == "refinement":
        reason = outcome.reason or "the finer mesh did not agree with the parent"
        wing = _wing(facts)
        where = f" on the {wing}" if wing else ""
        return f"I am refusing the finer-mesh comparison{where}. { _sentence(reason) }"
    goal = _panel(outcome, "goal")
    if goal and gate in {"", "incomplete"}:
        return goal
    if outcome.reason:
        return _sentence(outcome.reason)
    return "I could not finish that request."


def _mesh_refusal(facts: dict, outcome: Refusal) -> str:
    wing = _wing(facts)
    opening = f"I started the {wing}, and the mesh did not pass." if wing else "The mesh did not pass."
    detail = ""
    skew = facts.get("max_skew")
    non_ortho = facts.get("max_non_ortho")
    if isinstance(skew, (int, float)) and float(skew) > Bounds.max_skew:
        detail = f" Skew reached {float(skew):.2f}, over the limit of {Bounds.max_skew:g}."
    elif isinstance(non_ortho, (int, float)) and float(non_ortho) > Bounds.max_non_ortho:
        detail = f" Non-orthogonality reached {float(non_ortho):.1f}, over the limit of {Bounds.max_non_ortho:g}."
    elif outcome.reason:
        detail = " " + _sentence(outcome.reason)
    return opening + detail + " I stopped before the solve."


def _solve_refusal(facts: dict) -> str:
    wing = _wing(facts)
    parts: list[str] = []
    if wing:
        parts.append(f"I ran the {wing} on the {facts.get('density') or 'coarse'} mesh.")
    mesh = _mesh_clause(facts)
    if mesh:
        parts.append(mesh)
    failure = str(facts.get("failure") or "")
    steps = facts.get("iterations")
    end_time = facts.get("end_time") or Bounds.end_time
    if failure == "wall_clock":
        parts.append("simpleFoam ran out of its time allowance before the forces were ready.")
        return " ".join(parts)
    if failure == "fatal":
        parts.append("simpleFoam hit a fatal error, so I have no force result to trust.")
        return " ".join(parts)
    if steps:
        parts.append(f"simpleFoam completed {int(steps):,} steps.")
    else:
        parts.append("simpleFoam did not leave a settled force history.")
    cl = facts.get("cl")
    cd = facts.get("cd")
    if cl is not None and cd is not None:
        parts.append(f"Lift is {_lift(cl)} and drag is {_drag(cd)}.")
    cl_drift = facts.get("drift_cl")
    cd_drift = facts.get("drift_cd")
    limit = Bounds.force_drift_max
    if cl_drift is None or cd_drift is None:
        parts.append("The last 100 steps are not a long enough window to accept, so I am refusing the solve.")
        return " ".join(parts)
    cl_over = float(cl_drift) > limit
    cd_over = float(cd_drift) > limit
    if cl_over and cd_over:
        parts.append(
            f"Lift is still moving by {_small(cl_drift)} and drag by {_small(cd_drift)}. "
            f"The limit is {_small(limit)}, so I am refusing the solve."
        )
    elif cl_over:
        parts.append(
            f"Drag has settled, within {_small(cd_drift)}. "
            f"Lift is still moving by {_small(cl_drift)}, and the limit is {_small(limit)}, "
            f"so I am refusing the solve."
        )
    elif cd_over:
        parts.append(
            f"Lift has settled, within {_small(cl_drift)}. "
            f"Drag is still moving by {_small(cd_drift)}, and the limit is {_small(limit)}, "
            f"so I am refusing the solve."
        )
    else:
        parts.append(
            f"The forces are inside the {_small(limit)} drift limit, and the end time on the case is {int(end_time)}."
        )
    return " ".join(parts)


def _physics_refusal(facts: dict, outcome: Refusal) -> str:
    del outcome
    wing = _wing(facts)
    where = f" for the {wing}" if wing else ""
    cl = facts.get("cl")
    cd = facts.get("cd")
    numbers = ""
    if cl is not None and cd is not None:
        numbers = f" Lift is {_lift(cl)} and drag is {_drag(cd)}."
    return (
        f"The flow finished{where}.{numbers} "
        "The lift sits outside the NeuralFoil band, so I am refusing it."
    )


def _facts(run_dir: Path) -> dict:
    facts: dict = {}
    spec_path = run_dir / "spec.json"
    if spec_path.is_file():
        try:
            spec = json.loads(spec_path.read_text())
        except (OSError, json.JSONDecodeError):
            spec = None
        if isinstance(spec, dict):
            facts["naca"] = spec.get("naca")
            facts["alpha_deg"] = spec.get("alpha_deg")
            facts["u_ms"] = spec.get("u_ms")
            facts["density"] = spec.get("density")
    ledger = run_dir / "ledger"
    if ledger.is_dir():
        for kind, name in (("mesh", "rcpt-mesh"), ("solve", "rcpt-solve"), ("forces", "rcpt-forces")):
            receipt = _latest(ledger, name)
            if receipt is None:
                continue
            if receipt.get("status") in {"red", "stopped"} and "bad_stage" not in facts:
                facts["bad_stage"] = receipt.get("stage")
            if kind == "mesh":
                facts["cells"] = receipt.get("cells")
                facts["stream"] = receipt.get("stream")
                facts["grading"] = receipt.get("grading")
                facts["max_skew"] = receipt.get("max_skew")
                facts["max_non_ortho"] = receipt.get("max_non_ortho")
                facts["mesh_status"] = receipt.get("status")
            elif kind == "solve":
                facts["failure"] = receipt.get("failure")
                facts["drift_cl"] = receipt.get("force_drift_cl")
                facts["drift_cd"] = receipt.get("force_drift_cd")
                facts["iterations"] = receipt.get("force_iterations")
                facts["end_time"] = receipt.get("end_time")
            elif kind == "forces" and receipt.get("status") == "green":
                facts["cl"] = receipt.get("cl")
                facts["cd"] = receipt.get("cd")
    if facts.get("cl") is None or facts.get("cd") is None:
        coeff = run_dir / "case" / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat"
        if coeff.is_file():
            try:
                parsed_cl, parsed_cd = last_coefficients(coeff.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                parsed_cl, parsed_cd = None, None
            if parsed_cl is not None:
                facts["cl"] = parsed_cl
            if parsed_cd is not None:
                facts["cd"] = parsed_cd
    return facts


def measured(run_dir: Path | None) -> dict:
    """Numbers already on the receipts. The closing seat may quote these and nothing else."""
    if run_dir is None:
        return {}
    return _facts(Path(run_dir))


def _latest(ledger: Path, prefix: str) -> dict | None:
    paths = sorted(ledger.glob(prefix + "*.json"))
    if not paths:
        return None
    try:
        payload = json.loads(paths[-1].read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _panel(outcome: object, kind: str) -> str:
    for panel in getattr(outcome, "panels", ()) or ():
        if getattr(panel, "kind", "") == kind and getattr(panel, "text", ""):
            return str(panel.text).strip()
    return ""


def _wing(facts: dict) -> str:
    naca = facts.get("naca")
    alpha = facts.get("alpha_deg")
    speed = facts.get("u_ms")
    if not naca or alpha is None or speed is None:
        return ""
    return f"NACA {naca} at {alpha:g} degrees and {speed:g} m/s"


def _mesh_clause(facts: dict) -> str:
    cells = facts.get("cells")
    if facts.get("mesh_status") != "green" or cells in (None, ""):
        return ""
    stream = facts.get("stream")
    grading = facts.get("grading")
    where = ""
    if stream not in (None, "") and grading not in (None, ""):
        where = f" at stream {stream} and grading {grading}"
    return f"The mesh passed, with {int(cells):,} cells{where}."


def _lift(value: object) -> str:
    return f"{float(value):.3f}"


def _drag(value: object) -> str:
    return f"{float(value):.4f}"


def _small(value: object) -> str:
    return f"{float(value):.5f}".rstrip("0").rstrip(".")


def _sentence(text: str) -> str:
    clean = " ".join(str(text).split())
    if not clean:
        return ""
    if clean[-1] not in ".!?":
        clean += "."
    return clean[0].upper() + clean[1:]
