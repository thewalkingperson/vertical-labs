"""Read-only views of one run: specs, logs, and coefficients."""

from __future__ import annotations

import json
import re
from pathlib import Path

from wingline.kernel import DesignGrade, Screening, parse_naca, project

_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"

_PREFERRED = (
    "spec.json",
    "inner.json",
    "steer.jsonl",
    "case/system/blockMeshDict",
    "case/system/controlDict",
    "case/system/fvSchemes",
    "case/system/fvSolution",
    "case/constant/transportProperties",
    "case/constant/turbulenceProperties",
    "case/0/U",
    "case/0/p",
    "case/log.blockMesh",
    "case/log.checkMesh",
    "case/log.simpleFoam",
    "case/log.forceCoeffs",
    "case/postProcessing/forceCoeffs/0/coefficient.dat",
    "diagnosis.txt",
    "outer/verdict.json",
    "outer/search.json",
)
_PREVIEW_CHARS = 120_000


def jail(run_dir: Path, rel: str) -> Path:
    """A file inside this run. Rejects absolute paths and parent segments."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")):
        raise ValueError("path")
    if "\\" in rel or "\x00" in rel:
        raise ValueError("path")
    parts = Path(rel).parts
    if ".." in parts or any(part in {"", "."} for part in parts):
        raise ValueError("path")
    root = Path(run_dir).resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError("path")
    return target


def _kind(rel: str) -> str:
    name = rel.lower()
    if name in {"spec.json", "inner.json"}:
        return "spec"
    if "transport" in name or name.endswith("/u") or name.endswith("/p"):
        return "dimension"
    if "checkmesh" in name or "blockmesh" in name:
        return "mesh"
    if "simplefoam" in name or "fvsolution" in name or "fvschemes" in name or "controldict" in name:
        return "solve"
    if "coefficient" in name or "force" in name:
        return "forces"
    if name.startswith("outer/"):
        return "outer"
    if name.startswith("ledger/"):
        return "ledger"
    if name == "steer.jsonl":
        return "steer"
    if name == "diagnosis.txt":
        return "diagnosis"
    return "file"


def _entry(run_dir: Path, path: Path) -> dict:
    rel = path.resolve().relative_to(run_dir.resolve()).as_posix()
    return {
        "path": rel,
        "kind": _kind(rel),
        "bytes": path.stat().st_size,
    }


def list_files(run_dir: Path) -> list[dict]:
    run_dir = Path(run_dir)
    rows = []
    seen = set()
    for rel in _PREFERRED:
        path = run_dir / rel
        if path.is_file():
            rows.append(_entry(run_dir, path))
            seen.add(rel)
    ledger = run_dir / "ledger"
    if ledger.is_dir():
        for path in sorted(ledger.glob("*.json")):
            rel = path.resolve().relative_to(run_dir.resolve()).as_posix()
            if rel not in seen:
                rows.append(_entry(run_dir, path))
    return rows


def _chip(label: str, value: object) -> dict:
    return {"label": label, "value": str(value)}


def _last_coefficients(text: str) -> tuple[float | None, float | None]:
    header: list[str] | None = None
    header_at = 0
    for index, line in enumerate(text.splitlines()):
        raw = line.strip()
        if raw.startswith("#"):
            raw = raw[1:].strip()
        tokens = raw.split()
        if "Cl" in tokens and "Cd" in tokens:
            header = tokens
            header_at = index
    if header is None:
        return None, None
    cl_at = header.index("Cl")
    cd_at = header.index("Cd")
    last: list[str] | None = None
    for line in text.splitlines()[header_at + 1 :]:
        tokens = line.split()
        if len(tokens) <= max(cl_at, cd_at):
            continue
        try:
            float(tokens[0])
            float(tokens[cl_at])
            float(tokens[cd_at])
        except ValueError:
            continue
        last = tokens
    if last is None:
        return None, None
    return float(last[cl_at]), float(last[cd_at])


def highlights(rel: str, text: str) -> list[dict]:
    """Short facts for the inspector. The raw file is still shown underneath."""
    chips: list[dict] = []
    kind = _kind(rel)
    if rel == "spec.json":
        try:
            spec = json.loads(text)
        except json.JSONDecodeError:
            return chips
        for key, label in (
            ("naca", "NACA"),
            ("alpha_deg", "Angle"),
            ("u_ms", "Speed"),
            ("density", "Mesh"),
        ):
            if key in spec:
                value = spec[key]
                if key == "alpha_deg":
                    value = f"{value}°"
                elif key == "u_ms":
                    value = f"{value} m/s"
                chips.append(_chip(label, value))
        return chips
    if rel.endswith("transportProperties"):
        nu = re.search(r"nu\s+([0-9.eE+-]+)\s*;", text)
        if nu:
            chips.append(_chip("nu", nu.group(1)))
        return chips
    if rel.endswith("/U"):
        dimensions = re.search(r"dimensions\s+(\[[^\]]+\])\s*;", text)
        field = re.search(r"internalField\s+uniform\s+(\([^)]+\))\s*;", text)
        if dimensions:
            chips.append(_chip("Dimensions", dimensions.group(1)))
        if field:
            chips.append(_chip("Freestream", field.group(1)))
        return chips
    if rel.endswith("log.blockMesh"):
        cells = re.findall(r"nCells:\s*(\d+)", text)
        if cells:
            chips.append(_chip("Cells", cells[-1]))
        return chips
    if rel.endswith("log.checkMesh"):
        non_ortho = re.search(rf"Mesh non-orthogonality Max:\s*({_NUM})", text, re.IGNORECASE)
        if non_ortho is None:
            non_ortho = re.search(rf"Max non-orthogonality\s*=\s*({_NUM})", text, re.IGNORECASE)
        skew = re.search(rf"Max skewness\s*=\s*({_NUM})", text, re.IGNORECASE)
        chips.append(_chip("Mesh", "OK" if "Mesh OK" in text else "Failed"))
        if non_ortho:
            chips.append(_chip("Non-ortho", f"{float(non_ortho.group(1)):.2f}"))
        if skew:
            chips.append(_chip("Skew", f"{float(skew.group(1)):.2f}"))
        return chips
    if rel.endswith("log.simpleFoam"):
        locals_ = re.findall(rf"sum local\s*=\s*({_NUM})", text, re.IGNORECASE)
        if locals_:
            chips.append(_chip("Continuity", f"{float(locals_[-1]):.4g}"))
        else:
            cumulatives = re.findall(rf"cumulative\s*=\s*({_NUM})", text, re.IGNORECASE)
            if cumulatives:
                chips.append(_chip("Continuity", f"{float(cumulatives[-1]):.4g}"))
        chips.append(_chip("Finished", "yes" if "ExecutionTime" in text else "no"))
        return chips
    if rel.endswith("turbulenceProperties"):
        match = re.search(r"RASModel\s+(\w+)", text)
        if match:
            chips.append(_chip("Turbulence", match.group(1)))
        return chips
    if rel.endswith("coefficient.dat"):
        cl, cd = _last_coefficients(text)
        if cl is not None:
            chips.append(_chip("Cl", f"{cl:.4f}"))
        if cd is not None:
            chips.append(_chip("Cd", f"{cd:.4f}"))
        return chips
    if rel == "outer/verdict.json":
        try:
            verdict = json.loads(text)
        except json.JSONDecodeError:
            return chips
        for key in ("gate", "physics", "review"):
            if verdict.get(key):
                chips.append(_chip(key.capitalize(), verdict[key]))
        return chips
    if rel == "outer/search.json":
        try:
            search = json.loads(text)
        except json.JSONDecodeError:
            return chips
        if search.get("objective"):
            chips.append(_chip("Objective", search["objective"]))
        rows = search.get("rows")
        if isinstance(rows, list) and len(rows) > 1:
            chips.append(_chip("Probes", str(len(rows) - 1)))
        if search.get("best_naca"):
            chips.append(_chip("Neighbor", f"{search['best_naca']} at {search.get('best_alpha_deg')}°"))
        return chips
    if kind == "ledger":
        try:
            receipt = json.loads(text)
        except json.JSONDecodeError:
            return chips
        chips.append(_chip("Stage", receipt.get("stage", "")))
        chips.append(_chip("Status", receipt.get("status", "")))
        return chips
    return chips


def read_view(run_dir: Path, rel: str) -> dict:
    path = jail(run_dir, rel)
    raw = path.read_bytes()
    binary = b"\x00" in raw[:1024]
    truncated = False
    if binary:
        text = "Binary file. Size is shown above. The mesh geometry is not printed here."
        chips: list[dict] = []
    else:
        decoded = raw.decode("utf-8", errors="replace")
        text = decoded
        if len(text) > _PREVIEW_CHARS:
            text = text[-_PREVIEW_CHARS:]
            truncated = True
        chips = highlights(rel, text)
    return {
        "path": rel,
        "kind": _kind(rel),
        "bytes": path.stat().st_size,
        "truncated": truncated,
        "highlights": chips,
        "text": text,
    }


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _worth_finer(run_dir: Path) -> bool:
    """True only when project itself commissions a finer run. A child run is not asked again."""
    if (run_dir / "parent").is_file():
        return False
    ledger = run_dir / "ledger"
    if not ledger.is_dir():
        return False
    verdict = run_dir / "outer" / "verdict.json"
    try:
        view = project(ledger, None, verdict if verdict.is_file() else None)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return isinstance(view, Screening) and view.worth_finer


def _design_delta(parent: Path, child: Path) -> dict | None:
    if not (parent / "ledger").is_dir() or not (child / "ledger").is_dir():
        return None
    parent_verdict = parent / "outer" / "verdict.json"
    child_verdict = child / "outer" / "verdict.json"
    try:
        view = project(
            parent / "ledger",
            child / "ledger",
            (
                parent_verdict if parent_verdict.is_file() else None,
                child_verdict if child_verdict.is_file() else None,
            ),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(view, DesignGrade):
        return None
    return {
        "child_id": child.name,
        "parent_id": parent.name,
        "cl": view.cl,
        "cd": view.cd,
        "delta_cl": view.delta_cl,
        "delta_cd": view.delta_cd,
        "sentence": view.sentence,
    }


def summarize_run(run_dir: Path, active: str | None = None, children: list[Path] | None = None) -> dict:
    run_dir = Path(run_dir)
    spec = _load(run_dir / "spec.json") or {}
    verdict = _load(run_dir / "outer" / "verdict.json") or {}
    cl = cd = None
    ledger = run_dir / "ledger"
    if ledger.is_dir():
        for path in sorted(ledger.glob("*.json")):
            row = _load(path)
            if row and row.get("stage") == "forces" and row.get("status") == "green":
                cl, cd = row.get("cl"), row.get("cd")
    gate = ""
    if verdict.get("gate"):
        gate = str(verdict["gate"])
    elif verdict.get("physics") == "red":
        gate = "physics"
    elif verdict.get("physics") == "green":
        gate = "screening"
    elif run_dir.name == active:
        gate = "running"
    delta = None
    for child in children or []:
        delta = _design_delta(run_dir, child)
        if delta is not None:
            break
    return {
        "id": run_dir.name,
        "naca": spec.get("naca", ""),
        "alpha_deg": spec.get("alpha_deg", ""),
        "u_ms": spec.get("u_ms", ""),
        "density": spec.get("density", ""),
        "gate": gate,
        "cl": cl,
        "cd": cd,
        "worth_finer": _worth_finer(run_dir),
        "delta": delta,
        "has_board": (run_dir / "outer" / "search.json").is_file(),
        "active": run_dir.name == active,
    }


def _pointer(path: Path, name: str) -> str:
    file = path / name
    if not file.is_file():
        return ""
    try:
        text = file.read_text().strip()
    except OSError:
        return ""
    if not text.startswith("run-") or text != Path(text).name:
        return ""
    return text


def _born(path: Path) -> float:
    stat = path.stat()
    return float(getattr(stat, "st_birthtime", stat.st_mtime))


def _out_in(origin: Path, other: Path) -> tuple[float, float, float]:
    """Farther neighborhood steps first. The same step keeps the earlier run."""
    left = _load(origin / "spec.json") or {}
    right = _load(other / "spec.json") or {}
    try:
        asked = parse_naca(str(left.get("naca") or ""))
        neighbor = parse_naca(str(right.get("naca") or ""))
        section = float(abs(asked.m - neighbor.m) + abs(asked.p - neighbor.p) + abs(asked.t - neighbor.t))
    except (TypeError, ValueError):
        section = 0.0
    try:
        angle = abs(float(left.get("alpha_deg")) - float(right.get("alpha_deg")))
    except (TypeError, ValueError):
        angle = 0.0
    return (-section, -angle, _born(other))


def list_runs(store: Path, active: str | None = None) -> list[dict]:
    """Newest asked wing first. Its finer mesh follows, then neighborhood runs, outer step first."""
    store = Path(store)
    if not store.is_dir():
        return []
    paths = [path for path in store.iterdir() if path.is_dir() and path.name.startswith("run-")]
    names = {path.name for path in paths}
    refine: dict[str, list[Path]] = {}
    explored: dict[str, list[Path]] = {}
    anchors: list[Path] = []
    for path in paths:
        parent = _pointer(path, "parent")
        origin = _pointer(path, "origin")
        if parent and parent in names and parent != path.name:
            refine.setdefault(parent, []).append(path)
            continue
        if origin and origin in names and origin != path.name:
            explored.setdefault(origin, []).append(path)
            continue
        anchors.append(path)
    anchors.sort(key=_born, reverse=True)
    ordered: list[Path] = []
    placed: set[str] = set()

    def place(path: Path) -> None:
        if path.name in placed:
            return
        ordered.append(path)
        placed.add(path.name)

    for anchor in anchors:
        place(anchor)
        for child in sorted(refine.get(anchor.name, []), key=_born):
            place(child)
        neighbors = sorted(explored.get(anchor.name, []), key=lambda item: _out_in(anchor, item))
        for neighbor in neighbors:
            place(neighbor)
            for child in sorted(refine.get(neighbor.name, []), key=_born):
                place(child)
    leftovers = [path for path in paths if path.name not in placed]
    leftovers.sort(key=_born, reverse=True)
    for path in leftovers:
        place(path)
    return [
        summarize_run(path, active, sorted(refine.get(path.name, []), key=_born, reverse=True))
        for path in ordered
    ]
