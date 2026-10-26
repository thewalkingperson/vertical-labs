"""Render a wall-modeled Spalart-Allmaras RANS case, parse foam logs, and append receipts.

The wall condition is nutUSpaldingWallFunction. y+ is recorded and is not a gate.
OpenFOAM is started only from run_foam and open_parafoam, as ["openfoam", "-c", snippet].
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import shutil
import signal
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from wingline.kernel import (
    Bounds,
    Commit,
    Admission,
    admit,
    cell_count,
    first_cell_height,
    load_ledger,
    parse_naca,
    wall_grading_ratio,
)

# Fixed launcher strings. No model text is concatenated into these.
MESH_SNIPPET = "blockMesh > log.blockMesh 2>&1 && checkMesh > log.checkMesh 2>&1"
SOLVE_SNIPPET = "simpleFoam > log.simpleFoam 2>&1"
FORCES_SNIPPET = "postProcess -func forceCoeffs > log.forceCoeffs 2>&1"
PILOT_SNIPPET = (
    'rm -rf case && cp -R "$FOAM_TUTORIALS/basic/potentialFoam/cylinder" case'
    " && cd case && ./Allrun"
)
SNIPPETS = {
    "mesh": MESH_SNIPPET,
    "solve": SOLVE_SNIPPET,
    "forces": FORCES_SNIPPET,
    "pilot": PILOT_SNIPPET,
}

_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_NACA_SAMPLES = 80
_FACE_OF = {
    "xmin": lambda a, b, c, d, A, B, C, D: (a, d, D, A),
    "xmax": lambda a, b, c, d, A, B, C, D: (b, c, C, B),
    "ymin": lambda a, b, c, d, A, B, C, D: (a, b, B, A),
    "ymax": lambda a, b, c, d, A, B, C, D: (d, c, C, D),
}
_FLIP_FACE = {"ymin": "xmin", "xmin": "ymin", "ymax": "xmax", "xmax": "ymax"}


def _num(text: str) -> float | None:
    match = re.search(_NUM, text)
    if match is None:
        return None
    return float(match.group(0))


def parse_block_mesh(text: str) -> dict:
    """nCells from a blockMesh log. The last match wins."""
    found = re.findall(r"nCells:\s*(\d+)", text)
    cells = int(found[-1]) if found else None
    return {
        "cells": cells,
        "fatal": "FOAM FATAL" in text,
        "log": "log.blockMesh",
    }


def parse_check_mesh(text: str) -> dict:
    """Mesh OK, max non-orthogonality, max skewness, and FOAM FATAL."""
    non_ortho = re.search(
        rf"Mesh non-orthogonality Max:\s*({_NUM})",
        text,
        re.IGNORECASE,
    )
    if non_ortho is None:
        non_ortho = re.search(
            rf"Max non-orthogonality\s*=\s*({_NUM})",
            text,
            re.IGNORECASE,
        )
    skew = re.search(rf"Max skewness\s*=\s*({_NUM})", text, re.IGNORECASE)
    mesh_ok = "Mesh OK" in text
    return {
        "mesh_ok": mesh_ok,
        "max_non_ortho": float(non_ortho.group(1)) if non_ortho else None,
        "max_skew": float(skew.group(1)) if skew else None,
        "fatal": "FOAM FATAL" in text or "negative volume" in text.lower(),
        "log": "log.checkMesh",
        "log_text": text,
    }


def parse_simple_foam(text: str, elapsed_s: float) -> dict:
    """Last local, global, and cumulative continuity, plus ExecutionTime.

    The solve passes on the final local residual when the log has one.
    Cumulative continuity is kept for older logs.
    """
    locals_ = re.findall(rf"sum local\s*=\s*({_NUM})", text, re.IGNORECASE)
    globals_ = re.findall(rf"global\s*=\s*({_NUM})", text, re.IGNORECASE)
    cumulatives = re.findall(rf"cumulative\s*=\s*({_NUM})", text, re.IGNORECASE)
    continuity_local = float(locals_[-1]) if locals_ else None
    continuity_global = float(globals_[-1]) if globals_ else None
    continuity = float(cumulatives[-1]) if cumulatives else None
    finished = "ExecutionTime" in text
    fatal = "FOAM FATAL" in text
    if fatal:
        failure = "fatal"
    elif elapsed_s >= Bounds.solve_timeout_s:
        failure = "wall_clock"
    elif not finished:
        failure = "residual"
    elif continuity_local is not None:
        failure = "none" if continuity_local <= Bounds.continuity_local else "residual"
    elif continuity is not None:
        failure = "none" if continuity <= Bounds.continuity_cum else "residual"
    else:
        failure = "residual"
    return {
        "continuity_local": continuity_local,
        "continuity_global": continuity_global,
        "continuity_cum": continuity,
        "finished": finished,
        "fatal": fatal,
        "failure": failure,
        "elapsed_s": float(elapsed_s),
        "log": "log.simpleFoam",
        "log_text": text,
    }


def _header_tokens(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("#"):
        raw = raw[1:].strip()
    return raw.split()


def parse_force_coeffs(text: str) -> dict:
    """Cl and Cd from the header tokens, last data row. Not a fixed column."""
    lines = text.splitlines()
    header_at: int | None = None
    header: list[str] | None = None
    for index, line in enumerate(lines):
        tokens = _header_tokens(line)
        if "Cl" in tokens and "Cd" in tokens:
            header_at = index
            header = tokens
    if header is None or header_at is None:
        return {"cl": None, "cd": None, "header": "", "log": "postProcessing/forceCoeffs/0/coefficient.dat"}
    cl_at = header.index("Cl")
    cd_at = header.index("Cd")
    last: list[str] | None = None
    for line in lines[header_at + 1 :]:
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
        return {
            "cl": None,
            "cd": None,
            "header": " ".join(header),
            "log": "postProcessing/forceCoeffs/0/coefficient.dat",
        }
    return {
        "cl": float(last[cl_at]),
        "cd": float(last[cd_at]),
        "header": " ".join(header),
        "log": "postProcessing/forceCoeffs/0/coefficient.dat",
    }


def parse_cylinder(block_log: str, solver_log: str) -> dict:
    """Cylinder smoke: nCells, Continuity error, Interpolated velocity error."""
    blob = f"{block_log}\n{solver_log}"
    cells_found = re.findall(r"nCells:\s*(\d+)", blob)
    continuity = re.search(rf"Continuity error\s*=\s*({_NUM})", blob)
    velocity = re.search(rf"Interpolated velocity error\s*=\s*({_NUM})", blob)
    return {
        "cells": int(cells_found[-1]) if cells_found else None,
        "continuity": float(continuity.group(1)) if continuity else None,
        "velocity_error": float(velocity.group(1)) if velocity else None,
        "fatal": "FOAM FATAL" in blob,
        "log_mesh": "log.blockMesh",
        "log_solver": "log.potentialFoam",
        "log_text": blob,
    }


def parse_yplus(text: str) -> dict[str, float] | None:
    """Highest-time wall row. A later all-zero max does not replace a positive max."""
    rows: list[tuple[float, str, float, float, float]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 5:
            continue
        try:
            sampled = float(parts[0])
            ymin = float(parts[2])
            ymax = float(parts[3])
            yavg = float(parts[4])
        except ValueError:
            continue
        rows.append((sampled, parts[1], ymin, ymax, yavg))
    if not rows:
        return None
    latest = max(row[0] for row in rows)
    at_time = [row for row in rows if row[0] == latest]
    walls = [row for row in at_time if row[1] == "walls" and row[3] > 0.0]
    positive = [row for row in at_time if row[3] > 0.0]
    chosen = (walls or positive or at_time)[-1]
    return {"min": chosen[2], "max": chosen[3], "avg": chosen[4]}


def yplus_from_case(case: Path) -> dict[str, float] | None:
    """Read every yPlus*.dat. A missing table is not a solve failure."""
    root = Path(case) / "postProcessing"
    if not root.is_dir():
        return None
    chunks: list[str] = []
    for path in sorted(root.rglob("yPlus*.dat")):
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    if not chunks:
        return None
    return parse_yplus("\n".join(chunks))


def mesh_evidence(
    block_text: str,
    check_text: str,
    *,
    cells_expected: int,
    stream: int,
    grading: int,
    chord_m: float = 1.0,
) -> dict:
    """One mapping for judge_mesh / Commit."""
    block = parse_block_mesh(block_text)
    check = parse_check_mesh(check_text)
    cells = block["cells"]
    if cells is None:
        listed = re.search(r"^\s*cells:\s*(\d+)\s*$", check_text, re.MULTILINE)
        if listed:
            cells = int(listed.group(1))
    ratio = wall_grading_ratio(grading)
    # Far-field height scales with chord. The unit chord keeps the old height.
    length = float(chord_m) * float(Bounds.far_y)
    return {
        "cells": cells,
        "cells_expected": cells_expected,
        "max_non_ortho": check["max_non_ortho"],
        "max_skew": check["max_skew"],
        "mesh_ok": check["mesh_ok"],
        "fatal": bool(check["fatal"] or block["fatal"]),
        "stream": stream,
        "grading": grading,
        "n_normal": int(Bounds.n_normal),
        "wall_grading": ratio,
        "first_cell_m": first_cell_height(length, Bounds.n_normal, ratio),
        "log": "log.checkMesh",
        "log_text": check_text,
    }


@dataclass(frozen=True)
class _Sample:
    x: float
    yt: float
    upper: tuple[float, float]
    lower: tuple[float, float]


def _camber(x: float, m: float, p: float) -> tuple[float, float]:
    if m == 0.0 or p == 0.0:
        return 0.0, 0.0
    if x < p:
        yc = (m / (p * p)) * (2.0 * p * x - x * x)
        dyc = (2.0 * m / (p * p)) * (p - x)
    else:
        denom = (1.0 - p) ** 2
        yc = (m / denom) * ((1.0 - 2.0 * p) + 2.0 * p * x - x * x)
        dyc = (2.0 * m / denom) * (p - x)
    return yc, dyc


def naca_samples(
    naca: object, n: int = _NACA_SAMPLES, *, chord_m: float = 1.0
) -> tuple[list[_Sample], int]:
    """Closed-TE ordinates scaled by chord_m. n is the cosine denominator. Crown is max thickness."""
    foil = parse_naca(naca)
    c = float(chord_m)
    m = foil.m / 100.0
    p = foil.p / 10.0
    t = foil.t / 100.0
    samples: list[_Sample] = []
    for i in range(n + 1):
        x_norm = 0.5 * (1.0 - math.cos(math.pi * i / n))
        yt_norm = 5.0 * t * (
            0.2969 * math.sqrt(max(x_norm, 0.0))
            - 0.1260 * x_norm
            - 0.3516 * x_norm * x_norm
            + 0.2843 * x_norm**3
            - 0.1036 * x_norm**4
        )
        yc_norm, dyc_norm = _camber(x_norm, m, p)
        theta = math.atan(dyc_norm)
        st = math.sin(theta)
        ct = math.cos(theta)
        x_pt = (x_norm - yt_norm * st) * c
        y_upper = (yc_norm + yt_norm * ct) * c
        y_lower = (yc_norm - yt_norm * ct) * c
        x_lower = (x_norm + yt_norm * st) * c
        samples.append(
            _Sample(
                x=x_norm * c,
                yt=yt_norm * c,
                upper=(x_pt, y_upper),
                lower=(x_lower, y_lower),
            )
        )
    crown = max(range(1, n), key=lambda i: samples[i].yt)
    return samples, crown


def _fmt(value: float) -> str:
    if abs(value) < 5e-13:
        return "0"
    text = f"{value:.12g}"
    if text in {"-0", "-0.0"}:
        return "0"
    return text


def _fmt_grade(value: float) -> str:
    if abs(value - round(value)) < 1e-12:
        return str(int(round(value)))
    return _fmt(value)


def _vec(x: float, y: float, z: float = 0.0) -> str:
    return f"({_fmt(x)} {_fmt(y)} {_fmt(z)})"


@dataclass
class _Hex:
    name: str
    verts: tuple[int, int, int, int]
    counts: tuple[int, int, int]
    grading: tuple[float, float, float]
    patches: tuple[tuple[str, str], ...]

    def flip(self) -> "_Hex":
        a, b, c, d = self.verts
        nx, ny, nz = self.counts
        gx, gy, gz = self.grading
        return _Hex(
            self.name,
            (a, d, c, b),
            (ny, nx, nz),
            (gy, gx, gz),
            tuple((patch, _FLIP_FACE[face]) for patch, face in self.patches),
        )


def _signed_area(verts: tuple[int, int, int, int], xy: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, vid in enumerate(verts):
        x1, y1 = xy[vid]
        x2, y2 = xy[verts[(index + 1) % 4]]
        area += x1 * y2 - x2 * y1
    return 0.5 * area


def _divisions(stream: int, density: str) -> dict[str, int]:
    """Same chord rule as kernel.cell_count. Six hexes, one cell in z."""
    if density == "refined":
        n_chord = stream * 2
    elif density == "coarse":
        n_chord = stream
    else:
        raise ValueError("density")
    n_lead = n_chord // 2
    n_trail = n_chord - n_lead
    n_wake = n_chord
    n_normal = Bounds.n_normal
    cells = 2 * (n_lead + n_trail + n_wake) * n_normal
    if cells != 4 * n_chord * n_normal or cells != cell_count(stream, density):
        raise RuntimeError("cell formula drifted")
    return {
        "n_chord": n_chord,
        "n_lead": n_lead,
        "n_trail": n_trail,
        "n_wake": n_wake,
        "n_normal": n_normal,
        "nz": 1,
        "cells": cells,
    }


def _hexes(div: Mapping[str, int], grading: float) -> list[_Hex]:
    """j=0 is the airfoil, or the wake centerline on the wake blocks.

    Lower blocks are the user's quads cycled so that edge stays j=0 and the
    corner order stays counter-clockwise. Upper blocks are the given order.
    """
    g = wall_grading_ratio(grading)
    # j=0 is the airfoil or the wake, so the wall-normal ratio is the plan grading.
    # lead_lower's chordwise ratio is 2 (larger cells toward the LE). Ratio 1
    # leaves lower-nose faces over the skew check at grading 8.
    grade = (1.0, g, 1.0)
    # Chordwise ratio: larger cells toward the leading edge. The upper block's
    # i-axis runs LE to crown, so its ratio is 1/2. The lower block's i-axis
    # runs crown to LE, so its ratio is 2. Ratio 1 leaves the nose over the
    # skew check on a symmetric section.
    lead_upper_grade = (0.5, g, 1.0)
    lead_lower_grade = (2.0, g, 1.0)
    lead = (div["n_lead"], div["n_normal"], div["nz"])
    trail = (div["n_trail"], div["n_normal"], div["nz"])
    wake = (div["n_wake"], div["n_normal"], div["nz"])
    return [
        _Hex("lead_upper", (4, 5, 1, 0), lead, lead_upper_grade, (("walls", "ymin"), ("farfield", "ymax"))),
        _Hex("trail_upper", (5, 7, 2, 1), trail, grade, (("walls", "ymin"), ("farfield", "ymax"))),
        _Hex("wake_upper", (7, 8, 3, 2), wake, grade, (("farfield", "ymax"), ("outlet", "xmax"))),
        # (4 0 9 6) cycled to (6 4 0 9); airfoil edge is 4-6.
        _Hex("lead_lower", (6, 4, 0, 9), lead, lead_lower_grade, (("walls", "ymin"), ("farfield", "ymax"))),
        # (6 9 10 7) cycled to (7 6 9 10); airfoil edge is 6-7.
        _Hex("trail_lower", (7, 6, 9, 10), trail, grade, (("walls", "ymin"), ("farfield", "ymax"))),
        # (7 10 11 8) cycled to (8 7 10 11); wake edge 7-8 is j=0, not an edges entry.
        _Hex("wake_lower", (8, 7, 10, 11), wake, grade, (("farfield", "ymax"), ("outlet", "xmin"))),
    ]


def _orient(blocks: list[_Hex], xy: list[tuple[float, float]]) -> list[_Hex]:
    oriented: list[_Hex] = []
    for block in blocks:
        if _signed_area(block.verts, xy) < 0.0:
            oriented.append(block.flip())
        else:
            oriented.append(block)
    return oriented


def _between(samples: list[_Sample], start: int, end: int, side: str) -> list[tuple[float, float]]:
    step = 1 if end > start else -1
    points: list[tuple[float, float]] = []
    index = start + step
    while index != end:
        sample = samples[index]
        points.append(sample.upper if side == "upper" else sample.lower)
        index += step
    return points


def _airfoil_edges(
    samples: list[_Sample], crown: int, span_m: float = 0.1
) -> list[tuple[int, int, list[tuple[float, float]]]]:
    """Four surface edges, then the z=span copies. Endpoints are vertices."""
    last = len(samples) - 1
    front = [
        (4, 5, _between(samples, 0, crown, "upper")),
        (5, 7, _between(samples, crown, last, "upper")),
        (4, 6, _between(samples, 0, crown, "lower")),
        (6, 7, _between(samples, crown, last, "lower")),
    ]
    edges: list[tuple[int, int, list[tuple[float, float]]]] = []
    seen: set[frozenset[int]] = set()
    span = float(span_m)
    for a, b, pts in front:
        for za, zb, z in ((a, b, 0.0), (a + 12, b + 12, span)):
            pair = frozenset((za, zb))
            if pair in seen:
                raise RuntimeError(f"duplicate edge {za} {zb}")
            seen.add(pair)
            edges.append((za, zb, [(x, y) for x, y in pts]))
    for forbidden in (frozenset((7, 8)), frozenset((19, 20))):
        if forbidden in seen:
            raise RuntimeError("wake centerline must stay a straight edge")
    return edges


def _vertices(
    samples: list[_Sample],
    crown: int,
    chord_m: float = 1.0,
    span_m: float = 0.1,
) -> list[tuple[float, float, float]]:
    le = samples[0].upper
    te_u = samples[-1].upper
    te_l = samples[-1].lower
    te = ((te_u[0] + te_l[0]) / 2.0, (te_u[1] + te_l[1]) / 2.0)
    x_crown = samples[crown].x
    c = float(chord_m)
    span = float(span_m)
    far_x_in = Bounds.far_x_in * c
    far_x_out = Bounds.far_x_out * c
    far_y = Bounds.far_y * c
    xy = [
        (far_x_in, 0.0),
        (x_crown, far_y),
        (c, far_y),
        (far_x_out, far_y),
        le,
        samples[crown].upper,
        samples[crown].lower,
        te,
        (far_x_out, te[1]),
        (x_crown, -far_y),
        (c, -far_y),
        (far_x_out, -far_y),
    ]
    verts: list[tuple[float, float, float]] = []
    for z in (0.0, span):
        for x, y in xy:
            verts.append((x, y, z))
    return verts


def _foam(obj: str, cls: str) -> str:
    return (
        "FoamFile\n"
        "{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        "    class       " + cls + ";\n"
        "    object      " + obj + ";\n"
        "}\n"
    )


def _face(verts: tuple[int, int, int, int], kind: str) -> tuple[int, int, int, int]:
    a, b, c, d = verts
    A, B, C, D = a + 12, b + 12, c + 12, d + 12
    return _FACE_OF[kind](a, b, c, d, A, B, C, D)


def _block_mesh_dict(
    samples: list[_Sample],
    crown: int,
    blocks: list[_Hex],
    edges: list[tuple[int, int, list[tuple[float, float]]]],
    chord_m: float = 1.0,
    span_m: float = 0.1,
) -> str:
    verts = _vertices(samples, crown, chord_m=chord_m, span_m=span_m)
    xy = [(x, y) for x, y, z in verts[:12]]
    lines = [_foam("blockMeshDict", "dictionary"), "scale 1;", "", "vertices", "("]
    for index, (x, y, z) in enumerate(verts):
        lines.append(f"    ({_fmt(x)} {_fmt(y)} {_fmt(z)}) // {index}")
    lines.append(");")
    lines.append("")
    lines.append("blocks")
    lines.append("(")
    for block in blocks:
        if _signed_area(block.verts, xy) <= 0.0:
            raise RuntimeError(f"{block.name} is inverted")
        a, b, c, d = block.verts
        nx, ny, nz = block.counts
        gx, gy, gz = block.grading
        lines.append(
            f"    hex ({a} {b} {c} {d} {a + 12} {b + 12} {c + 12} {d + 12})"
            f" ({nx} {ny} {nz})"
            f" simpleGrading ({_fmt_grade(gx)} {_fmt_grade(gy)} {_fmt_grade(gz)})"
            f" // {block.name}"
        )
    lines.append(");")
    lines.append("")
    lines.append("edges")
    lines.append("(")
    for a, b, pts in edges:
        z = 0.0 if a < 12 else float(span_m)
        lines.append(f"    polyLine {a} {b}")
        lines.append("    (")
        for x, y in pts:
            lines.append(f"        ({_fmt(x)} {_fmt(y)} {_fmt(z)})")
        lines.append("    )")
    lines.append(");")
    lines.append("")
    groups: dict[str, list[tuple[int, int, int, int]]] = {
        "walls": [],
        "farfield": [],
        "outlet": [],
        "frontAndBack": [],
    }
    for block in blocks:
        for patch, kind in block.patches:
            groups[patch].append(_face(block.verts, kind))
        a, b, c, d = block.verts
        groups["frontAndBack"].append((a, b, c, d))
        groups["frontAndBack"].append((a + 12, b + 12, c + 12, d + 12))
    if not groups["walls"]:
        raise RuntimeError("walls patch has no faces")
    types = {
        "walls": "wall",
        "farfield": "patch",
        "outlet": "patch",
        "frontAndBack": "empty",
    }
    lines.append("boundary")
    lines.append("(")
    for name in ("walls", "farfield", "outlet", "frontAndBack"):
        lines.append(f"    {name}")
        lines.append("    {")
        lines.append(f"        type {types[name]};")
        lines.append("        faces")
        lines.append("        (")
        for face in groups[name]:
            lines.append("            (" + " ".join(str(v) for v in face) + ")")
        lines.append("        );")
        lines.append("    }")
    lines.append(");")
    lines.append("")
    return "\n".join(lines) + "\n"


def _control(alpha: float, speed: float, chord_m: float = 1.0, span_m: float = 0.1) -> str:
    lift = _vec(-math.sin(alpha), math.cos(alpha), 0.0)
    drag = _vec(math.cos(alpha), math.sin(alpha), 0.0)
    c = float(chord_m)
    span = float(span_m)
    aref = c * span
    cofr = _vec(0.25 * c, 0.0, 0.0)
    # Volume fields are kept every 20 steps. forceCoeffs still writes every step.
    return f"""{_foam("controlDict", "dictionary")}
application     simpleFoam;

startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {Bounds.end_time};
deltaT          1;
writeControl    timeStep;
writeInterval   20;
purgeWrite      10;
writeFormat     ascii;
writePrecision  8;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;

functions
{{
    forceCoeffs
    {{
        type            forceCoeffs;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        patches         (walls);
        rho             rhoInf;
        rhoInf          {_fmt(Bounds.rho)};
        CofR            {cofr};
        pitchAxis       (0 0 1);
        liftDir         {lift};
        dragDir         {drag};
        magUInf         {_fmt(speed)};
        lRef            {_fmt(c)};
        Aref            {_fmt(aref)};
    }}
    yPlus
    {{
        type            yPlus;
        libs            (fieldFunctionObjects);
        writeControl    timeStep;
        writeInterval   600;
        writeFields     false;
    }}
}}
"""


def _schemes() -> str:
    return f"""{_foam("fvSchemes", "dictionary")}
ddtSchemes
{{
    default         steadyState;
}}

gradSchemes
{{
    default         Gauss linear;
    grad(U)         Gauss linear;
}}

divSchemes
{{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,nuTilda) bounded Gauss linearUpwind grad(nuTilda);
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}}

laplacianSchemes
{{
    default         Gauss linear corrected;
}}

interpolationSchemes
{{
    default         linear;
}}

snGradSchemes
{{
    default         corrected;
}}

wallDist
{{
    method          meshWave;
}}
"""


def _solution(u_relax: float, non_ortho: int) -> str:
    return f"""{_foam("fvSolution", "dictionary")}
solvers
{{
    p
    {{
        solver          GAMG;
        tolerance       1e-6;
        relTol          0.1;
        smoother        GaussSeidel;
    }}

    U
    {{
        solver          smoothSolver;
        smoother        GaussSeidel;
        nSweeps         2;
        tolerance       1e-8;
        relTol          0.1;
    }}

    nuTilda
    {{
        solver          smoothSolver;
        smoother        GaussSeidel;
        nSweeps         2;
        tolerance       1e-8;
        relTol          0.1;
    }}
}}

SIMPLE
{{
    nNonOrthogonalCorrectors {int(non_ortho)};
    residualControl
    {{
        p               1e-5;
        U               1e-5;
        nuTilda         1e-5;
    }}
}}

relaxationFactors
{{
    fields
    {{
        p               0.3;
    }}
    equations
    {{
        U               {_fmt(u_relax)};
        nuTilda         0.7;
    }}
}}
"""


def _transport() -> str:
    return f"""{_foam("transportProperties", "dictionary")}
transportModel  Newtonian;

nu              {_fmt(Bounds.nu)};
"""


def _turbulence() -> str:
    return f"""{_foam("turbulenceProperties", "dictionary")}
simulationType  RAS;

RAS
{{
    RASModel        SpalartAllmaras;
    turbulence      on;
    printCoeffs     on;
}}
"""


def _nu_tilda() -> str:
    freestream = _fmt(3.0 * Bounds.nu)
    return f"""{_foam("nuTilda", "volScalarField")}
dimensions      [0 2 -1 0 0 0 0];

internalField   uniform {freestream};

boundaryField
{{
    farfield
    {{
        type            freestream;
        freestreamValue $internalField;
    }}

    outlet
    {{
        type            freestream;
        freestreamValue $internalField;
    }}

    walls
    {{
        type            fixedValue;
        value           uniform 0;
    }}

    frontAndBack
    {{
        type            empty;
    }}
}}
"""


def _nut() -> str:
    return f"""{_foam("nut", "volScalarField")}
dimensions      [0 2 -1 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
    farfield
    {{
        type            freestream;
        freestreamValue $internalField;
    }}

    outlet
    {{
        type            freestream;
        freestreamValue $internalField;
    }}

    walls
    {{
        type            nutUSpaldingWallFunction;
        value           uniform 0;
    }}

    frontAndBack
    {{
        type            empty;
    }}
}}
"""


def _velocity(alpha: float, speed: float) -> str:
    internal = _vec(speed * math.cos(alpha), speed * math.sin(alpha), 0.0)
    return f"""{_foam("U", "volVectorField")}
dimensions      [0 1 -1 0 0 0 0];

internalField   uniform {internal};

boundaryField
{{
    farfield
    {{
        type            freestreamVelocity;
        freestreamValue $internalField;
    }}

    outlet
    {{
        type            freestreamVelocity;
        freestreamValue $internalField;
    }}

    walls
    {{
        type            noSlip;
    }}

    frontAndBack
    {{
        type            empty;
    }}
}}
"""


def _pressure() -> str:
    return f"""{_foam("p", "volScalarField")}
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
    farfield
    {{
        type            freestreamPressure;
        freestreamValue $internalField;
    }}

    outlet
    {{
        type            freestreamPressure;
        freestreamValue $internalField;
    }}

    walls
    {{
        type            zeroGradient;
    }}

    frontAndBack
    {{
        type            empty;
    }}
}}
"""


def _geometry(spec: Mapping[str, object], mesh: Mapping[str, object]) -> tuple[
    list[_Sample], int, list[_Hex], list[tuple[int, int, list[tuple[float, float]]]]
]:
    chord_m = float(spec.get("chord_m", 1.0))
    span_m = float(spec.get("span_m", Bounds.z_thickness))
    samples, crown = naca_samples(spec["naca"], chord_m=chord_m)
    div = _divisions(int(mesh["stream"]), str(spec["density"]))
    xy = [(x, y) for x, y, _z in _vertices(samples, crown, chord_m=chord_m, span_m=span_m)[:12]]
    blocks = _orient(_hexes(div, float(mesh["grading"])), xy)
    edges = _airfoil_edges(samples, crown, span_m=span_m)
    return samples, crown, blocks, edges


def render(spec: Mapping[str, object], mesh: Mapping[str, object], solve: Mapping[str, object]) -> dict[str, str]:
    """Typed spec to case file text. Points stay at alpha = 0; alpha is the freestream."""
    alpha = math.radians(float(spec["alpha_deg"]))
    speed = float(spec["u_ms"])
    chord_m = float(spec.get("chord_m", 1.0))
    span_m = float(spec.get("span_m", Bounds.z_thickness))
    samples, crown, blocks, edges = _geometry(spec, mesh)
    return {
        "system/blockMeshDict": _block_mesh_dict(
            samples, crown, blocks, edges, chord_m=chord_m, span_m=span_m
        ),
        "system/controlDict": _control(alpha, speed, chord_m=chord_m, span_m=span_m),
        "system/fvSchemes": _schemes(),
        "system/fvSolution": _solution(float(solve["u_relax"]), int(solve["non_ortho"])),
        "constant/transportProperties": _transport(),
        "constant/turbulenceProperties": _turbulence(),
        "0/U": _velocity(alpha, speed),
        "0/p": _pressure(),
        "0/nuTilda": _nu_tilda(),
        "0/nut": _nut(),
    }


def write_case(dest: Path, files: Mapping[str, str]) -> None:
    dest = Path(dest)
    for rel, text in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def _shell_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


def _para_view(foam: Path) -> str:
    """ParaView startup script. Shows the wing and the mid-span speed field."""
    literal = json.dumps(str(foam))
    return f"""from paraview.simple import *

foam = {literal}
walls = OpenFOAMReader(FileName=foam)
walls.MeshRegions = ["patch/walls"]
walls.CellArrays = ["p", "U"]
flow = OpenFOAMReader(FileName=foam)
flow.MeshRegions = ["internalMesh"]
flow.CellArrays = ["U", "p"]
flow.UpdatePipelineInformation()
times = list(flow.TimestepValues)
last = times[-1] if times else 0.0
walls.UpdatePipeline(last)
flow.UpdatePipeline(last)

view = GetActiveViewOrCreate("RenderView")
view.ViewTime = last
view.Background = [0.08, 0.09, 0.11]
view.UseColorPaletteForBackground = 0
view.OrientationAxesVisibility = 1

bounds = walls.GetDataInformation().GetBounds()
span_x = bounds[1] - bounds[0]
span_y = bounds[3] - bounds[2]
if span_x <= 0.0 and span_y <= 0.0:
    cx, cy, cz, chord = 0.5, 0.0, 0.05, 1.0
else:
    cx = (bounds[0] + bounds[1]) / 2.0
    cy = (bounds[2] + bounds[3]) / 2.0
    cz = (bounds[4] + bounds[5]) / 2.0
    chord = max(span_x, span_y, 1e-6)

plane = Slice(Input=flow)
plane.SliceType = "Plane"
plane.SliceType.Origin = [cx, cy, cz]
plane.SliceType.Normal = [0.0, 0.0, 1.0]
HideInteractiveWidgets(plane)
speed = Show(plane, view)
speed.Representation = "Surface"
ColorBy(speed, ("CELLS", "U", "Magnitude"))
speed.RescaleTransferFunctionToDataRange(True, False)
lut = GetColorTransferFunction("U")
lut.ApplyPreset("Cool to Warm", True)
speed.SetScalarBarVisibility(view, True)
bar = GetScalarBar(lut, view)
bar.Title = "speed"
bar.ComponentTitle = ""

body = Show(walls, view)
body.Representation = "Surface With Edges"
ColorBy(body, ("CELLS", "p"))
body.RescaleTransferFunctionToDataRange(True, False)
body.SetScalarBarVisibility(view, False)
body.EdgeColor = [0.95, 0.95, 0.95]
body.LineWidth = 2.0

for source in list(GetSources().values()):
    if source not in (walls, flow, plane):
        Hide(source, view)

Render()
camera = view.GetActiveCamera()
camera.SetParallelProjection(True)
camera.SetFocalPoint(cx, cy, cz)
camera.SetPosition(cx, cy, cz + 5.0)
camera.SetViewUp(0.0, 1.0, 0.0)
camera.SetParallelScale(0.7 * chord)
Render()
"""


def open_parafoam(case: Path) -> subprocess.Popen:
    """Open this case in paraFoam and leave the window up. Fixed launcher, no shell=True."""
    case = Path(case).resolve()
    if not (case / "system" / "controlDict").is_file():
        raise FileNotFoundError("case")
    if not shutil.which("openfoam"):
        raise RuntimeError("paraFoam's launcher is not on PATH")
    foam = case / (case.name + ".foam")
    script = case / "paraView.py"
    script.write_text(_para_view(foam))
    # ParaView 6 rejects --script together with --data, and paraFoam adds --data
    # unless -touch is used. -touch writes the .foam file; the script then opens it.
    snippet = (
        "export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8; "
        "paraFoam -vtk -touch -case "
        + _shell_quote(case)
        + " && paraview --script="
        + _shell_quote(script)
    )
    log_path = case / "log.paraFoam"
    log = log_path.open("ab")
    try:
        proc = subprocess.Popen(
            ["openfoam", "-c", snippet],
            cwd=case,
            start_new_session=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    finally:
        log.close()
    try:
        code = proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        return proc
    detail = ""
    if log_path.is_file():
        detail = log_path.read_text(errors="replace")[-2000:].strip()
    raise RuntimeError(detail or f"paraFoam exited {code}")


def run_foam(case: Path, which: str, timeout_s: int) -> int:
    """Run one fixed snippet. Never shell=True. Pilot's cwd is the parent of ./case."""
    if which not in SNIPPETS:
        raise ValueError("which")
    case = Path(case)
    case.mkdir(parents=True, exist_ok=True)
    launcher = case / "log.launcher"
    with launcher.open("w") as handle:
        proc = subprocess.Popen(
            ["openfoam", "-c", SNIPPETS[which]],
            cwd=case,
            start_new_session=True,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        try:
            return int(proc.wait(timeout=timeout_s))
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            return proc.returncode if proc.returncode is not None else 124


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive flock. Released when the holder returns, or when the process dies."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _fsync_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def persist_receipt(ledger: Path, receipt: Mapping[str, object]) -> None:
    """Write the receipt, fsync, append the index pointer, fsync."""
    ledger = Path(ledger)
    ledger.mkdir(parents=True, exist_ok=True)
    name = f"{receipt['id']}.json"
    _fsync_write(ledger / name, json.dumps(dict(receipt), indent=2) + "\n")
    _fsync_append(ledger / "index.jsonl", json.dumps({"file": name}) + "\n")


def commit_under_lock(run_dir: Path, commit: Commit) -> Admission:
    """admit under runs/<id>/case.lock. A rejected commit writes nothing."""
    run_dir = Path(run_dir)
    ledger = run_dir / "ledger"
    with file_lock(run_dir / "case.lock"):
        receipts = load_ledger(ledger) if ledger.is_dir() else []
        admission = admit(receipts, commit)
        if admission.appended and admission.receipt is not None:
            persist_receipt(ledger, admission.receipt)
        return admission


def append_observe(store: Path, record: Mapping[str, object]) -> None:
    store = Path(store)
    line = json.dumps(dict(record), sort_keys=True, default=str) + "\n"
    with file_lock(store / "observe.lock"):
        _fsync_append(store / "observe.jsonl", line)


def _inverted(text: str) -> bool:
    """True only when the log reports inverted or negative cells. Mesh OK is not inverted."""
    if "Mesh OK" in text:
        return False
    lowered = text.lower().replace("face pyramids ok", "")
    markers = (
        "inverted cell",
        "cells inverted",
        "inverted cells",
        "negative volume",
        "negative cell",
        "illegal cells",
        "zero or negative",
        "incorrectly oriented",
    )
    return any(marker in lowered for marker in markers)


def _block_indexes(text: str, count: int) -> list[int]:
    found = []
    for match in re.findall(r"\bblock\s+(\d+)\b", text, flags=re.IGNORECASE):
        index = int(match)
        if 0 <= index < count and index not in found:
            found.append(index)
    return found


def _clear_mesh(case: Path) -> None:
    mesh = case / "constant" / "polyMesh"
    if mesh.exists():
        shutil.rmtree(mesh)


def patch_nfaces(boundary_text: str, name: str) -> int | None:
    match = re.search(rf"\b{name}\s*\{{[^}}]*?\bnFaces\s+(\d+)", boundary_text, re.DOTALL)
    if match is None:
        return None
    return int(match.group(1))


def run_block_mesh(
    dest: Path,
    spec: Mapping[str, object],
    mesh: Mapping[str, object],
    solve: Mapping[str, object],
    *,
    timeout_s: int | None = None,
) -> dict:
    """Write the case, run blockMesh and checkMesh. Flip one inverted block once."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    samples, crown, blocks, edges = _geometry(spec, mesh)
    files = render(spec, mesh, solve)
    write_case(dest, files)
    timeout = Bounds.mesh_timeout_s if timeout_s is None else timeout_s
    _clear_mesh(dest)
    code = run_foam(dest, "mesh", timeout)
    check_path = dest / "log.checkMesh"
    block_path = dest / "log.blockMesh"
    check_text = check_path.read_text() if check_path.is_file() else ""
    block_text = block_path.read_text() if block_path.is_file() else ""
    flipped: list[str] = []
    if _inverted(check_text + "\n" + block_text) and "Mesh OK" not in check_text:
        indexes = _block_indexes(check_text + "\n" + block_text, len(blocks))
        if indexes:
            for index in indexes:
                blocks[index] = blocks[index].flip()
                flipped.append(blocks[index].name)
            chord_m = float(spec.get("chord_m", 1.0))
            span_m = float(spec.get("span_m", Bounds.z_thickness))
            files["system/blockMeshDict"] = _block_mesh_dict(
                samples, crown, blocks, edges, chord_m=chord_m, span_m=span_m
            )
            write_case(dest, files)
            _clear_mesh(dest)
            code = run_foam(dest, "mesh", timeout)
            check_text = check_path.read_text() if check_path.is_file() else ""
            block_text = block_path.read_text() if block_path.is_file() else ""
    boundary = dest / "constant" / "polyMesh" / "boundary"
    boundary_text = boundary.read_text() if boundary.is_file() else ""
    ncells_line = ""
    for line in block_text.splitlines():
        if "nCells:" in line:
            ncells_line = line.strip()
    return {
        "exit_code": code,
        "mesh_ok": "Mesh OK" in check_text,
        "ncells_line": ncells_line,
        "walls_nfaces": patch_nfaces(boundary_text, "walls"),
        "check_log": str(check_path),
        "block_log": str(block_path),
        "flipped": flipped,
        "case": str(dest),
    }
