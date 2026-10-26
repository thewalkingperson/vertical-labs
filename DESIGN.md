# Wingline

Primitive AI engineer for one question: a NACA 4-digit wing on this Mac, screened by a coarse 2D OpenFOAM run, or refused at a named gate. The human asks in English. The model may only turn typed mesh and solver knobs. OpenFOAM v2606 runs only inside `wingline/case_writer.py`. A report is a projection of a legal receipt ledger plus an outer verdict. The model's stdout is stored and is not evidence.


## What the engineer can do

By hand, a coarse wing study is a pile of dictionaries, a mesh that may be illegal, and a transcript that can quote a lift number after a failed check. This workflow will not print that number unless every prior gate is a green receipt.

```text
python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

Happy path, one line per panel:

```text
goal: NACA 2412 at 6 degrees, 10 m/s, coarse.
meshing: stream 100, grading 8.
mesh accepted: 64000 cells. Log: log.checkMesh
solving: simpleFoam.
screening: Cl=0.8825 Cd=0.0284. This mesh is not a design answer until the refinement gate passes.
```

A bad mesh stops at:

```text
refused: Refused at gate mesh. Log: log.checkMesh
```

There is no `Cl` on a refusal. A second angle (`Same wing at 8 degrees.`) is a new run and the same gates. `refine` runs only when the screening says the finer run is worth it. Its last line is either a design grade or a refusal at `refinement`. Design grade is force agreement between the parent mesh and a 20% chordwise refinement on this wall-modeled Spalart-Allmaras setup. It is not mesh independence and it is not a flight-hardware release. `hardware_decision` accepts only that design grade and raises `TypeError` on a screening.

Another thing the engineer can do that a tutorial `Allrun` does not: hand the same gates a correction (`max_non_ortho` was too loose, a tool ran that is not in the list) and get at most one structural change back, which cannot mark the CFD run as passed.

## Public surface

```python
from pathlib import Path
from wingline import Engineer, hardware_decision

eng = Engineer.open(Path("runs"))
view = eng.ask("NACA 2412 at 6 degrees. Is this worth a finer run?")
# view.outcome is Screening or Refusal
# Screening.grade == "screening"
# Screening.sentence is the fixed screening sentence
# Refusal has gate, log, reason, and no coefficients

graded = eng.refine(view.run_id)          # commission, or Refusal(gate="commission")
decision = hardware_decision(graded.outcome)  # TypeError unless DesignGrade
```

`Engineer.open` takes `runs/engineer.lock`. A second open on that store raises. `ask` returns only after the outer loop has written a verdict. `show`, if passed, receives each panel.

Grammar in `engineer.parse`. Anything else is `Refusal(gate="parse")` and no case directory.

- `NACA 2412 at 6 degrees. Is this worth a finer run?` → 2412, α=6, U=10, coarse, intent finer.
- `NACA 2412 at 6 degrees and 15 m/s.` → U=15.
- `Same wing at 8 degrees.` → last NACA and U, α=8. No previous spec → parse refusal.
- `NACA 2412 at 40 degrees.` → parse refusal (α outside [-2, 10]).
- `NACA 9999 at 6 degrees.` → parse refusal (thickness outside 6..24).

Operator entry, not a method on `Engineer`:

```python
from wingline.meta import correct, propose
propose(Path("runs"))   # no grade field on the return value
```

## Types

Public exports: `Engineer`, `EngineerView`, `Screening`, `DesignGrade`, `Refusal`, `HardwareDecision`, `hardware_decision`.

`GateName` is `pilot | parse | scope | mesh | solve | forces | physics | review | commission | refinement | budget | incomplete | lean | shacl | bounds | agent`.

`Screening.sentence` must equal `Bounds.screening_sentence`. `DesignGrade.sentence` must equal `Bounds.design_sentence`. `DesignGrade.__post_init__` rejects a delta outside `Bounds.delta_cl` / `Bounds.delta_cd`. `hardware_decision` raises `TypeError` unless its argument's type is `DesignGrade`.

`project(parent, child, outer)` is the only function that builds a `Screening` or a `DesignGrade`. No outer verdict → `Refusal(gate="incomplete")`. Physics red → `Refusal(gate="physics")` with no coefficients on the view. Review challenge → `Screening` with `worth_finer` false. Review affirm mints commission. It does not mint design and it is not evidence for `Cl`. The reviewer receives an evidence packet and runs in a scratch directory with no tools and no file access.

`DesignGrade` requires: parent projects to a `Screening` with `worth_finer` true, child density is `refined`, NACA / α / U / chord / span match, both force receipts green, `judge_delta` true, child outer review affirmed.

Panels, and only these tuples:

- mesh refusal: goal, meshing, mesh_refused
- solve refusal: goal, meshing, mesh_accepted, solving, solve_refused
- other refusal: goal, refused (or the prefix up to the failed stage). Never a screening panel.
- screening: goal, meshing, mesh_accepted, solving, screening
- design: that screening tuple plus a design panel with the refined coefficients and the deltas

## Legal prefix table

Stages on a receipt: `pilot`, `scope`, `mesh`, `solve`, `forces`. Status: `green`, `red`, `stopped`. The model does not supply status. `judge_*` does.

`shape(ledger)` returns one of: `empty`, `pilot_red`, `pilot_green`, `scoped`, `mesh_open`, `mesh_green`, `mesh_closed`, `solve_open`, `solve_green`, `solve_closed`, `forces_green`, `forces_red`.

`NEXT` (the only edges):

| shape | allowed next (stage, status) |
|---|---|
| empty | pilot green, pilot red |
| pilot_green | scope green |
| scoped | mesh green, mesh red |
| mesh_open | mesh green, mesh red, mesh stopped |
| mesh_green | solve green, solve red |
| solve_open | solve green, solve red, solve stopped |
| solve_green | forces green, forces red |

`pilot_red`, `mesh_closed`, `solve_closed`, `forces_green`, and `forces_red` have no successor. Scope is green only. A red pilot never gains a wing case receipt beyond the copied pilot receipt.

`mesh_open` means one or two red mesh receipts, budget remaining (3), and the new evidence signature differs from the last red mesh. A repeated signature is not `mesh_open`; the only legal append is `stopped` with why `repeated_mesh`, and the shape becomes `mesh_closed`. The third red mesh with a new signature may only be followed by `stopped` (`budget`). `solve_open` is the same idea with budget 2 and failure `residual`. `fatal`, `wall_clock`, and `not_converged` close the solve immediately.

`admit` returns the existing receipt when `(run, stage, args, parent receipt id)` was already committed, and does not exec. It returns a rejection that writes nothing when the edge is absent. Status on the receipt is the judge, never an argument.

Idempotency key and the signature rule are in `kernel.admit`. `case_writer` calls `admit` under `flock` on `runs/<id>/case.lock`. Ledger append is: write `ledger/<id>.json`, fsync, append one line to `ledger/index.jsonl`, fsync. A pointer whose file is missing is ignored on read.

## Bounds

Single numeric source: `wingline/kernel.py` `Bounds`. `python -m wingline.kernel --emit` writes `harness/shapes/receipt.shacl.ttl` and `harness/gate/Gate.lean`. `Engineer.open` and the outer loop refuse (`gate="bounds"`) when a derived file differs. They do not overwrite it.

| name | value |
|---|---|
| alpha | [-2, 10] deg |
| U | [5, 25] m/s, default 10 |
| chord | default 1 m, allowed 0.01..100. Span defaults to 0.1 m |
| nu | 1.5e-5 |
| Reynolds number | speed * chord / nu, accepted only in 1e5..5e6 |
| rho reference for coefficients | 1 |
| thickness digit | 6..24 |
| n_normal | 160 |
| wall grading scale | 125, so knob 8 becomes blockMesh ratio 1000 |
| farfield | x ∈ [-8c, 12c], y ∈ [-8c, 8c], z ∈ [0, span]. At the defaults this is [-8, 12] × [-8, 8] × [0, 0.1] |
| end_time | 1500 |
| force drift | last 100 iterations against the previous 100, each of Cl and Cd within 1e-4 |
| mesh budget | 3 |
| solve budget | 2 |
| max agent turns | 12 |
| max non-ortho | 65 |
| max skew | 4 |
| local continuity | 1e-4. Cumulative 5e-3 is only a fallback for an old log |
| reference tolerance | max(0.03, 0.05 * abs(Cl_ref)), minimum NeuralFoil confidence 0.90 |
| cylinder cells / continuity / velocity error | 2000 / 5e-4 / 5e-5 |
| delta Cl / Cd | 0.05 / 0.01 |
| timeouts | mesh 90 s, solve 1800 s, pilot 180 s |

Cells: `n_chord = stream * (2 if refined else 1)` with `stream ∈ {60, 80, 100}`. Six hexes give `4 * n_chord * n_normal` cells. The production parent is stream 100 coarse: 64000 cells. The production child is stream 60 refined: 76800 cells, which is 120 chordwise cells. That is a 20% chordwise increase, a local mesh sensitivity, not asymptotic mesh independence. The agent grading knob stays 2, 4, 8, or 12. The writer multiplies it by 125. At grading 8 the first cell over the 8 m farfield height is about 0.000340 m, and it scales with chord. The wall function is `nutUSpaldingWallFunction`, Spalding's blended wall function, valid across the sublayer, buffer, and log region. This is not a y+ below 1 integration. The first chordwise cells are clustered at the nose, ratio 16. The solve is set to `end_time` 1500 and is refused as `not_converged` when the force drift exceeds 1e-4. `residualControl` at 1e-5 can still end `simpleFoam` before that, and the drift check uses the history that was written. A second solve in the same case can leave the first `coefficient.dat` in place; OpenFOAM then writes `coefficient_0.dat`, and the drift check reads the unsuffixed file. y+ min, max, and average are recorded on the solve receipt when the function object writes them. They are not a gate. `max_skew` stays 4. A skew failure is answered with milder grading or more wall-normal cells, not a looser bound. The refinement pair must share section, angle, speed, chord, and span.

One ratio-16 parent, NACA 2412 at 6 degrees and 10 m/s, ran 1500 iterations in 227 seconds. Iteration 600 measured Cl 0.8955 and Cd 0.02785. Iteration 1500 measured Cl 0.9000 and Cd 0.02691. Lift drift was 0.00146 and drag drift was 0.000029, so the solve was refused as `not_converged`. Pressure Cd was 0.0153 and viscous Cd was 0.0117. checkMesh passed: skew 2.58, non-orthogonality 63.8. y+ was about 1.2 to 13.2. This is not a design grade. The fixture ledger remains the previous 600-iteration pair.

## Loops

Inner. `ask` holds `engineer.lock` for the whole call. Order:

1. Diff emitted policy. Mismatch → `bounds`, no case.
2. Parse. Failure → `parse`, no case.
3. Mint `run_id`. Write `runs/<id>/lease` (32 urlsafe bytes, mode `0600`).
4. `perform_pilot()` in `mcp_server.py`. Not a model tool. Reuse `runs/pilot/latest.json` when it is green and the `openfoam` path plus the tutorial `Allrun` hash are unchanged. Red pilot → `Refusal(gate="pilot")`, do not plan a wing.
5. Drive the five tools until the ledger shape is terminal or 12 turns elapse.
6. Ignore model stdout. Load the ledger.
7. Outer loop. Then `project`.

Two drivers, same tools, same `admit`:

- `WINGLINE_AGENT` unset, or any value other than `0`, spawns one headless parent. No subagents. Command flags that exist on this Grok: `--prompt-file`, `--cwd`, `-m grok-4.7`, `--reasoning-effort xhigh`, `--max-turns 12`, `--output-format json`, `--no-subagents`, `--disable-web-search`, `--permission-mode dontAsk`, `--allow wingline__observe`, `--allow wingline__commit_spec`, `--allow wingline__commit_mesh`, `--allow wingline__commit_solve`, `--allow wingline__commit_forces`, `--deny` for Bash, Read, Edit, Write, Grep, WebFetch, WebSearch, Task, and `--disallowed-tools` for `run_terminal_command,write,search_replace,spawn_subagent,web_search,web_fetch,read_file,grep`. `--permission-mode dontAsk` alone is not enough: a headless MCP call still asks for approval unless the exact tool is allowed. MCP stays available because the five tools are allowed. Environment: `WINGLINE_RUN_ID`, `WINGLINE_LEASE`, `WINGLINE_STORE`. The prompt contains the lease, the run id, the parsed spec, and the exact five tool names. It does not restate gate policy. If the agent exits with a non-terminal ledger, the run refuses at gate `agent`; there is no automatic fallback.
- `WINGLINE_AGENT=0` selects `wingline.planner` explicitly. It is a deterministic client for tests and debugging, not a rescue. It is a client, not an authority. The parent's first mesh is stream 100, grading 8. The refined child's first mesh is stream 60, grading 8. On a red mesh it changes one knob: milder grading in that density's table, then stream 80. On `stopped`, `wall_clock`, or `not_converged` it stops. The first solve is `u_relax` 0.5, `non_ortho` 1. A local-residual red may move to 0.5 or 0.3 and then add a non-orthogonal corrector. The server accepts any in-enum plan. The planner cannot append a receipt except through the tools.

Outer. `outer.py` reads the ledger and the case. It does not read the agent transcript. It writes only `runs/<id>/outer/verdict.json`. It does not edit a skill, a bound, a hook, or a case file. First failure wins:

1. Policy diff → `bounds`.
2. `pyshacl` on the derived graph. Non-zero or missing → `shacl`. Do not skip.
3. `lean` on `harness/gate/Gate.lean` with the emitted table. Non-zero or missing → `lean`. Do not skip.
4. Re-judge every receipt with the same `judge_*`. Disagreement refuses at that gate.
5. Inner red or stopped → refused at that gate. No reviewer.
6. `judge_physics`. It compares measured Cl with the NeuralFoil polar. Red → `physics`. A missing install, an out-of-range Reynolds number, or a low-confidence analysis fails closed. There is no second model. Reference Cd is recorded and is not a pass or fail. A forced-transition polar is recorded after the free-transition reference clears the confidence floor, and it is not a pass or fail. `cd_pressure` and `cd_viscous` are recorded and are not a gate. y+ is reported when present and is not required to be below 1. The physics gate stays lift-only. Mesh-to-mesh drag still uses `delta_cd` 0.01.
7. Fresh-context review only if physics is green and `WINGLINE_AGENT` is not `0`. The prompt is the evidence packet: spec, mesh cells and expected cells, local continuity, Cl, Cd, the NeuralFoil reference, its confidence, the tolerance, and any red log tail. The reviewer runs in a scratch directory with no tools and cannot read files. It may affirm only when every receipt is green, the cell count matches, local continuity is inside the bound, Cd is positive, and Cl is inside the NeuralFoil tolerance. Reference Cd is context, not a pass or fail. y+ is reported when present and is not required to be below 1. Otherwise the review step records `challenge`, except the fixture's `outer_affirm.json` path used by tests. A challenge still yields `Screening` with `worth_finer` false. Unparseable reviewer stdout is `challenge`. The evidence and raw answer are saved in `outer/review.json`.

`refine` refuses up front, without a case, unless the parent outcome is a `Screening` with `worth_finer` true. The child is a new run, density `refined`, same NACA, α, U, chord, and span. The planner and the agent then open that child at stream 60, grading 8. They do not keep the parent's stream 100, which would double it to 200 chordwise cells. The parent case is not modified.

Meta. `propose` holds `engineer.lock` between runs. It does not write a ledger, a case, or a verdict. Newest unresolved correction wins. Ship the first template that holds. Do not ship the ones below it.

| order | template | writes | holds when |
|---|---|---|---|
| 1 | BoundTighten | one `Bounds` field, stricter only, then `--emit` | the recorded receipt now fails `judge_*`, and the green fixture still screens |
| 2 | ShaclRequire | one catalog property (`writer`, `parent_id`, `log`) | pyshacl fails the bad receipt and passes the green fixture |
| 3 | DenyTool | the observed tool name outside the five | emitted deny list contains it |
| 4 | LeanGuard | one guard of the failing row | lean still proves the three theorems |
| 5 | ReplayTest | `tests/replay/<id>.py` expecting `Refusal` | that test and the green fixture pass |
| 6 | SkillDraft | `runs/meta/proposals/<id>.md` only | stops for a human. Does not edit `SKILL.md` |

After a template in 1–5, run `perform_pilot`. If it fails, revert the one change and stop. Return type is `Shipped`, `NeedsHuman`, or `Noop`. None has a grade. `correct` only appends `runs/corrections.jsonl`. A looser value matches no template. Meta cannot declare a CFD run passed.

## MCP

Server name `wingline`. FastMCP. Stdio. `.grok/config.toml` points at the repo venv:

```toml
[mcp_servers.wingline]
command = "<repo>/.venv/bin/python"
args = ["-m", "wingline.mcp_server"]
tool_timeout_sec = 120
tool_timeouts = { observe = 10, commit_spec = 30, commit_mesh = 120, commit_solve = 2100, commit_forces = 60 }
```

The solve process cap is 1800 seconds. `commit_solve` is allowed 2100 seconds so the receipt can still be written if the process hits that cap. The other tool allowances stay as written. `commit_mesh` at 120 seconds is longer than the 90 second mesh process cap.

| tool | arguments | effect |
|---|---|---|
| observe | run_id | open stage, budgets, last receipt, log tail of at most 40 lines if the last receipt is red |
| commit_spec | run_id, lease | renders the harness spec. No NACA argument |
| commit_mesh | run_id, lease, stream, grading | enums only. blockMesh and checkMesh |
| commit_solve | run_id, lease, u_relax, non_ortho | re-renders fvSolution and runs simpleFoam |
| commit_forces | run_id, lease | parses coefficient.dat. Fixed postProcess snippet only if that file is missing |

Lease compare is `hmac.compare_digest`. A bad lease writes nothing. `run_id` that escapes the store is a tool error. `perform_pilot()` is not a tool.

Each tool appends `{ts, tool, run_id, decision, receipt_id}` to `runs/observe.jsonl` under the lock.

## Hooks and skills

Hooks are defense in depth. The server still judges. Project hooks do not run until this folder is trusted (`grok --trust` or `/hooks-trust`). A headless demo that has not been trusted must still refuse illegal receipts.

`.grok/hooks/deny-unlisted.json`, `PreToolUse`, matcher `Bash|Read|Edit|Write|Grep|WebFetch|WebSearch|Task`. Command form with `args` so there is no shell. Stdout `{"decision":"deny","reason":"wingline: no shell and no case edits"}`. A second matcher denies any `wingline__` tool that is not one of the five.

`.grok/hooks/trim-receipt.json`, `PostToolUse`, matcher `wingline__.*`. Replaces the model-facing copy with the receipt JSON (`updatedMCPToolOutput`). Exit 0. Does not decide green.

`.grok/hooks/stop-ledger.json`, `Stop`. If `stopHookActive` is true, print nothing. If the ledger has no terminal inner receipt, print `{"decision":"block","reason":"inner loop has no terminal receipt"}`. A Stop that fails open still cannot screen: `ask` projects the ledger after the process exits, and no outer verdict means `incomplete`.

Skills, policy the server enforces is not restated as a second authority:

- `.grok/skills/screen-wing/SKILL.md` — inner parent. `observe`, then the one commit for the open stage. Pass the lease unchanged. First knobs as the planner. Stop when `observe` says terminal. Do not paraphrase `Cl` into a hardware opinion.
- `.grok/skills/review-case/SKILL.md` — scratch review workspace. One JSON object `{"verdict":"affirm"|"challenge","gate":"...","note":"..."}`. Cannot pass a run and cannot edit a gate. Reference Cd is context. y+ is not required to be below 1.
- `.grok/skills/ask-engineer/SKILL.md` — interview before a case exists. NACA 4-digit only. Ask for the angle. Do not choose an angle. Do not start a solve.
- `.grok/skills/meta-diagnose/SKILL.md` — scratch draft for template 6. One skill sentence. Do not declare CFD passed. Do not edit a file.

## Receipts

`runs/<id>/ledger/<receipt_id>.json` plus `index.jsonl`. `outer/graph.jsonld` is derived. `@vocab` is `https://vertical-labs.local/wingline#`.

Required on every receipt: `id`, `type`, `run_id`, `stage`, `status`, `parent_id` (null only on the first), `spec_hash`, `log`, `writer` = `case-writer`, `written_at`. No `grade` field. `sh:closed true` makes a `grade` property fail.

Types: `PilotReceipt`, `ScopeReceipt`, `MeshReceipt`, `SolveReceipt`, `ForcesReceipt`, `StopReceipt`. The closed shape allows `stream`, `grading`, `cells`, `cells_expected`, `max_non_ortho`, `max_skew`, `signature`, `n_normal`, `wall_grading`, `first_cell_m`, `u_relax`, `non_ortho`, `continuity_cum`, `continuity_local`, `continuity_global`, `nu`, `reynolds`, `turbulence_model`, `reference_model`, `end_time`, `yplus_min`, `yplus_max`, `yplus_avg`, `force_drift_cl`, `force_drift_cd`, `force_iterations`, `cd_pressure`, `cd_viscous`, `failure`, `cl`, and `cd`. Pilot receipts use `cells`, `continuity`, and `velocity_error`. Scope receipts may carry `naca`, `alpha_deg`, `u_ms`, `density`, `chord_m`, `span_m`, `camber`, `camber_pos`, `thickness`, and `pilot_id`. The y+, force-drift, and drag-split properties are optional. A receipt without them still conforms.

pySHACL command: `pyshacl -s harness/shapes/receipt.shacl.ttl -df json-ld <graph.jsonld>`. The shape file is emitted. It includes:

- writer is `case-writer`
- status in green / red / stopped
- stream in 60 / 80 / 100, grading in 2 / 4 / 8 / 12, on mesh receipts
- `Cd > 0` on forces receipts
- a SPARQL constraint: a `ForcesReceipt` with status green in a run that also contains red or stopped fails
- a SPARQL or datatype constraint that a refinement delta record with `abs(delta_cl) > 0.05` or `abs(delta_cd) > 0.01` fails

Lean file states three theorems over the emitted table, proved by cases, not by adding a constructor to make a goal true:

- a screening or design label is unreachable if any prior gate is red or stopped
- design requires refinement green, delta inside the bound, and review affirm
- a challenge review is not design

If `lean` or `pyshacl` is not on `PATH` and not in the venv, the outer loop returns that gate's refusal. Tests cover the refusal. Tests that need the tool run when the tool is installed.

## OpenFOAM

Launcher: `openfoam` on `PATH` (`/opt/homebrew/bin/openfoam`, app `/Applications/OpenFOAM-v2606.app`). Prefix command is `["openfoam", "-c", snippet]` with `start_new_session=True`. No `shell=True`. On timeout, kill the process group. Snippets are fixed strings in `run_foam`. No model text is concatenated into a dictionary or a snippet.

```text
mesh:   blockMesh > log.blockMesh 2>&1 && checkMesh > log.checkMesh 2>&1
solve:  simpleFoam > log.simpleFoam 2>&1
forces: postProcess -func forceCoeffs > log.forceCoeffs 2>&1
pilot:  rm -rf case && cp -R "$FOAM_TUTORIALS/basic/potentialFoam/cylinder" case && cd case && ./Allrun
```

`checkMesh` must report `Mesh OK` and `nCells` equal to `cell_count`. The cylinder pilot is green only when `judge_pilot` matches the smoke bounds. The smoke on this Mac printed `nCells: 2000`, `Continuity error = 0.000135471`, `Interpolated velocity error = 1.18729e-05`.

Case: `simpleFoam`, RAS Spalart–Allmaras, Newtonian `nu 1.5e-5`, `endTime 1500`, `deltaT 1`. Volume fields use `writeInterval 20` and `purgeWrite 10`. `forceCoeffs` still uses `writeInterval 1`. A `forces` function object, `libs (forces)`, and a `yPlus` function object, `libs (fieldFunctionObjects)`, both write at `writeInterval 1500`. `yPlus` sets `writeFields false`. `paraFoam -vtk` opens that case. No `decomposePar`, no `snappyHexMesh`. Do not copy `airFoil2D`'s `polyMesh` and do not run its `Allrun` (`parallel=true`). The cylinder pilot stays the potentialFoam tutorial copy. It is not an SA wing.

Schemes: `ddt steadyState`; `grad Gauss linear`; `div(phi,U) bounded Gauss linearUpwind grad(U)`; `div(phi,nuTilda) bounded Gauss linearUpwind grad(nuTilda)`; `div((nuEff*dev2(T(grad(U))))) Gauss linear`; laplacian `Gauss linear corrected`; `wallDist meshWave`.

`fvSolution`: GAMG on `p` (tolerance 1e-6, relTol 0.1), smoothSolver GaussSeidel on `U` and `nuTilda` (tolerance 1e-8, relTol 0.1, nSweeps 2). SIMPLE residual control on `p`, `U`, and `nuTilda` is 1e-5. `nNonOrthogonalCorrectors` from the plan. Relaxation `p` 0.3, `U` from the plan, `nuTilda` 0.7. The production opening plan is `u_relax` 0.5 and one non-orthogonal corrector.

`0/U`: internalField `(U cos α, U sin α, 0)`. `farfield` and `outlet` `freestreamVelocity`. `walls` `noSlip`. `frontAndBack` `empty`. `0/p`: uniform 0, farfield and outlet `freestreamPressure`, walls `zeroGradient`. `0/nuTilda`: internal `3*nu`, walls `fixedValue 0`, farfield and outlet freestream. `0/nut`: internal 0, walls `nutUSpaldingWallFunction`.

`forceCoeffs` in `controlDict`: `libs (forces)`, `patches (walls)`, `rho rhoInf`, `rhoInf 1`, `CofR (0.25 0 0)`, `pitchAxis (0 0 1)`, `lRef 1`, `Aref 0.1`, `magUInf` from the spec, `liftDir (-sin α, cos α, 0)`, `dragDir (cos α, sin α, 0)` with α in radians. Parser reads `postProcessing/forceCoeffs/0/coefficient.dat`, columns by the header tokens `Cl` and `Cd`, last data row. Never by a fixed column index.

NACA ordinates, closed trailing edge (coefficient `-0.1036`):

```text
x = (1 - cos(π i / n)) / 2
yt = 5 t (0.2969 √x - 0.1260 x - 0.3516 x² + 0.2843 x³ - 0.1036 x⁴)
```

`n` for the polyLine is 80. Cell count does not follow `n`. Standard 4-digit camber; θ = atan(dyc/dx); upper `(x - yt sin θ, yc + yt cos θ)`; lower the minus. `t` is the fraction (0.12 for 2412).

Mesh: six hexes, one vertex list, no `mergePatchPairs`. BlockMesh allows one edge between a given vertex pair, so the upper and lower surfaces must not both be a single edge from the leading edge to the trailing edge. Split each surface at the crown (the polyline sample nearest maximum thickness). Four airfoil edges, four vertex pairs: leading-edge to crown-upper, crown-upper to trailing-edge, leading-edge to crown-lower, crown-lower to trailing-edge. The trailing edge is one shared vertex. The wake centerline from that vertex to the outlet mid-height is one straight edge, shared by the upper and lower wake blocks, and it is not listed twice.

Cell counts: `n_chord` is `stream` on a coarse mesh and `2 * stream` on a refined mesh (same rule as `kernel.cell_count`). `n_lead = n_chord // 2`, `n_trail = n_chord - n_lead`, `n_wake = n_chord`, `n_normal = 160`. Cells = `2 * (n_lead + n_trail + n_wake) * n_normal`, which equals `4 * n_chord * n_normal` (64000 at stream 100 coarse, 76800 at stream 60 refined). One cell from `z = 0` to `z = 0.1`. Wall-normal `simpleGrading` is the agent knob times 125. Nose ratios are 16 on the upper lead block and 1/16 on the lower lead block, so the small chordwise cells sit at the leading edge.

Corners: nose `(-8, 0)`, top `y = 8`, bottom `y = -8`, outlet `x = 12`. Straight edges on the farfield (no arcs). Angle of attack stays on the freestream, not on the points. `simpleGrading` clusters cells toward the airfoil. Patches: `farfield` (nose, top, and bottom), `outlet` (`x = 12`), `walls` (the four airfoil faces only), `frontAndBack` empty. If `checkMesh` reports inverted cells, flip that block once. A remaining failure is a red mesh receipt, not a rewritten cell formula.

## Fixture

`fixtures/naca2412-a6/`. Offline. `WINGLINE_FIXTURE=1` makes `ask` and `perform_pilot` replay these files. It does not start Grok and does not call `openfoam`.

| file | contract |
|---|---|
| ask.txt | `NACA 2412 at 6 degrees. Is this worth a finer run?` |
| again.txt | `Same wing at 8 degrees.` |
| spec.json | naca 2412, alpha 6, u 10, density coarse |
| mesh_plan.json | stream 100, grading 8, cells 64000 |
| solve_plan.json | u_relax 0.5, non_ortho 1 |
| sentence.txt | screening sentence, exact |
| design_sentence.txt | design sentence, exact |
| ledger_green/ | pilot, scope, mesh, solve, forces, all green. Cl 0.882458973, Cd 0.0284136587 |
| ledger_mesh_red/ | scope green, mesh red (`max_non_ortho` 80), no forces file |
| ledger_design/ | parent Cl 0.882458973 Cd 0.0284136587; child stream 60 refined, Cl 0.855949564 Cd 0.0280312507 |
| ledger_wide/ | child Cl 0.97 against parent 0.882458973, outside delta_cl 0.05 |
| cylinder_log.txt | the three smoke lines |
| outer_affirm.json | physics green, review affirm |
| outer_challenge.json | physics green, review challenge |

`tests/test_project_2412.py` asserts: green + affirm → `Screening`, exact sentence, `worth_finer` true, and `hardware_decision` on that screening raises `TypeError`. Green + challenge → `worth_finer` false. Mesh red → `Refusal(gate="mesh")`, no screening panel. Wide delta → not a `DesignGrade`. Missing outer file → `Refusal(gate="incomplete")`. `judge_delta(0.05, 0.01)` is true. `judge_delta(0.051, 0.0)` is false. A forces-green ledger with an earlier red mesh is rejected by `admit` and by pySHACL.

`tests/test_openfoam_boundary.py` fails if the substring `openfoam` appears outside `case_writer.py`. `tests/test_import_boundary.py` fails if anything other than `mcp_server.py` imports `case_writer`. `meta.py` must not name `DesignGrade`.

`tests/test_live_2412.py` is skipped unless `WINGLINE_LIVE=1`. It runs the planner (not a nested Grok) on `ask.txt` and asserts the outcome is a `Screening` or a `Refusal` whose `gate` is one of `mesh`, `solve`, `forces`, `physics`, `pilot` and whose `log` names a real file under the run.

## Workbench

`python -m wingline.web` serves `http://127.0.0.1:8765` (`WINGLINE_WEB_PORT`). Starting it calls `clear_session`, which deletes the chat, `last_spec.json`, `observe.jsonl`, `meta/`, and every `run-*` directory. It keeps the cylinder health cache and `runs/explore.json`. Reset clears the transcript only. An open question stays, and a running study keeps going.

A finished direct ask is one engineer paragraph taken from the receipts. When that paragraph has lift and drag, and `WINGLINE_AGENT` is not `0`, the closing seat (`SUGGEST_SYSTEM`) adds one or two sentences. The prompt limits those sentences to another NACA 4-digit section or an angle inside the bounds. The sentences are not a gate. A missing `grok` binary or a failed call leaves the receipt unchanged. A commission refusal does not ask for advice. The inner loop still may not propose another wing; its text is not the page.

The side picture reads the mesh and the solved fields. The section stays level. The angle is the freestream. That picture does not launch ParaView. A finished direct ask opens the asked case once with `paraFoam -vtk`. The button posts the same opener. Run the finer mesh is available only when `worth_finer` is true.

Constant exploration is on unless `runs/explore.json` says otherwise. When that file is absent, `WINGLINE_EXPLORE=0` starts the page with the switch off. When the switch is on, the page solves the asked wing, then up to 10 neighbors with the tool client (`WINGLINE_AGENT=0` only around those neighbor asks). Neighbors are stored and do not add chat lines. The command line still stops at 4 (`EXPLORE_BUDGET`). The asked wing stays first.

## Modules

```text
wingline/__init__.py
wingline/kernel.py          Bounds, NEXT, admit, project, judge_*, emit
wingline/reference.py       NeuralFoil polar. Lazy import. No wingline imports
wingline/case_writer.py     render, parsers, flock, ledger io, run_foam
wingline/mcp_server.py      five tools and perform_pilot. Only importer of case_writer
wingline/engineer.py        open, ask, refine, parse, locks
wingline/planner.py         knob policy. Calls the same tool functions
wingline/outer.py           shacl, lean, re-judge, review, verdict
wingline/meta.py            correct, propose, templates
wingline/files.py           read-only views of one run
wingline/steer.py           messages sent while a run is open
wingline/grok_call.py       one headless Grok call
wingline/prompts.py         system prompts for meta, the inner loop, review, and the closing note
wingline/suggest.py         closing seat. Calls grok after the receipt. Not a gate
wingline/picture.py         mesh and force picture. Does not launch ParaView
wingline/web.py             local workbench on 127.0.0.1:8765
web/                        the workbench page
.grok/config.toml
.grok/hooks/
.grok/skills/
harness/gate/Gate.lean
harness/shapes/receipt.shacl.ttl
fixtures/naca2412-a6/
tests/
```

| path | writer | everyone else |
|---|---|---|
| runs/engineer.lock | engineer.py | — |
| runs/<id>/lease | engineer.py creates | case_writer reads |
| runs/pilot/ | case_writer | read |
| runs/<id>/case/ | case_writer | read |
| runs/<id>/ledger/ | case_writer | read |
| runs/observe.jsonl | case_writer | meta reads |
| runs/<id>/outer/ | outer.py | project reads |
| runs/corrections.jsonl | meta.correct | propose reads |
| runs/meta/ | meta.propose | human reads |
| runs/explore.json | web.py | read on workbench start |

## Build order

Each unit is green before the next starts.

1. `kernel.py`, fixtures, `tests/test_project_2412.py`, emit of the shape and the Lean file. No OpenFOAM.
2. `case_writer.py` parsers against the cylinder snippet and a scrambled coefficient header. Import and `openfoam` boundary tests.
3. Render one NACA 2412 coarse mesh and run `checkMesh`. Keep the cell formula. Fix vertex order if cells are inverted.
4. MCP server, planner, engineer `ask` under `WINGLINE_FIXTURE=1`.
5. Outer loop, including the refusal when a tool is missing, and meta templates that do not mint a grade.
6. Hooks, skills, `.grok/config.toml`, README with the customer command.
7. `WINGLINE_LIVE=1` planner run. Record the screening or the named refusal and the log path. Do not loosen a bound to turn a red gate green.
