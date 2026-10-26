"""One correction, one template, and the interview when a request is incomplete.

The return value of propose is not a grade. resolve asks for a wing or an angle
the engineer left out, then hands back a spec. It does not run OpenFOAM.
"""

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

from wingline.engineer import parse, parse_message, peel_dimensions
from wingline.grok_call import run_grok
from wingline.prompts import INTERVIEW_SCHEMA, META_SYSTEM
from wingline.kernel import (
    Bounds,
    Panel,
    Refusal,
    emit,
    judge_forces,
    judge_mesh,
    judge_pilot,
    judge_solve,
    parse_naca,
    project,
)

_REPO = Path(__file__).resolve().parents[1]
_FIX = _REPO / "fixtures" / "naca2412-a6"
_FIVE = {"observe", "commit_spec", "commit_mesh", "commit_solve", "commit_forces"}
_MIN_FIELDS = {"alpha_min", "u_min", "t_min"}
_FIELDS = _MIN_FIELDS | {
    "max_non_ortho",
    "continuity_local",
    "continuity_cum",
    "reynolds_min",
    "reynolds_max",
    "reference_abs",
    "reference_rel",
    "reference_min_confidence",
    "wall_grading_scale",
    "n_normal",
    "end_time",
    "alpha_max",
    "u_max",
    "t_max",
    "cylinder_continuity",
    "cylinder_velocity",
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
    from wingline.outer import find_lean

    lean = find_lean()
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


class NeedsAnswer(Exception):
    """The interview needs a person, and stdin is not a terminal."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        super().__init__(prompt)


@dataclass(frozen=True, slots=True)
class Study:
    """One direct case, plus how the outer search may move."""

    spec: dict
    objective: str
    alpha_fixed: bool
    sentence: str
    notes: tuple[str, ...]


_NACA_CODE = re.compile(r"NACA\s+(?P<naca>\d{4})", re.IGNORECASE)
_ANGLE = re.compile(r"at\s+(?P<alpha>-?\d+(?:\.\d+)?)\s+degrees?", re.IGNORECASE)
_SPEED = re.compile(r"(?P<speed>\d+(?:\.\d+)?)\s+m/s", re.IGNORECASE)
_LOW = re.compile(r"low\s+drag", re.IGNORECASE)
_HIGH = re.compile(r"(?:high|more)\s+lift", re.IGNORECASE)


def _tok(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


def _spec(naca: str, alpha: float, speed: float, extra: dict | None = None) -> dict:
    payload = {
        "naca": naca,
        "alpha_deg": int(alpha) if float(alpha).is_integer() else float(alpha),
        "u_ms": int(speed) if float(speed).is_integer() else float(speed),
        "density": "coarse",
    }
    for key in ("chord_m", "span_m", "camber", "camber_pos", "thickness"):
        if extra is None or extra.get(key) is None:
            continue
        number = float(extra[key])
        payload[key] = int(number) if number.is_integer() else number
    return payload


def _sentence(spec: dict) -> str:
    text = (
        f"NACA {spec['naca']} at {_tok(float(spec['alpha_deg']))} degrees"
        f" and {_tok(float(spec['u_ms']))} m/s"
    )
    if spec.get("chord_m") is not None:
        text += f" chord {_tok(float(spec['chord_m']))}m"
    if spec.get("span_m") is not None:
        text += f" span {_tok(float(spec['span_m']))}m"
    return text + "."


def _study(spec: dict, objective: str, alpha_fixed: bool, notes: tuple[str, ...]) -> Study:
    return Study(spec, objective, alpha_fixed, _sentence(spec), notes)


def _meta_refusal(reason: str) -> Refusal:
    return Refusal(
        gate="meta",
        log="meta/dialogue.jsonl",
        reason=reason,
        panels=(Panel("goal", reason), Panel("refused", "meta")),
    )


def _parse_refusal(reason: str, store: Path | None = None) -> Refusal:
    return Refusal(
        gate="parse",
        log="",
        reason=reason,
        panels=(Panel("goal", parse_message(reason, store)), Panel("refused", "parse")),
    )


def _log(store: Path, role: str, text: str) -> None:
    folder = Path(store) / "meta"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "dialogue.jsonl"
    row = {"at": _now(), "role": role, "text": text}
    with path.open("a") as handle:
        handle.write(json.dumps(row) + "\n")


def _objective(text: str) -> str:
    if _LOW.search(text):
        return "min_cd"
    if _HIGH.search(text):
        return "max_cl"
    return "max_ld"


def _speed_of(text: str) -> tuple[float, bool]:
    match = _SPEED.search(text)
    if match is None:
        return float(Bounds.u_default), False
    return float(match.group("speed")), True


def _ask(store: Path, reply: Callable[[str], str], prompt: str) -> str | Refusal:
    _log(store, "meta", prompt)
    try:
        answer = reply(prompt).strip()
    except NeedsAnswer as exc:
        return _meta_refusal(str(exc))
    except EOFError:
        return _meta_refusal(prompt)
    _log(store, "engineer", answer)
    return answer


def _blank(value: object) -> str | float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value  # type: ignore[return-value]


def _length(result: dict, known: dict, key: str, lo: float, hi: float, label: str) -> tuple[float | None, str | None]:
    value = _blank(result.get(key))
    if value is None:
        value = known.get(key)
    if value is None:
        return None, None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, f"{label} is not a number"
    if not (lo <= number <= hi):
        return None, f"{label} must be from {lo:g} to {hi:g}"
    return number, None


def _accept(result: dict, known: dict) -> dict | str:
    """A ready object becomes a spec, or a sentence the model can read."""
    naca = _blank(result.get("naca")) or known.get("naca")
    alpha = _blank(result.get("alpha_deg"))
    if alpha is None:
        alpha = known.get("alpha_deg")
    speed = _blank(result.get("u_ms"))
    if speed is None:
        speed = known.get("u_ms")
    objective = str(result.get("objective") or known.get("objective") or "")
    if objective not in {"min_cd", "max_cl", "max_ld"}:
        return "objective must be min_cd, max_cl, or max_ld"
    try:
        foil = parse_naca(str(naca).replace("NACA", "").replace("naca", "").strip())
    except ValueError as exc:
        return str(exc)
    try:
        alpha_f = float(alpha)  # type: ignore[arg-type]
        speed_f = float(speed)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "angle and speed are still missing"
    if not (Bounds.alpha_min <= alpha_f <= Bounds.alpha_max):
        return f"angle must be from {Bounds.alpha_min:g} to {Bounds.alpha_max:g}"
    if not (Bounds.u_min <= speed_f <= Bounds.u_max):
        return f"speed must be from {Bounds.u_min:g} to {Bounds.u_max:g}"
    chord, chord_err = _length(result, known, "chord_m", Bounds.chord_min, Bounds.chord_max, "chord")
    if chord_err:
        return chord_err
    span, span_err = _length(result, known, "span_m", Bounds.span_min, Bounds.span_max, "span")
    if span_err:
        return span_err
    extra = {}
    if chord is not None:
        extra["chord_m"] = chord
    if span is not None:
        extra["span_m"] = span
    return {
        "spec": _spec(foil.code(), alpha_f, speed_f, extra),
        "objective": objective,
    }


def _gap_phrases(known: dict) -> list[str]:
    gaps = []
    if not known.get("naca"):
        gaps.append(f"a NACA 4-digit section with thickness {Bounds.t_min:g} to {Bounds.t_max:g}")
    if known.get("alpha_deg") is None:
        gaps.append(
            f"an angle of attack from {Bounds.alpha_min:g} to {Bounds.alpha_max:g} degrees"
        )
    return gaps


def _needed(known: dict, problem: str = "") -> str:
    """Name the facts that still block a case. Defaults are stated as assumptions."""
    gaps = _gap_phrases(known)
    if len(gaps) == 1:
        needed = gaps[0]
    elif len(gaps) > 1:
        needed = f"{gaps[0]} and {gaps[1]}"
    else:
        needed = ""
    held = []
    if known.get("naca"):
        held.append(f"NACA {known['naca']}")
    if known.get("alpha_deg") is not None:
        held.append(f"{_tok(float(known['alpha_deg']))} degrees")
    if known.get("speed_was_stated"):
        held.append(f"{_tok(float(known['u_ms']))} m/s")
    if known.get("chord_m") is not None:
        held.append(f"chord {_tok(float(known['chord_m']))} m")
    if known.get("span_m") is not None:
        held.append(f"span {_tok(float(known['span_m']))} m")
    sentence = f"Still needed: {needed}." if needed else "The direct case is ready."
    if held:
        sentence = f"Already set: {', '.join(held)}. " + sentence
    assumed = []
    if not known.get("speed_was_stated"):
        assumed.append(f"speed {_tok(float(known['u_ms']))} m/s")
    if known.get("chord_m") is None:
        assumed.append(f"chord {_tok(Bounds.chord_m)} m")
    if known.get("span_m") is None:
        assumed.append(f"span {_tok(Bounds.z_thickness)} m")
    if assumed and needed:
        sentence += " Assumed until you say otherwise: " + ", ".join(assumed) + "."
    if problem:
        sentence = problem.rstrip(".") + ". " + sentence
    return sentence


_ASIDE = re.compile(r"checking how|how the interview", re.IGNORECASE)
_NARRATION = re.compile(r"(?i)\s*(?:the )?last reply did not close the interview\.?\s*")


def _spoken(say: str) -> str:
    """Drop harness narration. The engineer reads the rest of the model's reply."""
    return " ".join(_NARRATION.sub(" ", say).split())


def _usable_say(say: str, _known: dict) -> bool:
    """Show the model's reply. A skill lookup falls back to the gap sentence."""
    if _ASIDE.search(say):
        return False
    return len(_spoken(say).split()) >= 4


def _angle_in(text: str) -> float | None:
    match = _ANGLE.search(text)
    if match is not None:
        return float(match.group("alpha"))
    match = re.search(
        r"\b(?:angle(?:\s+of\s+attack)?|aoa|alpha)\s*(?:of|is|:|=)?\s*(-?\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if match is not None:
        return float(match.group(1))
    match = re.search(r"(-?\d+(?:\.\d+)?)\s+degrees?", text, re.IGNORECASE)
    if match is not None:
        return float(match.group(1))
    return None


def _code_in(text: str) -> str | None:
    match = _NACA_CODE.search(text)
    if match is not None:
        return match.group("naca")
    bare = re.search(r"\b(\d{4})\b", text)
    if bare is not None:
        return bare.group(1)
    return None


def _missing(known: dict) -> bool:
    return not known.get("naca") or known.get("alpha_deg") is None


def _engineer_gave_angle(history: list[dict], alpha: float) -> bool:
    for turn in history:
        if turn.get("role") != "engineer":
            continue
        text = str(turn.get("text") or "")
        found = _angle_in(text)
        if found is not None and abs(found - alpha) < 1e-9:
            return True
        bare = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*", text)
        if bare is not None and abs(float(bare.group(1)) - alpha) < 1e-9:
            return True
    return False


def _ready_angle_ok(result: dict, known: dict, history: list[dict]) -> bool:
    """An angle the engineer never gave cannot mark the interview ready."""
    raw = _blank(result.get("alpha_deg"))
    if raw is None:
        return known.get("alpha_deg") is not None
    try:
        alpha = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if known.get("alpha_deg") is not None and abs(float(known["alpha_deg"]) - alpha) < 1e-9:
        return True
    return _engineer_gave_angle(history, alpha)


def _apply_answer(known: dict, answer: str) -> str | None:
    """Fold a direct reply into known. Return the first problem, if any."""
    text = " ".join(answer.split())
    if not text:
        return "That reply was empty"
    before = (
        known.get("naca"),
        known.get("alpha_deg"),
        known.get("u_ms"),
        known.get("speed_was_stated"),
        known.get("chord_m"),
        known.get("span_m"),
    )
    problems: list[str] = []
    cleaned, extra = peel_dimensions(text)
    if not known.get("naca"):
        code = _code_in(cleaned) or _code_in(text)
        if code is not None:
            try:
                known["naca"] = parse_naca(code).code()
            except ValueError as exc:
                problems.append(str(exc))
    alpha = _angle_in(text)
    if alpha is None and known.get("alpha_deg") is None:
        bare = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*", text)
        if bare is not None:
            alpha = float(bare.group(1))
    if alpha is not None:
        if Bounds.alpha_min <= alpha <= Bounds.alpha_max:
            known["alpha_deg"] = alpha
        else:
            problems.append(f"angle must be from {Bounds.alpha_min:g} to {Bounds.alpha_max:g}")
    speed_match = _SPEED.search(text)
    if speed_match is not None:
        speed = float(speed_match.group("speed"))
        if Bounds.u_min <= speed <= Bounds.u_max:
            known["u_ms"] = speed
            known["speed_was_stated"] = True
        else:
            problems.append(f"speed must be from {Bounds.u_min:g} to {Bounds.u_max:g}")
    for key, lo, hi, label in (
        ("chord_m", Bounds.chord_min, Bounds.chord_max, "chord"),
        ("span_m", Bounds.span_min, Bounds.span_max, "span"),
    ):
        if key not in extra:
            continue
        number = float(extra[key])
        if lo <= number <= hi:
            known[key] = number
        else:
            problems.append(f"{label} must be from {lo:g} to {hi:g}")
    if problems:
        return problems[0]
    after = (
        known.get("naca"),
        known.get("alpha_deg"),
        known.get("u_ms"),
        known.get("speed_was_stated"),
        known.get("chord_m"),
        known.get("span_m"),
    )
    if after == before and _missing(known):
        return "That reply did not include the missing value"
    return None


def _case_from_known(known: dict) -> Study:
    extra = {}
    if known.get("chord_m") is not None:
        extra["chord_m"] = known["chord_m"]
    if known.get("span_m") is not None:
        extra["span_m"] = known["span_m"]
    spec = _spec(str(known["naca"]), float(known["alpha_deg"]), float(known["u_ms"]), extra)
    note = (
        f"Opening NACA {spec['naca']} at {_tok(float(spec['alpha_deg']))} degrees"
        f" and {_tok(float(spec['u_ms']))} m/s."
    )
    if spec.get("chord_m") is not None:
        note += f" Chord {_tok(float(spec['chord_m']))} m."
    if spec.get("span_m") is not None:
        note += f" Span {_tok(float(spec['span_m']))} m."
    note += " Nearby camber and thickness will be compared afterward."
    return _study(spec, str(known.get("objective") or "max_ld"), True, (note,))


def _missing_names(known: dict) -> list[str]:
    names = []
    if not known.get("naca"):
        names.append("section")
    if known.get("alpha_deg") is None:
        names.append("angle")
    return names


def _interview_prompt(raw: str, known: dict, history: list[dict], rejection: str) -> str:
    missing = _missing_names(known)
    lines = [
        "Engineer request:",
        raw,
        "",
        "Already known. Keep these unless the engineer changes them:",
        json.dumps(known),
        "",
        "Missing before a case can open:",
        ", ".join(missing) if missing else "nothing",
        "",
        "Dialogue:",
    ]
    if not history:
        lines.append("(none)")
    for turn in history:
        lines.append(f"{turn['role']}: {turn['text']}")
    if rejection:
        lines.extend(
            [
                "",
                "The gate rejected the last ready spec:",
                rejection,
                "Explain that to the engineer in your own words. Do not quote this label.",
            ]
        )
    lines.extend(
        [
            "",
            "Write say as the message the engineer will read.",
            "Answer what they asked. Do not quote the missing list or these instructions.",
            "Leave ready false while section or angle is still missing.",
            "Do not choose an angle. Do not look up files, skills, or how to ask.",
            "Reply with the interview object only.",
        ]
    )
    return "\n".join(lines)


def _grok_model(prompt: str) -> dict:
    return run_grok(META_SYSTEM, prompt, schema=INTERVIEW_SCHEMA, max_turns=1, allow_tools=False)


def _interview(
    raw: str,
    store: Path,
    reply: Callable[[str], str],
    known: dict,
    model: Callable[[str], dict],
) -> Study | Refusal:
    history: list[dict] = []
    rejection = ""
    for _ in range(4):
        result: dict | None = None
        try:
            got = model(_interview_prompt(raw, known, history, rejection))
        except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
            got = None
        if isinstance(got, dict):
            result = got
        ready = result is not None and result.get("ready") is True
        if ready and result is not None and _ready_angle_ok(result, known, history):
            accepted = _accept(result, known)
            if isinstance(accepted, str):
                rejection = accepted
                history.append({"role": "gate", "text": accepted})
                continue
            note = str(result.get("say") or "").strip() or "The direct case is settled."
            return _study(
                accepted["spec"],
                accepted["objective"],
                True,
                (note,),
            )
        raw_say = ""
        if result is not None and not ready:
            raw_say = str(result.get("say") or "").strip()
        say = _spoken(raw_say)
        if say and _usable_say(raw_say, known):
            answer = _ask(store, reply, say)
            if isinstance(answer, Refusal):
                return answer
            history.append({"role": "meta", "text": say})
            history.append({"role": "engineer", "text": answer})
            rejection = ""
            continue
        question = _needed(known, rejection)
        answer = _ask(store, reply, question)
        if isinstance(answer, Refusal):
            return answer
        problem = _apply_answer(known, answer)
        history.append({"role": "meta", "text": question})
        history.append({"role": "engineer", "text": answer})
        if problem:
            rejection = problem
            continue
        rejection = ""
        if not _missing(known):
            return _case_from_known(known)
    return _meta_refusal("the interview did not settle a wing inside the bounds")


def resolve(
    text: str,
    store: Path,
    reply: Callable[[str], str],
    model: Callable[[str], dict] | None = None,
) -> Study | Refusal:
    """Fill a direct spec. A missing wing or angle is a Grok interview."""
    raw = " ".join(text.split())
    store = Path(store)
    if not raw:
        return _parse_refusal("unrecognized request", store)
    parsed = parse(raw, store)
    if not isinstance(parsed, Refusal):
        return _study(parsed, _objective(raw), True, ())
    if parsed.reason != "unrecognized request":
        return parsed
    cleaned, extra = peel_dimensions(raw)
    if "chord_m" in extra and not (Bounds.chord_min <= float(extra["chord_m"]) <= Bounds.chord_max):
        return _parse_refusal("chord outside bounds", store)
    if "span_m" in extra and not (Bounds.span_min <= float(extra["span_m"]) <= Bounds.span_max):
        return _parse_refusal("span outside bounds", store)
    if _NACA_CODE.search(cleaned) is None and not re.search(r"\bwing\b", raw, re.IGNORECASE):
        if _LOW.search(raw) is None and _HIGH.search(raw) is None:
            return parsed
    angle_match = _ANGLE.search(cleaned)
    stated_alpha: float | None = None
    if angle_match is not None:
        stated_alpha = float(angle_match.group("alpha"))
        if not (Bounds.alpha_min <= stated_alpha <= Bounds.alpha_max):
            return _parse_refusal("alpha outside bounds", store)
    speed, speed_given = _speed_of(cleaned)
    if not (Bounds.u_min <= speed <= Bounds.u_max):
        return _parse_refusal("speed outside bounds", store)
    code_match = _NACA_CODE.search(cleaned)
    known: dict = {
        "naca": None,
        "alpha_deg": stated_alpha,
        "u_ms": speed,
        "objective": _objective(raw),
        "speed_was_stated": speed_given,
    }
    if extra.get("chord_m") is not None:
        known["chord_m"] = extra["chord_m"]
    if extra.get("span_m") is not None:
        known["span_m"] = extra["span_m"]
    if code_match is not None:
        try:
            known["naca"] = parse_naca(code_match.group("naca")).code()
        except ValueError as exc:
            return _parse_refusal(str(exc), store)
    if known["naca"] and stated_alpha is not None:
        return _study(_spec(str(known["naca"]), float(stated_alpha), speed, extra), _objective(raw), True, ())
    return _interview(raw, store, reply, known, model or _grok_model)
