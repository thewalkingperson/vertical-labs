# Wingline

Wingline answers one question: is this NACA 4-digit section worth a finer airflow simulation?

You ask in a sentence. If the section or the angle is missing, the model asks you in its own words. One case is then meshed and solved. A receipt is written at each gate, and the next gate does not start unless the last one passed. The agent's own prose is saved and is not the evidence. `kernel.project` is the only function that can call a result a screening or a design grade.

```text
NACA 2412 at 6 degrees. Is this worth a finer run?
```

Angle is −2 to 10 degrees. Speed is 5 to 25 m/s, and 10 is the default. Thickness is 6 to 24. Chord defaults to 1 m and span to 0.1 m. Span is the depth of the 2D mesh, not an airplane wingspan. Reynolds number must be 1e5 to 5e6.

## What a result means

| Result | Meaning |
|---|---|
| Screening | The coarse case passed. A finer mesh may be worth running. It is not a design. |
| Design grade | The same wing on a mesh with 20% more chordwise cells stayed within 0.05 in lift and 0.01 in drag. |
| Refused | A named gate failed. No later number repairs it. |

A design grade is force agreement on that pair for this wall-modeled Spalart–Allmaras setup. It is not mesh independence, and it is not a release for flight hardware. `hardware_decision` accepts only a design grade.

Lift is compared with NeuralFoil 0.3.3, free transition, confidence at least 0.90. The band is `max(0.03, 0.05 × |Cl_ref|)`. If NeuralFoil is missing, the gate fails closed. Drag must be positive. Absolute drag is not compared with NeuralFoil. A forced-transition polar is recorded beside the free one and is not a pass or fail.

## The solver

Serial `simpleFoam`, wall-modeled Spalart–Allmaras, `nutUSpaldingWallFunction`. That wall function is Spalding's blend across the sublayer, the buffer, and the log region. y+ is recorded and is not required to be below 1.

The parent mesh is stream 100, grading 8, 160 cells wall-normal, wall ratio 1000, 64,000 cells. The first chordwise cells sit at the nose, ratio 16. The child, when it runs, is stream 60 refined: 76,800 cells, 120 along the chord. That is a 20% check, not mesh independence.

The solve is set for 1,500 iterations. The mean lift and the mean drag of the last 100 iterations must each stay within 1e-4 of the previous 100, or the solve is refused as not converged. `residualControl` at 1e-5 can still stop `simpleFoam` earlier. A second solve in the same folder can leave the first `coefficient.dat` in place, and the drift check reads that file. Both limits are left as they are. Mesh limits stay skew 4 and non-orthogonality 65. One solve at a time. The process cap is 1,800 seconds.

## What was measured

These are three different results.

The saved fixture is the older nose, stopped at iteration 600. Replaying it does not start OpenFOAM. Parent Cl 0.8825, Cd 0.0284. Child Cl 0.8559, Cd 0.0280. That pair passed the lift band and the force delta. It is not the current nose.

The current nose, NACA 2412 at 6 degrees and 10 m/s, ran 1,500 iterations in 227 seconds. Lift finished at 0.900 and drag at 0.0269. Drag had settled. Lift was still moving by 0.00146, so the solve was refused. Free-transition NeuralFoil at that point is about Cl 0.899 and Cd 0.010. The finer mesh was not run. This is not a design grade.

NACA 2412 at 3 degrees, same setup, finished at lift 0.594 and drag 0.0207. Lift was still moving by 0.00017, over the 0.0001 limit, so that solve was refused too. NeuralFoil lift there is about 0.607.

## How to run

```bash
cd /path/to/vertical-labs
python3 -m venv .venv
.venv/bin/pip install 'mcp>=1.8,<2' pyshacl pytest 'neuralfoil==0.3.3' 'aerosandbox==4.2.10'
.venv/bin/python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

Stay below mcp 2. This machine uses OpenFOAM v2606 (`openfoam` on `PATH`), Lean, and a venv with mcp 1.30.0, pyshacl 0.40.1, pytest 9.1.1, neuralfoil 0.3.3, and aerosandbox 4.2.10.

The command line prints one line per gate: goal, mesh, solve, then screening, design, or refused. Cases land in `runs/run-…/`. Open `log.checkMesh`, `log.simpleFoam`, and `postProcessing/forceCoeffs/0/coefficient.dat`. `Cd(f)` and `Cd(r)` in that file are front and rear axle, not pressure and viscous. The pressure and viscous split is the `forces` file, and it is context.

```bash
WINGLINE_EXPLORE=0 .venv/bin/python -m wingline "NACA 2412 at 6 degrees."
WINGLINE_AGENT=0   .venv/bin/python -m wingline "NACA 2412 at 6 degrees."
WINGLINE_FIXTURE=1 .venv/bin/python -m wingline "NACA 2412 at 6 degrees."
```

Leaving `WINGLINE_AGENT` unset means the agent drives the case. `0` is the planner, for tests and for neighbor search. It is not a fallback. If the agent stops with the ledger still open, the run is refused. The command line searches four nearby sections. `WINGLINE_EXPLORE=0` skips that. The fixture replays the older pair and does not start OpenFOAM.

The page is the same store:

```bash
.venv/bin/python -m wingline.web
```

Open http://127.0.0.1:8765. A finished study is one paragraph, not the gate lines. The sketch keeps the wing level and draws the wind at the angle. paraFoam opens the case in ParaView: color the slice by speed, and read Ux and Uy for the freestream. The words "NACA 2412" are in `spec.json` on the page, under Case files. Starting the page clears the chat and the `run-*` folders. The cylinder check and the exploration switch stay.

## Where the detail lives

`DESIGN.md` is the contract. `wingline/case_writer.py` is the only file that starts OpenFOAM. `wingline/kernel.py` holds the bounds and is the only minter of a screening or a design grade. The policy files under `harness/` are emitted from that module. A mismatch refuses the run before a case exists.
