"""Replace the model-facing tool copy with the receipt JSON. Does not decide green."""

import json
import sys


def _parse(value):
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return text
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and "text" in item:
                return _parse(item["text"])
        return value
    if isinstance(value, dict):
        receipt = value.get("receipt")
        if isinstance(receipt, dict) and "stage" in receipt:
            return receipt
        for key in ("structuredContent", "content", "result", "output"):
            if key in value:
                parsed = _parse(value[key])
                if parsed is not None:
                    return parsed
        return value
    return value


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}
    result = None
    for key in ("toolResult", "tool_result", "result", "output", "toolOutput"):
        if key in event:
            result = _parse(event[key])
            break
    if result is None:
        result = {}
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedMCPToolOutput": result,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
