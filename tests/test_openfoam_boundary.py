"""The launcher name appears in case_writer.py and nowhere else in Python sources."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", "brief", "runs", ".git", "__pycache__", ".pytest_cache"}
NEEDLE = "open" + "foam"


def test_launcher_name_only_in_case_writer() -> None:
    allowed = ROOT / "wingline" / "case_writer.py"
    assert NEEDLE in allowed.read_text()
    hits: list[str] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP for part in path.parts):
            continue
        if path.resolve() == allowed.resolve():
            continue
        if NEEDLE in path.read_text(errors="ignore"):
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []
