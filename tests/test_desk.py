"""Workbench routing, steer knobs, and case-file views. No OpenFOAM."""

import json
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

from wingline.files import highlights, jail, read_view
from wingline.steer import append_steer, interpret_steer, steer_fresh, steer_texts
from wingline.web import Workbench, clear_session, serve

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
        page.close()
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
    _ = read_view
    _ = ROOT
