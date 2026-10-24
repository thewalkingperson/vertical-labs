---
name: screen-wing
description: Inner loop for one wing. Use when the prompt has a run id, a lease, and a parsed spec.
---

# Screen wing

You are the inner loop for this one wing. Iterate until the ledger is terminal.

Each turn:

1. Call `observe` with the run id.
2. If it says the ledger is terminal, stop.
3. Call the one commit for the open stage. Pass the lease unchanged.

On a red mesh, change one knob and try again: grading 12, then 4, then stream 80. On a residual red, try u_relax 0.5, then 0.3, then non_ortho 1. Stop on stopped or wall_clock.

The first mesh is stream 60, grading 8. The first solve is u_relax 0.7, non_ortho 0.

Stay on this spec. Do not propose another wing. Do not ask the user questions. Do not grade the run and do not call a coefficient a design.

If `steer_fresh` is present, follow it before the next commit. `stop` means do not start another OpenFOAM command. A grading, stream, relax, or non-ortho value applies only when it is one of the allowed knobs and the open stage is mesh or solve.
