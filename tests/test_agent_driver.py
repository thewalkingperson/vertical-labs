"""The inner loop is the agent, and a failed agent does not become the planner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wingline import engineer, grok_call, outer
from wingline.engineer import Engineer, _MCP_ALLOW
from wingline.kernel import Refusal, load_ledger

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "naca2412-a6"
SPEC = {"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"}


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "run-one"
    run.mkdir()
    (run / "spec.json").write_text(json.dumps(SPEC))
    return run


def test_agent_command_allows_the_five_mcp_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path)
    seen: list[list[str]] = []
    monkeypatch.setattr(
        engineer.shutil,
        "which",
        lambda name: "/usr/bin/grok" if name == "grok" else None,
    )

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.delenv("WINGLINE_MODEL", raising=False)
    monkeypatch.setattr(grok_call, "_dotenv_pairs", lambda path=None: {})
    monkeypatch.setattr(engineer.subprocess, "run", fake_run)
    engineer._drive_agent(run, "run-one", "lease", SPEC, tmp_path)

    assert len(seen) == 1
    cmd = seen[0]
    allowed = [cmd[index + 1] for index, value in enumerate(cmd[:-1]) if value == "--allow"]
    assert set(allowed) == set(_MCP_ALLOW)
    assert cmd[cmd.index("--max-turns") + 1] == "12"
    assert cmd[cmd.index("-m") + 1] == "grok-4.7"
    assert "wingline__observe" in (run / "prompt.txt").read_text()
    assert (run / "agent.log").is_file()


def test_agent_failure_does_not_fall_back_to_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path)
    monkeypatch.setenv("WINGLINE_AGENT", "1")
    monkeypatch.setattr(
        engineer.shutil,
        "which",
        lambda name: "/usr/bin/grok" if name == "grok" else None,
    )
    monkeypatch.setattr(engineer, "_drive_agent", lambda *args, **kwargs: None)

    def planner(*args, **kwargs):
        raise AssertionError("the planner must not run after an agent failure")

    monkeypatch.setattr(engineer, "drive", planner)
    failed = Engineer(tmp_path, None)._drive(run, "run-one", "lease", SPEC)

    assert failed == "agent"
    inner = json.loads((run / "inner.json").read_text())
    assert inner["driver"] == "agent"
    assert "left the ledger open" in inner["why"]


def test_agent_failure_is_a_visible_refusal(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run / "inner.json").write_text(
        json.dumps({"driver": "agent", "why": "agent left the ledger open"})
    )

    refusal = engineer._drive_refusal(run, "agent")

    assert isinstance(refusal, Refusal)
    assert refusal.gate == "agent"
    assert "left the ledger open" in refusal.reason
    assert refusal.panels[0].kind == "goal"


def test_planner_is_selected_explicitly_not_fallen_back_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path)
    monkeypatch.setenv("WINGLINE_AGENT", "0")
    monkeypatch.setattr(engineer, "drive", lambda run_id, lease: None)

    failed = Engineer(tmp_path, None)._drive(run, "run-one", "lease", SPEC)

    assert failed == "tools"
    inner = json.loads((run / "inner.json").read_text())
    assert inner["driver"] == "tools"


def test_review_prompt_carries_the_measurements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipts = load_ledger(FIX / "ledger_green")
    (tmp_path / "outer").mkdir()
    captured: dict[str, str] = {}
    monkeypatch.setenv("WINGLINE_AGENT", "1")
    monkeypatch.setattr(
        outer.shutil,
        "which",
        lambda name: "/usr/bin/grok" if name == "grok" else None,
    )

    def fake_run(cmd, **kwargs):
        prompt = Path(cmd[cmd.index("--prompt-file") + 1]).read_text()
        captured["prompt"] = prompt
        return subprocess.CompletedProcess(
            cmd, 0, '{"verdict":"affirm","gate":"physics","note":"measured"}', ""
        )

    monkeypatch.setattr(outer.subprocess, "run", fake_run)
    verdict = outer._review(tmp_path, receipts)

    assert verdict == "affirm"
    assert '"cl": 0.882458973' in captured["prompt"]
    assert '"cd": 0.0284136587' in captured["prompt"]
    assert "reynolds" in captured["prompt"]
    saved = json.loads((tmp_path / "outer" / "review.json").read_text())
    assert saved["verdict"] == "affirm"
    assert saved["evidence"]["physics"]["model"] == "NeuralFoil"
    assert "neuralfoil" in str(saved["evidence"]["physics"]["source"]).lower()
