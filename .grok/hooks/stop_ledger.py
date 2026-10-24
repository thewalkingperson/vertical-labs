"""Block Stop until the inner ledger has no successor. Print nothing if stopHookActive."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from wingline.kernel import NEXT, load_ledger, shape


def _terminal(name: str) -> bool:
    edges = NEXT.get(name)
    return name != "empty" and not edges


def _ledger() -> Path | None:
    store_env = os.environ.get("WINGLINE_STORE")
    if store_env:
        store = Path(store_env)
    else:
        root = os.environ.get("GROK_WORKSPACE_ROOT") or os.getcwd()
        store = Path(root) / "runs"
    run_id = os.environ.get("WINGLINE_RUN_ID")
    if run_id:
        return store / run_id / "ledger"
    if not store.is_dir():
        return None
    found: list[tuple[float, Path]] = []
    for child in store.iterdir():
        index = child / "ledger" / "index.jsonl"
        if index.is_file():
            found.append((index.stat().st_mtime, child / "ledger"))
    if not found:
        return None
    found.sort()
    return found[-1][1]


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}
    if event.get("stopHookActive") in (True, "true", 1):
        return 0
    ledger = _ledger()
    current = "empty"
    if ledger is not None and ledger.is_dir():
        current = shape(load_ledger(ledger))
    if not _terminal(current):
        json.dump(
            {"decision": "block", "reason": "inner loop has no terminal receipt"},
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
