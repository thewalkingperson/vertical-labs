# Wingline

```text
python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

A screening line includes this sentence:

```text
This mesh is not a design answer until the refinement gate passes.
```

A refusal has no coefficients. On this Mac, NACA 2412 at 6 degrees passed the cylinder health check, a 9600-cell mesh, and simpleFoam, then stopped here:

```text
goal: NACA 2412 at 6 degrees, 10 m/s, coarse.
meshing: stream 60, grading 8.
mesh accepted: 9600 cells. Log: log.checkMesh
solving: simpleFoam.
refused: Refused at gate thin_airfoil. Log: postProcessing/forceCoeffs/0/coefficient.dat
```

The lift in that file is outside the thin-airfoil band, so it is not a screening and it cannot become a hardware decision. Set `WINGLINE_AGENT=1` to drive the same tools with a Grok session. The default driver is the planner.
