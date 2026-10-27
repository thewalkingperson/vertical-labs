# Wingline

Wingline runs one NACA 4-digit wing through OpenFOAM and says whether that coarse case is worth a finer mesh.

The model may run the case. It does not grade the run. A result is the receipt ledger. The model's sentences are saved and are not evidence.

A screening means the coarse case passed. It is not a design. A design grade means the same wing on a mesh with 20% more chordwise cells stayed inside the force bounds. It is not a release for flight hardware. A named gate that fails is a refusal, and no later number repairs it.

## Run

You need OpenFOAM v2606 (`openfoam` on `PATH`) and Lean. Stay below mcp 2. The seats call the `grok` command.

```bash
cd /path/to/vertical-labs
python3 -m venv .venv
.venv/bin/pip install 'mcp>=1.8,<2' pyshacl pytest 'neuralfoil==0.3.3' 'aerosandbox==4.2.10'
.venv/bin/python -m wingline "NACA 2412 at 6 degrees. Is this worth a finer run?"
```

The command line prints one line per gate. The case lands in `runs/run-…/`. Read `log.checkMesh`, `log.simpleFoam`, and `postProcessing/forceCoeffs/0/coefficient.dat`.

Run the page with:

```bash
.venv/bin/python -m wingline.web
```

Open http://127.0.0.1:8765 and ask in a sentence. If the section or the angle is missing, the page asks. A finished study is one paragraph. A red gate does not print a lift number. When the paragraph has lift and drag, the model may add one or two sentences of advice. That sentence is not a gate. paraFoam opens the case. The finer mesh runs only after the screening says so. Starting the page clears the chat and the `run-*` folders. The cylinder check and the exploration switch stay. The page tries up to ten neighbors when that switch is on.

To run the page without Grok:

```bash
WINGLINE_AGENT=0 .venv/bin/python -m wingline.web
```

Open the same address and ask with a section and an angle, such as `NACA 2412 at 6 degrees`. The planner runs that wing. The paragraph is the receipt. The finer mesh stays off.

The seats call `grok -m grok-4.7`. Set `WINGLINE_MODEL` to use another model. The name has to be one that `grok models` lists, including a model you added yourself. The prompts and the gates stay the same.

Your key stays out of this repo. `grok login` stores a Grok sign-in in your home directory. Or copy `.env.example` to `.env` and put the key there. Git ignores `.env`. Wingline reads `.env` and does not replace a variable you already exported. A Grok model uses `XAI_API_KEY` when you are not signed in.

Another provider is a model entry in `~/.grok/config.toml`. That file is outside this repo. Point `env_key` at the variable in `.env`. Do not write the key in the config. If the provider wants the key in a header, set `env_http_headers` to the variable name.

```toml
[model.gpt-4o]
model = "gpt-4o"
base_url = "https://api.openai.com/v1"
env_key = "OPENAI_API_KEY"
```

Then run with `WINGLINE_MODEL=gpt-4o`.

Angle is −2 to 10 degrees. Speed is 5 to 25 m/s, and 10 is the default. Thickness is 6 to 24.

## The three loops

A request moves in this order. Meta settles the ask. The inner loop runs that one wing. The outer loop grades the ledger. Later, each loop can use a different model, so a strong model is only where the judgment is.

### Meta

Meta speaks before a case exists. If the section or the angle is missing, it asks in its own words. It may recommend one NACA 4-digit section. It does not choose the angle. A full sentence such as `NACA 2412 at 6 degrees` goes straight to the inner loop. Later, meta can take more of the orchestration. That wants a strong model.

### Inner

The inner loop finishes the wing you named. It may call five tools, in order: look at the open stage, write the spec, mesh, solve, and read the forces. Each gate writes a receipt. The next gate does not start unless the last one passed. The model may change only the allowed mesh and solver knobs. It stays on this wing. It does not grade the run. If it stops with the ledger still open, the run is refused. Later, this loop can be a small model, such as Haiku or a local model. The work is those five tools.

### Outer

The outer loop reads the ledger after the inner loop stops. It does not read the model's transcript. It checks the receipt shape, re-runs the same judges, and compares the measured lift with NeuralFoil. `kernel.project` is the only function that can call a result a screening or a design grade. Later, this is the loop that wants the stronger model.

`DESIGN.md` is the full contract.
