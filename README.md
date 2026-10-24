# Wingline

Wingline is an agent harness for one engineering question: is this wing shape worth a finer airflow simulation?

You ask in a sentence. The program builds a mesh, runs a coarse 2D OpenFOAM case, and reads lift and drag from the solver log. Each step has to pass before the next one starts. The lines you see are a projection of those pass/fail receipts. Anything the driver prints on its own is saved, and it is not the evidence.

The full contract is `DESIGN.md`. This page is how the project works and how to run it.

## What you ask

```text
NACA 2412 at 6 degrees. Is this worth a finer run?
```

NACA 2412 is a standard wing section. Six degrees is the angle into the wind. If you leave the speed out, it uses 10 m/s and a 1 m chord. The first mesh is coarse.

A few more sentences the parser accepts:

- `NACA 2412 at 6 degrees and 15 m/s.` sets the speed. Speed must be from 5 to 25 m/s.
- `Same wing at 8 degrees.` reuses the last wing and speed stored for that run folder, and changes the angle.

Angle must be from -2 to 10 degrees. The thickness digit must be from 6 to 20, so `NACA 2412` is legal and `NACA 9999` is not. Anything outside those limits stops at the `parse` gate, and no case folder is created.

## What you get back

On this Mac the example question produced:

```text
goal: NACA 2412 at 6 degrees, 10 m/s, coarse.
meshing: stream 60, grading 8.
mesh accepted: 9600 cells. Log: log.checkMesh
solving: simpleFoam.
refused: Refused at gate thin_airfoil. Log: postProcessing/forceCoeffs/0/coefficient.dat
```

Read that from the top. The cylinder health check passed, the mesh passed, and the solver passed. The lift in the coefficient file was about 0.23. A simple textbook estimate for this wing at 6 degrees is about 0.89, and the allowed gap is 0.31. The run stopped there. The lift number stays in the log. The printed answer leaves it out.

Each line is a panel:

| line | meaning |
|---|---|
| `goal` | the wing, angle, speed, and mesh density that were accepted |
| `meshing` | the mesh settings that were actually run |
| `mesh accepted` | checkMesh passed, with the cell count and the log to open |
| `solving` | `simpleFoam` ran |
| `screening` | every physics check passed; this mesh is still not a design |
| `design` | a finer rerun stayed inside the force-delta bound |
| `refused` | the named gate failed, and the log to open |

A screening, when the physics checks pass and the review agrees, includes this sentence:

```text
This mesh is not a design answer until the refinement gate passes.
```

That is permission to consider a finer mesh. A design grade needs a second run of the same wing, on a refined mesh, whose lift and drag stay within 0.05 and 0.01 of the first run. Its sentence is:

```text
Design grade is mesh agreement within the force-delta bound on this laminar setup. It is not a flight-hardware release.
```

`hardware_decision` accepts only that design grade. A screening and a refusal both raise `TypeError`.

The live wing on this Mac stopped at `thin_airfoil`. The screening numbers in `DESIGN.md` (`Cl=0.8600`, `Cd=0.0200`) come from the saved fixture, which the tests replay. They are not what this Mac printed.

## How a run works

1. **Parse the sentence.** The result is a small spec: wing code, angle, speed, coarse or refined.
2. **Check the policy files.** `harness/shapes/receipt.shacl.ttl` and `harness/gate/Gate.lean` must match the numbers in `wingline/kernel.py`. A mismatch stops at `bounds`.
3. **Health-check OpenFOAM.** A stock cylinder tutorial must report 2000 cells, with continuity and velocity error inside a fixed bound. A green result is reused on the next question.
4. **Hand the driver five tools.** It may look at the run, commit the spec, commit a mesh, commit a solve, and commit the force coefficients. It holds a lease for that run. A bad lease writes nothing.
5. **Run OpenFOAM in one place.** Only `wingline/case_writer.py` starts `openfoam`. The commands are fixed: `blockMesh` and `checkMesh`, then `simpleFoam`, then `postProcess -func forceCoeffs`. One case is written at a time.
6. **Admit a receipt, or refuse it.** The legal order is cylinder, spec, mesh, solve, forces. The checker sets green or red from the log. The driver does not get to choose the status. A green force receipt after an earlier failure is rejected.
7. **Grade from the outside.** After the driver stops, `wingline/outer.py` reads the ledger and the case files. It runs pySHACL, runs Lean, re-reads every log with the same judges, then compares lift with the thin-airfoil estimate. The first failure wins. It does not read the driver's transcript.
8. **Project the answer.** `kernel.project` is the only function that builds a screening or a design grade. The command prints those panels.

The default driver is `wingline/planner.py`. It is a client of the same tools. The first mesh is stream 60 and grading 8. A red mesh changes one knob at a time. The first solve uses under-relaxation 0.7. A red residual may try a smaller value. Eight turns is the cap.

`WINGLINE_AGENT=1` puts one Grok session in that seat. It sees the same five tools and the same receipt rules. The run recorded above used the planner.

## Three loops

**Inner.** The planner, or the Grok session, turns the mesh and solver knobs until the ledger is finished or it runs out of turns. It cannot mark its own work correct.

**Outer.** The checker writes `outer/verdict.json` and nothing else. It does not edit a case, a bound, a skill, or a hook. If pySHACL or Lean is missing, that gate refuses. The checker does not skip ahead.

**Meta.** Between runs, `wingline.meta.propose` can ship one structural fix: a stricter bound, a required receipt field, a denied tool, a Lean guard, a replay test, or a skill draft for a human to copy. A looser number is ignored. After a code change, the cylinder check still has to pass, or the change is reverted. This pass cannot declare the wing run successful.

## The setup this harness simulates

The solver is laminar `simpleFoam`, serial, end time 200. Viscosity is `1e-2`, so 10 m/s and a 1 m chord is Reynolds number 1000. The mesh is one cell thick (0.1 m). Reference density is 1 and reference area is 0.1, so the reported coefficient is per unit span. A coarse mesh at stream 60 has 9600 cells. The far field runs from x = -8 to x = 12 and y = -8 to y = 8.

That is a screening setup. The design sentence says it is not a release for flight hardware.

On the live NACA 2412 case the coefficient file showed about Cl 0.23 and Cd 0.14, after a mesh that `checkMesh` accepted and a solve whose cumulative continuity was about 0.002. The thin-airfoil gate refused that lift. Leaving the band where it is keeps that refusal honest.

## Set up

This machine already has the pieces the checker calls:

- `openfoam` on `PATH` (OpenFOAM v2606)
- Lean at `/Users/saarthakbhatia/.elan/bin/lean` (the checker uses this path)
- pyshacl at `.venv/bin/pyshacl`

The virtualenv is not in git. From a fresh clone on this Mac:

```bash
cd /Users/saarthakbhatia/Documents/vertical-labs
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/pip install 'mcp>=1.8,<2' pyshacl pytest
```

Stay below `mcp` 2. This project imports `mcp.server.fastmcp`, and mcp 2 renamed that class. The venv used here has mcp 1.30.0, pyshacl 0.40.1, and pytest 9.1.1.

## Run it

Ask the question:

```bash
cd /Users/saarthakbhatia/Documents/vertical-labs
.venv/bin/python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

The case, the logs, and the receipt ledger are written under `runs/run-…/`. `runs/` is gitignored. Open `runs/<id>/case/log.checkMesh` for the mesh, `log.simpleFoam` for the solver, and `postProcessing/forceCoeffs/0/coefficient.dat` for the coefficients. The outer verdict is `runs/<id>/outer/verdict.json`.

Put the store somewhere else:

```bash
WINGLINE_STORE=/tmp/wingline-runs \
  .venv/bin/python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

Run one question at a time. A second command against the same store raises, because `Engineer.open` holds `runs/engineer.lock` until the process exits. The first cylinder run can pause while OpenFOAM compiles a small library. Later questions reuse a green cylinder result. On this Mac the coarse solve itself finished in a couple of seconds. The solve budget allows up to 540 seconds, so leave the command running.

Replay the saved fixture. This does not start OpenFOAM or Grok, and it prints a screening:

```bash
WINGLINE_FIXTURE=1 \
  .venv/bin/python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

Put a Grok session in the driver's seat. `grok` must be on your `PATH`. It gets the same tools and the same gates:

```bash
WINGLINE_AGENT=1 \
  .venv/bin/python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

From Python, a finer run and a hardware decision look like this:

```python
from pathlib import Path
from wingline import Engineer, hardware_decision

eng = Engineer.open(Path("runs"))
view = eng.ask("NACA 2412 at 6 degrees. Is this worth a finer run?")
graded = eng.refine(view.run_id)
decision = hardware_decision(graded.outcome)
```

`refine` creates a new run only when the parent outcome is a screening that says the finer run is worth it. Otherwise the gate is `commission` and no case is written. `hardware_decision` returns a decision only for a `DesignGrade`.

The tests do not start a new solve:

```bash
.venv/bin/pytest tests -q --disable-warnings
```

## Where the code lives

| path | job |
|---|---|
| `wingline/__main__.py` | the command, and the panel lines |
| `wingline/engineer.py` | parse, the store lock, and which driver runs |
| `wingline/planner.py` | the default driver |
| `wingline/mcp_server.py` | the five tools, plus the cylinder health check |
| `wingline/case_writer.py` | the only process that starts OpenFOAM |
| `wingline/kernel.py` | bounds, the legal receipt order, judges, and the projection |
| `wingline/outer.py` | the checker |
| `wingline/meta.py` | one between-run fix |
| `harness/gate/Gate.lean` | the emitted proof of the receipt rules |
| `harness/shapes/receipt.shacl.ttl` | the emitted shape of a legal ledger |
| `fixtures/naca2412-a6/` | the offline wing used by the tests |
| `brief/` | the take-home brief |
| `DESIGN.md` | the full contract |

Regenerate the Lean file and the SHACL file from the bounds with:

```bash
.venv/bin/python -m wingline.kernel --emit
```

A normal question does not do this. If those files drift from the code, the next question stops at `bounds`.
