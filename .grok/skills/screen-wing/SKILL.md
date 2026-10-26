---
name: screen-wing
description: Inner loop for one wing. Use when the prompt has a run id, a lease, and a parsed spec.
---

# Screen wing

You are the inner loop for this one wing. Iterate until the ledger is terminal.

Each turn:

1. Call `wingline__observe` with the run id.
2. If it says the ledger is terminal, stop.
3. Call the one commit for the open stage: `wingline__commit_spec`,
   `wingline__commit_mesh`, `wingline__commit_solve`, or
   `wingline__commit_forces`. Pass the lease unchanged.

The case is wall-modeled Spalart-Allmaras with nutUSpaldingWallFunction.
y+ is recorded and is not required to be below 1. The parent's first mesh is
stream 100, grading 8. The refined child's first mesh is stream 60, grading 8.
The first solve is u_relax 0.5, non_ortho 1.
High skew gets milder grading (4, then 2) in the current density's table. High
non-orthogonality gets stream 80. On a residual red, under-relax velocity
(0.5, then 0.3) and then add a non-orthogonal corrector. Stop on stopped or
wall_clock. Do not loosen max_skew.

Stay on this spec. Do not propose another wing. Do not ask the user questions. Do not grade the run and do not call a coefficient a design.

If `steer_fresh` is present, follow it before the next commit. `stop` means do not start another OpenFOAM command. A grading, stream, relax, or non-ortho value applies only when it is one of the allowed knobs and the open stage is mesh or solve.
