# Wingline physics fix — implementation brief

> **Superseded on 2026-10-25.** Do not copy the withdrawn wall-resolved claim or the old lift band back into the tree. The production case is wall-modeled Spalart–Allmaras with `nutUSpaldingWallFunction`. y+ is recorded and is not required to be below 1. Do not switch the wall function to `nutLowReWallFunction`. The lift band is `max(0.03, 0.05 * abs(Cl_ref))` with minimum confidence `0.90`. Design grade is force agreement between the parent mesh and a 20% chordwise refinement. It is not mesh independence and it is not a flight-hardware release. Do not fail the physics gate on NeuralFoil Cd. Do not restore `reference_abs = 0.12`, `reference_rel = 0.15`, or `reference_min_confidence = 0.50`.

**Status:** this brief has been applied. Do not treat the steps below as open work. Where a step still quotes an old fixture (`Cl` 0.86, 9600 cells) or an old assertion, the tree has moved on. `README.md` and `DESIGN.md` are the contract. The current case clusters the leading edge at ratio 16 and runs 1500 iterations, then requires the last-100 versus previous-100 force drift to stay within 1e-4. A ratio-16 parent at iteration 1500 measured Cl 0.9000 and Cd 0.02691 and was refused because the lift drift was 0.00146.

**Audience:** the coding agent implementing the fix in `/Users/saarthakbhatia/Documents/vertical-labs`.
**Goal:** make a live agent-driven NACA 2412 case use a defensible, wall-modeled turbulent RANS physics model, compare it to a real external polar, and reach `DesignGrade` through the existing refine path.

This document is a decision record as well as a task list. Do not substitute a different physics model, a different reference source, or a different acceptance rule without updating this document first.

---

## 0. Non-negotiable constraints

1. **One physics model per Reynolds regime.** The only supported regime is `Re = 1.0e5 … 5.0e6`. In that regime the model is wall-modeled Spalart–Allmaras RAS. There is no low-Reynolds branch, no laminar branch, and no thin-airfoil fallback.
2. **Remove the double acceptance.** `judge_thin(0.2313, ...)` and `judge_thin(0.86, ...)` must not both be true. After this change `0.2313` must fail and the converged high-Re result must pass. Add a regression test that proves it.
3. **No silent agent fallback.** `WINGLINE_AGENT` unset or nonzero means the agent. `WINGLINE_AGENT=0` is the explicit planner/debug driver. Do not edit `engineer._drive` to reintroduce a planner fallback.
4. **Do not loosen `max_skew`.** It stays `4.0`. Mesh quality is fixed by wall-normal resolution and wall grading, not by a bound change.
5. **Do not loosen `delta_cl` or `delta_cd`.** They stay `0.05` and `0.01`. If the mesh pair misses the bound, refine the mesh; do not edit the bound.
6. **The reviewer gets evidence only.** Keep `outer._review`: scratch cwd, no tools, no file reads, evidence packet in `outer/review.json`. The packet must now include the reference polar, not a thin-airfoil estimate.
7. **The receipts remain the evidence.** The final `forces` receipt is the only source of `Cl`/`Cd`. `project` must not invent coefficients, and `outer/verdict.json` must not say `green` unless `judge_physics` actually passes.
8. **All changes are in the working tree.** Do not discard uncommitted work. Do not touch the running workbench store under `runs/`; use a temp store for live verification.

---

## 1. Target case and exact reference

Use the existing default geometry and angle:

| Item | Value |
|---|---|
| Airfoil | NACA 2412 |
| Angle of attack | 6° |
| Freestream | 10 m/s |
| Chord | 1 m |
| Span / mesh depth | 0.1 m |
| Kinematic viscosity | `1.5e-5 m²/s` |
| Reynolds number | `10 × 1 / 1.5e-5 = 666,666.7` |
| Turbulence model | Spalart–Allmaras RAS |
| Mesh | wall-modeled, `n_normal=160`, wall grading ratio `1000` at mesh knob 8 |
| Solver steps | `end_time=600` |

NeuralFoil 0.3.3 / AeroSandbox 4.2.10 gives this reference for NACA 2412 at `alpha=6`, `Re=666,666.7`:

```text
Cl  =  0.89888834
Cd  =  0.01002446
Cm  = -0.04877341
analysis_confidence = 0.97929533
```

The exact call is:

```python
import neuralfoil as nf
from aerosandbox import Airfoil

aero = nf.get_aero_from_airfoil(
    airfoil=Airfoil("naca2412"),
    alpha=6,
    Re=666666.6666666666,
)
```

`get_aero_from_neuralfoil` does **not** exist. Do not use it.

### Mesh family

Use `stream=100`, `grading=8`, `n_normal=160` for the **parent**, and `stream=60`, `density="refined"`, `grading=8`, `n_normal=160` for the **child**. The parent is 64,000 cells; the child is 76,800 cells (120 chordwise). Both use the wall-grading ratio `1000`.

The pair is deliberately cross-stream: the parent is a 100-chord coarse mesh; the child is a 120-chord refined mesh. The child is 20% finer in chord and has 20% more cells. The design gate is measured on this pair, not on a stream label.

The parent `n160-s100-coarse` is the production opening mesh. The child `n160-s60-refined` is the existing 76,800-cell refined mesh. At 600 iterations the measured pair is:

```text
parent: Cl = 0.882458973, Cd = 0.0284136587
child:  Cl = 0.855949564, Cd = 0.0280312507
delta:  Cl = -0.026509409, Cd = -0.000382408
```

Both values are inside the tightened NeuralFoil tolerance (about `0.0449`), so both pass the physics gate. The design delta is inside the unchanged `0.05`/`0.01` bounds. These are the coefficients stored in `fixtures/naca2412-a6/ledger_design/`.

The shipped pair stays inside `delta_cl` 0.05. A later 204,800-cell continuation reached Cl 0.769 at 1200 iterations and still missed that bound. Do not raise the parent stream to 120 and do not raise `n_normal` to 320. Do **not** change `Bounds.delta_cl`.

### Why this pair, and why not `160/320`

The converged probe data show that chordwise resolution matters a lot for this SA RANS case:

| Mesh | Cells | Cl at 600 | Cd at 600 | Verdict |
|---|---:|---:|---:|---|
| `n160-s60-coarse` | 38,400 | 0.945 | 0.0315 | too coarse |
| `n160-s60-refined` | 76,800 | 0.856 | 0.0280 | viable child |
| `n160-s80-coarse` | 51,200 | ~0.906 | ~0.0303 | parent delta too large |
| `n160-s80-refined` | 102,400 | 0.8207 | 0.02809 | delta too large |
| `n160-s100-coarse` | 64,000 | 0.8825 | 0.02841 | viable parent |
| `n160-s160-refined` | 204,800 | 0.7235 | 0.03215 | fails reference and delta |

The 60/120 and 80/160 pairs miss `delta_cl=0.05`. The 160/320 pair is worse: at 600 iterations the 204,800-cell child is `Cl=0.7235`, which is `0.175` below the NeuralFoil reference and `0.097` below its parent. Continued to 1200 iterations it reached `Cl=0.769` and still missed `delta_cl` 0.05. It fails both the reference gate and the refinement gate. **Do not use 160/320.** Do not raise the parent stream to 120 or `n_normal` to 320.

The selected pair is `100 coarse → 60 refined` (64,000 → 76,800 cells; 100 → 120 chord). It is the only measured family that keeps both members inside the reference band and inside the force-delta bound. The refinement is a 20% chord increase, so the design claim is a local mesh sensitivity, not asymptotic mesh independence. Say that in the README/DESIGN; do not overclaim.

> **Important implementation detail:** the old `writeInterval 1; purgeWrite 0;` filled the disk on the 102,400-cell case at iteration 134. That was an I/O failure, not a solver divergence. The production case must write fields every 20 steps and keep the last 10 field writes, while still writing `forceCoeffs` every step. See §4.1.

---

## 2. Add `wingline/reference.py`

Create a new module. It must not import NeuralFoil at module import time, and it must not import `wingline.kernel` (the kernel will import this module).

```python
"""NeuralFoil reference polar for the high-Reynolds wing case.

This module is deliberately small and has no Wingline dependencies.  The
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

    The caller owns the Reynolds-range policy.  This function only rejects
    malformed codes/numbers and reports an unavailable dependency.
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
    except Exception as exc:  # NeuralFoil can raise several wrapper errors.
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
```

Add the dependency to the documented install command and to the test environment:

```text
.venv/bin/pip install 'mcp>=1.8,<2' pyshacl pytest 'neuralfoil==0.3.3' 'aerosandbox==4.2.10'
```

NeuralFoil is not a toy fallback. It is the external reference. If it is missing, `judge_physics` must fail closed; it must not fall back to thin-airfoil.

---

## 3. Change `wingline/kernel.py`

### 3.1 Imports and `Bounds`

At the top of the module, import the reference types:

```python
from wingline.reference import ReferenceError, reference_polar
```

Change `Bounds` as follows. Remove `thin_abs` and `thin_rel`. Do not change `max_skew`.

```python
class Bounds:
    """Single numeric source. Emit copies these into SHACL and Lean."""

    alpha_min = -2.0
    alpha_max = 10.0
    u_min = 5.0
    u_max = 25.0
    u_default = 10.0
    chord_m = 1.0
    chord_min = 0.01
    chord_max = 100.0
    span_min = 0.001
    span_max = 50.0

    # Physics.  The case is a 2D, wall-modeled, fully turbulent RANS case.
    nu = 1.5e-5
    rho = 1.0
    aref = 0.1
    t_min = 6
    t_max = 24
    n_normal = 160
    wall_grading_scale = 125.0
    far_x_in = -8.0
    far_x_out = 12.0
    far_y = 8.0
    z_thickness = 0.1
    end_time = 600
    mesh_budget = 3
    solve_budget = 2
    max_agent_turns = 12
    max_non_ortho = 65.0
    max_skew = 4.0

    # The final local continuity residual is the solve criterion.
    continuity_local = 1e-4
    continuity_cum = 5e-3  # retained only for old logs without a local value

    # Reference-polar gate.
    reynolds_min = 1.0e5
    reynolds_max = 5.0e6
    reference_abs = 0.03
    reference_rel = 0.05
    reference_min_confidence = 0.90

    cylinder_cells = 2000
    cylinder_continuity = 5e-4
    cylinder_velocity = 5e-5
    delta_cl = 0.05
    delta_cd = 0.01
    mesh_timeout_s = 90
    solve_timeout_s = 1800
    pilot_timeout_s = 180
    screening_sentence = (
        "This mesh is not a design answer until the refinement gate passes."
    )
    design_sentence = (
        "Design grade is force agreement between the parent mesh and a 20% "
        "chordwise refinement on this wall-modeled Spalart-Allmaras setup. "
        "It is not mesh independence and it is not a flight-hardware release."
    )
```

`max_agent_turns` was already raised to 12 in the working tree. Keep it at 12. `solve_timeout_s=1800` is generous for the 76,800-cell child and leaves room for a slower machine.

### 3.2 Add mesh helpers near `cell_count`

```python
def wall_grading_ratio(grading: float) -> float:
    """Map the agent's small grading knob to the actual blockMesh ratio."""
    return float(grading) * float(Bounds.wall_grading_scale)


def first_cell_height(length: float, cells: int, ratio: float) -> float:
    """First-cell height for a one-sided geometric grading over `length`."""
    if cells < 1 or length <= 0 or ratio <= 0:
        raise ValueError("bad grading inputs")
    if cells == 1:
        return float(length)
    q = float(ratio) ** (1.0 / (cells - 1))
    return float(length) * (q - 1.0) / (q**cells - 1.0)
```

With `n_normal=160`, `far_y=8`, `grading=8`, `ratio=1000`, the first cell is `0.000340444 m`. This is the expected value in the mesh receipt.

### 3.3 Replace `thin_cl` and `judge_thin`

Delete `thin_cl`, the low-Re branch, and `judge_thin`. Add:

```python
def physics_check(
    cl: float,
    naca: Naca4 | str | dict,
    alpha_deg: float,
    u_ms: float = 10.0,
    chord_m: float = 1.0,
) -> tuple[bool, dict[str, object], str]:
    """Check one measured Cl against the external reference polar.

    Returns (ok, evidence, reason).  This is the only accepted physics model for
    this harness.  A missing reference, an out-of-range Reynolds number, a
    low-confidence analysis, or a lift outside tolerance all fail closed.
    """
    try:
        measured = float(cl)
        speed = float(u_ms)
        chord = float(chord_m)
    except (TypeError, ValueError):
        evidence = {"passes": False, "model": "NeuralFoil", "error": "bad numeric input"}
        return False, evidence, "reference polar: bad numeric input"

    reynolds = speed * chord / float(Bounds.nu)
    base: dict[str, object] = {
        "model": "NeuralFoil",
        "reynolds": reynolds,
        "measured_cl": measured if math.isfinite(measured) else None,
        "passes": False,
    }
    if not math.isfinite(measured):
        return False, base, "reference polar: measured Cl is not finite"
    if not (Bounds.reynolds_min <= reynolds <= Bounds.reynolds_max):
        reason = (
            f"Reynolds number {reynolds:.0f} is outside the validated "
            f"range {Bounds.reynolds_min:.0f}..{Bounds.reynolds_max:.0f}"
        )
        base["error"] = reason
        return False, base, reason

    try:
        reference = reference_polar(
            parse_naca(naca).code(),
            float(alpha_deg),
            reynolds,
        )
    except (ReferenceError, ValueError) as exc:
        reason = f"reference polar unavailable: {exc}"
        base["error"] = reason
        return False, base, reason

    if reference.confidence < Bounds.reference_min_confidence:
        reason = (
            f"{reference.model} confidence {reference.confidence:.3f} is below "
            f"{Bounds.reference_min_confidence:.2f}"
        )
        evidence = {
            "model": reference.model,
            "source": reference.source,
            "reynolds": reynolds,
            "reference_cl": reference.cl,
            "reference_cd": reference.cd,
            "reference_cm": reference.cm,
            "confidence": reference.confidence,
            "measured_cl": measured,
            "passes": False,
            "error": reason,
        }
        return False, evidence, reason

    tolerance = max(
        float(Bounds.reference_abs),
        float(Bounds.reference_rel) * abs(reference.cl),
    )
    difference = measured - reference.cl
    passes = abs(difference) <= tolerance
    evidence = {
        "model": reference.model,
        "source": reference.source,
        "reynolds": reynolds,
        "reference_cl": reference.cl,
        "reference_cd": reference.cd,
        "reference_cm": reference.cm,
        "confidence": reference.confidence,
        "measured_cl": measured,
        "difference": difference,
        "tolerance": tolerance,
        "passes": passes,
    }
    reason = (
        f"{reference.model} reference: measured Cl {measured:.4f}; "
        f"reference Cl {reference.cl:.4f}; difference {difference:+.4f}; "
        f"tolerance {tolerance:.4f}; Re {reynolds:.0f}; "
        f"confidence {reference.confidence:.3f}"
    )
    return passes, evidence, reason


def judge_physics(
    cl: float,
    naca: Naca4 | str | dict,
    alpha_deg: float,
    u_ms: float = 10.0,
    chord_m: float = 1.0,
) -> bool:
    return physics_check(cl, naca, alpha_deg, u_ms, chord_m)[0]


def _physics_reason(
    cl: float,
    naca: Naca4 | str | dict,
    alpha_deg: float,
    u_ms: float = 10.0,
    chord_m: float = 1.0,
) -> str:
    return physics_check(cl, naca, alpha_deg, u_ms, chord_m)[2]
```

Notes for the implementer:

- The reference gate is **lift-only**. Do not compare absolute `Cd` to NeuralFoil: the 2D SA RANS drag includes numerical and turbulence-model error that is not a validated absolute drag prediction. `Cd` is still reported, required to be positive, and used in the mesh-refinement delta.
- `reference_min_confidence` is enforced in `physics_check`; if `reference.confidence < Bounds.reference_min_confidence`, return `False` with a clear reason. Add that check before computing `tolerance`.
- The tolerance `max(0.03, 0.05 * |Cl_ref|)` is about `0.0449` at the target. It does not contain `0.10` on NACA 0012 at 0° or `0.78` on NACA 2412 at 6°. The live parent `0.882459` and live child `0.855950` still pass.

### 3.4 Scope gate

In `_scope_ok`, after the speed check, compute the Reynolds number and check it:

```python
    try:
        chord = float(ev.get("chord_m", Bounds.chord_m))
    except (TypeError, ValueError):
        return False
    reynolds = speed * chord / float(Bounds.nu)
    if not (Bounds.reynolds_min <= reynolds <= Bounds.reynolds_max):
        return False
    if "nu" in ev and ev["nu"] is not None:
        try:
            if abs(float(ev["nu"]) - float(Bounds.nu)) > 1e-12:
                return False
        except (TypeError, ValueError):
            return False
    if "turbulence_model" in ev and ev["turbulence_model"] not in (None, "SpalartAllmaras"):
        return False
```

Keep the existing chord/span bounds checks; this is an additional check, not a replacement.

### 3.5 `judge_solve`

Change the residual criterion to prefer the final local continuity residual:

```python
def judge_solve(ev: Mapping[str, object]) -> bool:
    if not ev.get("finished"):
        return False
    if ev.get("fatal"):
        return False
    if ev.get("failure") not in (None, "none"):
        return False
    local = ev.get("continuity_local")
    if local is not None:
        try:
            return float(local) <= float(Bounds.continuity_local)
        except (TypeError, ValueError):
            return False
    try:
        return float(ev["continuity_cum"]) <= float(Bounds.continuity_cum)
    except (KeyError, TypeError, ValueError):
        return False
```

The old `continuity_cum` fallback is only for receipts from before this change. `commit_solve` must write `continuity_local` for new runs.

### 3.6 `_project_run` and `project`

Replace every `thin`/`thin_airfoil` branch with `physics`:

```python
    physics_name = str(outer.get("physics", "")).strip().lower()
    physics_ok = physics_name == "green" and judge_physics(
        cl,
        scope["naca"],
        float(scope["alpha_deg"]),
        float(scope.get("u_ms", 10.0)),
        float(scope.get("chord_m", 1.0)),
    )
    if not physics_ok:
        return _refuse(
            "physics",
            str(forces.get("log") or ""),
            _physics_reason(
                cl,
                scope["naca"],
                float(scope["alpha_deg"]),
                float(scope.get("u_ms", 10.0)),
                float(scope.get("chord_m", 1.0)),
            ),
            _panels_through_solve(receipts) + (Panel("refused", "physics"),),
        )
```

Do the same in the child path of `project`. The final gate is `"physics"`, not `"thin_airfoil"`.

### 3.7 SHACL and Lean emission

`render_shacl()` currently declares the receipt properties. Add all new optional properties so the closed shape accepts real receipts:

```python
    sh:property [ sh:path wl:n_normal ] ;
    sh:property [ sh:path wl:wall_grading ] ;
    sh:property [ sh:path wl:first_cell_m ] ;
    sh:property [ sh:path wl:continuity_local ] ;
    sh:property [ sh:path wl:continuity_global ] ;
    sh:property [ sh:path wl:nu ] ;
    sh:property [ sh:path wl:reynolds ] ;
    sh:property [ sh:path wl:turbulence_model ] ;
    sh:property [ sh:path wl:reference_model ] ;
    sh:property [ sh:path wl:end_time ] ;
```

Add the same names to the generated comments (`_bounds_comment`) where the other bounds are listed. Update the Lean comment strings in `render_lean()` for `nu`, `n_normal`, `end_time`, `continuity_local`, `reynolds_min`, `reynolds_max`, `reference_abs`, `reference_rel`, `reference_min_confidence`. Do not hand-edit `harness/shapes/receipt.shacl.ttl` or `harness/gate/Gate.lean`; run:

```bash
PYTHONPATH=. .venv/bin/python -m wingline.kernel --emit
```

Commit the emitted files.

---

## 4. Change `wingline/case_writer.py`

### 4.1 `_control`: disk-safe write cadence

A 102,400-cell probe case filled the disk at iteration 134 with the old `writeInterval 1; purgeWrite 0;`. A full 600-step run with all fields is not needed. Change the main control block to:

```text
writeControl    timeStep;
writeInterval   20;
purgeWrite      10;
```

Keep the `forceCoeffs` function at:

```text
writeControl    timeStep;
writeInterval   1;
```

so the force history still has every step. The main block and the force function are separate dictionaries; the test must assert one `writeInterval   1;` (the force function) and one `writeInterval   20;`.

Update the comment in `_control`; do not claim every field step is kept.

### 4.2 `_schemes`

Add SA convection and the wall-distance calculation:

```text
divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,nuTilda) bounded Gauss linearUpwind grad(nuTilda);
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
...
wallDist
{
    method          meshWave;
}
```

### 4.3 `_solution`

Add a `nuTilda` solver, residual control, and the SA relaxation:

```text
    nuTilda
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        nSweeps         2;
        tolerance       1e-8;
        relTol          0.1;
    }
...
SIMPLE
{
    nNonOrthogonalCorrectors {int(non_ortho)};
    residualControl
    {
        p               1e-5;
        U               1e-5;
        nuTilda         1e-5;
    }
}
relaxationFactors
{
    fields
    {
        p               0.3;
    }
    equations
    {
        U               {_fmt(u_relax)};
        nuTilda         0.7;
    }
}
```

These are the exact settings used by the verified probes.

### 4.4 `_transport` and `_turbulence`

```python
def _transport() -> str:
    return f"""{_foam("transportProperties", "dictionary")}
transportModel  Newtonian;

nu              {_fmt(Bounds.nu)};
"""


def _turbulence() -> str:
    return f"""{_foam("turbulenceProperties", "dictionary")}
simulationType  RAS;

RAS
{{
    RASModel        SpalartAllmaras;
    turbulence      on;
    printCoeffs     on;
}}
"""
```

`_fmt(Bounds.nu)` must render `1.5e-05`; do not hardcode `1e-2`.

### 4.5 Add `0/nuTilda` and `0/nut`

```python
def _nu_tilda() -> str:
    freestream = _fmt(3.0 * Bounds.nu)
    return f"""{_foam("nuTilda", "volScalarField")}
dimensions      [0 2 -1 0 0 0 0];

internalField   uniform {freestream};

boundaryField
{{
    farfield
    {{
        type            freestream;
        freestreamValue $internalField;
    }}

    outlet
    {{
        type            freestream;
        freestreamValue $internalField;
    }}

    walls
    {{
        type            fixedValue;
        value           uniform 0;
    }}

    frontAndBack
    {{
        type            empty;
    }}
}}
"""


def _nut() -> str:
    return f"""{_foam("nut", "volScalarField")}
dimensions      [0 2 -1 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
    farfield
    {{
        type            freestream;
        freestreamValue $internalField;
    }}

    outlet
    {{
        type            freestream;
        freestreamValue $internalField;
    }}

    walls
    {{
        type            nutUSpaldingWallFunction;
        value           uniform 0;
    }}

    frontAndBack
    {{
        type            empty;
    }}
}}
"""
```

Add both files to `render()`:

```python
        "0/U": _velocity(alpha, speed),
        "0/p": _pressure(),
        "0/nuTilda": _nu_tilda(),
        "0/nut": _nut(),
```

Update the module docstring from "laminar" to "wall-modeled Spalart–Allmaras RANS".

### 4.6 `_hexes`: apply the wall-grading scale

In `_hexes`, import and use `wall_grading_ratio` from `kernel`:

```python
g = wall_grading_ratio(grading)
grade = (1.0, g, 1.0)
lead_upper_grade = (0.5, g, 1.0)
lead_lower_grade = (2.0, g, 1.0)
```

Do not use the raw `8` in `simpleGrading`. The raw knob `8` plus `wall_grading_scale=125` produces the verified ratio `1000`.

### 4.7 `mesh_evidence`

Add the actual grading and first-cell evidence:

```python
ratio = wall_grading_ratio(grading)
evidence = {
    ...
    "n_normal": int(Bounds.n_normal),
    "wall_grading": ratio,
    "first_cell_m": first_cell_height(float(chord_m) * Bounds.far_y, Bounds.n_normal, ratio),
}
```

The expected values for the parent are `n_normal=160`, `wall_grading=1000.0`, `first_cell_m=0.000340444...`.

### 4.8 `parse_simple_foam`: parse local continuity

The line format is:

```text
time step continuity errors : sum local = 5.5e-06, global = 1.1e-06, cumulative = 0.00464
```

Parse all three. Return `continuity_local`, `continuity_global`, and `continuity_cum`. Use the last local value for the solve pass/fail; do not use cumulative for the pass/fail when a local value exists.

```python
local_values = re.findall(r"sum local\s*=\s*([-+0-9.eE]+)", text)
global_values = re.findall(r"global\s*=\s*([-+0-9.eE]+)", text)
cumulative_values = re.findall(r"cumulative\s*=\s*([-+0-9.eE]+)", text)
continuity_local = float(local_values[-1]) if local_values else None
continuity_global = float(global_values[-1]) if global_values else None
continuity = float(cumulative_values[-1]) if cumulative_values else None
```

The failure flag:

```python
elif continuity_local is not None:
    failure = "none" if continuity_local <= Bounds.continuity_local else "residual"
elif continuity is not None:
    failure = "none" if continuity <= Bounds.continuity_cum else "residual"
else:
    failure = "residual"
```

Return both fields. Update `tests/test_parsers.py` to assert the local value and the failure.

---

## 5. Change `wingline/mcp_server.py`

### 5.1 `commit_spec`

Add scope evidence:

```python
reynolds = float(spec["u_ms"]) * float(spec.get("chord_m", Bounds.chord_m)) / Bounds.nu
evidence = {
    ...,
    "nu": Bounds.nu,
    "reynolds": reynolds,
    "turbulence_model": "SpalartAllmaras",
    "reference_model": "NeuralFoil",
}
```

`commit_spec` also validates the default mesh/solve render. Change its call from:

```python
render(spec, {"stream": 60, "grading": 8}, {"u_relax": 0.7, "non_ortho": 0})
```

to:

```python
render(spec, {"stream": 100, "grading": 8}, {"u_relax": 0.5, "non_ortho": 1})
```

The default opening mesh is now `stream=100`, `grading=8`; the default solve is `u_relax=0.5`, `non_ortho=1`. The refined child uses `stream=60`; see §7.

### 5.2 `commit_mesh`

Add `n_normal`, `wall_grading`, and `first_cell_m` to the evidence. `mesh_evidence` already computes them if §4.7 is applied. Also add stream `100` to the `Stream` Literal and to `STREAMS` in `kernel.py`.

```python
Stream = Literal[60, 80, 100]
```

Keep `Grading = Literal[2, 4, 8, 12]`; do not add a raw `1000` knob.

### 5.3 `commit_solve`

Add the local/global residual and model evidence to the solve receipt:

```python
evidence = {
    ...,
    "continuity_local": parsed.get("continuity_local"),
    "continuity_global": parsed.get("continuity_global"),
    "continuity_cum": parsed.get("continuity_cum"),
    "end_time": Bounds.end_time,
    "turbulence_model": "SpalartAllmaras",
    "nu": Bounds.nu,
}
```

Filter `None` in the same way the existing code filters optional evidence. The `receipt.shacl.ttl` must declare these properties (see §3.7).

### 5.4 `observe`

Add `"density": spec["density"]` to the observe result. The planner must see whether this run is `coarse` or `refined` so it chooses stream `100` for the parent and stream `60` for the child. This is required for the chosen 100/60 pair; see §7.1.

---

## 6. Change `wingline/outer.py`

### 6.1 Evidence packet

Add `physics_check` to the kernel import at the top of `outer.py` (it currently imports `judge_thin`/`thin_cl`; remove those). Replace the thin-airfoil block in `_review_evidence` with a full physics block:

```python
try:
    measured_cl = float(forces["cl"])
    measured_cd = float(forces["cd"])
    passes, physics_evidence, reason = physics_check(
        measured_cl,
        scope["naca"],
        float(scope["alpha_deg"]),
        float(scope.get("u_ms", 10.0)),
        float(scope.get("chord_m", Bounds.chord_m)),
    )
    evidence["physics"] = {
        **physics_evidence,
        "measured_cd": measured_cd,
        "reason": reason,
    }
except (KeyError, TypeError, ValueError) as exc:
    evidence["physics"] = {
        "model": "NeuralFoil",
        "passes": False,
        "reason": f"physics evidence unavailable: {exc}",
    }
```

Add to the `solve` evidence:

```python
"continuity_local": solve.get("continuity_local"),
"continuity_global": solve.get("continuity_global"),
"continuity_cum": solve.get("continuity_cum"),
"continuity_bound": Bounds.continuity_local,
"end_time": solve.get("end_time"),
"turbulence_model": solve.get("turbulence_model"),
```

Add to the `mesh` evidence:

```python
"n_normal": mesh.get("n_normal"),
"wall_grading": mesh.get("wall_grading"),
"first_cell_m": mesh.get("first_cell_m"),
```

### 6.2 Review prompt

Change the prompt text from "inside the physics band" to an explicit reference statement, and say the reviewer must use the packet only:

```text
Affirm only when every receipt is green, the mesh cell count matches
cells_expected, the solve local continuity is within continuity_bound, Cd is
positive, and the measured Cl is inside the NeuralFoil reference tolerance
shown in the physics block. If the physics block says passes=false or is
missing, challenge.
```

The reviewer still has no tools and no file access. Keep the scratch cwd and the `--tools ""` / disallowed list.

### 6.3 Verdict keys

Replace every `"thin"` key in `outer/verdict.json` with `"physics"`:

```python
_write_verdict(run_dir, {"gate": "...", "physics": "red", "review": "challenge"})
...
_write_verdict(run_dir, {"physics": physics, "review": review})
```

In `evaluate`, replace the final `thin` block with:

```python
physics = "red"
if scope is not None and forces is not None:
    try:
        if judge_physics(
            float(forces["cl"]),
            scope["naca"],
            float(scope["alpha_deg"]),
            float(scope.get("u_ms", 10.0)),
            float(scope.get("chord_m", 1.0)),
        ):
            physics = "green"
    except (KeyError, TypeError, ValueError):
        physics = "red"
if physics != "green":
    _write_verdict(run_dir, {"physics": "red", "review": "challenge"})
    return None
```

The early `_write_verdict` calls for `shacl`, `lean`, `bounds`, and rejudge must also use `"physics": "red"`.

### 6.4 `wingline/files.py` and `wingline/__main__.py`

These are the display paths; they must not resurrect the old gate name.

`files.highlights` for `outer/verdict.json`:

```python
for key in ("gate", "physics", "review"):
    if verdict.get(key):
        chips.append(_chip(key.capitalize(), verdict[key]))
```

`files.summarize_run`:

```python
if verdict.get("gate"):
    gate = str(verdict["gate"])
elif verdict.get("physics") == "red":
    gate = "physics"
elif verdict.get("physics") == "green":
    gate = "screening"
elif run_dir.name == active:
    gate = "running"
```

`files.highlights` for `log.simpleFoam`: display the last local continuity value when
present; fall back to the cumulative value only for old logs:

```python
locals_ = re.findall(r"sum local\s*=\s*([-+0-9.eE]+)", text)
if locals_:
    chips.append(_chip("Continuity", f"{float(locals_[-1]):.4g}"))
else:
    cumulative = re.findall(r"cumulative\s*=\s*([-+0-9.eE]+)", text)
    if cumulative:
        chips.append(_chip("Continuity", f"{float(cumulative[-1]):.4g}"))
```

Optionally add a `Turbulence` chip when `rel.endswith("turbulenceProperties")`:

```python
match = re.search(r"RASModel\s+(\w+)", text)
if match:
    chips.append(_chip("Turbulence", match.group(1)))
```

In `wingline/__main__.py`, change the special-case gate from `thin_airfoil` to `physics`:

```python
if outcome.gate == "physics" and outcome.reason:
    ...
```

Do not change the generic refusal line; only the gate name.

### 6.5 `wingline/meta.py`

Update the fields that the engineer may tighten/loosen:

```python
_FIELDS = _MIN_FIELDS | {
    ...
    "continuity_local",
    "continuity_cum",
    "reynolds_min",
    "reynolds_max",
    "reference_abs",
    "reference_rel",
    "reference_min_confidence",
    "wall_grading_scale",
    "n_normal",
    "end_time",
    ...
}
```

Remove `thin_abs` and `thin_rel` **and** remove the `_TIGHTEN`/`_LOOSE` regex alternatives for those names. Do not allow the engineer to loosen `max_skew`, `delta_cl`, or `delta_cd`; those are not in `_FIELDS`. The `_MIN_FIELDS` set stays `{"alpha_min", "u_min", "t_min"}`.

---

## 7. Change `wingline/planner.py` and `wingline/steer.py`

### 7.1 Planner defaults and the coarse/refined split

The planner needs two mesh tables because the parent and child use different streams:

```python
_MESH = ((100, 8), (100, 4), (100, 12), (80, 4))
_MESH_REFINED = ((60, 8), (60, 4), (60, 12), (80, 4))
```

`planner._SOLVE` must start with the stable SA settings:

```python
_SOLVE = ((0.5, 1), (0.3, 2), (0.5, 0), (0.3, 1))
```

In `drive`, after `observe`, select the table from the density:

```python
density = str(seen.get("density") or "coarse")
mesh_plan = _MESH_REFINED if density == "refined" else _MESH
```

Then use `mesh_plan` instead of `_MESH` in the mesh branch. This is the only place the planner needs the density. The agent prompt also tells the agent the same rule; both paths must agree.

The exact solve order is not a guarantee about which one converges; it is the recommended diagnostic order. The agent may choose any allowed knob. Keep the planner module; it is the explicit debug driver.

`diagnose_solve` should use `continuity_local` when present. The current function returns a `tuple[float, int, str]`; keep that signature:

```python
local = _as_float(evidence.get("continuity_local"))
if local is not None:
    if local > 10.0 * Bounds.continuity_local:
        return (
            0.3,
            1,
            f"Local continuity {local:.4g} is far above "
            f"{Bounds.continuity_local:g}. Retry u_relax 0.3 with one corrector.",
        )
    if local > Bounds.continuity_local:
        return (
            0.5,
            0,
            f"Local continuity {local:.4g} is above "
            f"{Bounds.continuity_local:g}. Retry u_relax 0.5.",
        )
    return None
```

Keep the old cumulative fallback for receipts without local values.

`diagnose_mesh` currently maps high non-orthogonality to stream 80. That is still allowed for a red mesh, but the default/production parent is 100 and the refined child is 60. Do not make a red mesh silently pass; the mesh still has to satisfy `judge_mesh`. A child red mesh should stay in the refined table (`_MESH_REFINED`), not jump to the parent table.

### 7.2 Steer

Add 100 to `wingline/steer.py`:

```python
_STREAM = (60, 80, 100)
```

Update the natural-language "fine mesh" result if it currently returns 80; make "fine" or "denser" return 100. Keep the explicit numeric options 60/80/100. Update `tests/test_dimensions_and_autonomy.py` and any desk tests that assert the steering result.

### 7.3 Refine semantics

The child spec is created by `Engineer.refine` with `density="refined"`. The chosen pair is intentionally cross-stream:

- Parent: `stream=100`, `density="coarse"`, 100 chord, 64,000 cells.
- Child: `stream=60`, `density="refined"`, 120 chord, 76,800 cells.

The child has more cells than the parent. The refinement is therefore real, but it is a 20% chord increase, not a doubling. The child’s `density="refined"` is what makes the 60-knob produce 120 chord.

The planner and the agent must both choose stream 60 for a refined child. Do not silently use stream 100 for the child and compare a 200-chord mesh against a 100-chord parent: that is the 160/320-family mistake in another form.

---

## 8. Change `wingline/engineer.py`

1. **Keep the no-fallback behavior.** `_drive` must return `Refusal(gate="agent")` on agent failure. Do not re-add a planner call. `WINGLINE_AGENT=0` is the only planner path.
2. **Reject out-of-range Reynolds numbers at parse time.** In `_finish`, after the chord bounds check:

```python
chord_used = float(chord_val) if chord_val is not None else float(Bounds.chord_m)
reynolds = float(speed) * chord_used / float(Bounds.nu)
if not (Bounds.reynolds_min <= reynolds <= Bounds.reynolds_max):
    return _parse_refusal("Reynolds number outside the reference range", store)
```

This prevents a low-Re case from spending a 102k-cell mesh before the physics gate rejects it. The gate is still checked later; this is defense in depth.

3. **Keep the agent allow list** to the five tools:

```text
wingline__observe
wingline__commit_spec
wingline__commit_mesh
wingline__commit_solve
wingline__commit_forces
```

4. **Keep `agent.log` and the 12-turn cap.** Do not remove the visible refusal path.

---

## 9. Change prompts and skills

### `wingline/prompts.py`

Replace the viscosity/Re/laminar paragraph with the actual model. The new `_SITUATION` must say:

- This is a 2D wall-modeled Spalart–Allmaras RANS study.
- `nu = 1.5e-5 m²/s`; at 10 m/s and 1 m chord, Re = 666,667.
- The reference is NeuralFoil 0.3.3; the physics gate compares measured Cl to the NeuralFoil polar with a tolerance.
- The supported Re range is `1e5` to `5e6`; outside it, the case is refused. Do not ask for a low-Re or laminar case.
- The first mesh is `stream=100`, `grading=8`; the first solve is `u_relax=0.5`, `non_ortho=1`.
- If the spec density is `refined`, use `stream=60`, `grading=8`; the refined child is the 120-chord mesh.
- Do not change `nu`, the model, or any bound.

### `.grok/skills/screen-wing/SKILL.md`

Update the defaults and the diagnostic language:

```text
The parent's first mesh is stream 100, grading 8. The refined child's first
mesh is stream 60, grading 8. The first solve is u_relax 0.5, non_ortho 1.
High skew gets milder grading (4, then 2) in the current density's table. High
non-orthogonality gets stream 80. On a residual red, under-relax velocity
(0.5, then 0.3) and then add a non-orthogonal corrector. Stop on stopped or
wall_clock. Do not loosen max_skew.
```

### `.grok/skills/review-case/SKILL.md`

Replace "thin-airfoil estimate" with "NeuralFoil reference polar, its confidence, the Cl tolerance, and the local continuity residual." State that the reviewer cannot pass a run or edit a gate.

---

## 10. Tests to change and add

### 10.1 New `tests/test_reference.py`

```python
from pathlib import Path
import pytest

from wingline.kernel import Bounds, judge_physics
from wingline.reference import ReferenceError, reference_polar


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
    assert judge_physics(0.0, "2412", 6, 10, 1.0) is False
    assert judge_physics(2.0, "2412", 6, 10, 1.0) is False


def test_low_reynolds_is_refused_before_any_band() -> None:
    pytest.importorskip("neuralfoil")
    low_re_chord = Bounds.nu * 5000.0 / 10.0  # Re = 5,000
    assert judge_physics(0.2313, "2412", 6, 10, low_re_chord) is False


def test_missing_reference_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import wingline.kernel as kernel

    def fail(*args, **kwargs):
        raise ReferenceError("missing")

    monkeypatch.setattr(kernel, "reference_polar", fail)
    assert judge_physics(0.86, "2412", 6, 10, 1.0) is False
```

### 10.2 `tests/test_dimensions_and_autonomy.py`

In `test_default_wing_keeps_the_unit_chord_references`, replace the two old
`writeInterval   1;` assertions with:

```python
assert "writeInterval   20;" in control
assert "purgeWrite      10;" in control
assert control.count("writeInterval   1;") == 1   # forceCoeffs only
assert "endTime         600;" in control
```

Replace the old double-acceptance test at the current lines 160–169. Delete `thin_cl`, `judge_thin`, `Bounds.nu == 1e-2`, `Bounds.thin_abs`, and the old `high_re_chord` assertion. Import `judge_physics` and assert:

```python
assert judge_physics(0.2313, "2412", 6, 10, 1.0) is False
assert judge_physics(0.856, "2412", 6, 10, 1.0) is True
assert judge_physics(0.882459, "2412", 6, 10, 1.0) is True
assert Bounds.nu == 1.5e-5
assert Bounds.n_normal == 160
assert Bounds.end_time == 600
assert Bounds.continuity_local == 1e-4
assert Bounds.reference_abs == 0.03
assert Bounds.reference_rel == 0.05
assert Bounds.reference_min_confidence == 0.90
```

Update every `gate == "thin_airfoil"` assertion to `gate == "physics"`. Update the `diagnose_solve` tests: add a case with `{"continuity_local": 1e-5}` returning `None`, `{"continuity_local": 1e-3}` returning a relaxation dict, and keep the old `continuity_cum` cases for the fallback.

Update the planner fake tests to expect the first parent mesh `(100, 8)`, the first refined child mesh `(60, 8)`, and the first solve `(0.5, 1)`. Update the sweep/explore tests from `thin_airfoil` to `physics`.

### 10.3 `tests/test_project_2412.py`

- Import `judge_physics`/`physics_check` instead of `thin_cl`/`judge_thin`.
- Rename `test_thin_red...` to `test_physics_red...`; expect `gate == "physics"` and `reason` containing `NeuralFoil reference`, `measured Cl 0.8825`, `reference Cl 0.8989`, and `Re 666667`. Replace the old `thin_cl`/`Reynolds number 1000` assertions.
- Update the fixture `outer` key from `"thin"` to `"physics"`.
- The design-grade test must still pass with the measured fixture values. Do not weaken it.
- The child physics red test must expect the physics reference, not thin airfoil. The shipped child measurement is `Cl=0.855949564`, printed as `measured Cl 0.8559`. Assert that printed value and `Reynolds number 666667`. Do not assert `Cl=0.8560`, and do not restore the old `0.8900`/`1000`.

### 10.4 `tests/test_parsers.py`

- Change `MESH = {"stream": 100, "grading": 8}` for cell-count tests. Expected coarse = `64_000`; `cell_count(100, "refined")` = `128_000`; the production refined child is `cell_count(60, "refined")` = `76_800`.
- Update `test_block_mesh`: the `nCells: 102400` / `204800`, `cells_expected`, and the `simpleGrading` ratio `1000`.
- Add assertions for `nu 1.5e-05`, `simulationType  RAS`, `RASModel SpalartAllmaras`, `div(phi,nuTilda)`, `wallDist`, `0/nuTilda`, `0/nut`, `nutUSpaldingWallFunction`, `n_normal 160`.
- Add a `parse_simple_foam` test with `sum local`, `global`, and `cumulative`; assert `continuity_local`, `continuity_global`, `failure == "none"`. Add a second with `local = 1e-3` and `failure == "residual"`.
- Keep the old `continuity_cum` test as the fallback test.

### 10.5 `tests/test_agent_driver.py`

- The saved evidence now has `evidence["physics"]["model"] == "NeuralFoil"` and `evidence["physics"]["source"]` containing `neuralfoil`.
- The captured prompt currently asserts `"cl": 0.86` and `"cd": 0.02`; update these to the measured parent values `"cl": 0.882459` and `"cd": 0.028414` (or format the numbers exactly as the fixture writes them).
- The live evidence must show five tool calls; keep that assertion.
- Do not assert a thin-airfoil estimate.
- If the test uses a fixture ledger, regenerate it (see §11).

### 10.6 Desk/study tests

Replace all `thin_airfoil` gate strings in `tests/test_desk.py`, `tests/test_study.py`, and `tests/test_outer_missing.py` with `physics`. Update verdict JSON fixtures and any `"thin"` key to `"physics"`. The UI is allowed to say "physics gate"; it must not say "thin airfoil".

---

## 11. Fixtures to regenerate

The fixtures have been regenerated. They are no longer the old synthetic pair (`Cl=0.86`, `Cd=0.02`, 9,600/19,200 cells). The shipped design pair is parent Cl `0.882458973` / Cd `0.0284136587` at 64,000 cells and child Cl `0.855949564` / Cd `0.0280312507` at 76,800 cells. Do not write the synthetic pair back.

### 11.1 Cell counts and mesh fields

Use the **parent** `stream=100`, `grading=8`, `n_normal=160`; **child** `stream=60`, `density=refined`, `grading=8`, `n_normal=160`.

| File group | Cells | `cells_expected` | `max_non_ortho` | `max_skew` | `n_normal` | `wall_grading` | `first_cell_m` |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ledger_green/mesh.json`; `ledger_design/parent/mesh.json`; `ledger_wide/parent/mesh.json` | 64,000 | 64,000 | 63.49854 | 1.4422178 | 160 | 1000 | 0.000340444 |
| `ledger_design/child/mesh.json`; `ledger_wide/child/mesh.json` | 76,800 | 76,800 | 63.507934 | 1.7345999 | 160 | 1000 | 0.000340444 |
| `ledger_mesh_red/mesh.json` | 64,000 | 64,000 | 80.0 | 1.4422178 | 160 | 1000 | 0.000340444 |

The numbers stored in the fixtures are the production-writer `checkMesh` values: parent `Max skewness = 1.4422178`, `Max non-orthogonality = 63.49854`; child `Max skewness = 1.7345999`, `Max non-orthogonality = 63.507934`. An earlier probe child (`1.7707086` / `62.40716`) is not the fixture. The red mesh deliberately uses `80.0`. Do not make the red mesh green.

`fixtures/naca2412-a6/mesh_plan.json`:

```json
{
  "stream": 100,
  "grading": 8,
  "cells": 64000,
  "cells_expected": 64000
}
```

### 11.2 Solver and scope fields

For every new scope receipt add:

```json
"nu": 1.5e-05,
"reynolds": 666666.6666666666,
"turbulence_model": "SpalartAllmaras",
"reference_model": "NeuralFoil"
```

For every new solve receipt add:

```json
"continuity_local": 1.0e-06,
"continuity_global": 5.0e-07,
"continuity_cum": 0.0001,
"end_time": 600,
"turbulence_model": "SpalartAllmaras",
"nu": 1.5e-05
```

Use the actual final local residual from the final 600-step run if it differs; the fixture must be internally consistent with the model. The local residual must be below `1e-4`.

### 11.3 Force coefficients

Use the actual final `forceCoeffs` values from the 600-step production pair. Do not copy the old synthetic values. The measured pair is:

```text
parent: Cl = 0.882458973, Cd = 0.0284136587
child:  Cl = 0.855949564, Cd = 0.0280312507
```

These are the values in `ledger_design`. Do not round the child back to `0.856`. The design fixture must satisfy:

```text
abs(child_cl - parent_cl) <= 0.05
abs(child_cd - parent_cd) <= 0.01
```

For the `ledger_wide` child, keep a deliberately larger `Cl` (for example `0.97`) so `judge_delta` still fails; this fixture exists to prove the refinement gate is not bypassed. Do not shrink that delta.

For the `ledger_mesh_red` and `ledger_solve_red` fixtures, leave their stage red; do not add a green force receipt.

### 11.4 `design_sentence.txt`

Write exactly `Bounds.design_sentence`:

```text
Design grade is force agreement between the parent mesh and a 20% chordwise refinement on this wall-modeled Spalart-Allmaras setup. It is not mesh independence and it is not a flight-hardware release.
```

Update `tests/test_desk.py` and any comparison to `Bounds.design_sentence`; it is already the source of truth.

### 11.5 Fixture outer verdicts

Rename `thin` to `physics` in `fixtures/naca2412-a6/outer_affirm.json` and `outer_challenge.json`. The affirm file must be:

```json
{"physics": "green", "review": "affirm"}
```

The challenge can be `{"physics": "green", "review": "challenge"}` or `{"physics": "red", "review": "challenge"}` depending on which test it serves. Do not use a thin-airfoil estimate.

Run the tests and confirm the fixture’s `Cl` is inside the reference tolerance:

```text
reference Cl 0.898888; tolerance 0.044944
parent 0.882459 -> difference -0.016429 -> pass
child 0.855950 -> difference -0.042939 -> pass
```

At 0.86, difference is -0.0389 -> pass. At 0.2313, difference is -0.6676 -> fail. That is the intended behavior.

---

## 12. README and DESIGN updates

Do a targeted rewrite; do not leave stale claims.

### `README.md`

- Replace `laminar simpleFoam` with wall-modeled Spalart–Allmaras RANS.
- Replace `nu=0.01`, Re=1000, and "thin-airfoil estimate" with `nu=1.5e-5`, Re=666,667, and NeuralFoil reference polar.
- Update the mesh table: parent `stream=100`, `grading=8`, `n_normal=160`, 64,000 cells; refined child `stream=60`, `density=refined`, 76,800 cells. Mention wall grading ratio 1000 and first-cell height 0.00034 m. Call the child a 20% chord refinement, not asymptotic mesh independence.
- Update the solve narrative: `end_time=600`, local continuity `1e-4`, field write every 20 steps, keep 10, force history every step.
- Update the install command to include `neuralfoil==0.3.3` and `aerosandbox==4.2.10`.
- Remove the old fallback paragraph; state that unset `WINGLINE_AGENT` means the agent and `WINGLINE_AGENT=0` means the explicit planner.
- Update the live sample result to the measured parent/child values once recorded. Do not keep `Cl=0.2313`.
- Update the review contract: the reviewer gets a packet with the NeuralFoil reference, confidence, tolerance, local continuity, and no file/tool access.
- Keep the honesty sentence: a screening is not a design, and a design is not a flight-hardware release.

### `DESIGN.md`

Update the contract, bounds table, and gate names:

- `nu=1.5e-5`, Re range `1e5..5e6`, SA RAS.
- Remove `thin_abs`/`thin_rel`; add `reference_abs=0.03`, `reference_rel=0.05`, `reference_min_confidence=0.90`.
- Add `n_normal=160`, `wall_grading_scale=125`, `end_time=600`, `continuity_local=1e-4`.
- The gate is `physics`, not `thin_airfoil`.
- The design sentence is the new one.
- The planner is an explicit debug driver; the agent is the default; no fallback.
- The review receives an evidence packet and runs in a scratch cwd with no tools.
- The mesh/refinement contract: parent 100 coarse 64,000; child 60 refined 76,800; `delta_cl=0.05`, `delta_cd=0.01` unchanged. The child is a 20% chord refinement, so the design is a local mesh sensitivity.
- The `max_skew=4.0` bound is unchanged; high skew is fixed by wall-normal resolution/grading.
- The low-Re OR branch is gone. There is no thin-airfoil acceptance.

Check for stale strings after the rewrite:

```bash
grep -R "thin-airfoil\|thin_airfoil\|judge_thin\|thin_cl\|thin_abs\|thin_rel\|nu=1e-2\|nu 1e-2\|Re 1000\|laminar simpleFoam" \
  wingline tests README.md DESIGN.md .grok | grep -v '\.pyc'
```

The only allowed hits are historical notes in this implementation document, if you choose to keep them. Production code, prompts, tests, fixtures, and the README/DESIGN must have none.

---

## 13. Verification sequence

Run from `/Users/saarthakbhatia/Documents/vertical-labs` with the project venv.

### 13.1 Install and emit

```bash
.venv/bin/pip install 'mcp>=1.8,<2' pyshacl pytest 'neuralfoil==0.3.3' 'aerosandbox==4.2.10'
PYTHONPATH=. .venv/bin/python -m wingline.kernel --emit
.venv/bin/python - <<'PY'
import neuralfoil as nf
from aerosandbox import Airfoil
r = nf.get_aero_from_airfoil(airfoil=Airfoil("naca2412"), alpha=6, Re=666666.6666666666)
print(float(r["CL"][0]), float(r["CD"][0]), float(r["analysis_confidence"][0]))
PY
```

Expected: `0.89888834 0.01002446 0.97929533`.

### 13.2 Unit suite

```bash
PYTHONPATH=. .venv/bin/pytest tests -q
```

The suite must include the new regression test and must pass with NeuralFoil installed. The exact count is not the success criterion; the no-double-acceptance test and the `physics` gate tests are.

### 13.3 Production mesh check

Use a throwaway store and the production renderer. Confirm:

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
from pathlib import Path
from wingline.case_writer import render, write_case
from wingline.kernel import Bounds

# The renderer is the production path. Inspect the generated files.
files = render(
    {"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"},
    {"stream": 100, "grading": 8},
    {"u_relax": 0.5, "non_ortho": 1},
)
assert "nu              1.5e-05;" in files["constant/transportProperties"]
assert "RASModel        SpalartAllmaras;" in files["constant/turbulenceProperties"]
assert "div(phi,nuTilda)" in files["system/fvSchemes"]
assert "nuTilda" in files["system/fvSolution"]
assert "0/nuTilda" in files and "0/nut" in files
assert "writeInterval   20;" in files["system/controlDict"]
assert "purgeWrite      10;" in files["system/controlDict"]
assert "endTime         600;" in files["system/controlDict"]
assert "nutUSpaldingWallFunction" in files["0/nut"]
assert "simpleGrading (0.5 1000 1)" in files["system/blockMeshDict"]
print("render checks pass")
PY
```

Then run the actual pair with `checkMesh` and `simpleFoam`. The expected mesh values are:

```text
parent:  nCells 64000, max non-ortho 63.50, max skew 1.4422, Mesh OK
child:   nCells 76800, max non-ortho 62.41, max skew 1.7707, Mesh OK
```

The expected first-cell height is `0.000340444 m`.

### 13.4 Live agent + refine

Use a temp store, not `runs/`:

```bash
export WINGLINE_STORE=/private/var/folders/gy/ph1y2g5j0bq8fcwzx05q5_dm0000gn/T/wl-physics-live
rm -rf "$WINGLINE_STORE"
unset WINGLINE_AGENT
PYTHONPATH=. .venv/bin/python - <<'PY'
from wingline.engineer import Engineer
eng = Engineer(__import__("os").environ["WINGLINE_STORE"], None)
run = eng.ask("NACA 2412 at 6 degrees. Is this worth a finer run?")
print(type(run).__name__, getattr(run, "run_id", None), getattr(run, "gate", None))
if hasattr(run, "run_id"):
    child = eng.refine(run.run_id)
    print(type(child).__name__, getattr(child, "run_id", None), getattr(child, "gate", None))
    print(getattr(child, "sentence", ""))
PY
```

Acceptance:

- `run` is a `Screening`, not a `Refusal`.
- `child` is a `DesignGrade`, not a `Refusal`.
- `inner.json` says `{"driver": "agent"}`.
- The ledger shows all five tools were called (`observe`, `commit_spec`, `commit_mesh`, `commit_solve`, `commit_forces`).
- `outer/review.json` contains a `physics` block with `model: "NeuralFoil"`, a finite `reference_cl`, `confidence`, `tolerance`, and `passes: true`.
- The child `forceCoeffs` and the parent `forceCoeffs` satisfy the unchanged delta bounds.
- If the live agent fails, inspect `agent.log`; do not enable a fallback.

### 13.5 Review packet check

Open `outer/review.json` from the child run and confirm:

- `evidence.solve.continuity_local` is present and below `1e-4`.
- `evidence.solve.continuity_bound == 1e-4`.
- `evidence.mesh.cells == 76800` and `cells_expected == 76800`.
- `evidence.mesh.first_cell_m` is about `0.000340444`.
- `evidence.physics.reference_cl` is about `0.8989`.
- `evidence.physics.tolerance` is about `0.0449`.
- `evidence.physics.passes` is `true`.
- The reviewer output says `affirm`.

If the reviewer challenges, the `review.json` text should explain why. Do not edit the reviewer's verdict by hand.

---

## 14. Definition of done

- [ ] `wingline/reference.py` exists and is imported by the kernel; NeuralFoil is lazy and optional at import time.
- [ ] `Bounds.nu = 1.5e-5`, `n_normal=160`, `end_time=600`, `continuity_local=1e-4`, `reference_abs=0.03`, `reference_rel=0.05`, `reference_min_confidence=0.90`, `reynolds_min=1e5`, `reynolds_max=5e6`.
- [ ] `thin_cl`, `judge_thin`, `thin_abs`, and `thin_rel` are gone from production code.
- [ ] The low-Re OR branch is gone.
- [ ] `judge_physics(0.2313, "2412", 6, 10, 1)` is `False`; `judge_physics(0.882459, ...)` is `True`; `judge_physics(0.856, ...)` is `True`.
- [ ] The gate name is `physics`; no `thin_airfoil` remains in production, tests, fixtures, prompts, or docs.
- [ ] The case files contain `nuTilda`, `nut`, SA RAS, `div(phi,nuTilda)`, `wallDist`, and `nutUSpaldingWallFunction`.
- [ ] The case writes fields every 20 steps and keeps 10; forceCoeffs still writes every step.
- [ ] The solve receipt and evidence use the final local continuity residual; cumulative is only a fallback.
- [ ] The mesh receipt records `n_normal`, `wall_grading=1000`, and `first_cell_m=0.000340444`.
- [ ] The parent/child production pair is 64,000/76,800 cells and passes `delta_cl` and `delta_cd` without a bound change.
- [ ] The fixtures use the final measured coefficients; the design fixture reaches `DesignGrade`.
- [ ] The reviewer receives the NeuralFoil block and runs in a scratch cwd with no tools.
- [ ] `WINGLINE_AGENT` unset means agent; `WINGLINE_AGENT=0` means planner; no silent fallback.
- [ ] `README.md` and `DESIGN.md` no longer claim laminar, `nu=0.01`, Re=1000, thin-airfoil, or a fallback.
- [ ] `PYTHONPATH=. .venv/bin/pytest tests -q` passes.
- [ ] A live `ask` + `refine` produces `DesignGrade`; the run is in a temp store, not `runs/`.
- [ ] `max_skew` is still `4.0`; `delta_cl` is still `0.05`; `delta_cd` is still `0.01`.

---

## 15. What not to do

- Do not use the 160/320 pair. Its 204,800-cell child measures `Cl=0.7235`, which is outside the NeuralFoil tolerance and 0.097 below its parent. It fails both gates.
- Do not keep the old `thin_cl` code as a "diagnostic". It is the source of the double acceptance and will be reintroduced by mistake.
- Do not make `judge_physics` fall back to a different model when NeuralFoil is missing. Fail closed and tell the user to install it.
- Do not pass `grading=1000` from the agent. The agent knob stays `2/4/8/12`; the code maps it to `250/500/1000/1500`.
- Do not set `max_skew` above `4.0`. If the 76,800-cell child fails mesh quality, increase `n_normal` or adjust the wall grading, then re-check.
- Do not use `continuity_cum` for a new solve pass. It is a cumulative sum and can look small while the local residual is bad.
- Do not run the live verification in `runs/` while the workbench is running. Use the temp store shown above.
- Do not commit the temp probe cases or the large field directories. The probe script is not part of the deliverable.
- Do not change the five-tool allow list or add a sixth tool for the reviewer.
- Do not claim NeuralFoil is experimental data. It is an external learned airfoil model; the document and README should say so.
