"""propose does not mint a grade."""

from pathlib import Path

from wingline.meta import propose

ROOT = Path(__file__).resolve().parents[1]


def test_propose_has_no_grade(tmp_path: Path) -> None:
    result = propose(tmp_path)
    assert not hasattr(result, "grade")
    assert type(result).__name__ in {"Noop", "Shipped", "NeedsHuman"}
    source = (ROOT / "wingline" / "meta.py").read_text()
    assert "DesignGrade" not in source
