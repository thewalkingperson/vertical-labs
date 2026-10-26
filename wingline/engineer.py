"""Ask, refine, and the store lock. The inner loop is the agent unless WINGLINE_AGENT=0."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from wingline.kernel import (
    Bounds,
    DesignGrade,
    Panel,
    Refusal,
    Screening,
    load_ledger,
    parse_naca,
    project,
    shape,
)
from wingline.mcp_server import canonical_spec, perform_pilot, spec_hash
from wingline.prompts import INNER_SYSTEM
from wingline.outer import evaluate
from wingline.planner import drive

import re
import secrets

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO / "fixtures" / "naca2412-a6"
_NACA = re.compile(
    r"^NACA\s+(?P<naca>\d{4})\s+at\s+(?P<alpha>-?\d+(?:\.\d+)?)\s+degrees?"
    r"(?:\s+and\s+(?P<speed>\d+(?:\.\d+)?)\s+m/s)?"
    r"\.?"
    r"(?:\s+Is this worth a finer run\?)?"
    r"$",
    re.IGNORECASE,
)
_SAME = re.compile(
    r"^Same wing at\s+(?P<alpha>-?\d+(?:\.\d+)?)\s+degrees?\.?$",
    re.IGNORECASE,
)
_DEGREES = re.compile(
    r"(?P<alpha>-?\d+(?:\.\d+)?)\s*(?:degrees?|deg|°)\b",
    re.IGNORECASE,
)
_SPEED_ANY = re.compile(
    r"(?P<speed>\d+(?:\.\d+)?)\s*m/s\b",
    re.IGNORECASE,
)
_TOPIC = re.compile(
    r"\b(?:wing|airfoil|naca|camber|thickness|drag|lift)\b",
    re.IGNORECASE,
)
_ENV = ("WINGLINE_STORE", "WINGLINE_RUN_ID", "WINGLINE_LEASE")
# Grok qualifies MCP tools as <server>__<tool>. These are the only five
# the inner loop needs. --permission-mode dontAsk is not enough: an
# unlisted MCP tool still asks for approval and a headless run cannot.
_MCP_ALLOW = (
    "wingline__observe",
    "wingline__commit_spec",
    "wingline__commit_mesh",
    "wingline__commit_solve",
    "wingline__commit_forces",
)
_CHORD = re.compile(
    r"\b(?:chord(?:\s+of)?\s+|c\s*=\s*)(?P<c>\d+(?:\.\d+)?)\s*m?\b",
    re.IGNORECASE,
)
_SPAN = re.compile(
    r"\b(?:span|depth)(?:\s+of)?\s+(?P<s>\d+(?:\.\d+)?)\s*m?\b",
    re.IGNORECASE,
)
_PROFILE = re.compile(
    r"\bcamber\s+(?P<m>\d+(?:\.\d+)?)\s*%?\s+(?:at\s+)?(?P<p>\d+(?:\.\d+)?)\s*%?"
    r"\s+thickness\s+(?P<t>\d+(?:\.\d+)?)\s*%?",
    re.IGNORECASE,
)
_MPT = re.compile(
    r"\bm\s*=\s*(?P<m>\d+(?:\.\d+)?)\b.*?\bp\s*=\s*(?P<p>\d+(?:\.\d+)?)\b.*?"
    r"\bt\s*=\s*(?P<t>\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_NACA_CODE = re.compile(r"\bNACA\s+\d{4}\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class EngineerView:
    run_id: str
    outcome: Screening | DesignGrade | Refusal


def _last_spec(store: Path | None) -> dict | None:
    if store is None:
        return None
    path = Path(store) / "last_spec.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "naca" not in payload:
        return None
    return payload


def parse_message(reason: str, store: Path | None = None) -> str:
    """Plain sentence for a parse refusal. The reason code stays for the gate."""
    if reason == "no previous spec":
        return (
            "There is no earlier wing to change. "
            "Name a section and an angle, such as NACA 2412 at 6 degrees."
        )
    if reason == "alpha outside bounds":
        return f"The angle has to be from {Bounds.alpha_min:g} to {Bounds.alpha_max:g} degrees."
    if reason == "speed outside bounds":
        return f"The speed has to be from {Bounds.u_min:g} to {Bounds.u_max:g} m/s."
    if reason == "thickness outside bounds":
        return f"The thickness digit has to be from {Bounds.t_min:g} to {Bounds.t_max:g}."
    if reason == "chord outside bounds":
        return f"The chord has to be from {Bounds.chord_min:g} to {Bounds.chord_max:g} m."
    if reason == "span outside bounds":
        return f"The span has to be from {Bounds.span_min:g} to {Bounds.span_max:g} m."
    if reason == "thickness digit out of range":
        return f"The thickness digit has to be from {Bounds.t_min:g} to {Bounds.t_max:g}."
    if reason == "naca digit out of range":
        return "Each NACA digit has to be from 0 to 9, and the thickness digit from 6 to 24."
    if reason == "cambered section needs a position digit":
        return "A cambered section needs a camber position. 2412 is a section. 2012 is not."
    if reason not in {"unrecognized request", ""}:
        return reason
    last = _last_spec(store)
    if last is not None:
        return (
            f"The last wing is NACA {last.get('naca')} at {last.get('alpha_deg')} degrees"
            f" and {last.get('u_ms')} m/s. Say the new angle, for example 10 degrees,"
            " or name another section such as NACA 2412 at 6 degrees."
        )
    return (
        "Name a section and an angle, such as NACA 2412 at 6 degrees. "
        "A later message can change that angle, for example 10 degrees."
    )


def _parse_refusal(reason: str, store: Path | None = None) -> Refusal:
    return Refusal(
        gate="parse",
        log="",
        reason=reason,
        panels=(Panel("goal", parse_message(reason, store)), Panel("refused", "parse")),
    )


def _bounds_refusal() -> Refusal:
    return Refusal(
        gate="bounds",
        log="harness/shapes/receipt.shacl.ttl",
        reason="emitted policy differs",
        panels=(Panel("goal", ""), Panel("refused", "bounds")),
    )


def _commission() -> Refusal:
    return Refusal(
        gate="commission",
        log="",
        reason="screening was not worth a finer run",
        panels=(Panel("goal", ""), Panel("refused", "commission")),
    )


def _number(text: str) -> float:
    return float(text)


def _whole(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return float(value)


def policy_differs() -> bool:
    from wingline.kernel import render_lean, render_shacl

    shape = _REPO / "harness" / "shapes" / "receipt.shacl.ttl"
    lean = _REPO / "harness" / "gate" / "Gate.lean"
    if not shape.is_file() or not lean.is_file():
        return True
    return shape.read_text() != render_shacl() or lean.read_text() != render_lean()


def _camber_digit(value: float) -> int:
    if value < 1:
        return int(round(value * 100))
    return int(round(value))


def _position_digit(value: float) -> int:
    if value > 9:
        return int(round(value / 10.0))
    if value <= 1 and not value.is_integer():
        return int(round(value * 10.0))
    return int(round(value))


def _thickness_digit(value: float) -> int:
    if value < 1:
        return int(round(value * 100))
    return int(round(value))


def _tidy_request(raw: str) -> str:
    for _ in range(3):
        cleaned = re.sub(r"\b(?:with|and)\s+(?=(?:and|at)\b)", "", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bwith\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+\.", ".", cleaned)
        cleaned = " ".join(cleaned.split()).strip()
        if cleaned == raw:
            return cleaned
        raw = cleaned
    return raw


def peel_dimensions(text: str) -> tuple[str, dict]:
    """Pull chord, span, and an explicit camber statement off a request."""
    raw = " ".join(text.split())
    extra: dict = {}
    chord = _CHORD.search(raw)
    if chord:
        extra["chord_m"] = float(chord.group("c"))
        raw = raw[: chord.start()] + " " + raw[chord.end() :]
    span = _SPAN.search(raw)
    if span:
        extra["span_m"] = float(span.group("s"))
        raw = raw[: span.start()] + " " + raw[span.end() :]
    if _NACA_CODE.search(raw) is None:
        profile = _PROFILE.search(raw) or _MPT.search(raw)
        if profile:
            camber = _camber_digit(float(profile.group("m")))
            position = _position_digit(float(profile.group("p")))
            thickness = _thickness_digit(float(profile.group("t")))
            extra["camber"] = camber
            extra["camber_pos"] = position
            extra["thickness"] = thickness
            code = f"{camber}{position}{thickness:02d}"
            raw = raw[: profile.start()] + f" NACA {code} " + raw[profile.end() :]
    return _tidy_request(raw), extra


def _attach(payload: dict, extra: dict) -> dict:
    for key in ("chord_m", "span_m", "camber", "camber_pos", "thickness"):
        if key in extra and extra[key] is not None:
            payload[key] = _whole(float(extra[key]))
    return payload


def _finish(
    foil: object,
    alpha: float,
    speed: float,
    chord_val: float | None,
    span_val: float | None,
    extra: dict,
    store: Path,
) -> dict | Refusal:
    if not (Bounds.alpha_min <= alpha <= Bounds.alpha_max):
        return _parse_refusal("alpha outside bounds", store)
    if not (Bounds.u_min <= speed <= Bounds.u_max):
        return _parse_refusal("speed outside bounds", store)
    if not (Bounds.t_min <= foil.t <= Bounds.t_max):  # type: ignore[attr-defined]
        return _parse_refusal("thickness outside bounds", store)
    if chord_val is not None and not (Bounds.chord_min <= float(chord_val) <= Bounds.chord_max):
        return _parse_refusal("chord outside bounds", store)
    chord_used = float(chord_val) if chord_val is not None else float(Bounds.chord_m)
    reynolds = float(speed) * chord_used / float(Bounds.nu)
    if not (Bounds.reynolds_min <= reynolds <= Bounds.reynolds_max):
        return _parse_refusal("Reynolds number outside the reference range", store)
    if span_val is not None and not (Bounds.span_min <= float(span_val) <= Bounds.span_max):
        return _parse_refusal("span outside bounds", store)
    payload = {
        "naca": foil.code(),  # type: ignore[attr-defined]
        "alpha_deg": _whole(alpha),
        "u_ms": _whole(speed),
        "density": "coarse",
    }
    if chord_val is not None:
        extra["chord_m"] = float(chord_val)
    if span_val is not None:
        extra["span_m"] = float(span_val)
    return canonical_spec(_attach(payload, extra))


def _follow(raw: str, store: Path, extra: dict) -> dict | Refusal | None:
    """A later angle or speed keeps the last section. A new wing stays unrecognized."""
    if _TOPIC.search(raw):
        return None
    angles = list(_DEGREES.finditer(raw))
    speeds = list(_SPEED_ANY.finditer(raw))
    if not angles and not speeds:
        return None
    previous = _last_spec(store)
    if previous is None:
        return _parse_refusal("no previous spec", store)
    try:
        foil = parse_naca(previous["naca"])
        alpha = float(angles[-1].group("alpha")) if angles else float(previous["alpha_deg"])
        speed = float(speeds[-1].group("speed")) if speeds else float(previous["u_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and "digit" in str(exc):
            return _parse_refusal(str(exc), store)
        return _parse_refusal("no previous spec", store)
    chord_val = extra.get("chord_m")
    span_val = extra.get("span_m")
    if chord_val is None and "chord_m" in previous:
        chord_val = float(previous["chord_m"])
    if span_val is None and "span_m" in previous:
        span_val = float(previous["span_m"])
    return _finish(foil, alpha, speed, chord_val, span_val, extra, store)


def parse(text: str, store: Path) -> dict | Refusal:
    """NACA sentence, Same wing, or a later angle or speed. Anything else is a parse refusal."""
    raw, extra = peel_dimensions(text)
    chord_val = extra.get("chord_m")
    span_val = extra.get("span_m")
    naca_match = _NACA.match(raw)
    same_match = _SAME.match(raw)
    if naca_match is None and same_match is None:
        followed = _follow(raw, store, extra)
        if followed is not None:
            return followed
        return _parse_refusal("unrecognized request", store)
    if same_match is not None:
        last = Path(store) / "last_spec.json"
        if not last.is_file():
            return _parse_refusal("no previous spec", store)
        try:
            previous = json.loads(last.read_text())
            foil = parse_naca(previous["naca"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return _parse_refusal("no previous spec", store)
        alpha = _number(same_match.group("alpha"))
        speed = float(previous["u_ms"])
        if chord_val is None and "chord_m" in previous:
            chord_val = float(previous["chord_m"])
        if span_val is None and "span_m" in previous:
            span_val = float(previous["span_m"])
    else:
        assert naca_match is not None
        try:
            foil = parse_naca(naca_match.group("naca"))
        except ValueError as exc:
            return _parse_refusal(str(exc), store)
        alpha = _number(naca_match.group("alpha"))
        speed_text = naca_match.group("speed")
        speed = float(speed_text) if speed_text is not None else float(Bounds.u_default)
    return _finish(foil, alpha, speed, chord_val, span_val, extra, store)


def _remember(store: Path, spec: dict) -> None:
    (store / "last_spec.json").write_text(json.dumps(canonical_spec(spec), indent=2) + "\n")


def _show(show, outcome: Screening | DesignGrade | Refusal) -> None:
    if show is None:
        return
    for panel in outcome.panels:
        show(panel)


def _pilot_refusal(record: dict) -> Refusal:
    return Refusal(
        gate="pilot",
        log=str(record.get("log") or ""),
        reason="pilot",
        panels=(Panel("goal", ""), Panel("refused", "pilot")),
    )


def _goal_panel(run_dir: Path) -> Panel:
    path = run_dir / "spec.json"
    if not path.is_file():
        return Panel("goal", "")
    try:
        spec = json.loads(path.read_text())
        return Panel(
            "goal",
            f"NACA {spec.get('naca')} at {spec.get('alpha_deg')} degrees, "
            f"{spec.get('u_ms')} m/s, {spec.get('density')}.",
        )
    except (OSError, json.JSONDecodeError, TypeError):
        return Panel("goal", "")


def _drive_refusal(run_dir: Path, driver: str) -> Refusal:
    """The driver stopped before the ledger was terminal. Never hide this as a fallback."""
    reason = f"{driver} did not finish the ledger"
    path = run_dir / "inner.json"
    if path.is_file():
        try:
            payload = json.loads(path.read_text())
            reason = str(payload.get("why") or reason)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    gate = "agent" if driver == "agent" else "incomplete"
    return Refusal(
        gate=gate,
        log=str(path),
        reason=reason,
        panels=(_goal_panel(run_dir), Panel("refused", gate)),
    )


class Engineer:
    def __init__(self, store: Path, lock) -> None:
        self.store = Path(store)
        self._lock = lock

    @classmethod
    def open(cls, store: Path) -> "Engineer":
        """Exclusive runs/engineer.lock. A second open on that store raises."""
        store = Path(store)
        store.mkdir(parents=True, exist_ok=True)
        handle = (store / "engineer.lock").open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("store is already open") from exc
        return cls(store, handle)

    def close(self) -> None:
        if self._lock is None:
            return
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock.close()
            self._lock = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    @contextmanager
    def _env(self, run_id: str, lease: str) -> Iterator[None]:
        previous = {key: os.environ.get(key) for key in _ENV}
        os.environ["WINGLINE_STORE"] = str(self.store)
        os.environ["WINGLINE_RUN_ID"] = run_id
        os.environ["WINGLINE_LEASE"] = lease
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def ask(self, text: str, show=None, on_run=None) -> EngineerView:
        if policy_differs():
            outcome: Screening | DesignGrade | Refusal = _bounds_refusal()
            _show(show, outcome)
            return EngineerView("", outcome)
        parsed = parse(text, self.store)
        if isinstance(parsed, Refusal):
            _show(show, parsed)
            return EngineerView("", parsed)
        _remember(self.store, parsed)
        if os.environ.get("WINGLINE_FIXTURE") == "1":
            outcome = project(
                _FIXTURE / "ledger_green",
                None,
                _FIXTURE / "outer_affirm.json",
            )
            _show(show, outcome)
            run_id = str(getattr(outcome, "run_id", ""))
            return EngineerView(run_id, outcome)
        run_id = "run-" + secrets.token_hex(8)
        run_dir = self.store / run_id
        run_dir.mkdir(parents=True)
        if on_run is not None:
            on_run(run_id)
        (run_dir / "spec.json").write_text(json.dumps(parsed, indent=2) + "\n")
        lease = _write_lease(run_dir / "lease")
        with self._env(run_id, lease):
            pilot = perform_pilot(self.store)
            if not pilot.get("green"):
                outcome = self._finish(run_dir, pilot)
                _show(show, outcome)
                return EngineerView(run_id, outcome)
            failed = self._drive(run_dir, run_id, lease, parsed)
            if failed is not None:
                outcome = _drive_refusal(run_dir, failed)
                _show(show, outcome)
                return EngineerView(run_id, outcome)
            outcome = self._finish(run_dir, None)
        _show(show, outcome)
        return EngineerView(str(getattr(outcome, "run_id", run_id)), outcome)

    def refine(self, run_id: str, show=None, on_run=None) -> EngineerView:
        if policy_differs():
            outcome: Screening | DesignGrade | Refusal = _bounds_refusal()
            _show(show, outcome)
            return EngineerView("", outcome)
        parent_dir = self.store / run_id
        parent_ledger = parent_dir / "ledger"
        parent_verdict = parent_dir / "outer" / "verdict.json"
        if not parent_ledger.is_dir():
            outcome = _commission()
            _show(show, outcome)
            return EngineerView("", outcome)
        parent_outcome = project(
            parent_ledger,
            None,
            parent_verdict if parent_verdict.is_file() else None,
        )
        if not isinstance(parent_outcome, Screening) or not parent_outcome.worth_finer:
            outcome = _commission()
            _show(show, outcome)
            return EngineerView(run_id, outcome)
        spec_path = parent_dir / "spec.json"
        if not spec_path.is_file():
            outcome = _commission()
            _show(show, outcome)
            return EngineerView(run_id, outcome)
        spec = canonical_spec(json.loads(spec_path.read_text()))
        spec["density"] = "refined"
        child_id = "run-" + secrets.token_hex(8)
        child_dir = self.store / child_id
        child_dir.mkdir(parents=True)
        (child_dir / "spec.json").write_text(json.dumps(spec, indent=2) + "\n")
        (child_dir / "parent").write_text(run_id + "\n")
        if on_run is not None:
            on_run(child_id)
        lease = _write_lease(child_dir / "lease")
        with self._env(child_id, lease):
            pilot = perform_pilot(self.store)
            if not pilot.get("green"):
                outcome = self._finish(child_dir, pilot)
                _show(show, outcome)
                return EngineerView(child_id, outcome)
            failed = self._drive(child_dir, child_id, lease, spec)
            if failed is not None:
                early = _drive_refusal(child_dir, failed)
                _show(show, early)
                return EngineerView(child_id, early)
            early = evaluate(child_dir)
        if isinstance(early, Refusal):
            _show(show, early)
            return EngineerView(child_id, early)
        child_verdict = child_dir / "outer" / "verdict.json"
        outcome = project(
            parent_ledger,
            child_dir / "ledger",
            (
                parent_verdict if parent_verdict.is_file() else None,
                child_verdict if child_verdict.is_file() else None,
            ),
        )
        _show(show, outcome)
        return EngineerView(str(getattr(outcome, "run_id", child_id)), outcome)

    def _drive(self, run_dir: Path, run_id: str, lease: str, spec: dict) -> str | None:
        """Inner loop. Return the driver name that failed, or None when terminal.

        There is no automatic fallback. The planner is a separate, explicit
        driver for tests (WINGLINE_AGENT=0), not a rescue for a failed agent.
        """
        use_agent = os.environ.get("WINGLINE_AGENT", "1") != "0"
        if use_agent:
            if not shutil.which("grok"):
                _mark_inner(run_dir, "agent", "grok is not on PATH")
                return "agent"
            _drive_agent(run_dir, run_id, lease, spec, self.store)
            if _ledger_terminal(run_dir):
                _mark_inner(run_dir, "agent", "")
                return None
            _mark_inner(run_dir, "agent", "agent left the ledger open")
            return "agent"
        _mark_inner(run_dir, "tools", "WINGLINE_AGENT=0")
        drive(run_id, lease)
        return None if _ledger_terminal(run_dir) else "tools"

    def _finish(self, run_dir: Path, pilot: dict | None) -> Screening | DesignGrade | Refusal:
        early = evaluate(run_dir)
        if isinstance(early, Refusal):
            return early
        verdict = run_dir / "outer" / "verdict.json"
        outcome = project(run_dir / "ledger", None, verdict if verdict.is_file() else None)
        if (
            pilot is not None
            and not pilot.get("green")
            and isinstance(outcome, Refusal)
            and outcome.gate == "incomplete"
        ):
            return _pilot_refusal(pilot)
        return outcome


def _write_lease(path: Path) -> str:
    lease = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, lease.encode("ascii"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return lease


def _drive_agent(run_dir: Path, run_id: str, lease: str, spec: dict, store: Path) -> None:
    prompt = run_dir / "prompt.txt"
    prompt.write_text(
        "\n".join(
            [
                f"Run id: {run_id}",
                f"Lease: {lease}",
                "Spec: " + json.dumps(canonical_spec(spec)),
                "Tools (exact names): " + ", ".join(_MCP_ALLOW) + ".",
                "Call wingline__observe first. Then call exactly one commit for open_stage.",
                "Repeat until observe says terminal. Pass the lease unchanged.",
                "Do not read files, search for tools, or use a skill. All evidence is in observe.",
                "",
            ]
        )
    )
    grok = shutil.which("grok")
    if not grok:
        return
    cmd = [
        grok,
        "--trust",
        "--system-prompt-override",
        INNER_SYSTEM,
        "--prompt-file",
        str(prompt),
        "--cwd",
        str(_REPO),
        "-m",
        "grok-4.7",
        "--reasoning-effort",
        "xhigh",
        "--max-turns",
        "12",
        "--output-format",
        "json",
        "--no-subagents",
        "--disable-web-search",
        "--permission-mode",
        "dontAsk",
    ]
    for tool in _MCP_ALLOW:
        cmd.extend(["--allow", tool])
    cmd.extend(
        [
            "--deny",
            "Bash",
            "--deny",
            "Read",
            "--deny",
            "Edit",
            "--deny",
            "Write",
            "--deny",
            "Grep",
            "--deny",
            "WebFetch",
            "--deny",
            "WebSearch",
            "--deny",
            "Task",
            "--disallowed-tools",
            "run_terminal_command,write,search_replace,spawn_subagent,web_search,web_fetch,read_file,grep",
        ]
    )
    env = os.environ.copy()
    env["WINGLINE_RUN_ID"] = run_id
    env["WINGLINE_LEASE"] = lease
    env["WINGLINE_STORE"] = str(store)
    log_path = run_dir / "agent.log"
    try:
        with log_path.open("w") as log:
            subprocess.run(
                cmd,
                cwd=_REPO,
                env=env,
                timeout=3600,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    except (OSError, subprocess.TimeoutExpired):
        return


_TERMINAL_SHAPES = {
    "pilot_red",
    "mesh_closed",
    "solve_closed",
    "forces_green",
    "forces_red",
}


def _ledger_terminal(run_dir: Path) -> bool:
    folder = run_dir / "ledger"
    if not folder.is_dir():
        return False
    return shape(load_ledger(folder)) in _TERMINAL_SHAPES


def _mark_inner(run_dir: Path, driver: str, why: str) -> None:
    payload = {"driver": driver}
    if why:
        payload["why"] = why
    (run_dir / "inner.json").write_text(json.dumps(payload, indent=2) + "\n")


# spec_hash is part of the receipt identity the driver does not invent.
_ = spec_hash
