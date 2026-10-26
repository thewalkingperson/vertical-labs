"""Knob policy. A client of the five tools, not an authority."""

from __future__ import annotations

from wingline.kernel import Bounds
from wingline.mcp_server import (
    commit_forces,
    commit_mesh,
    commit_solve,
    commit_spec,
    observe,
    store_root,
)
from wingline.steer import interpret_steer

# One knob at a time, in this order. The server still admits or refuses.
# The parent opens at stream 100. A refined child opens at stream 60, which is
# 120 chordwise cells, not a doubled copy of the parent.
_MESH = ((100, 8), (100, 4), (100, 12), (80, 4))
_MESH_REFINED = ((60, 8), (60, 4), (60, 12), (80, 4))
_SOLVE = ((0.5, 1), (0.3, 2), (0.5, 0), (0.3, 1))


def _as_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def diagnose_mesh(evidence: dict) -> tuple[int, int, str] | None:
    """Pick stream or grading from the worse mesh fault. None keeps the table."""
    skew = _as_float(evidence.get("max_skew"))
    ortho = _as_float(evidence.get("max_non_ortho"))
    tried_grading = _as_float(evidence.get("grading"))
    tried_stream = _as_float(evidence.get("stream"))
    skew_ratio = 0.0 if skew is None else skew / Bounds.max_skew
    ortho_ratio = 0.0 if ortho is None else ortho / Bounds.max_non_ortho
    if tried_stream in (60.0, 80.0, 100.0):
        home = int(tried_stream)
    else:
        home = 100
    if skew_ratio >= ortho_ratio and skew_ratio > 1.0:
        if tried_stream == 80:
            return None
        if tried_grading is not None and tried_grading <= 2:
            return (
                80,
                4,
                f"Skew {skew:.2f} remains after grading 2. Retry stream 80 and grading 4.",
            )
        if tried_grading is not None and tried_grading <= 4:
            grading = 2
        else:
            grading = 2 if skew_ratio > 1.5 else 4
        return (
            home,
            grading,
            f"Skew {skew:.2f} is the mesh fault (bound {Bounds.max_skew:g}). "
            f"Retry stream {home} with milder grading {grading}.",
        )
    if ortho_ratio > 1.0:
        return (
            80,
            8,
            f"Non-orthogonality {ortho:.1f} is the mesh fault (bound {Bounds.max_non_ortho:g}). "
            "Retry stream 80 and grading 8.",
        )
    return None


def diagnose_solve(evidence: dict) -> tuple[float, int, str] | None:
    """Pick relaxation from the continuity residual. None keeps the table.

    A local residual decides when the receipt has one. Cumulative continuity
    is only the fallback for an older log.
    """
    local = _as_float(evidence.get("continuity_local"))
    if local is not None:
        if local > 10.0 * Bounds.continuity_local:
            return (
                0.3,
                1,
                f"Local continuity {local:.4g} is far above {Bounds.continuity_local:g}. "
                "Retry u_relax 0.3 with one corrector.",
            )
        if local > Bounds.continuity_local:
            return (
                0.5,
                0,
                f"Local continuity {local:.4g} is above {Bounds.continuity_local:g}. "
                "Retry u_relax 0.5.",
            )
        return None
    continuity = _as_float(evidence.get("continuity_cum"))
    if continuity is None:
        return None
    if continuity > 0.05:
        return (
            0.3,
            1,
            f"Continuity {continuity:.4g} is far above {Bounds.continuity_cum:g}. "
            "Retry u_relax 0.3 with one non-orthogonal corrector.",
        )
    if continuity > Bounds.continuity_cum:
        return (
            0.5,
            0,
            f"Continuity {continuity:.4g} is above {Bounds.continuity_cum:g}. Retry u_relax 0.5.",
        )
    return None


def _record(run_id: str, text: str) -> None:
    path = store_root() / run_id / "diagnosis.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(text.strip() + "\n")


def _halt(result: dict) -> bool:
    if result.get("error"):
        return True
    if result.get("status") == "stopped":
        return True
    if result.get("failure") in ("wall_clock", "fatal", "not_converged"):
        return True
    if result.get("why") in ("wall_clock", "fatal", "budget", "repeated_mesh"):
        return result.get("status") == "stopped"
    return False


def drive(run_id: str, lease: str) -> None:
    """Commit the open stage until the ledger is terminal, stopped, or out of turns."""
    for _turn in range(Bounds.max_agent_turns):
        seen = observe(run_id)
        if seen.get("error") or seen.get("terminal"):
            return
        stage = seen.get("open_stage")
        choice = interpret_steer(seen.get("steer_fresh") or [], str(stage or ""))
        if choice["action"] == "stop":
            return
        if stage == "scope":
            result = commit_spec(run_id, lease)
        elif stage == "mesh":
            density = str(seen.get("density") or "coarse")
            mesh_plan = _MESH_REFINED if density == "refined" else _MESH
            reds = int(seen.get("mesh_reds") or 0)
            plan = mesh_plan[min(reds, len(mesh_plan) - 1)]
            last = seen.get("last") if isinstance(seen.get("last"), dict) else {}
            diagnosed = diagnose_mesh(last) if reds and choice["action"] != "knob" else None
            if diagnosed is None:
                stream, grading = plan
                note = f"Mesh plan: stream {stream}, grading {grading}."
            else:
                stream, grading, note = diagnosed
            if choice.get("stream") is not None:
                stream = int(choice["stream"])
                note = f"Steer set stream {stream}. {note}"
            if choice.get("grading") is not None:
                grading = int(choice["grading"])
                note = f"Steer set grading {grading}. {note}"
            _record(run_id, note)
            result = commit_mesh(run_id, lease, stream, grading)
        elif stage == "solve":
            reds = int(seen.get("solve_reds") or 0)
            plan = _SOLVE[min(reds, len(_SOLVE) - 1)]
            last = seen.get("last") if isinstance(seen.get("last"), dict) else {}
            diagnosed = diagnose_solve(last) if reds and choice["action"] != "knob" else None
            if diagnosed is None:
                relax, ortho = plan
                note = f"Solve plan: u_relax {relax}, non_ortho {ortho}."
            else:
                relax, ortho, note = diagnosed
            if choice.get("u_relax") is not None:
                relax = float(choice["u_relax"])
                note = f"Steer set u_relax {relax}. {note}"
            if choice.get("non_ortho") is not None:
                ortho = int(choice["non_ortho"])
                note = f"Steer set non_ortho {ortho}. {note}"
            _record(run_id, note)
            result = commit_solve(run_id, lease, relax, ortho)
        elif stage == "forces":
            result = commit_forces(run_id, lease)
        else:
            return
        if _halt(result):
            return
        if stage == "forces":
            return
