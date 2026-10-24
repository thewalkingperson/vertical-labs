"""Meta interview and the outer neighborhood search. No OpenFOAM."""

from pathlib import Path

from wingline.kernel import Refusal
from wingline.meta import Study, resolve
from wingline.outer import better, probes

ROOT = Path(__file__).resolve().parents[1]


def _boom(_prompt: str) -> str:
    raise AssertionError("the request was already complete")


def test_classic_sentence_does_not_ask(tmp_path: Path) -> None:
    study = resolve(
        "NACA 2412 at 6 degrees. Is this worth a finer run?",
        tmp_path,
        _boom,
    )
    assert isinstance(study, Study)
    assert study.spec["naca"] == "2412"
    assert study.spec["alpha_deg"] == 6
    assert study.spec["u_ms"] == 10
    assert study.objective == "max_ld"
    assert study.alpha_fixed is True
    assert study.notes == ()


def _ask(say: str, **fields: object) -> dict:
    payload = {
        "say": say,
        "ready": False,
        "naca": "",
        "alpha_deg": 0,
        "u_ms": 0,
        "objective": "max_ld",
    }
    payload.update(fields)
    return payload


def test_low_drag_asks_in_the_models_words(tmp_path: Path) -> None:
    question = "Low drag at 10 degrees still needs a section. Which 4-digit should I open with?"

    def model(prompt: str) -> dict:
        if "(none)" in prompt:
            return _ask(question, alpha_deg=10, u_ms=10, objective="min_cd")
        return _ask(
            "I'll open NACA 2412 at 10 degrees and 10 m/s, then compare nearby sections on drag.",
            ready=True,
            naca="2412",
            alpha_deg=10,
            u_ms=10,
            objective="min_cd",
        )

    def reply(prompt: str) -> str:
        assert prompt == question
        return "Use 2412."

    study = resolve("a wing with low drag at 10 degrees", tmp_path, reply, model)
    assert isinstance(study, Study)
    assert study.spec == {
        "naca": "2412",
        "alpha_deg": 10,
        "u_ms": 10,
        "density": "coarse",
    }
    assert study.objective == "min_cd"
    assert study.notes[0].startswith("I'll open NACA 2412")
    dialogue = (tmp_path / "meta" / "dialogue.jsonl").read_text()
    assert question in dialogue
    assert "press enter" not in dialogue.lower()


def test_bare_naca_asks_for_the_angle(tmp_path: Path) -> None:
    def model(prompt: str) -> dict:
        if "(none)" in prompt:
            return _ask("What angle should this 2412 meet the air at?")
        return _ask(
            "2412 at 6 degrees and 10 m/s.",
            ready=True,
            naca="2412",
            alpha_deg=6,
            u_ms=10,
            objective="max_ld",
        )

    def reply(prompt: str) -> str:
        assert "angle" in prompt.lower()
        return "6"

    study = resolve("NACA 2412", tmp_path, reply, model)
    assert isinstance(study, Study)
    assert study.spec["naca"] == "2412"
    assert study.spec["alpha_deg"] == 6


def test_a_rejected_spec_goes_back_to_the_model(tmp_path: Path) -> None:
    seen: list[str] = []

    def model(prompt: str) -> dict:
        seen.append(prompt)
        if "The gate rejected" in prompt:
            return _ask(
                "2412 at 6 degrees is inside the setup.",
                ready=True,
                naca="2412",
                alpha_deg=6,
                u_ms=10,
            )
        return _ask("Trying a section.", ready=True, naca="9999", alpha_deg=6, u_ms=10)

    study = resolve("a wing at 6 degrees", tmp_path, _boom, model)
    assert isinstance(study, Study)
    assert study.spec["naca"] == "2412"
    assert len(seen) == 2


def test_stated_angle_outside_bounds_does_not_ask(tmp_path: Path) -> None:
    refused = resolve("a wing with low drag at 40 degrees", tmp_path, _boom)
    assert isinstance(refused, Refusal)
    assert refused.gate == "parse"


def test_neighbors_keep_a_named_angle() -> None:
    spec = {"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"}
    found = probes(spec, alpha_fixed=True, budget=4)
    assert [item["naca"] for item in found] == ["2410", "2414", "3412", "1412"]
    assert all(item["alpha_deg"] == 6 for item in found)


def test_lower_drag_wins_the_step() -> None:
    direct = {"naca": "2412", "cl": 0.23, "cd": 0.14, "gate": "thin_airfoil"}
    rows = [
        {"naca": "2410", "cl": 0.22, "cd": 0.10, "gate": "thin_airfoil"},
        {"naca": "3412", "cl": 0.40, "cd": 0.20, "gate": "thin_airfoil"},
    ]
    chosen = better(direct, rows, "min_cd")
    assert chosen is not None
    assert chosen["naca"] == "2410"


def test_meta_source_has_no_grade_type() -> None:
    source = (ROOT / "wingline" / "meta.py").read_text()
    assert "DesignGrade" not in source
    assert "Which NACA 4-digit should I start from?" not in source
    assert "press enter to start at 2412" not in source


def test_inner_session_overrides_the_system_prompt() -> None:
    source = (ROOT / "wingline" / "engineer.py").read_text()
    assert "--system-prompt-override" in source
    assert "INNER_SYSTEM" in source
    prompt = (ROOT / "wingline" / "prompts.py").read_text()
    assert "You are the inner loop." in prompt
    assert "You are the meta loop." in prompt
