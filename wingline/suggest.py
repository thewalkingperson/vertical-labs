"""Closing seat. One design note after the receipt, from the system prompt.

The note is not a gate. A missing model, a planner run, or a bad reply leaves
the receipt paragraph unchanged.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from wingline.brief import measured
from wingline.grok_call import grok_available, run_grok
from wingline.kernel import Bounds, DesignGrade, Refusal, Screening, parse_naca
from wingline.prompts import SUGGEST_SYSTEM

_FORCE_GATES = {"solve", "physics", "forces"}
_DEGREE = re.compile(r"(-?\d+(?:\.\d+)?)\s+degrees\b", re.IGNORECASE)
_NACA4 = re.compile(r"\bNACA\s*(\d{4})\b", re.IGNORECASE)
_NACA_LONG = re.compile(r"\bNACA\s*\d{5,}\b", re.IGNORECASE)
_END = re.compile(r"[.!?](?=\s|$)")


def close_paragraph(paragraph: str, outcome: object, run_dir: Path | None) -> str:
    """Append the model's note when this result is allowed to ask for one."""
    note = design_note(paragraph, outcome, run_dir)
    if not note:
        return paragraph
    return " ".join(f"{paragraph} {note}".split())


def design_note(paragraph: str, outcome: object, run_dir: Path | None) -> str:
    """One or two sentences. Empty when the seat should stay quiet."""
    if os.environ.get("WINGLINE_AGENT", "1") == "0":
        return ""
    if not _reports_forces(outcome):
        return ""
    facts = measured(run_dir)
    pair = _pair(outcome, facts)
    if pair is None:
        return ""
    if not grok_available():
        return ""
    prompt = _prompt(paragraph, facts, pair)
    try:
        with tempfile.TemporaryDirectory(prefix="wingline-suggest-") as scratch:
            result = run_grok(
                SUGGEST_SYSTEM,
                prompt,
                max_turns=1,
                allow_tools=False,
                env=_quiet_env(),
                cwd=Path(scratch),
            )
    except Exception:
        return ""
    if not isinstance(result, dict):
        return ""
    return _accept(paragraph, _text(result))


def _reports_forces(outcome: object) -> bool:
    if isinstance(outcome, (Screening, DesignGrade)):
        return True
    if isinstance(outcome, Refusal):
        return outcome.gate in _FORCE_GATES
    return False


def _pair(outcome: object, facts: dict) -> tuple[float, float] | None:
    lift = facts.get("cl")
    drag = facts.get("cd")
    if isinstance(outcome, (Screening, DesignGrade)):
        if lift is None:
            lift = outcome.cl
        if drag is None:
            drag = outcome.cd
    try:
        cl = float(lift)  # type: ignore[arg-type]
        cd = float(drag)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if cl != cl or cd != cd:
        return None
    return cl, cd


def _prompt(paragraph: str, facts: dict, pair: tuple[float, float]) -> str:
    packet = {
        "paragraph": paragraph,
        "naca": facts.get("naca"),
        "alpha_deg": facts.get("alpha_deg"),
        "u_ms": facts.get("u_ms"),
        "cl": pair[0],
        "cd": pair[1],
        "force_drift_cl": facts.get("drift_cl"),
        "force_drift_cd": facts.get("drift_cd"),
        "force_drift_max": Bounds.force_drift_max,
        "alpha_min": Bounds.alpha_min,
        "alpha_max": Bounds.alpha_max,
        "thickness_min": Bounds.t_min,
        "thickness_max": Bounds.t_max,
    }
    return (
        "The case is finished. Write the advice from this evidence only.\n"
        + json.dumps(packet, sort_keys=True)
    )


def _quiet_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("WINGLINE_STORE", "WINGLINE_RUN_ID", "WINGLINE_LEASE"):
        env.pop(key, None)
    return env


def _text(result: dict) -> str:
    for key in ("text", "result"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _accept(paragraph: str, raw: str) -> str:
    clean = _two(raw)
    if not clean:
        return ""
    if clean == " ".join(paragraph.split()):
        return ""
    if not _inside_bounds(clean):
        return ""
    return clean


def _two(raw: str) -> str:
    clean = " ".join(raw.split())
    if not clean or clean.startswith("{"):
        return ""
    ends = list(_END.finditer(clean))
    if not ends:
        return clean[:400]
    stop = ends[1].end() if len(ends) > 1 else ends[0].end()
    return clean[:stop].strip()


def _inside_bounds(text: str) -> bool:
    if _NACA_LONG.search(text):
        return False
    for code in _NACA4.findall(text):
        try:
            parse_naca(code)
        except ValueError:
            return False
    for match in _DEGREE.finditer(text):
        angle = float(match.group(1))
        if not (Bounds.alpha_min <= angle <= Bounds.alpha_max):
            return False
    return True
