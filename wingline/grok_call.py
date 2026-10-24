"""One headless Grok call. The caller supplies the system prompt."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def grok_available() -> bool:
    return shutil.which("grok") is not None


def run_grok(
    system: str,
    prompt: str,
    *,
    schema: dict | None = None,
    max_turns: int = 1,
    allow_tools: bool = False,
    env: dict | None = None,
    timeout_s: int = 600,
) -> dict:
    """Run grok. With a schema, return structured_output. Otherwise return the raw result object."""
    grok = shutil.which("grok")
    if not grok:
        raise RuntimeError("grok is not on PATH")
    handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    path = Path(handle.name)
    handle.write(prompt)
    handle.close()
    cmd = [
        grok,
        "--trust",
        "--system-prompt-override",
        system,
        "--prompt-file",
        str(path),
        "--cwd",
        str(_REPO),
        "-m",
        "grok-4.7",
        "--reasoning-effort",
        "xhigh",
        "--max-turns",
        str(max_turns),
        "--output-format",
        "json",
        "--no-subagents",
        "--no-plan",
        "--disable-web-search",
        "--permission-mode",
        "dontAsk",
    ]
    if schema is not None:
        cmd.extend(["--json-schema", json.dumps(schema)])
    if not allow_tools:
        cmd.extend(
            [
                "--tools",
                "",
                "--disallowed-tools",
                "run_terminal_command,write,search_replace,spawn_subagent,web_search,web_fetch,read_file,grep",
            ]
        )
    try:
        completed = subprocess.run(
            cmd,
            cwd=_REPO,
            env=env,
            timeout=timeout_s,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        path.unlink(missing_ok=True)
    if completed.returncode != 0 and not completed.stdout.strip():
        detail = (completed.stderr or "").strip()[:500]
        raise RuntimeError(detail or f"grok exited {completed.returncode}")
    return _extract(completed.stdout, schema is not None)


def _extract(stdout: str, structured: bool) -> dict:
    data = _json_object(stdout)
    if data is None:
        raise RuntimeError("grok returned no JSON")
    if not structured:
        return data
    payload = data.get("structured_output")
    if not isinstance(payload, dict):
        payload = data.get("structuredOutput")
    if isinstance(payload, dict):
        return payload
    text = data.get("text") or data.get("result")
    if isinstance(text, dict):
        return text
    if isinstance(text, str):
        inner = _json_object(text)
        if isinstance(inner, dict) and "ready" in inner:
            return inner
    if "ready" in data and "say" in data:
        return data
    raise RuntimeError("grok returned no interview object")


def _json_object(text: str) -> dict | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        return value
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None
