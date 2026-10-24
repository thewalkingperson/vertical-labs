"""Ask, refine, and the store lock. The driver is the planner unless WINGLINE_AGENT=1."""

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
    parse_naca,
    project,
)
from wingline.mcp_server import canonical_spec, perform_pilot, spec_hash
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
_ENV = ("WINGLINE_STORE", "WINGLINE_RUN_ID", "WINGLINE_LEASE")


@dataclass(frozen=True, slots=True)
class EngineerView:
    run_id: str
    outcome: Screening | DesignGrade | Refusal


def _parse_refusal(reason: str) -> Refusal:
    return Refusal(
        gate="parse",
        log="",
        reason=reason,
        panels=(Panel("goal", ""), Panel("refused", "parse")),
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


def parse(text: str, store: Path) -> dict | Refusal:
    """NACA sentence, or Same wing. Anything else is a parse refusal."""
    raw = " ".join(text.split())
    naca_match = _NACA.match(raw)
    same_match = _SAME.match(raw)
    if naca_match is None and same_match is None:
        return _parse_refusal("unrecognized request")
    if same_match is not None:
        last = Path(store) / "last_spec.json"
        if not last.is_file():
            return _parse_refusal("no previous spec")
        try:
            previous = json.loads(last.read_text())
            foil = parse_naca(previous["naca"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return _parse_refusal("no previous spec")
        alpha = _number(same_match.group("alpha"))
        speed = float(previous["u_ms"])
    else:
        assert naca_match is not None
        try:
            foil = parse_naca(naca_match.group("naca"))
        except ValueError as exc:
            return _parse_refusal(str(exc))
        alpha = _number(naca_match.group("alpha"))
        speed_text = naca_match.group("speed")
        speed = float(speed_text) if speed_text is not None else float(Bounds.u_default)
    if not (Bounds.alpha_min <= alpha <= Bounds.alpha_max):
        return _parse_refusal("alpha outside bounds")
    if not (Bounds.u_min <= speed <= Bounds.u_max):
        return _parse_refusal("speed outside bounds")
    if not (Bounds.t_min <= foil.t <= Bounds.t_max):
        return _parse_refusal("thickness outside bounds")
    return canonical_spec(
        {
            "naca": foil.code(),
            "alpha_deg": _whole(alpha),
            "u_ms": _whole(speed),
            "density": "coarse",
        }
    )


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

    def ask(self, text: str, show=None) -> EngineerView:
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
        (run_dir / "spec.json").write_text(json.dumps(parsed, indent=2) + "\n")
        lease = _write_lease(run_dir / "lease")
        with self._env(run_id, lease):
            pilot = perform_pilot(self.store)
            if not pilot.get("green"):
                outcome = self._finish(run_dir, pilot)
                _show(show, outcome)
                return EngineerView(run_id, outcome)
            self._drive(run_dir, run_id, lease, parsed)
            outcome = self._finish(run_dir, None)
        _show(show, outcome)
        return EngineerView(str(getattr(outcome, "run_id", run_id)), outcome)

    def refine(self, run_id: str, show=None) -> EngineerView:
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
        lease = _write_lease(child_dir / "lease")
        with self._env(child_id, lease):
            pilot = perform_pilot(self.store)
            if not pilot.get("green"):
                outcome = self._finish(child_dir, pilot)
                _show(show, outcome)
                return EngineerView(child_id, outcome)
            self._drive(child_dir, child_id, lease, spec)
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

    def _drive(self, run_dir: Path, run_id: str, lease: str, spec: dict) -> None:
        if os.environ.get("WINGLINE_AGENT") == "1":
            _drive_agent(run_dir, run_id, lease, spec, self.store)
            return
        drive(run_id, lease)

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
                "Use the screen-wing skill. Pass the lease unchanged.",
                "",
            ]
        )
    )
    grok = shutil.which("grok")
    if not grok:
        return
    cmd = [
        grok,
        "--prompt-file",
        str(prompt),
        "--cwd",
        str(_REPO),
        "-m",
        "grok-4.7",
        "--reasoning-effort",
        "xhigh",
        "--max-turns",
        "8",
        "--output-format",
        "json",
        "--no-subagents",
        "--disable-web-search",
        "--permission-mode",
        "dontAsk",
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
    env = os.environ.copy()
    env["WINGLINE_RUN_ID"] = run_id
    env["WINGLINE_LEASE"] = lease
    env["WINGLINE_STORE"] = str(store)
    try:
        subprocess.run(cmd, cwd=_REPO, env=env, timeout=3600, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return


# spec_hash is part of the receipt identity the driver does not invent.
_ = spec_hash
