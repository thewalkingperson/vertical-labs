---
name: review-case
description: Scratch review of one case. Reply with a single JSON verdict. Use when asked to affirm or challenge a screening.
---

# Review case

The prompt contains the evidence packet: spec, mesh cells and expected cells,
the local continuity residual, Cl, Cd, the NeuralFoil reference polar, its
confidence, the Cl tolerance, and any red log tail. Use only that packet. Do
not read files.

Reply with one JSON object and nothing else:

{"verdict":"affirm"|"challenge","gate":"...","note":"..."}

Affirm only when every receipt is green, the cell count matches, the local
continuity residual is inside the bound, Cd is positive, and Cl is inside the
NeuralFoil tolerance. Otherwise challenge. Reference Cd is context, not a
pass or fail. y+ is reported when present and is not required to be below 1.
You cannot pass a run. You cannot edit a gate.
