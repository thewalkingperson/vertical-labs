"""A 2D picture of one case. Reads the mesh and the solved fields. Does not launch ParaView."""

from __future__ import annotations

import json
import re
from pathlib import Path

_FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_TRIPLE = re.compile(rf"\(\s*({_FLOAT})\s+({_FLOAT})\s+({_FLOAT})\s*\)")
_FACE = re.compile(r"\d+\(([^)]*)\)")
_UNIFORM = re.compile(rf"uniform\s+({_FLOAT})\s*;")
_UNIFORM_VECTOR = re.compile(rf"uniform\s+\(\s*({_FLOAT})\s+({_FLOAT})\s+({_FLOAT})\s*\)\s*;")


def _strip(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*?$", "", text, flags=re.MULTILINE)


def _inside(text: str) -> str:
    body = _strip(text)
    start = body.find("(")
    end = body.rfind(")")
    if start < 0 or end <= start:
        return ""
    return body[start + 1 : end]


def _internal(text: str) -> str:
    body = _strip(text)
    match = re.search(r"internalField\b(.*?)(?:boundaryField\b|$)", body, flags=re.DOTALL)
    if match is None:
        return ""
    return match.group(1)


def _points(text: str) -> list[tuple[float, float, float]]:
    return [(float(item[0]), float(item[1]), float(item[2])) for item in _TRIPLE.findall(_inside(text))]


def _faces(text: str) -> list[list[int]]:
    found = []
    for raw in _FACE.findall(_inside(text)):
        found.append([int(token) for token in raw.split()])
    return found


def _owners(text: str) -> list[int]:
    return [int(token) for token in _inside(text).split()]


def _scalars(text: str, count: int) -> list[float] | None:
    internal = _internal(text)
    uniform = _UNIFORM.search(internal)
    if uniform and "nonuniform" not in internal:
        return [float(uniform.group(1))] * count
    values = [float(token) for token in re.findall(_FLOAT, _inside(internal))]
    if len(values) != count:
        return None
    return values


def _speeds(text: str, count: int) -> list[float] | None:
    internal = _internal(text)
    uniform = _UNIFORM_VECTOR.search(internal)
    if uniform and "nonuniform" not in internal:
        speed = (float(uniform.group(1)) ** 2 + float(uniform.group(2)) ** 2 + float(uniform.group(3)) ** 2) ** 0.5
        return [speed] * count
    triples = _TRIPLE.findall(_inside(internal))
    if len(triples) != count:
        return None
    speeds = []
    for ux, uy, uz in triples:
        speeds.append((float(ux) ** 2 + float(uy) ** 2 + float(uz) ** 2) ** 0.5)
    return speeds


def _latest(case: Path) -> Path | None:
    best: Path | None = None
    best_value: float | None = None
    if not case.is_dir():
        return None
    for child in case.iterdir():
        if not child.is_dir():
            continue
        try:
            value = float(child.name)
        except ValueError:
            continue
        if best_value is None or value > best_value:
            best = child
            best_value = value
    return best


def _boundary(text: str, name: str) -> tuple[int, int] | None:
    body = _strip(text)
    match = re.search(rf"\b{name}\b\s*\{{([^{{}}]*)\}}", body)
    if match is None:
        return None
    block = match.group(1)
    count = re.search(r"nFaces\s+(\d+)", block)
    start = re.search(r"startFace\s+(\d+)", block)
    if count is None or start is None:
        return None
    return int(start.group(1)), int(count.group(1))


def _forces(case: Path) -> dict:
    path = case / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat"
    empty = {"time": [], "cl": [], "cd": []}
    if not path.is_file():
        return empty
    header: list[str] | None = None
    header_at = 0
    lines = path.read_text(errors="replace").splitlines()
    for index, line in enumerate(lines):
        raw = line.strip()
        if raw.startswith("#"):
            raw = raw[1:].strip()
        tokens = raw.split()
        if "Cl" in tokens and "Cd" in tokens:
            header = tokens
            header_at = index
            break
    if header is None:
        return empty
    cl_at = header.index("Cl")
    cd_at = header.index("Cd")
    time: list[float] = []
    cl: list[float] = []
    cd: list[float] = []
    for line in lines[header_at + 1 :]:
        tokens = line.split()
        if len(tokens) <= max(cl_at, cd_at):
            continue
        try:
            time.append(float(tokens[0]))
            cl.append(float(tokens[cl_at]))
            cd.append(float(tokens[cd_at]))
        except ValueError:
            continue
    return {"time": time, "cl": cl, "cd": cd}


def _round(value: float, places: int) -> float:
    return round(float(value), places)


def _view(airfoil: list[list[float]], quads: list[list[float]]) -> list[float]:
    xs: list[float] = []
    ys: list[float] = []
    if airfoil:
        for edge in airfoil:
            xs.extend((edge[0], edge[2]))
            ys.extend((edge[1], edge[3]))
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        chord = max(xmax - xmin, 1e-6)
        mid_y = (ymin + ymax) / 2
        pad_y = max(0.55 * chord, ymax - ymin)
        return [
            _round(xmin - 0.35 * chord, 5),
            _round(mid_y - pad_y, 5),
            _round(xmax + 1.15 * chord, 5),
            _round(mid_y + pad_y, 5),
        ]
    for quad in quads:
        xs.extend(quad[0::2][:4])
        ys.extend(quad[1::2][:4])
    if not xs:
        return [0.0, 0.0, 1.0, 1.0]
    return [_round(min(xs), 5), _round(min(ys), 5), _round(max(xs), 5), _round(max(ys), 5)]


def _spec(run_dir: Path) -> dict:
    path = run_dir / "spec.json"
    if not path.is_file():
        return {}
    try:
        spec = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return spec if isinstance(spec, dict) else {}


def _title(run_dir: Path) -> str:
    spec = _spec(run_dir)
    naca = spec.get("naca")
    if not naca:
        return run_dir.name
    angle = spec.get("alpha_deg", "")
    return f"NACA {naca} at {angle}°"


def _with_wind(note: str, spec: dict) -> str:
    alpha = spec.get("alpha_deg")
    if alpha is None or alpha == "":
        return note
    return note + f" Wind at {alpha}°. The section stays level, and the flow comes in at that angle."


def flow_picture(run_dir: Path) -> dict:
    """Front-plane cells colored by pressure and speed, plus the force history."""
    run_dir = Path(run_dir)
    case = run_dir / "case"
    forces = _forces(case)
    spec = _spec(run_dir)
    mesh = case / "constant" / "polyMesh"
    base = {
        "ready": bool(forces["time"]),
        "title": _title(run_dir),
        "alpha_deg": spec.get("alpha_deg"),
        "u_ms": spec.get("u_ms"),
        "cells": 0,
        "quads": [],
        "airfoil": [],
        "view": [0.0, 0.0, 1.0, 1.0],
        "forces": forces,
        "note": _with_wind("The mesh picture appears after blockMesh.", spec),
    }
    points_path = mesh / "points"
    faces_path = mesh / "faces"
    owner_path = mesh / "owner"
    if not points_path.is_file() or not faces_path.is_file() or not owner_path.is_file():
        return base
    points = _points(points_path.read_text(errors="replace"))
    faces = _faces(faces_path.read_text(errors="replace"))
    owners = _owners(owner_path.read_text(errors="replace"))
    if not points or not faces or len(owners) != len(faces):
        return base
    zmin = min(point[2] for point in points)
    n_cells = max(owners) + 1
    time_dir = _latest(case)
    pressure = None
    speed = None
    if time_dir is not None:
        pressure_path = time_dir / "p"
        speed_path = time_dir / "U"
        if pressure_path.is_file():
            pressure = _scalars(pressure_path.read_text(errors="replace"), n_cells)
        if speed_path.is_file():
            speed = _speeds(speed_path.read_text(errors="replace"), n_cells)
    quads: list[list[float]] = []
    for face, owner in zip(faces, owners):
        if owner < 0 or owner >= n_cells or len(face) < 3:
            continue
        coords = [points[index] for index in face if 0 <= index < len(points)]
        if len(coords) != len(face):
            continue
        if any(abs(point[2] - zmin) > 1e-6 for point in coords):
            continue
        if len(coords) > 4:
            coords = coords[:4]
        flat: list[float] = []
        for point in coords:
            flat.append(_round(point[0], 5))
            flat.append(_round(point[1], 5))
        while len(flat) < 8:
            flat.extend(flat[:2])
        p_value = 0.0 if pressure is None else pressure[owner]
        u_value = 0.0 if speed is None else speed[owner]
        flat.append(_round(p_value, 4))
        flat.append(_round(u_value, 4))
        quads.append(flat)
    airfoil: list[list[float]] = []
    boundary_path = mesh / "boundary"
    if boundary_path.is_file():
        span = _boundary(boundary_path.read_text(errors="replace"), "walls")
        if span is not None:
            start, count = span
            for face in faces[start : start + count]:
                low = [points[index] for index in face if 0 <= index < len(points) and abs(points[index][2] - zmin) <= 1e-6]
                if len(low) < 2:
                    continue
                airfoil.append(
                    [
                        _round(low[0][0], 5),
                        _round(low[0][1], 5),
                        _round(low[1][0], 5),
                        _round(low[1][1], 5),
                    ]
                )
    solved = pressure is not None or speed is not None
    note = "Pressure and speed on the mesh." if solved else "Mesh only. The colors fill in after simpleFoam writes a field."
    if not quads and forces["time"]:
        note = "Forces are in. The mesh picture appears after blockMesh."
    return {
        "ready": bool(quads) or bool(forces["time"]),
        "title": _title(run_dir),
        "alpha_deg": spec.get("alpha_deg"),
        "u_ms": spec.get("u_ms"),
        "cells": len(quads),
        "quads": quads,
        "airfoil": airfoil,
        "view": _view(airfoil, quads),
        "forces": forces,
        "note": _with_wind(note, spec),
    }
