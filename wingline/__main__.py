"""Customer entry: python -m wingline \"<question>\"."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from wingline.engineer import Engineer
from wingline.kernel import Refusal
from wingline.meta import NeedsAnswer, Study, resolve
from wingline.outer import explore


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


def _reply(prompt: str) -> str:
    print(prompt, flush=True)
    if not sys.stdin.isatty():
        raise NeedsAnswer(prompt)
    return input("> ")


def _print_outcome(outcome: object) -> None:
    for line in panel_lines(outcome):
        print(line)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    text = " ".join(args).strip()
    store = Path(os.environ.get("WINGLINE_STORE", "runs"))
    engineer = Engineer.open(store)
    if os.environ.get("WINGLINE_FIXTURE") == "1":
        view = engineer.ask(text)
        _print_outcome(view.outcome)
        return 0
    study = resolve(text, store, _reply)
    if not isinstance(study, Study):
        _print_outcome(study)
        return 0
    for note in study.notes:
        print(f"meta: {note}")
    view = engineer.ask(study.sentence)
    _print_outcome(view.outcome)
    if os.environ.get("WINGLINE_EXPLORE", "1") == "0" or not view.run_id:
        return 0
    print("outer: direct result is above. Searching nearby camber and thickness.")
    for line in explore(store, study, view, engineer.ask):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
