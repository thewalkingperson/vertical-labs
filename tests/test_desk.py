"""Workbench routing, steer knobs, and case-file views. No OpenFOAM."""

import json
import os
import shutil
import subprocess
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

from wingline.engineer import EngineerView
from wingline.files import highlights, jail, list_runs, read_view
from wingline.kernel import Bounds, DesignGrade, Refusal
from wingline.meta import Study
from wingline.outer import EXPLORE_CAP
from wingline.steer import append_steer, interpret_steer, steer_fresh, steer_texts
from wingline.case_writer import open_parafoam
from wingline.web import Workbench, clear_session, narrate, serve

ROOT = Path(__file__).resolve().parents[1]


def test_steer_stop_and_mesh_knob() -> None:
    assert interpret_steer(["please stop"], "mesh")["action"] == "stop"
    choice = interpret_steer(["try grading 12"], "mesh")
    assert choice["action"] == "knob"
    assert choice["grading"] == 12
    assert "stream" not in choice


def test_fresh_steer_is_delivered_once(tmp_path: Path) -> None:
    run = tmp_path / "run-1"
    run.mkdir()
    append_steer(run, "grading 4")
    assert steer_texts(run) == ["grading 4"]
    assert steer_fresh(run) == ["grading 4"]
    assert steer_fresh(run) == []
    assert steer_texts(run) == ["grading 4"]


def test_file_jail_rejects_a_parent_path(tmp_path: Path) -> None:
    run = tmp_path / "run-1"
    run.mkdir()
    (run / "spec.json").write_text("{}\n")
    try:
        jail(run, "../spec.json")
    except ValueError:
        return
    raise AssertionError("parent path was accepted")


def test_highlights_name_mesh_and_forces() -> None:
    mesh = "Mesh non-orthogonality Max: 59.48\nMax skewness = 3.658119 OK.\nMesh OK.\n"
    chips = {item["label"]: item["value"] for item in highlights("case/log.checkMesh", mesh)}
    assert chips["Mesh"] == "OK"
    assert chips["Non-ortho"] == "59.48"
    coeff = "# Time Cl Cd\n200 0.2326 0.1381\n"
    forces = {item["label"]: item["value"] for item in highlights("case/postProcessing/forceCoeffs/0/coefficient.dat", coeff)}
    assert forces["Cl"] == "0.2326"
    assert forces["Cd"] == "0.1381"


def test_narrate_names_the_current_step(tmp_path: Path) -> None:
    bench = Workbench(tmp_path, runner=lambda *_args: None)
    idle = bench.snapshot()
    assert idle["who"] == ""
    assert idle["status"] == "Idle"
    assert idle["detail"] == ""

    bench.phase = "asking"
    waiting = bench.snapshot()
    assert waiting["who"] == "Meta"
    assert waiting["status"] == "Waiting for you"
    assert "missing fact" in waiting["detail"]

    bench.phase = "running"
    reading = bench.snapshot()
    assert reading["who"] == "Meta"
    assert reading["status"] == "Reading the request"
    assert "OpenFOAM" in reading["detail"]

    run = tmp_path / "run-wing"
    case = run / "case"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text("application simpleFoam;\n")
    (case / "12").mkdir()
    (case / "log.simpleFoam").write_text("Time = 11\n\nTime = 12\n\nSolving for Ux\n")
    (run / "spec.json").write_text(json.dumps({"naca": "2412", "alpha_deg": 3, "u_ms": 10}))
    bench.active_run = "run-wing"
    solving = bench.snapshot()
    assert solving["who"] == "Inner"
    assert solving["status"] == "Solving step 12"
    assert "NACA 2412" in solving["detail"]
    assert "simpleFoam is on step 12" in solving["detail"]

    bench.exploring = True
    neighbor = bench.snapshot()
    assert neighbor["who"] == "Outer"
    assert neighbor["status"] == "Exploring a neighbor"
    assert "asked wing is already stored" in neighbor["detail"]
    assert "NACA 2412" in neighbor["detail"]
    assert "simpleFoam" in neighbor["detail"]

    (case / "log.simpleFoam").write_text("Time = 200\n\nEnd\n")
    graded = narrate(tmp_path, "running", False, "run-wing")
    assert graded["status"] == "Grading the forces"
    assert "paraFoam" in graded["detail"]

    (case / "log.simpleFoam").unlink()
    (case / "log.checkMesh").write_text("Mesh OK.\n")
    mesh = narrate(tmp_path, "running", False, "run-wing")
    assert mesh["who"] == "Inner"
    assert mesh["status"] == "Checking the mesh"
    assert "checkMesh" in mesh["detail"]


def test_message_during_a_run_steers_that_run(tmp_path: Path) -> None:
    run = tmp_path / "run-abc"
    run.mkdir()
    bench = Workbench(tmp_path, runner=lambda *_args: None)
    bench.phase = "running"
    bench.active_run = "run-abc"
    result = bench.submit("use grading 12")
    assert result["mode"] == "steer"
    assert "grading 12" in (run / "steer.jsonl").read_text()


def test_an_answer_reaches_the_open_question(tmp_path: Path) -> None:
    got = threading.Event()

    def runner(text, reply, _on_run):
        assert text == "NACA 2412"
        assert reply("What angle?") == "6"
        got.set()

    bench = Workbench(tmp_path, runner=runner)
    assert bench.submit("NACA 2412")["mode"] == "study"
    for _ in range(50):
        if bench.phase == "asking":
            break
        time.sleep(0.02)
    assert bench.phase == "asking"
    assert bench.submit("6")["mode"] == "answer"
    assert got.wait(2)


def test_restart_clears_chat_and_run_files(tmp_path: Path) -> None:
    run = tmp_path / "run-old"
    (run / "case").mkdir(parents=True)
    (run / "case" / "log.checkMesh").write_text("Mesh OK.\n")
    (tmp_path / "desk.jsonl").write_text('{"role":"you","text":"hello"}\n')
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "dialogue.jsonl").write_text("{}\n")
    (tmp_path / "last_spec.json").write_text("{}\n")
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    (pilot / "latest.json").write_text("{}\n")
    clear_session(tmp_path)
    assert not (tmp_path / "desk.jsonl").exists()
    assert not run.exists()
    assert not (tmp_path / "meta").exists()
    assert not (tmp_path / "last_spec.json").exists()
    assert (pilot / "latest.json").is_file()
    bench = Workbench(tmp_path, runner=lambda *_args: None)
    assert bench.snapshot()["messages"] == []
    assert bench.snapshot()["runs"] == []


def test_reset_clears_the_chat_and_keeps_the_run(tmp_path: Path) -> None:
    run = tmp_path / "run-keep"
    (run / "case").mkdir(parents=True)
    (run / "spec.json").write_text(json.dumps({"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"}))
    (tmp_path / "desk.jsonl").write_text('{"role":"you","text":"hello"}\n')
    bench = Workbench(tmp_path, runner=lambda *_args: None)
    assert bench.snapshot()["messages"][0]["text"] == "hello"
    result = bench.reset_chat()
    assert result["ok"] is True
    assert result["messages"] == []
    assert not (tmp_path / "desk.jsonl").exists()
    assert result["runs"][0]["id"] == "run-keep"
    bench._remember("result", "goal: kept")
    assert [row["text"] for row in bench.snapshot()["messages"]] == ["goal: kept"]


def test_reset_keeps_the_open_question(tmp_path: Path) -> None:
    started = threading.Event()
    done = threading.Event()

    def runner(_text, reply, _on_run):
        started.set()
        assert reply("What angle?") == "6"
        done.set()

    bench = Workbench(tmp_path, runner=runner)
    assert bench.submit("a wing with low drag")["mode"] == "study"
    assert started.wait(2)
    for _ in range(50):
        if bench.phase == "asking":
            break
        time.sleep(0.02)
    result = bench.reset_chat()
    assert [row["text"] for row in result["messages"]] == ["What angle?"]
    assert bench.phase == "asking"
    assert bench.submit("6")["mode"] == "answer"
    assert done.wait(2)


def test_constant_exploration_stores_ten_and_stays_quiet(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WINGLINE_AGENT", "1")
    bench = Workbench(tmp_path)
    seen: list[str] = []

    class Quiet:
        def ask(self, text, show=None, on_run=None):
            assert os.environ.get("WINGLINE_AGENT") == "0"
            seen.append(text)
            if on_run:
                on_run("run-neighbor")
            return EngineerView("run-neighbor", Refusal(gate="parse", log="", reason="stored", panels=()))

    bench.engineer = Quiet()
    study = Study(
        {"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"},
        "max_ld",
        True,
        "NACA 2412 at 6 degrees and 10 m/s.",
        (),
    )
    origin = EngineerView("run-direct", Refusal(gate="physics", log="", reason="direct", panels=()))
    bench._search(study, origin)
    assert len(seen) == EXPLORE_CAP == 10
    assert bench.messages == []
    assert bench.exploring is False
    assert os.environ.get("WINGLINE_AGENT") == "1"
    saved = json.loads((tmp_path / "run-direct" / "outer" / "search.json").read_text())
    assert saved["budget"] == 10
    assert len(saved["rows"]) == 11
    bench.set_explore(False)
    seen.clear()
    bench._search(study, origin)
    assert seen == []
    assert bench.snapshot()["explore"] is False


def test_http_serves_the_page_and_a_case_file(tmp_path: Path) -> None:
    run = tmp_path / "run-demo"
    (run / "case").mkdir(parents=True)
    (run / "spec.json").write_text(json.dumps({"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"}))
    (run / "case" / "log.checkMesh").write_text("Mesh OK.\nMax skewness = 3.66 OK.\n")
    bench = Workbench(tmp_path, runner=lambda *_args: None)
    server = serve(bench, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        page = HTTPConnection("127.0.0.1", port, timeout=5)
        page.request("GET", "/")
        html = page.getresponse().read().decode()
        assert 'id="message"' in html
        assert 'id="reset"' in html
        assert 'id="thinking"' in html
        assert "Reading the request." in html
        assert 'id="explore-toggle"' in html
        assert 'id="refine"' in html
        assert "Run the finer mesh" in html
        assert 'id="paraview"' in html
        assert "Open in paraFoam" in html
        assert 'id="board"' in html
        page.close()
        toggle = HTTPConnection("127.0.0.1", port, timeout=5)
        body = json.dumps({"on": False}).encode()
        toggle.request("POST", "/api/explore", body, {"Content-Type": "application/json"})
        switched = json.loads(toggle.getresponse().read().decode())
        toggle.close()
        assert switched["ok"] is True
        assert switched["explore"] is False
        script_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        script_conn.request("GET", "/app.js")
        script = script_conn.getresponse().read().decode()
        script_conn.close()
        assert "showThinking" in script
        assert "worth_finer" in script
        assert "best neighbor" in script
        style_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        style_conn.request("GET", "/app.css")
        style = style_conn.getresponse().read().decode()
        style_conn.close()
        assert "wingline-blink" in style
        state_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        state_conn.request("GET", "/api/state")
        state = json.loads(state_conn.getresponse().read().decode())
        state_conn.close()
        assert state["phase"] == "idle"
        assert state["runs"][0]["naca"] == "2412"
        file_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        file_conn.request("GET", "/api/runs/run-demo/file?path=case/log.checkMesh")
        view = json.loads(file_conn.getresponse().read().decode())
        file_conn.close()
        assert view["highlights"][0]["value"] == "OK"
        escaped = HTTPConnection("127.0.0.1", port, timeout=5)
        escaped.request("GET", "/api/runs/run-demo/file?path=../spec.json")
        assert escaped.getresponse().status == 404
        escaped.close()
        bench._remember("you", "clear me")
        reset_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        reset_conn.request("POST", "/api/reset", b"{}")
        reset = json.loads(reset_conn.getresponse().read().decode())
        reset_conn.close()
        assert reset["ok"] is True
        assert reset["messages"] == []
        assert reset["runs"][0]["id"] == "run-demo"
        assert not (tmp_path / "desk.jsonl").exists()
    finally:
        server.shutdown()


def _copy_ledger(src: Path, run: Path) -> None:
    shutil.copytree(src, run / "ledger")


def _wait_idle(bench: Workbench) -> None:
    for _ in range(100):
        if bench.phase == "idle":
            return
        time.sleep(0.02)
    raise AssertionError("refine did not finish")


def test_parafoam_opens_one_window_for_a_case(tmp_path: Path, monkeypatch) -> None:
    case = tmp_path / "case"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text("application simpleFoam;\n")
    captured: dict[str, object] = {}

    class Alive:
        def wait(self, timeout: float = 0) -> int:
            raise subprocess.TimeoutExpired(cmd="paraFoam", timeout=timeout)

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return Alive()

    monkeypatch.setattr("wingline.case_writer.shutil.which", lambda _name: "/usr/bin/launcher")
    monkeypatch.setattr("wingline.case_writer.subprocess.Popen", fake_popen)
    open_parafoam(case)
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "open" + "foam"
    assert cmd[1] == "-c"
    assert str(cmd[2]).startswith("export LANG=")
    assert "paraFoam -vtk -touch -case " in str(cmd[2])
    assert str(case.resolve()) in str(cmd[2])
    assert "paraview --script=" in str(cmd[2])
    script = (case / "paraView.py").read_text()
    assert "OpenFOAMReader" in script
    assert "patch/walls" in script
    assert "internalMesh" in script
    assert str((case / "case.foam").resolve()) in script

    run = tmp_path / "run-design"
    (run / "case" / "system").mkdir(parents=True)
    (run / "case" / "system" / "controlDict").write_text("application simpleFoam;\n")
    seen: list[Path] = []

    class Held:
        def poll(self) -> None:
            return None

    def starter(path: Path) -> Held:
        seen.append(Path(path))
        return Held()

    bench = Workbench(tmp_path, runner=lambda *_args: None)
    bench._start_parafoam = starter
    server = serve(bench, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        body = json.dumps({"run_id": "run-design"}).encode()
        first = HTTPConnection("127.0.0.1", port, timeout=5)
        first.request("POST", "/api/paraview", body, {"Content-Type": "application/json"})
        opened = json.loads(first.getresponse().read().decode())
        first.close()
        assert opened["ok"] is True
        assert opened["showing"] is True
        second = HTTPConnection("127.0.0.1", port, timeout=5)
        second.request("POST", "/api/paraview", body, {"Content-Type": "application/json"})
        again = json.loads(second.getresponse().read().decode())
        second.close()
        assert again["showing"] is True
        assert len(seen) == 1
        assert seen[0] == (run / "case").resolve()
        bare = tmp_path / "run-empty"
        bare.mkdir()
        missing = HTTPConnection("127.0.0.1", port, timeout=5)
        missing.request(
            "POST",
            "/api/paraview",
            json.dumps({"run_id": "run-empty"}).encode(),
            {"Content-Type": "application/json"},
        )
        refused = json.loads(missing.getresponse().read().decode())
        missing.close()
        assert refused["ok"] is False
        assert refused["error"] == "case"
        assert len(seen) == 1
    finally:
        server.shutdown()


def test_asked_wing_stays_first_and_neighborhood_appends_outward_in(tmp_path: Path) -> None:
    def write_spec(path: Path, naca: str) -> None:
        path.mkdir()
        (path / "spec.json").write_text(
            json.dumps({"naca": naca, "alpha_deg": 6, "u_ms": 10, "density": "coarse"})
        )

    outer = tmp_path / "run-outer"
    near = tmp_path / "run-near"
    asked = tmp_path / "run-asked"
    write_spec(outer, "6412")
    write_spec(near, "3412")
    write_spec(asked, "2412")
    (outer / "origin").write_text("run-asked\n")
    (near / "origin").write_text("run-asked\n")
    os.utime(outer, None)
    assert [row["id"] for row in list_runs(tmp_path)] == ["run-asked", "run-outer", "run-near"]
    later = tmp_path / "run-later"
    write_spec(later, "0012")
    assert [row["id"] for row in list_runs(tmp_path)] == [
        "run-later",
        "run-asked",
        "run-outer",
        "run-near",
    ]


def test_comparison_board_marks_the_stored_neighbor(tmp_path: Path) -> None:
    direct = tmp_path / "run-direct"
    neighbor = tmp_path / "run-neighbor"
    direct.mkdir()
    neighbor.mkdir()
    (direct / "outer").mkdir()
    search = {
        "objective": "max_ld",
        "budget": 2,
        "rows": [
            {
                "run_id": "run-direct",
                "naca": "2412",
                "alpha_deg": 6,
                "cl": 0.23,
                "cd": 0.14,
                "gate": "physics",
            },
            {
                "run_id": "run-neighbor",
                "naca": "2410",
                "alpha_deg": 6,
                "cl": 0.4,
                "cd": 0.1,
                "gate": "screening",
            },
            {
                "run_id": "run-mesh",
                "naca": "3412",
                "alpha_deg": 6,
                "cl": None,
                "cd": None,
                "gate": "mesh",
            },
            {
                "run_id": "run-outer",
                "naca": "6412",
                "alpha_deg": 6,
                "cl": 0.1,
                "cd": 0.2,
                "gate": "screening",
            },
        ],
        "best_naca": "3412",
        "best_alpha_deg": 6,
    }
    (direct / "outer" / "search.json").write_text(json.dumps(search))
    bench = Workbench(tmp_path, runner=lambda *_args: None)
    server = serve(bench, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/runs/run-neighbor/board")
        payload = json.loads(conn.getresponse().read().decode())
        conn.close()
    finally:
        server.shutdown()
    assert payload["ready"] is True
    assert payload["run_id"] == "run-direct"
    assert payload["sentence"] == Bounds.design_sentence
    assert "flight-hardware release" in payload["sentence"]
    assert [row["naca"] for row in payload["rows"]] == ["2412", "6412", "2410", "3412"]
    assert payload["rows"][0]["direct"] is True
    assert payload["rows"][0]["best"] is False
    assert payload["rows"][1]["naca"] == "6412"
    assert payload["rows"][1]["best"] is False
    assert payload["rows"][2]["cl"] == 0.4
    assert payload["rows"][2]["best"] is False
    assert payload["rows"][3]["best"] is True
    assert payload["rows"][3]["cl"] is None
    assert payload["rows"][3]["gate"] == "mesh"
    assert bench.messages == []


def test_refine_stays_a_refusal_until_the_screening_says_so(tmp_path: Path) -> None:
    parent = tmp_path / "run-parent"
    _copy_ledger(ROOT / "fixtures" / "naca2412-a6" / "ledger_green", parent)
    (parent / "outer").mkdir()
    shutil.copy(
        ROOT / "fixtures" / "naca2412-a6" / "outer_challenge.json",
        parent / "outer" / "verdict.json",
    )
    (parent / "spec.json").write_text(
        json.dumps({"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"})
    )
    bench = Workbench(tmp_path)
    bench.open()
    server = serve(bench, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        state_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        state_conn.request("GET", "/api/state")
        state = json.loads(state_conn.getresponse().read().decode())
        state_conn.close()
        assert state["runs"][0]["worth_finer"] is False
        assert state["runs"][0]["delta"] is None
        body = json.dumps({"run_id": "run-parent"}).encode()
        refine_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        refine_conn.request("POST", "/api/refine", body, {"Content-Type": "application/json"})
        started = json.loads(refine_conn.getresponse().read().decode())
        refine_conn.close()
        assert started["ok"] is True
        _wait_idle(bench)
        texts = [row["text"] for row in bench.snapshot()["messages"]]
        assert any("Refused at gate commission" in text for text in texts)
        assert [path.name for path in tmp_path.iterdir() if path.is_dir() and path.name.startswith("run-")] == [
            "run-parent"
        ]
    finally:
        server.shutdown()
        bench.close()


def test_finer_mesh_shows_the_delta_beside_the_coarse_case(tmp_path: Path) -> None:
    fix = ROOT / "fixtures" / "naca2412-a6"
    parent = tmp_path / "run-parent"
    child = tmp_path / "run-child"
    _copy_ledger(fix / "ledger_design" / "parent", parent)
    _copy_ledger(fix / "ledger_design" / "child", child)
    (parent / "outer").mkdir()
    (child / "outer").mkdir()
    shutil.copy(fix / "outer_affirm.json", parent / "outer" / "verdict.json")
    shutil.copy(fix / "outer_affirm.json", child / "outer" / "verdict.json")
    (parent / "spec.json").write_text(
        json.dumps({"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "coarse"})
    )
    (child / "spec.json").write_text(
        json.dumps({"naca": "2412", "alpha_deg": 6, "u_ms": 10, "density": "refined"})
    )
    (child / "parent").write_text("run-parent\n")
    called: list[str] = []

    class Stub:
        def refine(self, run_id, show=None, on_run=None):
            called.append(run_id)
            return EngineerView(
                "run-child",
                DesignGrade(
                    run_id="run-child",
                    parent_run_id=run_id,
                    cl=0.89,
                    cd=0.018,
                    delta_cl=0.03,
                    delta_cd=-0.002,
                    sentence=Bounds.design_sentence,
                ),
            )

    bench = Workbench(tmp_path, runner=lambda *_args: None)
    bench.engineer = Stub()
    rows = {row["id"]: row for row in list_runs(tmp_path)}
    assert rows["run-parent"]["worth_finer"] is True
    assert rows["run-child"]["worth_finer"] is False
    assert abs(rows["run-parent"]["delta"]["delta_cl"] - (0.855949564 - 0.882458973)) < 1e-9
    assert abs(rows["run-parent"]["delta"]["delta_cd"] - (0.0280312507 - 0.0284136587)) < 1e-9
    assert rows["run-parent"]["delta"]["sentence"] == Bounds.design_sentence
    server = serve(bench, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        picture_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        picture_conn.request("GET", "/api/runs/run-parent/picture")
        picture = json.loads(picture_conn.getresponse().read().decode())
        picture_conn.close()
        assert picture["ready"] is False
        assert picture["quads"] == []
        body = json.dumps({"run_id": "run-parent"}).encode()
        refine_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        refine_conn.request("POST", "/api/refine", body, {"Content-Type": "application/json"})
        started = json.loads(refine_conn.getresponse().read().decode())
        refine_conn.close()
        assert started["ok"] is True
        _wait_idle(bench)
        assert called == ["run-parent"]
        names = sorted(path.name for path in tmp_path.iterdir() if path.is_dir() and path.name.startswith("run-"))
        assert names == ["run-child", "run-parent"]
        state_conn = HTTPConnection("127.0.0.1", port, timeout=5)
        state_conn.request("GET", "/api/state")
        state = json.loads(state_conn.getresponse().read().decode())
        state_conn.close()
        parent_row = next(row for row in state["runs"] if row["id"] == "run-parent")
        assert parent_row["worth_finer"] is True
        assert abs(parent_row["delta"]["delta_cl"] - (0.855949564 - 0.882458973)) < 1e-9
        assert abs(parent_row["delta"]["delta_cd"] - (0.0280312507 - 0.0284136587)) < 1e-9
    finally:
        server.shutdown()
