"""Mesh picture from a tiny ASCII case. No OpenFOAM."""

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from wingline.picture import flow_picture
from wingline.web import Workbench, serve


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_picture_draws_the_front_face_and_the_forces(tmp_path: Path) -> None:
    case = tmp_path / "case"
    _write(
        case / "constant" / "polyMesh" / "points",
        "8\n(\n(0 0 0)\n(1 0 0)\n(1 0.12 0)\n(0 0.12 0)\n"
        "(0 0 0.1)\n(1 0 0.1)\n(1 0.12 0.1)\n(0 0.12 0.1)\n)\n",
    )
    _write(
        case / "constant" / "polyMesh" / "faces",
        "3\n(\n4(0 1 2 3)\n4(4 5 6 7)\n4(0 1 5 4)\n)\n",
    )
    _write(case / "constant" / "polyMesh" / "owner", "3\n(\n0\n0\n0\n)\n")
    _write(
        case / "constant" / "polyMesh" / "boundary",
        "walls { type wall; nFaces 1; startFace 2; }\n",
    )
    _write(case / "200" / "p", "internalField nonuniform List<scalar>\n1\n(\n2.5\n);\nboundaryField {}\n")
    _write(case / "200" / "U", "internalField nonuniform List<vector>\n1\n(\n(3 4 0)\n);\nboundaryField {}\n")
    _write(
        case / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat",
        "# Time Cd Cl\n1 0.10 0.20\n2 0.11 0.25\n",
    )
    (tmp_path / "spec.json").write_text(json.dumps({"naca": "2412", "alpha_deg": 6}))
    picture = flow_picture(tmp_path)
    assert picture["ready"] is True
    assert picture["title"] == "NACA 2412 at 6°"
    assert picture["alpha_deg"] == 6
    assert "Wind at 6°" in picture["note"]
    assert picture["cells"] == 1
    assert len(picture["quads"]) == 1
    quad = picture["quads"][0]
    assert quad[8] == 2.5
    assert quad[9] == 5.0
    assert picture["airfoil"] == [[0.0, 0.0, 1.0, 0.0]]
    assert picture["forces"]["cl"] == [0.2, 0.25]
    assert picture["forces"]["cd"] == [0.1, 0.11]
    assert picture["view"][0] < 0
    assert picture["view"][2] > 1


def test_picture_route_is_quiet_before_the_mesh(tmp_path: Path) -> None:
    (tmp_path / "run-demo").mkdir()
    bench = Workbench(tmp_path, runner=lambda *_args: None)
    server = serve(bench, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/runs/run-demo/picture")
        payload = json.loads(conn.getresponse().read().decode())
        conn.close()
    finally:
        server.shutdown()
    assert payload["ready"] is False
    assert payload["quads"] == []
