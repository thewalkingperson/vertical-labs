---
name: screen-wing
description: Inner parent for one coarse wing. Use when the prompt has a run id, a lease, and a parsed spec.
---

# Screen wing

Call `observe` with the run id. If it says the ledger is terminal, stop.

Otherwise call the one commit for the open stage. Pass the lease unchanged.

First mesh is stream 60, grading 8. On a red mesh, change one knob: grading 12, then 4, then stream 80. Stop on stopped or wall_clock.

First solve is u_relax 0.7, non_ortho 0. On a residual red, try 0.5, then 0.3, then non_ortho 1. Stop on stopped or wall_clock.

Do not paraphrase Cl into a hardware opinion. Do not restate gate policy.
