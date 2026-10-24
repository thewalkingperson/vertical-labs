"""PreToolUse deny. The server still judges; this hook is defense in depth."""

import json
import sys


def main() -> int:
    sys.stdin.read()
    reason = sys.argv[1] if len(sys.argv) > 1 else "wingline: no shell and no case edits"
    json.dump({"decision": "deny", "reason": reason}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
