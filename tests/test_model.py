"""The model id and the API key come from the environment. Neither is a secret in the repo."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from wingline import engineer, grok_call, outer
from wingline.grok_call import load_local_env, model_name
from wingline.kernel import load_ledger

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "naca2412-a6"
SPEC = {"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"}


def test_model_defaults_to_grok_4_7(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WINGLINE_MODEL", raising=False)
    monkeypatch.setattr(grok_call, "_dotenv_pairs", lambda path=None: {})

    assert model_name() == "grok-4.7"


def test_model_reads_wingline_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGLINE_MODEL", " grok-4.5 ")
    monkeypatch.setattr(grok_call, "_dotenv_pairs", lambda path=None: {})

    assert model_name() == "grok-4.5"


def test_env_file_fills_a_blank_key_and_keeps_an_exported_one(tmp_path: Path) -> None:
    envfile = tmp_path / ".env"
    envfile.write_text(
        "\n".join(
            [
                "# comment",
                "export XAI_API_KEY=\"from-file\"",
                "OPENAI_API_KEY=",
                "WINGLINE_MODEL=gpt-4o",
                "not a pair",
                "",
            ]
        )
    )
    dest = {"OPENAI_API_KEY": "kept", "XAI_API_KEY": ""}

    load_local_env(dest, envfile)

    assert dest["XAI_API_KEY"] == "from-file"
    assert dest["OPENAI_API_KEY"] == "kept"
    assert dest["WINGLINE_MODEL"] == "gpt-4o"
    assert "not a pair" not in dest


def test_seats_pass_the_selected_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WINGLINE_MODEL", "gpt-4o")
    monkeypatch.setattr(grok_call, "_dotenv_pairs", lambda path=None: {})
    monkeypatch.setattr(
        engineer.shutil, "which", lambda name: "/usr/bin/grok" if name == "grok" else None
    )
    monkeypatch.setattr(
        outer.shutil, "which", lambda name: "/usr/bin/grok" if name == "grok" else None
    )
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd, 0, '{"verdict":"challenge","gate":"physics","note":"no"}', ""
        )

    monkeypatch.setattr(engineer.subprocess, "run", fake_run)
    monkeypatch.setattr(outer.subprocess, "run", fake_run)
    run = tmp_path / "run-one"
    run.mkdir()
    (run / "spec.json").write_text("{}")
    engineer._drive_agent(run, "run-one", "lease", SPEC, tmp_path)
    (tmp_path / "outer").mkdir()
    outer._review(tmp_path, load_ledger(FIX / "ledger_green"))

    assert len(seen) == 2
    for cmd in seen:
        assert cmd[cmd.index("-m") + 1] == "gpt-4o"


def test_run_grok_puts_a_file_key_on_the_child_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    envfile = tmp_path / ".env"
    envfile.write_text('XAI_API_KEY="from-file"\n')
    real = grok_call._dotenv_pairs
    monkeypatch.setattr(
        grok_call, "_dotenv_pairs", lambda path=None: real(envfile if path is None else path)
    )
    monkeypatch.delenv("WINGLINE_MODEL", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(grok_call.shutil, "which", lambda name: "/usr/bin/grok")
    held: dict = {}

    def fake_run(cmd, **kwargs):
        held["cmd"] = list(cmd)
        held["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, '{"text":"ok"}', "")

    monkeypatch.setattr(grok_call.subprocess, "run", fake_run)
    child = {"OPENAI_API_KEY": "kept"}

    grok_call.run_grok("system", "prompt", env=child)

    assert held["cmd"][held["cmd"].index("-m") + 1] == "grok-4.7"
    assert child["XAI_API_KEY"] == "from-file"
    assert child["OPENAI_API_KEY"] == "kept"
    assert held["env"]["XAI_API_KEY"] == "from-file"
    os.environ.pop("XAI_API_KEY", None)


def test_example_env_is_empty_and_ignored() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "XAI_API_KEY=" in example
    assert "OPENAI_API_KEY=" in example
    for line in example.splitlines():
        if not line or line.startswith("#"):
            continue
        _key, sep, value = line.partition("=")
        assert sep == "="
        assert value.strip() == ""
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=ROOT, check=False
    )
    example_ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"], cwd=ROOT, check=False
    )
    assert ignored.returncode == 0
    assert example_ignored.returncode != 0
