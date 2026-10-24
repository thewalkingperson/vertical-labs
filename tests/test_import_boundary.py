"""Only mcp_server.py may import the case writer. That file may be absent."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", "brief", "runs", ".git", "__pycache__", ".pytest_cache", "tests"}
NAME = "case_" + "writer"
PATTERN = re.compile(
    rf"(?:import\s+{NAME}\b"
    rf"|from\s+{NAME}\b"
    rf"|from\s+wingline\.{NAME}\b"
    rf"|import\s+wingline\.{NAME}\b"
    rf"|from\s+wingline\s+import\s+[^\n#]*\b{NAME}\b)"
)


def test_only_mcp_server_imports_case_writer() -> None:
    hits: list[str] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP for part in path.parts):
            continue
        if path.name in {"mcp_server.py", f"{NAME}.py"}:
            continue
        if PATTERN.search(path.read_text(errors="ignore")):
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []
