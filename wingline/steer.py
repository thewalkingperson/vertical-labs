"""Messages the engineer sends while a run is open.

observe returns them to the inner loop. The tool client applies a stop or an
allowed knob. The text is kept on the run either way.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_GRADING = (2, 4, 8, 12)
_STREAM = (60, 80, 100)
_RELAX = (0.7, 0.5, 0.3)
_ORTHO = (0, 1, 2)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path(run_dir: Path) -> Path:
    return Path(run_dir) / "steer.jsonl"


def _read(run_dir: Path) -> list[dict]:
    path = _path(run_dir)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("text"):
            rows.append(row)
    return rows


def _write(run_dir: Path, rows: list[dict]) -> None:
    path = _path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def append_steer(run_dir: Path, text: str) -> dict:
    row = {"at": _now(), "text": " ".join(text.split()), "seen": False}
    rows = _read(run_dir)
    rows.append(row)
    _write(run_dir, rows)
    return row


def steer_texts(run_dir: Path) -> list[str]:
    return [str(row["text"]) for row in _read(run_dir)][-12:]


def steer_fresh(run_dir: Path) -> list[str]:
    """Unseen messages. Marks them seen so the next turn does not repeat the action."""
    rows = _read(run_dir)
    fresh = [str(row["text"]) for row in rows if not row.get("seen")]
    if not fresh:
        return []
    for row in rows:
        row["seen"] = True
    _write(run_dir, rows)
    return fresh


def interpret_steer(messages: list[str], stage: str) -> dict:
    """A stop, or one allowed knob for the open stage. Anything else is a note."""
    text = " ".join(messages).lower()
    if not text.strip():
        return {"action": "none"}
    if re.search(r"\b(stop|halt|cancel|enough|abort|kill)\b", text):
        return {"action": "stop", "text": text}
    choice: dict = {"action": "note", "text": text}
    if stage == "mesh":
        grading = re.search(r"grading\s*(2|4|8|12)\b", text)
        stream = re.search(r"stream\s*(60|80|100)\b", text)
        if grading or stream:
            choice["action"] = "knob"
            if grading and int(grading.group(1)) in _GRADING:
                choice["grading"] = int(grading.group(1))
            if stream and int(stream.group(1)) in _STREAM:
                choice["stream"] = int(stream.group(1))
        elif re.search(
            r"\b(gentle|mild|mild\s+grading|gentle\s+clustering|reduce\s+clustering|lower\s+grading|reduce\s+skew)\b",
            text,
        ):
            choice["action"] = "knob"
            choice["grading"] = 4
        elif re.search(r"\b(tight|steep|high\s+clustering|higher\s+grading)\b", text):
            choice["action"] = "knob"
            choice["grading"] = 12
        elif re.search(r"\b(dense|fine|refine|higher\s+resolution)\b", text):
            choice["action"] = "knob"
            choice["stream"] = 100
        elif re.search(r"\b(coarse|fast|lower\s+resolution)\b", text):
            choice["action"] = "knob"
            choice["stream"] = 60
    elif stage == "solve":
        relax = re.search(r"(?:relax|u_relax)\s*(0\.7|0\.5|0\.3)\b", text)
        ortho = re.search(r"non[- ]?ortho\s*(0|1|2)\b", text)
        if relax or ortho:
            choice["action"] = "knob"
            if relax and float(relax.group(1)) in _RELAX:
                choice["u_relax"] = float(relax.group(1))
            if ortho and int(ortho.group(1)) in _ORTHO:
                choice["non_ortho"] = int(ortho.group(1))
        elif re.search(r"\b(relax|damp\w*|slow|stabilize|under[- ]?relax)\b", text):
            choice["action"] = "knob"
            choice["u_relax"] = 0.5
        elif re.search(r"\b(aggressive|fast|speed\s+up)\b", text):
            choice["action"] = "knob"
            choice["u_relax"] = 0.7
        elif re.search(
            r"\b(correct\s+(?:non[- ]?)?ortho|non[- ]?orthogonal|add\s+corrector)\b",
            text,
        ):
            choice["action"] = "knob"
            choice["non_ortho"] = 1
    return choice
