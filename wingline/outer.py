"""Outer gates. Reads the ledger and the case. Does not read an agent transcript."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from wingline.kernel import (
    Bounds,
    Panel,
    Refusal,
    judge_forces,
    judge_mesh,
    judge_pilot,
    judge_solve,
    judge_thin,
    load_ledger,
    render_lean,
    render_shacl,
    _scope_ok,
)

_REPO = Path(__file__).resolve().parents[1]
_PYSHACL = _REPO / ".venv" / "bin" / "pyshacl"
_LEAN = Path("/Users/saarthakbhatia/.elan/bin/lean")
_TERMINAL_REVIEW = ("affirm", "challenge")


def _goal(scope: dict | None) -> str:
    if not scope:
        return ""
    return (
        f"NACA {scope.get('naca')} at {scope.get('alpha_deg')} degrees, "
        f"{scope.get('u_ms')} m/s, {scope.get('density')}."
    )


def _find(receipts: list[dict], stage: str, status: str | None = None) -> dict | None:
    found = None
    for receipt in receipts:
        if receipt.get("stage") != stage:
            continue
        if status is not None and receipt.get("status") != status:
            continue
        found = receipt
    return found


def _refusal(gate: str, log: str, reason: str, receipts: list[dict]) -> Refusal:
    scope = _find(receipts, "scope")
    return Refusal(
        gate=gate,
        log=log,
        reason=reason,
        panels=(Panel("goal", _goal(scope)), Panel("refused", gate)),
    )


def _policy_differs() -> bool:
    shape = _REPO / "harness" / "shapes" / "receipt.shacl.ttl"
    lean = _REPO / "harness" / "gate" / "Gate.lean"
    if not shape.is_file() or not lean.is_file():
        return True
    return shape.read_text() != render_shacl() or lean.read_text() != render_lean()


def _graph(receipts: list[dict]) -> dict:
    nodes = []
    for receipt in receipts:
        node: dict = {
            "@id": f"https://vertical-labs.local/run/{receipt.get('id')}",
            "@type": receipt.get("type"),
        }
        for key, value in receipt.items():
            if value is None:
                continue
            node[key] = value
        nodes.append(node)
    return {
        "@context": {"@vocab": "https://vertical-labs.local/wingline#"},
        "@graph": nodes,
    }


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _log_text(run_dir: Path, receipt: dict) -> str:
    name = str(receipt.get("log") or "")
    for candidate in (run_dir / "case" / name, run_dir / name):
        if candidate.is_file():
            return candidate.read_text(errors="replace")
    return ""


def _judge_status(run_dir: Path, receipt: dict) -> str | None:
    """Return green or red from the same judges. None if the row is a stop."""
    status = receipt.get("status")
    if status == "stopped":
        why = receipt.get("why")
        if why in ("repeated_mesh", "budget", "fatal", "wall_clock"):
            return None
        return "red"
    stage = receipt.get("stage")
    evidence = dict(receipt)
    if stage == "pilot":
        ok = judge_pilot(evidence)
    elif stage == "scope":
        ok = _scope_ok(evidence)
    elif stage == "mesh":
        text = _log_text(run_dir, receipt)
        if text:
            evidence["log_text"] = text
            evidence["mesh_ok"] = "Mesh OK" in text
        elif "mesh_ok" not in evidence:
            return "red" if status == "green" else "red"
        ok = judge_mesh(evidence)
    elif stage == "solve":
        text = _log_text(run_dir, receipt)
        if text:
            evidence["log_text"] = text
        elif status == "green":
            return "red"
        ok = judge_solve(evidence)
    elif stage == "forces":
        ok = judge_forces(evidence)
    else:
        return "red"
    return "green" if ok else "red"


def _review(run_dir: Path, receipts: list[dict]) -> str:
    if os.environ.get("WINGLINE_AGENT") != "1":
        return "challenge"
    grok = shutil.which("grok")
    if not grok:
        return "challenge"
    scope = _find(receipts, "scope") or {}
    prompt = run_dir / "outer" / "review-prompt.txt"
    prompt.write_text(
        "\n".join(
            [
                "Reply with one JSON object only:",
                '{"verdict":"affirm"|"challenge","gate":"...","note":"..."}',
                f"Run id: {receipts[-1].get('run_id') if receipts else ''}",
                "Spec: "
                + json.dumps(
                    {
                        "naca": scope.get("naca"),
                        "alpha_deg": scope.get("alpha_deg"),
                        "u_ms": scope.get("u_ms"),
                        "density": scope.get("density"),
                    }
                ),
                "Use the review-case skill. You cannot pass a run and you cannot edit a gate.",
                "",
            ]
        )
    )
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
    try:
        proc = subprocess.run(
            cmd,
            cwd=_REPO,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "challenge"
    match = re.search(r'"verdict"\s*:\s*"(affirm|challenge)"', proc.stdout or "")
    if match is None:
        return "challenge"
    verdict = match.group(1)
    return verdict if verdict in _TERMINAL_REVIEW else "challenge"


def _write_verdict(run_dir: Path, payload: dict) -> None:
    outer = run_dir / "outer"
    outer.mkdir(parents=True, exist_ok=True)
    (outer / "verdict.json").write_text(json.dumps(payload, indent=2) + "\n")


def evaluate(
    run_dir: Path,
    *,
    pyshacl: Path | None = None,
    lean: Path | None = None,
) -> Refusal | None:
    """First failure wins. None means the caller should project the verdict."""
    run_dir = Path(run_dir)
    receipts = load_ledger(run_dir / "ledger") if (run_dir / "ledger").is_dir() else []
    if _policy_differs():
        return _refusal("bounds", "harness/shapes/receipt.shacl.ttl", "emitted policy differs", receipts)

    outer = run_dir / "outer"
    outer.mkdir(parents=True, exist_ok=True)
    graph_path = outer / "graph.jsonld"
    graph_path.write_text(json.dumps(_graph(receipts)))

    shacl_bin = Path(pyshacl) if pyshacl is not None else _PYSHACL
    shape = _REPO / "harness" / "shapes" / "receipt.shacl.ttl"
    if not shacl_bin.is_file():
        refusal = _refusal("shacl", str(shacl_bin), "pyshacl missing", receipts)
        _write_verdict(run_dir, {"gate": "shacl", "thin": "red", "review": "challenge"})
        return refusal
    try:
        shacl_proc = _run(
            [str(shacl_bin), "-s", str(shape), "-df", "json-ld", str(graph_path)],
            120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        refusal = _refusal("shacl", str(graph_path), str(exc), receipts)
        _write_verdict(run_dir, {"gate": "shacl", "thin": "red", "review": "challenge"})
        return refusal
    if shacl_proc.returncode != 0:
        reason = (shacl_proc.stderr or shacl_proc.stdout or "pyshacl failed").strip()
        refusal = _refusal("shacl", str(graph_path), reason[-500:], receipts)
        _write_verdict(run_dir, {"gate": "shacl", "thin": "red", "review": "challenge"})
        return refusal

    lean_bin = Path(lean) if lean is not None else _LEAN
    lean_file = _REPO / "harness" / "gate" / "Gate.lean"
    if not lean_bin.is_file():
        refusal = _refusal("lean", str(lean_bin), "lean missing", receipts)
        _write_verdict(run_dir, {"gate": "lean", "thin": "red", "review": "challenge"})
        return refusal
    try:
        lean_proc = _run([str(lean_bin), str(lean_file)], 180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        refusal = _refusal("lean", str(lean_file), str(exc), receipts)
        _write_verdict(run_dir, {"gate": "lean", "thin": "red", "review": "challenge"})
        return refusal
    if lean_proc.returncode != 0:
        reason = (lean_proc.stderr or lean_proc.stdout or "lean failed").strip()
        refusal = _refusal("lean", str(lean_file), reason[-500:], receipts)
        _write_verdict(run_dir, {"gate": "lean", "thin": "red", "review": "challenge"})
        return refusal

    for receipt in receipts:
        judged = _judge_status(run_dir, receipt)
        if judged is None:
            continue
        if judged != receipt.get("status"):
            gate = str(receipt.get("stage") or "incomplete")
            refusal = _refusal(gate, str(receipt.get("log") or ""), "rejudge disagreed", receipts)
            _write_verdict(run_dir, {"gate": gate, "thin": "red", "review": "challenge"})
            return refusal

    bad = next((item for item in receipts if item.get("status") in ("red", "stopped")), None)
    if bad is not None:
        _write_verdict(
            run_dir,
            {"gate": bad.get("stage"), "thin": "red", "review": "challenge"},
        )
        return None

    scope = _find(receipts, "scope", "green")
    forces = _find(receipts, "forces", "green")
    thin = "red"
    if scope is not None and forces is not None:
        try:
            if judge_thin(float(forces["cl"]), scope["naca"], float(scope["alpha_deg"])):
                thin = "green"
        except (KeyError, TypeError, ValueError):
            thin = "red"
    if thin != "green":
        _write_verdict(run_dir, {"thin": "red", "review": "challenge"})
        return None
    review = _review(run_dir, receipts)
    if review not in _TERMINAL_REVIEW:
        review = "challenge"
    _write_verdict(run_dir, {"thin": thin, "review": review})
    return None


# Bounds is referenced so a policy edit cannot drift unnoticed in this module's import.
_ = Bounds.screening_sentence
