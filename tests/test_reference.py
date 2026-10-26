"""NeuralFoil is the only lift reference, and a missing install fails closed."""

import pytest

from wingline.kernel import Bounds, judge_physics, physics_check
from wingline.reference import Reference, ReferenceError, reference_polar


def test_neuralfoil_reference_at_the_target() -> None:
    pytest.importorskip("neuralfoil")
    ref = reference_polar("2412", 6.0, 666666.6666666666)
    assert ref.model == "NeuralFoil"
    assert ref.cl == pytest.approx(0.8989, abs=0.005)
    assert ref.cd == pytest.approx(0.01002, abs=0.001)
    assert ref.cm == pytest.approx(-0.04877, abs=0.002)
    assert ref.confidence > 0.95


def test_one_physics_model_cannot_accept_both_results() -> None:
    pytest.importorskip("neuralfoil")
    assert judge_physics(0.2313, "2412", 6, 10, 1.0) is False
    assert judge_physics(0.856, "2412", 6, 10, 1.0) is True
    assert judge_physics(0.882459, "2412", 6, 10, 1.0) is True
    assert judge_physics(0.855949564, "2412", 6, 10, 1.0) is True
    assert judge_physics(0.0, "2412", 6, 10, 1.0) is False
    assert judge_physics(2.0, "2412", 6, 10, 1.0) is False
    assert judge_physics(0.10, "0012", 0, 10, 1.0) is False
    assert judge_physics(0.78, "2412", 6, 10, 1.0) is False


def test_low_reynolds_is_refused_before_any_band() -> None:
    pytest.importorskip("neuralfoil")
    low_re_chord = Bounds.nu * 5000.0 / 10.0
    assert judge_physics(0.2313, "2412", 6, 10, low_re_chord) is False


def test_missing_reference_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import wingline.kernel as kernel

    def fail(*_args, **_kwargs):
        raise ReferenceError("missing")

    monkeypatch.setattr(kernel, "reference_polar", fail)
    assert judge_physics(0.86, "2412", 6, 10, 1.0) is False


def test_low_confidence_reference_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import wingline.kernel as kernel

    def low(*_args, **_kwargs):
        return Reference(
            naca="2412",
            alpha_deg=6.0,
            reynolds=666666.6666666666,
            cl=0.89888834,
            cd=0.01002446,
            cm=-0.04877341,
            confidence=0.5,
        )

    monkeypatch.setattr(kernel, "reference_polar", low)
    assert judge_physics(0.882459, "2412", 6, 10, 1.0) is False


def test_forced_transition_polar_is_context() -> None:
    pytest.importorskip("neuralfoil")
    forced = reference_polar("2412", 6.0, 666666.6666666666, 0.0)
    assert forced.cl == pytest.approx(0.86499, abs=0.005)
    assert forced.cd == pytest.approx(0.01392, abs=0.001)
    assert forced.confidence < Bounds.reference_min_confidence
    ok, evidence, _reason = physics_check(0.882459, "2412", 6, 10, 1.0)
    assert ok is True
    assert evidence["reference_cd"] == pytest.approx(0.01002, abs=0.001)
    assert evidence["forced_cd"] == pytest.approx(0.01392, abs=0.001)
    assert evidence["forced_confidence"] < Bounds.reference_min_confidence
