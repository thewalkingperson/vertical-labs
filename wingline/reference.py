"""NeuralFoil reference polar for the high-Reynolds wing case.

This module is deliberately small and has no Wingline dependencies. The
expensive imports happen inside reference_polar so the rest of the harness can
load when the review-only dependency is absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math


class ReferenceError(RuntimeError):
    """The external reference is unavailable or outside its usable range."""


@dataclass(frozen=True, slots=True)
class Reference:
    naca: str
    alpha_deg: float
    reynolds: float
    cl: float
    cd: float
    cm: float
    confidence: float
    model: str = "NeuralFoil"
    source: str = "neuralfoil 0.3.3 / aerosandbox 4.2.10"


def _scalar(value: object) -> float:
    import numpy as np

    arr = np.asarray(value).reshape(-1)
    if arr.size != 1:
        raise ReferenceError("NeuralFoil returned a non-scalar")
    result = float(arr[0])
    if not math.isfinite(result):
        raise ReferenceError("NeuralFoil returned a non-finite value")
    return result


@lru_cache(maxsize=512)
def reference_polar(naca: str, alpha_deg: float, reynolds: float) -> Reference:
    """Return one external polar point.

    The caller owns the Reynolds-range policy. This function only rejects
    malformed codes or numbers and reports an unavailable dependency.
    """
    code = str(naca)
    if len(code) != 4 or not code.isdigit():
        raise ReferenceError(f"not a NACA 4-digit code: {naca!r}")
    if not math.isfinite(alpha_deg):
        raise ReferenceError("alpha is not finite")
    if not math.isfinite(reynolds) or reynolds <= 0:
        raise ReferenceError("Reynolds number is not positive")

    try:
        import neuralfoil as nf
        from aerosandbox import Airfoil
    except ImportError as exc:
        raise ReferenceError(
            "NeuralFoil is not installed; install neuralfoil==0.3.3"
        ) from exc

    try:
        aero = nf.get_aero_from_airfoil(
            airfoil=Airfoil(f"naca{code}"),
            alpha=float(alpha_deg),
            Re=float(reynolds),
        )
    except Exception as exc:
        raise ReferenceError(f"NeuralFoil analysis failed: {exc}") from exc

    return Reference(
        naca=code,
        alpha_deg=float(alpha_deg),
        reynolds=float(reynolds),
        cl=_scalar(aero["CL"]),
        cd=_scalar(aero["CD"]),
        cm=_scalar(aero["CM"]),
        confidence=_scalar(aero["analysis_confidence"]),
    )
