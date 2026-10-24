# Wingline

Primitive AI engineer for one question: a NACA 4-digit wing on this Mac, screened by a coarse 2D OpenFOAM run, or refused at a named gate. The human asks in English. The model may only turn typed mesh and solver knobs. OpenFOAM v2606 runs only inside `wingline/case_writer.py`. A report is a projection of a legal receipt ledger plus an outer verdict. The model's stdout is stored and is not evidence.

## Synthesis

Base is candidate 2 (Wingline), from the arena at `/tmp/arena-vertical-labs/`. Cross-judge (`/tmp/arena-vertical-labs/judge.md`) and the parent agreed on that base. Scores tied 9–9. Candidate 2 is the base because the brief requires the agent to plan inside a gate, and only this design gives the model `MeshPlan` and `SolvePlan` enums. Candidate 1's inner loop is `follow next_gate` on a frozen mesh.

Grafts from candidate 1, folded into this model:

1. One closed legal-prefix table. `admit` appends a receipt only when `(stage, status)` is in `NEXT[shape(ledger)]`. `project` and the Lean file are derived from that table. A green forces receipt after a red or stopped prefix is not a row. Design grade exists only when the refinement run is green and the force delta is inside the bound. A screening cannot be passed to `hardware_decision`.
2. Ledger pySHACL. A green report with an earlier red or stopped sibling fails. Force-delta numbers are shape constraints emitted from `Bounds`, not comments.

Rejected: candidate 1's fixed C-grid and Spalart–Allmaras recipe, storing `grade` on a receipt, and any gate that lives only in a hook. Hooks fail open on this Grok, and a child does not reliably inherit them.

Two numbers differ from candidate 2's draft because the live case would otherwise be unable to screen honestly:

- `nu = 1e-2`. Default `U = 10` m/s and chord `1` m then give chord Reynolds number 1000. Candidate 2's `nu = 1.5e-5` is flight air at Re ≈ 6.7×10⁵, which a laminar `simpleFoam` setup does not represent. The stock `airFoil2D` tutorial is Spalart–Allmaras at about 26 m/s; that recipe was rejected. The design sentence still says this is not a flight-hardware release.
- `rhoInf = 1`, `Aref = 0.1`. The mesh is one cell thick, `z = 0.1`. Incompressible `forceCoeffs` treats `p` as kinematic. With `rhoInf` equal to the coefficient density, `Aref = chord * thickness` makes `Cl` a per-unit-span coefficient. `Aref = 1` would shrink `Cl` by 10× and the thin-airfoil gate would refuse a correct integral.

## What the engineer can do

By hand, a coarse wing study is a pile of dictionaries, a mesh that may be illegal, and a transcript that can quote a lift number after a failed check. This workflow will not print that number unless every prior gate is a green receipt.

```text
python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

Happy path, one line per panel:

```text
goal: NACA 2412 at 6 degrees, 10 m/s, coarse.
meshing: stream 60, grading 8.
mesh accepted: 9600 cells. Log: log.checkMesh
solving: simpleFoam.
screening: Cl=0.8600 Cd=0.0200. This mesh is not a design answer until the refinement gate passes.
```

A bad mesh stops at:

```text
refused: Refused at gate mesh. Log: log.checkMesh
```

There is no `Cl` on a refusal. A second angle (`Same wing at 8 degrees.`) is a new run and the same gates. `refine` runs only when the screening says the finer run is worth it. Its last line is either a design grade or a refusal at `refinement`. Design grade is mesh agreement within the force-delta bound on this laminar setup. It is not a flight-hardware release. `hardware_decision` accepts only that design grade and raises `TypeError` on a screening.

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
- `NACA 9999 at 6 degrees.` → parse refusal (thickness outside 6..20).

Operator entry, not a method on `Engineer`:

```python
from wingline.meta import correct, propose
propose(Path("runs"))   # no grade field on the return value
```

## Types

Public exports: `Engineer`, `EngineerView`, `Screening`, `DesignGrade`, `Refusal`, `HardwareDecision`, `hardware_decision`.

`GateName` is `pilot | parse | scope | mesh | solve | forces | thin_airfoil | review | commission | refinement | budget | incomplete | lean | shacl | bounds`.

`Screening.sentence` must equal `Bounds.screening_sentence`. `DesignGrade.sentence` must equal `Bounds.design_sentence`. `DesignGrade.__post_init__` rejects a delta outside `Bounds.delta_cl` / `Bounds.delta_cd`. `hardware_decision` raises `TypeError` unless its argument's type is `DesignGrade`.

`project(parent, child, outer)` is the only function that builds a `Screening` or a `DesignGrade`. No outer verdict → `Refusal(gate="incomplete")`. Thin-airfoil red → `Refusal(gate="thin_airfoil")` with no coefficients on the view. Review challenge → `Screening` with `worth_finer` false. Review affirm mints commission. It does not mint design and it is not evidence for `Cl`.

`DesignGrade` requires: parent projects to a `Screening` with `worth_finer` true, child density is `refined`, NACA / α / U match, both force receipts green, `judge_delta` true, child outer review affirmed.

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

`mesh_open` means one or two red mesh receipts, budget remaining (3), and the new evidence signature differs from the last red mesh. A repeated signature is not `mesh_open`; the only legal append is `stopped` with why `repeated_mesh`, and the shape becomes `mesh_closed`. The third red mesh with a new signature may only be followed by `stopped` (`budget`). `solve_open` is the same idea with budget 2 and failure `residual`. `fatal` and `wall_clock` close the solve immediately.

`admit` returns the existing receipt when `(run, stage, args, parent receipt id)` was already committed, and does not exec. It returns a rejection that writes nothing when the edge is absent. Status on the receipt is the judge, never an argument.

Idempotency key and the signature rule are in `kernel.admit`. `case_writer` calls `admit` under `flock` on `runs/<id>/case.lock`. Ledger append is: write `ledger/<id>.json`, fsync, append one line to `ledger/index.jsonl`, fsync. A pointer whose file is missing is ignored on read.

## Bounds

Single numeric source: `wingline/kernel.py` `Bounds`. `python -m wingline.kernel --emit` writes `harness/shapes/receipt.shacl.ttl` and `harness/gate/Gate.lean`. `Engineer.open` and the outer loop refuse (`gate="bounds"`) when a derived file differs. They do not overwrite it.

| name | value |
|---|---|
| alpha | [-2, 10] deg |
| U | [5, 25] m/s, default 10 |
| chord | 1 m |
| nu | 1e-2 |
| rho reference for coefficients | 1 |
| thickness digit | 6..20 |
| n_normal | 40 |
| farfield | x ∈ [-8, 12], y ∈ [-8, 8], z ∈ [0, 0.1] |
| end_time | 200 |
| mesh budget | 3 |
| solve budget | 2 |
| max agent turns | 8 |
| max non-ortho | 65 |
| max skew | 4 |
| continuity cumulative | 5e-3 |
| cylinder cells / continuity / velocity error | 2000 / 5e-4 / 5e-5 |
| delta Cl / Cd | 0.05 / 0.01 |
| thin-airfoil | abs(cl - thin_cl) ≤ max(0.25, 0.35 * abs(thin_cl)) |
| timeouts | mesh 90 s, solve 540 s, pilot 180 s |

Cells: `n_chord = stream * (2 if refined else 1)` with `stream ∈ {60, 80}`. Six hexes give `4 * n_chord * n_normal` cells. Coarse is 9600 or 12800. Refined is 19200 or 25600.

`thin_cl` is the 81-point cosine quadrature in candidate 2's design (NACA 4-digit mean line, `Cl = 2π (A0 + A1/2)`).

## Loops

Inner. `ask` holds `engineer.lock` for the whole call. Order:

1. Diff emitted policy. Mismatch → `bounds`, no case.
2. Parse. Failure → `parse`, no case.
3. Mint `run_id`. Write `runs/<id>/lease` (32 urlsafe bytes, mode `0600`).
4. `perform_pilot()` in `mcp_server.py`. Not a model tool. Reuse `runs/pilot/latest.json` when it is green and the `openfoam` path plus the tutorial `Allrun` hash are unchanged. Red pilot → `Refusal(gate="pilot")`, do not plan a wing.
5. Drive the five tools until the ledger shape is terminal or 8 turns elapse.
6. Ignore model stdout. Load the ledger.
7. Outer loop. Then `project`.

Two drivers, same tools, same `admit`:

- `WINGLINE_AGENT=1` spawns one headless parent. No subagents. Command flags that exist on this Grok: `--prompt-file`, `--cwd`, `-m grok-4.7`, `--reasoning-effort xhigh`, `--max-turns 8`, `--output-format json`, `--no-subagents`, `--disable-web-search`, `--permission-mode dontAsk`, `--deny` for Bash, Read, Edit, Write, Grep, WebFetch, WebSearch, Task, and `--disallowed-tools` for `run_terminal_command,write,search_replace,spawn_subagent,web_search,web_fetch,read_file,grep`. Do not invent an allow-rule syntax. MCP stays available because it is not denied. Environment: `WINGLINE_RUN_ID`, `WINGLINE_LEASE`. The prompt contains the lease, the run id, and the parsed spec. It does not restate gate policy.
- Otherwise `wingline.planner` is the driver. It is a client, not an authority. First mesh commit is stream 60, grading 8. On a red mesh it changes one knob (grading 12, then 4, then stream 80). On `stopped` or `wall_clock` it stops. First solve is `u_relax` 0.7, `non_ortho` 0. A residual red may move to 0.5 or 0.3, then `non_ortho` 1. The server accepts any in-enum plan. The planner cannot append a receipt except through the tools.

Outer. `outer.py` reads the ledger and the case. It does not read the agent transcript. It writes only `runs/<id>/outer/verdict.json`. It does not edit a skill, a bound, a hook, or a case file. First failure wins:

1. Policy diff → `bounds`.
2. `pyshacl` on the derived graph. Non-zero or missing → `shacl`. Do not skip.
3. `lean` on `harness/gate/Gate.lean` with the emitted table. Non-zero or missing → `lean`. Do not skip.
4. Re-judge every receipt with the same `judge_*`. Disagreement refuses at that gate.
5. Inner red or stopped → refused at that gate. No reviewer.
6. `judge_thin`. Red → `thin_airfoil`.
7. Fresh-context review only if thin is green and `WINGLINE_AGENT=1`. Otherwise the review step records `challenge` when a physics-green screening is not worth an automatic finer run, except the fixture's `outer_affirm.json` path used by tests. A challenge still yields `Screening` with `worth_finer` false. Unparseable reviewer stdout is `challenge`.

`refine` refuses up front, without a case, unless the parent outcome is a `Screening` with `worth_finer` true. The child is a new run, density `refined`, same NACA, α, and U. The parent case is not modified.

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
tool_timeouts = { observe = 10, commit_spec = 30, commit_mesh = 120, commit_solve = 600, commit_forces = 60 }
```

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
- `.grok/skills/review-case/SKILL.md` — scratch review workspace. One JSON object `{"verdict":"affirm"|"challenge","gate":"...","note":"..."}`. Cannot pass a run and cannot edit a gate.
- `.grok/skills/meta-diagnose/SKILL.md` — scratch draft for template 6. One skill sentence. Do not declare CFD passed. Do not edit a file.

## Receipts

`runs/<id>/ledger/<receipt_id>.json` plus `index.jsonl`. `outer/graph.jsonld` is derived. `@vocab` is `https://vertical-labs.local/wingline#`.

Required on every receipt: `id`, `type`, `run_id`, `stage`, `status`, `parent_id` (null only on the first), `spec_hash`, `log`, `writer` = `case-writer`, `written_at`. No `grade` field. `sh:closed true` makes a `grade` property fail.

Types: `PilotReceipt`, `ScopeReceipt`, `MeshReceipt`, `SolveReceipt`, `ForcesReceipt`, `StopReceipt`. Evidence fields match candidate 2's table (`stream`, `grading`, `cells`, `cells_expected`, `max_non_ortho`, `max_skew`, `signature`, `u_relax`, `non_ortho`, `continuity_cum`, `failure`, `cl`, `cd`, pilot `cells` / `continuity` / `velocity_error`).

pySHACL command: `pyshacl -s harness/shapes/receipt.shacl.ttl -df json-ld <graph.jsonld>`. The shape file is emitted. It includes:

- writer is `case-writer`
- status in green / red / stopped
- stream in 60 / 80, grading in 2 / 4 / 8 / 12, on mesh receipts
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

Case: `simpleFoam`, `simulationType laminar`, Newtonian `nu 1e-2`, `endTime 200`, `deltaT 1`, `writeInterval 200`, `purgeWrite 1`. No `decomposePar`, no `paraFoam`, no `snappyHexMesh`. Do not copy `airFoil2D`'s `polyMesh` and do not run its `Allrun` (`parallel=true`).

Schemes: `ddt steadyState`; `grad Gauss linear`; `div(phi,U) bounded Gauss linearUpwind grad(U)`; `div((nuEff*dev2(T(grad(U))))) Gauss linear`; laplacian `Gauss linear corrected`. No `nuTilda`.

`fvSolution`: GAMG on `p` (tolerance 1e-6, relTol 0.1), smoothSolver GaussSeidel on `U` (tolerance 1e-8, relTol 0.1). `nNonOrthogonalCorrectors` from the plan. Relaxation `p` 0.3, `U` from the plan.

`0/U`: internalField `(U cos α, U sin α, 0)`. `farfield` and `outlet` `freestreamVelocity`. `walls` `noSlip`. `frontAndBack` `empty`. `0/p`: uniform 0, farfield and outlet `freestreamPressure`, walls `zeroGradient`.

`forceCoeffs` in `controlDict`: `libs (forces)`, `patches (walls)`, `rho rhoInf`, `rhoInf 1`, `CofR (0.25 0 0)`, `pitchAxis (0 0 1)`, `lRef 1`, `Aref 0.1`, `magUInf` from the spec, `liftDir (-sin α, cos α, 0)`, `dragDir (cos α, sin α, 0)` with α in radians. Parser reads `postProcessing/forceCoeffs/0/coefficient.dat`, columns by the header tokens `Cl` and `Cd`, last data row. Never by a fixed column index.

NACA ordinates, closed trailing edge (coefficient `-0.1036`):

```text
x = (1 - cos(π i / n)) / 2
yt = 5 t (0.2969 √x - 0.1260 x - 0.3516 x² + 0.2843 x³ - 0.1036 x⁴)
```

`n` for the polyLine is 80. Cell count does not follow `n`. Standard 4-digit camber; θ = atan(dyc/dx); upper `(x - yt sin θ, yc + yt cos θ)`; lower the minus. `t` is the fraction (0.12 for 2412).

Mesh: six hexes, one vertex list, no `mergePatchPairs`. BlockMesh allows one edge between a given vertex pair, so the upper and lower surfaces must not both be a single edge from the leading edge to the trailing edge. Split each surface at the crown (the polyline sample nearest maximum thickness). Four airfoil edges, four vertex pairs: leading-edge to crown-upper, crown-upper to trailing-edge, leading-edge to crown-lower, crown-lower to trailing-edge. The trailing edge is one shared vertex. The wake centerline from that vertex to the outlet mid-height is one straight edge, shared by the upper and lower wake blocks, and it is not listed twice.

Cell counts: `n_chord` is `stream` on a coarse mesh and `2 * stream` on a refined mesh (same rule as `kernel.cell_count`). `n_lead = n_chord // 2`, `n_trail = n_chord - n_lead`, `n_wake = n_chord`, `n_normal = 40`. Cells = `2 * (n_lead + n_trail + n_wake) * n_normal`, which equals `4 * n_chord * n_normal` (9600 at stream 60). One cell from `z = 0` to `z = 0.1`.

Corners: nose `(-8, 0)`, top `y = 8`, bottom `y = -8`, outlet `x = 12`. Straight edges on the farfield (no arcs). Angle of attack stays on the freestream, not on the points. `simpleGrading` clusters cells toward the airfoil. Patches: `farfield` (nose, top, and bottom), `outlet` (`x = 12`), `walls` (the four airfoil faces only), `frontAndBack` empty. If `checkMesh` reports inverted cells, flip that block once. A remaining failure is a red mesh receipt, not a rewritten cell formula.

## Fixture

`fixtures/naca2412-a6/`. Offline. `WINGLINE_FIXTURE=1` makes `ask` and `perform_pilot` replay these files. It does not start Grok and does not call `openfoam`.

| file | contract |
|---|---|
| ask.txt | `NACA 2412 at 6 degrees. Is this worth a finer run?` |
| again.txt | `Same wing at 8 degrees.` |
| spec.json | naca 2412, alpha 6, u 10, density coarse |
| mesh_plan.json | stream 60, grading 8, cells 9600 |
| solve_plan.json | u_relax 0.7, non_ortho 0 |
| sentence.txt | screening sentence, exact |
| design_sentence.txt | design sentence, exact |
| ledger_green/ | pilot, scope, mesh, solve, forces, all green |
| ledger_mesh_red/ | scope green, mesh red (`max_non_ortho` 80), no forces file |
| ledger_design/ | parent Cl 0.86 Cd 0.02; child Cl 0.89 Cd 0.018 |
| ledger_wide/ | child Cl 0.97 against parent 0.86 (delta 0.11) |
| cylinder_log.txt | the three smoke lines |
| outer_affirm.json | thin green, review affirm |
| outer_challenge.json | thin green, review challenge |

`tests/test_project_2412.py` asserts: green + affirm → `Screening`, exact sentence, `worth_finer` true, and `hardware_decision` on that screening raises `TypeError`. Green + challenge → `worth_finer` false. Mesh red → `Refusal(gate="mesh")`, no screening panel. Wide delta → not a `DesignGrade`. Missing outer file → `Refusal(gate="incomplete")`. `judge_delta(0.05, 0.01)` is true. `judge_delta(0.051, 0.0)` is false. A forces-green ledger with an earlier red mesh is rejected by `admit` and by pySHACL.

`tests/test_openfoam_boundary.py` fails if the substring `openfoam` appears outside `case_writer.py`. `tests/test_import_boundary.py` fails if anything other than `mcp_server.py` imports `case_writer`. `meta.py` must not name `DesignGrade`.

`tests/test_live_2412.py` is skipped unless `WINGLINE_LIVE=1`. It runs the planner (not a nested Grok) on `ask.txt` and asserts the outcome is a `Screening` or a `Refusal` whose `gate` is one of `mesh`, `solve`, `forces`, `thin_airfoil`, `pilot` and whose `log` names a real file under the run.

## Modules

```text
wingline/__init__.py
wingline/kernel.py          Bounds, NEXT, admit, project, judge_*, thin_cl, emit
wingline/case_writer.py     render, parsers, flock, ledger io, run_foam
wingline/mcp_server.py      five tools and perform_pilot. Only importer of case_writer
wingline/engineer.py        open, ask, refine, parse, locks
wingline/planner.py         knob policy. Calls the same tool functions
wingline/outer.py           shacl, lean, re-judge, review, verdict
wingline/meta.py            correct, propose, templates
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

## Build order

Each unit is green before the next starts.

1. `kernel.py`, fixtures, `tests/test_project_2412.py`, emit of the shape and the Lean file. No OpenFOAM.
2. `case_writer.py` parsers against the cylinder snippet and a scrambled coefficient header. Import and `openfoam` boundary tests.
3. Render one NACA 2412 coarse mesh and run `checkMesh`. Keep the cell formula. Fix vertex order if cells are inverted.
4. MCP server, planner, engineer `ask` under `WINGLINE_FIXTURE=1`.
5. Outer loop, including the refusal when a tool is missing, and meta templates that do not mint a grade.
6. Hooks, skills, `.grok/config.toml`, README with the customer command.
7. `WINGLINE_LIVE=1` planner run. Record the screening or the named refusal and the log path. Do not loosen a bound to turn a red gate green.
