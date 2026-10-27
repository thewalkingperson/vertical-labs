"""One headless Grok call. The caller supplies the system prompt."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_MODEL_DEFAULT = "grok-4.7"


def grok_available() -> bool:
    return shutil.which("grok") is not None


def model_name() -> str:
    """The id passed to grok -m. WINGLINE_MODEL overrides the default."""
    load_local_env()
    name = os.environ.get("WINGLINE_MODEL", "").strip()
    return name or _MODEL_DEFAULT


def load_local_env(target: dict | None = None, path: Path | None = None) -> None:
    """Fill blank variables from the repo .env. A variable already set wins."""
    dest = os.environ if target is None else target
    for key, value in _dotenv_pairs(path).items():
        if not dest.get(key):
            dest[key] = value


def _dotenv_pairs(path: Path | None = None) -> dict[str, str]:
    file = _REPO / ".env" if path is None else path
    if not file.is_file():
        return {}
    pairs: dict[str, str] = {}
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key[0].isdigit() or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # A blank example line must not become an empty bearer.
        if value == "":
            continue
        pairs[key] = value
    return pairs


def run_grok(
    system: str,
    prompt: str,
    *,
    schema: dict | None = None,
    max_turns: int = 1,
    allow_tools: bool = False,
    env: dict | None = None,
    timeout_s: int = 600,
    cwd: Path | None = None,
) -> dict:
    """Run grok. With a schema, return structured_output. Otherwise return the raw result object."""
    grok = shutil.which("grok")
    if not grok:
        raise RuntimeError("grok is not on PATH")
    work = Path(cwd) if cwd is not None else _REPO
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
        str(work),
        "-m",
        model_name(),
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
    load_local_env()
    if env is not None:
        load_local_env(env)
    try:
        completed = subprocess.run(
            cmd,
            cwd=work,
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
    found = _find_interview(data)
    if found is None:
        raise RuntimeError("grok returned no interview object")
    return found


def _find_interview(data: dict) -> dict | None:
    """Pull an interview object out of a headless JSON result.

    A one-turn call often leaves structuredOutput null and parks the object in
    text or thought, then exits because max turns was reached.
    """
    for key in ("structured_output", "structuredOutput"):
        found = _coerce_interview(data.get(key))
        if found is not None:
            return found
    for key in ("text", "result", "thought", "structuredOutputError", "structured_output_error"):
        found = _coerce_interview(data.get(key))
        if found is not None:
            return found
    if "ready" in data and "say" in data:
        return data
    return None


def _coerce_interview(value: object, depth: int = 0) -> dict | None:
    if depth > 3:
        return None
    if isinstance(value, dict):
        if _is_interview(value):
            return value
        if "structuredOutput" in value or "structured_output" in value or "text" in value:
            return None
        for item in value.values():
            found = _coerce_interview(item, depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(value, str):
        return _embedded_interview(value)
    return None


def _is_interview(value: dict) -> bool:
    if "structuredOutput" in value or "structured_output" in value:
        return False
    return "ready" in value or "say" in value


def _embedded_interview(text: str) -> dict | None:
    direct = _json_object(text)
    if isinstance(direct, dict) and _is_interview(direct):
        return direct
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            fenced = json.loads(fence.group(1))
        except json.JSONDecodeError:
            fenced = None
        if isinstance(fenced, dict) and _is_interview(fenced):
            return fenced
    start = 0
    while True:
        brace = text.find("{", start)
        if brace < 0:
            return None
        value = _balanced_json(text, brace)
        if isinstance(value, dict) and _is_interview(value):
            return value
        start = brace + 1


def _balanced_json(text: str, start: int) -> dict | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


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
