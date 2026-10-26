# Wingline

Wingline runs one NACA 4-digit wing through OpenFOAM and says whether that coarse case is worth a finer mesh.

## Run

You need OpenFOAM v2606 (`openfoam` on `PATH`), Lean, and Grok/another AI provider(I have some free grok credits so I just used that).

```bash
cd /path/to/vertical-labs
python3 -m venv .venv
.venv/bin/pip install 'mcp>=1.8,<2' pyshacl pytest 'neuralfoil==0.3.3' 'aerosandbox==4.2.10'
.venv/bin/python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

The command line prints one line per gate. The case lands in `runs/run-…/`. Read `log.checkMesh`, `log.simpleFoam`, and `postProcessing/forceCoeffs/0/coefficient.dat`.


Run it with:

```bash
.venv/bin/python -m wingline.web
```

Open http://127.0.0.1:8765. A finished study is one paragraph. When that paragraph has lift and drag, the model may add one or two sentences of advice. That sentence is not a gate. paraFoam opens the case. Starting the page clears the chat and the `run-*` folders. The cylinder check and the exploration switch stay. The page tries up to ten neighbors when that switch is on.

Angle is −2 to 10 degrees. Speed is 5 to 25 m/s, and 10 is the default. Thickness is 6 to 24.

## The three loops

A request moves through three loops. Meta agent is meant to be the main agent you interact with and describe your reasoning. The inner loop runs that one wing that the meta agent provides. The outer loop acts as a verifier before passing out results. This allows not only for concurrence in development, but also allows for seperation of tasks for verification and preventing hallucinations. It also allows for different tiers of intelligence to be used for different sections later on to reduce costs.

### Meta

Meta speaks before a case exists. If the section or the angle is missing, it asks in its own words. It may recommend one NACA 4-digit section. It does not choose the angle. A full sentence such as `NACA 2412 at 6 degrees` goes straight to the inner loop. This is meant to be the main agent the user interacts with, handling edge cases and managing multiple designs. Longer term, this can be expanded to provide more reasoning and agentic tools to allow for it to be more autonomous in its actions, basically orchestrating all other work. Would also require high end intelligence

### Outer

The outer loop reads the ledger after the inner loop stops. It does not read the model's transcript. It checks the receipt shape, re-runs the same judges, and compares the measured lift with NeuralFoil. `kernel.project` is the only function that can call a result a screening or a design grade. This would require the most tuning for intelligence, depending on the work and amount of self improvement being done this may require high end reasoning but should be functional with mid tier intelliegence like Sonnet

### Inner

The inner loop finishes the wing you named. It may call five tools, in order: look at the open stage, write the spec, mesh, solve, and read the forces. Each gate writes a receipt. The next gate does not start unless the last one passed. The model may change only the allowed mesh and solver knobs. It stays on this wing. It does not grade the run. If it stops with the ledger still open, the run is refused. Meant to be the simplest, lowest cost models used for grunt work. A model like haiku or small local models like gemma 16b or flint models would work for this loop.

More information on Design.md


A screening means the coarse case passed. It is not a design. A design grade means the same wing on a mesh with 20% more chordwise cells stayed inside the force bounds. It is not a release for flight hardware. A named gate that fails is a refusal, and no later number repairs it.

`DESIGN.md` is the full contract.
