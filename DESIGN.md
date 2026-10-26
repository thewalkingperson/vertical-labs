# Wingline

One question: is this NACA 4-digit wing worth a finer airflow simulation?

The model may run the case and turn a few knobs. It may not decide the result. A result is the receipt ledger. The model's sentences are saved and are not evidence.

| Result | Meaning |
|---|---|
| Screening | The coarse case passed. A finer mesh may be worth it. It is not a design. |
| Design grade | The same wing, with 20% more chordwise cells, stayed within 0.05 in lift and 0.01 in drag. |
| Refused | A named gate failed. No later number repairs it. |

A design grade is force agreement on this wall-modeled Spalart–Allmaras setup. It is not mesh independence, and it is not a flight release. `hardware_decision` accepts only a design grade.

## Three loops

**Meta** speaks before a case exists. If the section or the angle is missing, it asks. It may recommend one NACA 4-digit section. It does not choose the angle, start OpenFOAM, or grade a wing. A full sentence such as `NACA 2412 at 6 degrees` skips it.

**Inner** runs that wing through five tools, in order: look at the open stage, write the spec, mesh, solve, read the forces. Each step writes a receipt. The next step starts only if the last one passed. The knobs are stream, grading, velocity relaxation, and non-orthogonal correctors. The cylinder tutorial runs first, and a red cylinder stops the wing.

**Outer** reads the ledger, not the transcript. The first failure wins: the policy files match, the receipt shape holds, the gate proof checks, every receipt is judged again, then lift is checked against NeuralFoil. That check is free transition, confidence at least 0.90, and the band `max(0.03, 0.05 × |Cl_ref|)`. Drag must be positive. Absolute drag is not compared with NeuralFoil. A review may affirm or challenge. It cannot pass the run. A challenge withholds the finer mesh. `kernel.project` is the only function that can call a result a screening or a design grade.

The finer mesh is a new case of the same wing, with 20% more cells along the chord. It runs only when the screening says so. A design grade also needs that child to pass, the force change inside the bounds above, and the child's review to affirm.

On the page, a closing note may name another NACA 4-digit section or an angle inside the bounds. That sentence is not a gate.

## The chain

```text
pilot → scope → mesh → solve → forces
```

Status comes from the judge, never from the model. A red or stopped step has no successor, so a failed mesh cannot grow a lift number. Mesh may be tried three times, and each try must be a different mesh. Solve may be tried twice. A fatal error, a timeout, or forces that are still moving closes the solve. Still moving means the last 100 iterations differ from the previous 100 by more than 1e-4 in lift or in drag. The run is 1,500 iterations.

## The numbers

These live in `wingline/kernel.py`. If the emitted shape file or proof file drifts, the run is refused before a case exists.

| Bound | Value |
|---|---|
| Angle | −2 to 10 degrees |
| Speed | 5 to 25 m/s, default 10 |
| Thickness | 6 to 24 |
| Reynolds number | 1e5 to 5e6 |
| Parent mesh, finer mesh | 64,000 cells, 76,800 cells |
| Mesh quality | skew at most 4, non-orthogonality at most 65 |
| Force drift | 1e-4 on lift and on drag |
| Finer-mesh agreement | 0.05 in lift, 0.01 in drag |

y+ is recorded and is not a gate. The README is how to run it.
