"""Customer entry: python -m wingline \"<question>\"."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from wingline.engineer import Engineer
from wingline.kernel import Refusal


def panel_lines(outcome: object) -> list[str]:
    panels = getattr(outcome, "panels", ())
    lines: list[str] = []
    refused = False
    for panel in panels:
        kind = panel.kind
        if kind == "goal":
            lines.append(f"goal: {panel.text}")
        elif kind == "meshing":
            lines.append(f"meshing: {panel.text}")
        elif kind == "mesh_accepted":
            lines.append(f"mesh accepted: {panel.text} cells. Log: log.checkMesh")
        elif kind == "solving":
            lines.append("solving: simpleFoam.")
        elif kind == "screening":
            lines.append(f"screening: {panel.text}")
        elif kind == "design":
            lines.append(f"design: {panel.text}")
        elif kind in {"mesh_refused", "solve_refused", "refused"} and not refused:
            if isinstance(outcome, Refusal):
                lines.append(f"refused: Refused at gate {outcome.gate}. Log: {outcome.log}")
                refused = True
    if isinstance(outcome, Refusal) and not refused:
        lines.append(f"refused: Refused at gate {outcome.gate}. Log: {outcome.log}")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    text = " ".join(args).strip()
    store = Path(os.environ.get("WINGLINE_STORE", "runs"))
    engineer = Engineer.open(store)
    view = engineer.ask(text)
    for line in panel_lines(view.outcome):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
