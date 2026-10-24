"""One correction, one template. The return value is not a grade."""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from wingline.kernel import (
    Bounds,
    emit,
    judge_forces,
    judge_mesh,
    judge_pilot,
    judge_solve,
    project,
)

_REPO = Path(__file__).resolve().parents[1]
_FIX = _REPO / "fixtures" / "naca2412-a6"
_FIVE = {"observe", "commit_spec", "commit_mesh", "commit_solve", "commit_forces"}
_MIN_FIELDS = {"alpha_min", "u_min", "t_min"}
_FIELDS = _MIN_FIELDS | {
    "max_non_ortho",
    "max_skew",
    "continuity_cum",
    "delta_cl",
    "delta_cd",
    "alpha_max",
    "u_max",
    "t_max",
    "cylinder_continuity",
    "cylinder_velocity",
    "thin_abs",
    "thin_rel",
    "mesh_timeout_s",
    "solve_timeout_s",
    "pilot_timeout_s",
    "max_agent_turns",
    "mesh_budget",
    "solve_budget",
}
_TIGHTEN = re.compile(
    r"\btighten\s+([A-Za-z_][A-Za-z0-9_]*)\s+to\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b",
    re.IGNORECASE,
)
_LOOSE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s+was\s+too\s+loose\b",
    re.IGNORECASE,
)
_TOOL = re.compile(r"\btool\s+([A-Za-z_][A-Za-z0-9_]*)\b")


@dataclass(frozen=True, slots=True)
class Shipped:
    template: str
    correction_id: str


@dataclass(frozen=True, slots=True)
class NeedsHuman:
    template: str
    correction_id: str
    path: str


@dataclass(frozen=True, slots=True)
class Noop:
    reason: str = "no unresolved correction"


class _Looser:
    pass


_LOOSER = _Looser()


@dataclass
class _Change:
    template: str
    revert: Callable[[], None]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path(store: Path) -> Path:
    return Path(store) / "corrections.jsonl"


def _load(store: Path) -> list[dict]:
    path = _path(store)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _save(store: Path, rows: list[dict]) -> None:
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    _path(store).write_text(text)


def correct(store: Path, text: str) -> str:
    """Append one correction. Does not change a ledger or a bound."""
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    import secrets

    row = {
        "id": "corr-" + secrets.token_hex(6),
        "text": text,
        "at": _now(),
        "resolved": False,
    }
    with _path(store).open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return str(row["id"])


def _lock(store: Path):
    store.mkdir(parents=True, exist_ok=True)
    handle = (store / "engineer.lock").open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _newest(rows: list[dict]) -> dict | None:
    for row in reversed(rows):
        if not row.get("resolved"):
            return row
    return None


def _literal(value: object) -> str:
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _receipts(store: Path, field: str) -> list[dict]:
    found: list[tuple[float, dict]] = []
    for path in store.rglob("*.json"):
        if path.name in {"verdict.json", "latest.json", "spec.json", "last_spec.json"}:
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and field in data and data.get("stage"):
            found.append((path.stat().st_mtime, data))
    found.sort(key=lambda item: item[0])
    return [item for _mtime, item in found]


def _fails(receipt: dict) -> bool:
    stage = receipt.get("stage")
    if stage == "mesh":
        evidence = dict(receipt)
        evidence.setdefault("mesh_ok", True)
        return not judge_mesh(evidence)
    if stage == "solve":
        return not judge_solve(receipt)
    if stage == "pilot":
        return not judge_pilot(receipt)
    if stage == "forces":
        return not judge_forces(receipt)
    return False


def _below(old: float, witnessed: float | None, integer: bool) -> float | int:
    if integer:
        return int(old) - 1
    if witnessed is not None and witnessed <= old:
        target = witnessed
    else:
        target = old
    step = max(abs(float(old)) * 0.01, 1e-6)
    new = min(float(old), float(target)) - step
    if not new < float(old):
        new = math.nextafter(float(old), -math.inf)
    return float(new)


def _bound_plan(store: Path, text: str) -> tuple[str, object] | _Looser | None:
    tighten = _TIGHTEN.search(text)
    loose = _LOOSE.search(text)
    if tighten is None and loose is None:
        return None
    field = (tighten.group(1) if tighten else loose.group(1)).strip()
    if field not in _FIELDS or not hasattr(Bounds, field):
        return None
    old = getattr(Bounds, field)
    integer = isinstance(old, int) and not isinstance(old, bool)
    if tighten is not None:
        raw = float(tighten.group(2))
        new: object = int(raw) if integer else float(raw)
    else:
        witnessed = None
        rows = _receipts(store, field)
        if rows:
            try:
                witnessed = float(rows[-1][field])
            except (TypeError, ValueError):
                witnessed = None
        new = _below(float(old), witnessed, integer)
    try:
        new_f = float(new)
        old_f = float(old)
    except (TypeError, ValueError):
        return None
    if field in _MIN_FIELDS:
        if new_f < old_f:
            return _LOOSER
        if new_f == old_f:
            return None
    else:
        if new_f > old_f:
            return _LOOSER
        if new_f == old_f:
            return None
    return field, int(new) if integer else float(new)


def _try_bound(store: Path, text: str) -> _Change | _Looser | None:
    plan = _bound_plan(store, text)
    if plan is None or isinstance(plan, _Looser):
        return plan
    field, new = plan
    rows = _receipts(store, field)
    if not rows:
        return None
    kernel_path = Path(Bounds.__module__ and __import__("wingline.kernel", fromlist=["Bounds"]).__file__)
    original = kernel_path.read_bytes()
    shape_path = _REPO / "harness" / "shapes" / "receipt.shacl.ttl"
    lean_path = _REPO / "harness" / "gate" / "Gate.lean"
    shape_bytes = shape_path.read_bytes()
    lean_bytes = lean_path.read_bytes()
    old = getattr(Bounds, field)
    pattern = re.compile(rf"^(\s*{re.escape(field)}\s*=\s*).+$", re.MULTILINE)
    updated, count = pattern.subn(rf"\g<1>{_literal(new)}", original.decode(), count=1)
    if count != 1:
        return None

    def revert() -> None:
        kernel_path.write_bytes(original)
        shape_path.write_bytes(shape_bytes)
        lean_path.write_bytes(lean_bytes)
        setattr(Bounds, field, old)

    kernel_path.write_text(updated)
    setattr(Bounds, field, new)
    try:
        emit(_REPO)
        if not _fails(rows[-1]):
            revert()
            return None
        view = project(_FIX / "ledger_green", None, _FIX / "outer_affirm.json")
        if type(view).__name__ != "Screening":
            revert()
            return None
    except Exception:
        revert()
        return None
    return _Change("BoundTighten", revert)


def _try_shacl(text: str) -> None:
    # writer and log are already required. parent_id stays optional so the
    # first receipt still validates. Nothing to ship.
    if re.search(r"\b(writer|parent_id|log)\b", text):
        return None
    return None


def _try_deny(store: Path, text: str) -> _Change | None:
    name = None
    match = _TOOL.search(text)
    if match and match.group(1) not in _FIVE and match.group(1) not in {"ran", "name", "that"}:
        name = match.group(1)
    observe = store / "observe.jsonl"
    if name is None and observe.is_file():
        for line in observe.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            tool = str(row.get("tool") or "")
            if tool and tool not in _FIVE:
                name = tool
                break
    if name is None:
        return None
    path = _REPO / "harness" / "deny-tools.txt"
    previous = path.read_text() if path.is_file() else ""
    lines = [line for line in previous.splitlines() if line.strip()]
    if name not in lines:
        lines.append(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    if name not in path.read_text().splitlines():
        path.write_text(previous)
        return None

    def revert() -> None:
        if previous:
            path.write_text(previous)
        elif path.is_file():
            path.unlink()

    return _Change("DenyTool", revert)


def _try_lean(text: str) -> None:
    if "lean" not in text.lower():
        return None
    lean = Path("/Users/saarthakbhatia/.elan/bin/lean")
    gate = _REPO / "harness" / "gate" / "Gate.lean"
    if not lean.is_file() or not gate.is_file():
        return None
    # The emitted proofs already cover the row. Do not add a constructor.
    return None


def _try_replay(text: str, correction_id: str) -> _Change | None:
    if "replay" not in text.lower():
        return None
    folder = _REPO / "tests" / "replay"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{correction_id}.py"
    path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "from wingline import Refusal",
                "from wingline.kernel import project",
                "",
                "def test_replay_refuses() -> None:",
                "    root = Path(__file__).resolve().parents[2]",
                "    view = project(root / \"fixtures\" / \"naca2412-a6\" / \"ledger_mesh_red\", None, None)",
                "    assert isinstance(view, Refusal)",
                "",
            ]
        )
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(path),
            str(_REPO / "tests" / "test_project_2412.py"),
            "-q",
            "--disable-warnings",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    def revert() -> None:
        if path.is_file():
            path.unlink()

    if proc.returncode != 0:
        revert()
        return None
    return _Change("ReplayTest", revert)


def _skill(store: Path, row: dict) -> NeedsHuman:
    folder = store / "meta" / "proposals"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{row['id']}.md"
    cleaned = " ".join(str(row.get("text") or "").split()).strip()
    if not cleaned:
        cleaned = "No structural template held."
    lowered = cleaned.lower()
    if "cfd" in lowered and "pass" in lowered:
        cleaned = "The correction is recorded for a human and does not pass the run."
    if cleaned[-1] not in ".!?":
        cleaned += "."
    path.write_text(cleaned + "\n")
    return NeedsHuman("SkillDraft", str(row["id"]), str(path))


def _pilot(store: Path) -> bool:
    from wingline.mcp_server import perform_pilot

    flag = os.environ.pop("WINGLINE_FIXTURE", None)
    run_id = os.environ.pop("WINGLINE_RUN_ID", None)
    try:
        result = perform_pilot(store)
    except Exception:
        return False
    finally:
        if flag is not None:
            os.environ["WINGLINE_FIXTURE"] = flag
        if run_id is not None:
            os.environ["WINGLINE_RUN_ID"] = run_id
    return bool(result.get("green"))


def _resolve(store: Path, rows: list[dict], correction_id: str, result: str) -> None:
    for row in rows:
        if row.get("id") == correction_id:
            row["resolved"] = True
            row["result"] = result
    _save(store, rows)


def propose(store: Path) -> Shipped | NeedsHuman | Noop:
    """Ship the first template that holds. Never a grade."""
    store = Path(store)
    handle = _lock(store)
    try:
        rows = _load(store)
        row = _newest(rows)
        if row is None:
            return Noop()
        text = str(row.get("text") or "")
        correction_id = str(row["id"])
        bound = _try_bound(store, text)
        if isinstance(bound, _Looser):
            _resolve(store, rows, correction_id, "noop-looser")
            return Noop("looser value matches no template")
        change = bound if isinstance(bound, _Change) else None
        if change is None:
            _try_shacl(text)
            change = _try_deny(store, text)
        if change is None:
            _try_lean(text)
        if change is None:
            change = _try_replay(text, correction_id)
        if change is not None:
            if not _pilot(store):
                change.revert()
                _resolve(store, rows, correction_id, "reverted")
                return Noop("pilot failed; change reverted")
            _resolve(store, rows, correction_id, "shipped")
            return Shipped(change.template, correction_id)
        drafted = _skill(store, row)
        _resolve(store, rows, correction_id, "needs-human")
        return drafted
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
