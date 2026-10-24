"""Knob policy. A client of the five tools, not an authority."""

from __future__ import annotations

from wingline.kernel import Bounds
from wingline.mcp_server import (
    commit_forces,
    commit_mesh,
    commit_solve,
    commit_spec,
    observe,
)

# One knob at a time, in this order. The server still admits or refuses.
_MESH = ((60, 8), (60, 12), (60, 4), (80, 4))
_SOLVE = ((0.7, 0), (0.5, 0), (0.3, 0), (0.3, 1))


def _halt(result: dict) -> bool:
    if result.get("error"):
        return True
    if result.get("status") == "stopped":
        return True
    if result.get("failure") in ("wall_clock", "fatal"):
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
        if stage == "scope":
            result = commit_spec(run_id, lease)
        elif stage == "mesh":
            reds = int(seen.get("mesh_reds") or 0)
            plan = _MESH[min(reds, len(_MESH) - 1)]
            result = commit_mesh(run_id, lease, plan[0], plan[1])
        elif stage == "solve":
            reds = int(seen.get("solve_reds") or 0)
            plan = _SOLVE[min(reds, len(_SOLVE) - 1)]
            result = commit_solve(run_id, lease, plan[0], plan[1])
        elif stage == "forces":
            result = commit_forces(run_id, lease)
        else:
            return
        if _halt(result):
            return
        if stage == "forces":
            return
