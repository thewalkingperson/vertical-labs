"""Deny a tool name written to harness/deny-tools.txt."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIST = ROOT / "harness" / "deny-tools.txt"


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}
    if not LIST.is_file():
        return 0
    names = {line.strip() for line in LIST.read_text().splitlines() if line.strip()}
    tool = str(event.get("toolName") or "")
    short = tool.split("__")[-1]
    if tool in names or short in names:
        json.dump(
            {"decision": "deny", "reason": "wingline: tool is not one of the five"},
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
