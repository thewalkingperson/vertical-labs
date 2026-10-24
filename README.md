# Wingline

Wingline is an agent harness for one engineering question: is this wing shape worth a finer airflow simulation?

You ask in a sentence. Three loops handle it.

The meta loop asks you for a wing or an angle you left out, and it tells you when it assumes 10 m/s. The inner loop is the agent: it iterates the mesh and solver tools until that one case is finished. The outer loop grades the case, prints that direct result, then runs a small search of nearby camber and thickness to see whether another coarse design measures better.

Each step of a case has to pass before the next one starts. The lines you see are a projection of those pass/fail receipts. Anything the agent prints on its own is saved, and it is not the evidence.

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

1. **Meta reads the sentence.** A complete wing, such as `NACA 2412 at 6 degrees`, goes straight through. If the section or the angle is missing, Grok interviews you in its own words, under the Wingline system prompt. A code it settles on still has to pass the wing bounds before a case starts. The questions and answers are appended to `runs/meta/dialogue.jsonl`.
2. **Check the policy files.** `harness/shapes/receipt.shacl.ttl` and `harness/gate/Gate.lean` must match the numbers in `wingline/kernel.py`. A mismatch stops at `bounds`.
3. **Health-check OpenFOAM.** A stock cylinder tutorial must report 2000 cells, with continuity and velocity error inside a fixed bound. A green result is reused on the next question.
4. **Inner loop, one wing.** A Grok session is the driver. It may look at the run, commit the spec, commit a mesh, commit a solve, and commit the force coefficients, then observe again and repeat until the ledger is finished. It holds a lease for that run. A bad lease writes nothing. The skill is `.grok/skills/screen-wing/SKILL.md`. The session is started with `--trust`, so the project skill, the hooks, and the wingline MCP server load. Eight turns is the cap. If the agent stops with the ledger still open, the same tools finish that one case. `runs/<id>/inner.json` records which driver finished it.
5. **Run OpenFOAM in one place.** Only `wingline/case_writer.py` starts `openfoam`. The commands are fixed: `blockMesh` and `checkMesh`, then `simpleFoam`, then `postProcess -func forceCoeffs`. One case is written at a time.
6. **Admit a receipt, or refuse it.** The legal order is cylinder, spec, mesh, solve, forces. The checker sets green or red from the log. The agent does not get to choose the status. A green force receipt after an earlier failure is rejected.
7. **Outer grades the direct case.** `wingline/outer.py` reads the ledger and the case files. It runs pySHACL, runs Lean, re-reads every log with the same judges, then compares lift with the thin-airfoil estimate. The first failure wins. It does not read the agent transcript. `kernel.project` is the only function that builds a screening or a design grade. The command prints those panels before any search starts.
8. **Outer searches nearby designs.** After the direct panels, it runs up to four more coarse cases: thickness 2 counts thinner, thickness 2 counts thicker, camber one digit up, camber one digit down. The angle you named stays fixed. The cases run one at a time through the same gates. The tool client runs these neighbors. The search ranks them on your aim: lower drag, higher lift, or, when you did not name an aim, higher lift over drag. A neighbor that measures better is reported with its gate. That report is not a hardware decision. `WINGLINE_EXPLORE=0` skips the search.

`WINGLINE_AGENT=0` keeps the agent out of the direct case too, and the tool client runs it. The command you type uses the agent.

## Three loops

**Inner.** The agent, its skill, the hooks, and the five MCP tools. It iterates until this one wing's ledger is terminal. It cannot mark its own work correct, and it does not wander off into other wings.

**Outer.** Grades the direct case, then keeps testing. The neighborhood above is one step of a coordinate search over camber and thickness. It writes `outer/verdict.json` for the grade and `outer/search.json` for the probes. It does not edit a bound, a skill, or a hook. If pySHACL or Lean is missing, that gate refuses. The checker does not skip ahead.

**Meta.** When the wing or the angle is missing, Grok reasons about the request and asks you. The system prompt is the coarse laminar study, the bounds, and the rule that a screening is not a hardware release. Between runs, `wingline.meta.propose` can still ship one structural fix: a stricter bound, a required receipt field, a denied tool, a Lean guard, a replay test, or a skill draft for a human to copy. A looser number is ignored. After a code change, the cylinder check still has to pass, or the change is reverted. This pass cannot declare the wing run successful.

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

The command above uses the agent for the wing you named, prints that result, then searches four nearby sections. Skip the search:

```bash
WINGLINE_EXPLORE=0 \
  .venv/bin/python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

Run the direct case with the tool client instead of the agent:

```bash
WINGLINE_AGENT=0 \
  .venv/bin/python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

A request with a goal and no section stops so Grok can ask. Answer in the same terminal:

```text
.venv/bin/python -m wingline "a wing with low drag at 10 degrees"
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

## Workbench

A local page for the same store. The message box stays open. If a study is idle, Send starts it. If meta is waiting, Send answers the question. If a run is open, Send steers the agent: the text is written onto that run and the next tool turn sees it. `stop` ends the tool loop before the next OpenFOAM command. `grading 12` or `stream 80` during meshing, and `relax 0.5` or `non-ortho 1` during solving, are applied when they are in the allowed set.

The run list is every `run-*` directory. Click a run, then a file, to read the spec, the mesh and solver logs, the force file, or a dictionary. The chips above the text are the parsed cells, mesh check, continuity, Cl, Cd, and dimensions.

```bash
.venv/bin/python -m wingline.web
```

Open http://127.0.0.1:8765. The page binds to this machine only. `WINGLINE_STORE` chooses the folder, and `WINGLINE_WEB_PORT` chooses the port. One workbench holds the store lock, so close a command-line `python -m wingline` session before starting it. Starting the page clears the chat and the `run-*` case files in that store. The cylinder health cache is kept. Reset, beside Send, clears the transcript without stopping a study that is already running. An open question stays on screen so you can still answer it.

The tests do not start a new solve:

```bash
.venv/bin/pytest tests -q --disable-warnings
```

## Where the code lives

| path | job |
|---|---|
| `wingline/__main__.py` | the command, and the panel lines |
| `wingline/engineer.py` | parse, the store lock, and which driver runs |
| `wingline/planner.py` | the tool client that finishes a case, and that runs the outer search |
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
