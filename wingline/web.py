"""Local workbench. One conversation, the OpenFOAM runs, and their files.

    python -m wingline.web
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from urllib.parse import parse_qs, unquote, urlparse

from wingline.brief import engineer_brief
from wingline.suggest import close_paragraph
from wingline.engineer import Engineer
from wingline.files import list_files, list_runs, read_view
from wingline.kernel import Bounds, Refusal
from wingline.meta import Study, resolve
from wingline.outer import EXPLORE_CAP, board_view, explore, mark_origin
from wingline.mcp_server import show_case
from wingline.picture import flow_picture
from wingline.steer import append_steer

_WEB = Path(__file__).resolve().parents[1] / "web"
_HINTS = {
    "idle": "Ask for a wing. A full NACA sentence starts immediately.",
    "asking": "The study needs an answer before the next solve.",
    "running": "A run is open. A message now steers the agent on its next turn.",
}
_TIME = re.compile(r"^Time = (\d+)\s*$", re.MULTILINE)


def _tail(path: Path, size: int = 6000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            end = handle.tell()
            handle.seek(max(0, end - size))
            return handle.read().decode(errors="replace")
    except OSError:
        return ""


def _spec_of(run_dir: Path) -> dict:
    path = run_dir / "spec.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _wing_label(spec: dict) -> str:
    naca = spec.get("naca")
    if not naca:
        return "This wing"
    return f"NACA {naca} at {spec.get('alpha_deg')} degrees and {spec.get('u_ms')} m/s"


def _saved_steps(case: Path) -> int:
    count = 0
    if not case.is_dir():
        return 0
    for child in case.iterdir():
        if not child.is_dir():
            continue
        try:
            float(child.name)
        except ValueError:
            continue
        count += 1
    return count


def _case_step(case: Path) -> tuple[str, str]:
    """Short header, then the sentence for the thinking row."""
    if not (case / "system" / "controlDict").is_file() and not (case / "log.blockMesh").is_file():
        return (
            "Checking the cylinder",
            "The cylinder health check runs before this wing. Then the mesh, the solve, and the forces.",
        )
    foam = case / "log.simpleFoam"
    if foam.is_file():
        tail = _tail(foam)
        ended = any(line.strip() == "End" for line in tail.splitlines()[-12:])
        times = _TIME.findall(tail)
        step = times[-1] if times else ""
        saved = _saved_steps(case)
        if ended:
            return (
                "Grading the forces",
                "simpleFoam has finished. The force coefficients are graded next, then paraFoam opens on the saved steps.",
            )
        if step:
            return (
                f"Solving step {step}",
                f"simpleFoam is on step {step}. "
                f"{saved} steps are already on disk, with velocity, pressure, and the forces kept at each one.",
            )
        return (
            "Starting simpleFoam",
            "simpleFoam is starting. Every step of velocity, pressure, and the forces stays on disk.",
        )
    if (case / "log.checkMesh").is_file():
        return (
            "Checking the mesh",
            "checkMesh is grading skew and non-orthogonality. The solve waits for that gate.",
        )
    if (case / "log.blockMesh").is_file():
        return (
            "Building the mesh",
            "blockMesh is building the hex mesh around the section.",
        )
    return (
        "Writing the case",
        "The case is written. blockMesh is next, then checkMesh, then simpleFoam.",
    )


def narrate(store: Path, phase: str, exploring: bool, run_id: str | None) -> dict:
    """Who is working, a short status, and a sentence about the current step."""
    if phase == "idle" and not exploring:
        return {"who": "", "status": "Idle", "detail": ""}
    if phase == "asking":
        return {
            "who": "Meta",
            "status": "Waiting for you",
            "detail": "Waiting for the missing fact. No OpenFOAM case opens until that answer is in.",
        }
    if not run_id:
        return {
            "who": "Meta",
            "status": "Reading the request",
            "detail": (
                "Reading the request. If the section or the angle is missing, "
                "the next line asks for it before OpenFOAM starts."
            ),
        }
    spec = _spec_of(Path(store) / run_id)
    label = _wing_label(spec)
    short, step = _case_step(Path(store) / run_id / "case")
    if exploring:
        return {
            "who": "Outer",
            "status": "Exploring a neighbor",
            "detail": f"The asked wing is already stored. This neighbor is {label}. {step}",
        }
    return {"who": "Inner", "status": short, "detail": f"{label}. {step}"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clear_session(store: Path) -> None:
    """Drop the chat and the OpenFOAM runs. The cylinder health cache stays."""
    store = Path(store)
    if not store.is_dir():
        return
    for name in ("desk.jsonl", "last_spec.json", "observe.jsonl"):
        path = store / name
        if path.is_file():
            path.unlink()
    meta = store / "meta"
    if meta.is_dir():
        shutil.rmtree(meta)
    for path in list(store.iterdir()):
        if path.is_dir() and path.name.startswith("run-"):
            shutil.rmtree(path)


class Workbench:
    """Holds the store lock, the transcript, and at most one study."""

    def __init__(self, store: Path, runner=None) -> None:
        self.store = Path(store)
        self.store.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.engineer: Engineer | None = None
        self.phase = "idle"
        self.active_run: str | None = None
        self.open_prompt: str | None = None
        self.messages: list[dict] = []
        self.early: list[str] = []
        self.answers: Queue[str] = Queue()
        self.explore_on = os.environ.get("WINGLINE_EXPLORE", "1") != "0"
        self.exploring = False
        self._origin = ""
        self._viewers: dict[str, object] = {}
        self._lock = threading.Lock()
        self._load()
        self._load_explore()

    def open(self) -> None:
        self.engineer = Engineer.open(self.store)

    def close(self) -> None:
        if self.engineer is not None:
            self.engineer.close()
            self.engineer = None

    def _load(self) -> None:
        path = self.store / "desk.jsonl"
        if not path.is_file():
            return
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("text"):
                self.messages.append(row)

    def _load_explore(self) -> None:
        path = self.store / "explore.json"
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and "on" in payload:
            self.explore_on = bool(payload["on"])

    def set_explore(self, on: bool) -> dict:
        with self._lock:
            self.explore_on = bool(on)
            (self.store / "explore.json").write_text(json.dumps({"on": self.explore_on}) + "\n")
        snap = self.snapshot()
        snap["ok"] = True
        return snap

    def _remember(self, role: str, text: str, run_id: str | None = None) -> None:
        row = {
            "id": len(self.messages) + 1,
            "role": role,
            "text": text,
            "at": _now(),
            "run_id": run_id,
        }
        self.messages.append(row)
        with (self.store / "desk.jsonl").open("a") as handle:
            handle.write(json.dumps(row) + "\n")

    def reset_chat(self) -> dict:
        """Clear the transcript. An open question stays, and a running study keeps going."""
        with self._lock:
            self.messages.clear()
            desk = self.store / "desk.jsonl"
            if desk.is_file():
                desk.unlink()
            if self.phase == "asking" and self.open_prompt:
                self._remember("meta", self.open_prompt)
        snap = self.snapshot()
        snap["ok"] = True
        return snap

    def submit(self, text: str) -> dict:
        clean = " ".join(text.split())
        if not clean:
            return {"ok": False, "error": "empty"}
        with self._lock:
            if self.phase == "asking":
                self._remember("you", clean)
                self.answers.put(clean)
                return {"ok": True, "mode": "answer", "phase": self.phase}
            if self.phase == "running":
                self._remember("you", clean, self.active_run)
                if self.active_run:
                    append_steer(self.store / self.active_run, clean)
                    self._remember("steer", f"Passed to {self.active_run}. The next tool turn will see it.", self.active_run)
                else:
                    self.early.append(clean)
                    self._remember("steer", "Passed on as soon as the run directory opens.")
                return {"ok": True, "mode": "steer", "phase": self.phase}
            self.phase = "running"
            self._remember("you", clean)
        thread = threading.Thread(target=self._job, args=(clean,), daemon=True)
        thread.start()
        return {"ok": True, "mode": "study", "phase": "running"}

    def _reply(self, prompt: str) -> str:
        with self._lock:
            self.phase = "asking"
            self.open_prompt = prompt
            self._remember("meta", prompt)
        answer = self.answers.get()
        with self._lock:
            if self.open_prompt == prompt:
                self.open_prompt = None
        return answer

    def _note_run(self, run_id: str) -> None:
        with self._lock:
            self.phase = "running"
            self.active_run = run_id
            origin = self._origin
            pending = list(self.early)
            self.early.clear()
        mark_origin(self.store, origin, run_id)
        for text in pending:
            append_steer(self.store / run_id, text)
            with self._lock:
                self._remember("steer", f"Passed to {run_id}.", run_id)

    def _say(self, role: str, text: str) -> None:
        with self._lock:
            self._remember(role, text, self.active_run)

    def _job(self, text: str) -> None:
        try:
            if self.runner is not None:
                self.runner(text, self._reply, self._note_run)
                return
            self._study(text)
        except Exception as exc:
            self._say("system", f"The study stopped: {exc}")
        finally:
            with self._lock:
                self.phase = "idle"
                self.active_run = None
                self.open_prompt = None

    def _study(self, text: str) -> None:
        if self.engineer is None:
            raise RuntimeError("workbench is not open")
        study = resolve(text, self.store, self._reply)
        if not isinstance(study, Study):
            self._say("engineer", close_paragraph(engineer_brief(study), study, None))
            return
        for note in study.notes:
            self._say("meta", note)
        view = self.engineer.ask(study.sentence, on_run=self._note_run)
        self._report(view.outcome, view.run_id or None)
        self._search(study, view)

    def _report(self, outcome: object, run_id: str | None, *, open_viewer: bool = True) -> None:
        showing = False
        viewer_error = ""
        run_dir = self.store / run_id if run_id else None
        if open_viewer and run_id:
            opened = self.show_parafoam(run_id)
            if opened.get("ok"):
                showing = True
            elif opened.get("error") == "viewer":
                viewer_error = str(opened.get("detail") or "")
        self._say(
            "engineer",
            close_paragraph(
                engineer_brief(outcome, run_dir, showing=showing, viewer_error=viewer_error),
                outcome,
                run_dir,
            ),
        )

    def _search(self, study: object, view: object) -> None:
        """Quiet neighborhood. The tool client stores the cases. The chat stays still."""
        if not self.explore_on or not getattr(view, "run_id", ""):
            return
        outcome = getattr(view, "outcome", None)
        if isinstance(outcome, Refusal) and outcome.gate in {"parse", "bounds", "pilot", "meta"}:
            return
        if self.engineer is None:
            return
        previous = os.environ.get("WINGLINE_AGENT")
        os.environ["WINGLINE_AGENT"] = "0"
        with self._lock:
            self.exploring = True
            self._origin = str(getattr(view, "run_id", "") or "")
        try:

            def run_case(sentence: str):
                assert self.engineer is not None
                return self.engineer.ask(sentence, on_run=self._note_run)

            explore(self.store, study, view, run_case, budget=EXPLORE_CAP)
        finally:
            with self._lock:
                self.exploring = False
                self._origin = ""
            if previous is None:
                os.environ.pop("WINGLINE_AGENT", None)
            else:
                os.environ["WINGLINE_AGENT"] = previous

    def snapshot(self) -> dict:
        with self._lock:
            phase = self.phase
            active = self.active_run
            messages = self.messages[-200:]
            explore_on = self.explore_on
            exploring = self.exploring
        activity = "exploring" if exploring else phase
        story = narrate(self.store, phase, exploring, active)
        return {
            "phase": phase,
            "activity": activity,
            "explore": explore_on,
            "active_run": active,
            "who": story["who"],
            "status": story["status"],
            "detail": story["detail"],
            "hint": _HINTS[phase],
            "messages": messages,
            "runs": list_runs(self.store, active),
        }

    def run_files(self, run_id: str) -> dict:
        run_dir = self._run(run_id)
        summary = next((row for row in list_runs(self.store, self.active_run) if row["id"] == run_id), None)
        return {"run": summary, "files": list_files(run_dir)}

    def file_view(self, run_id: str, rel: str) -> dict:
        return read_view(self._run(run_id), rel)

    def picture(self, run_id: str) -> dict:
        return flow_picture(self._run(run_id))

    def show_parafoam(self, run_id: str) -> dict:
        """Open the asked case in paraFoam. A second call leaves the same window up."""
        try:
            case = self._run(run_id) / "case"
        except ValueError:
            return {"ok": False, "error": "run"}
        if not (case / "system" / "controlDict").is_file():
            return {"ok": False, "error": "case"}
        current = self._viewers.get(run_id)
        if current is not None and getattr(current, "poll", lambda: 0)() is None:
            return {"ok": True, "showing": True, "run_id": run_id}
        starter = getattr(self, "_start_parafoam", None) or show_case
        try:
            proc = starter(case)
        except (OSError, RuntimeError, FileNotFoundError) as exc:
            return {"ok": False, "error": "viewer", "detail": str(exc)}
        self._viewers[run_id] = proc
        return {"ok": True, "showing": True, "run_id": run_id}

    def board(self, run_id: str) -> dict:
        """Comparison for this run, or for the search that already listed it."""
        run_dir = self._run(run_id)
        own = run_dir / "outer" / "search.json"
        if own.is_file():
            return self._board_file(own, run_dir.name)
        store = self.store.resolve()
        for path in store.glob("run-*/outer/search.json"):
            try:
                if not path.resolve().is_relative_to(store):
                    continue
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            rows = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                continue
            if any(isinstance(row, dict) and row.get("run_id") == run_id for row in rows):
                return self._board_file(path, path.parents[1].name)
        return {"ready": False, "rows": [], "sentence": Bounds.design_sentence, "run_id": run_id}

    def _board_file(self, path: Path, owner: str) -> dict:
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            return {"ready": False, "rows": [], "sentence": Bounds.design_sentence, "run_id": owner}
        view = board_view(payload)
        view["run_id"] = owner
        return view

    def start_refine(self, run_id: str) -> dict:
        with self._lock:
            if self.phase != "idle":
                return {"ok": False, "error": "busy"}
            if self.engineer is None:
                return {"ok": False, "error": "closed"}
            try:
                self._run(run_id)
            except ValueError:
                return {"ok": False, "error": "run"}
            self.phase = "running"
            self.active_run = run_id
        thread = threading.Thread(target=self._refine_job, args=(run_id,), daemon=True)
        thread.start()
        return {"ok": True, "mode": "refine", "phase": "running"}

    def _refine_job(self, run_id: str) -> None:
        try:
            if self.engineer is None:
                raise RuntimeError("workbench is not open")
            view = self.engineer.refine(run_id, on_run=self._note_run)
            self._report(view.outcome, view.run_id or run_id, open_viewer=False)
        except Exception as exc:
            self._say("system", f"The refinement stopped: {exc}")
        finally:
            with self._lock:
                self.phase = "idle"
                self.active_run = None
                self.open_prompt = None

    def _run(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not run_id.startswith("run-"):
            raise ValueError("run")
        if run_id != Path(run_id).name or "/" in run_id or "\\" in run_id:
            raise ValueError("run")
        run_dir = (self.store / run_id).resolve()
        if not run_dir.is_dir() or not run_dir.is_relative_to(self.store.resolve()):
            raise ValueError("run")
        return run_dir


class Handler(BaseHTTPRequestHandler):
    bench: Workbench

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            self._static(_WEB / "index.html", "text/html; charset=utf-8")
            return
        if path == "/app.css":
            self._static(_WEB / "app.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._static(_WEB / "app.js", "text/javascript; charset=utf-8")
            return
        if path == "/api/state":
            self._json(self.bench.snapshot())
            return
        if path.startswith("/api/runs/"):
            self._run_get(path, parse_qs(parsed.query))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/reset":
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length:
                self.rfile.read(length)
            self._json(self.bench.reset_chat())
            return
        if path == "/api/explore":
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                body = json.loads(self.rfile.read(length).decode() or "{}") if length else {}
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "json"}, 400)
                return
            if not isinstance(body, dict) or not isinstance(body.get("on"), bool):
                self._json({"ok": False, "error": "on"}, 400)
                return
            self._json(self.bench.set_explore(body["on"]))
            return
        if path == "/api/refine":
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                body = json.loads(self.rfile.read(length).decode() or "{}") if length else {}
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "json"}, 400)
                return
            if not isinstance(body, dict):
                self._json({"ok": False, "error": "run"}, 400)
                return
            result = self.bench.start_refine(str(body.get("run_id") or ""))
            self._json(result, 200 if result.get("ok") else 400)
            return
        if path == "/api/paraview":
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                body = json.loads(self.rfile.read(length).decode() or "{}") if length else {}
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "json"}, 400)
                return
            if not isinstance(body, dict):
                self._json({"ok": False, "error": "run"}, 400)
                return
            result = self.bench.show_parafoam(str(body.get("run_id") or ""))
            self._json(result, 200 if result.get("ok") else 400)
            return
        if path != "/api/message":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            self._json({"ok": False, "error": "json"}, 400)
            return
        result = self.bench.submit(str(body.get("text") or ""))
        self._json(result, 200 if result.get("ok") else 400)

    def _run_get(self, path: str, query: dict) -> None:
        parts = [part for part in path.split("/") if part]
        # api / runs / id / file?
        if len(parts) < 3:
            self.send_error(404)
            return
        run_id = parts[2]
        try:
            if len(parts) == 3:
                self._json(self.bench.run_files(run_id))
                return
            if len(parts) == 4 and parts[3] == "picture":
                self._json(self.bench.picture(run_id))
                return
            if len(parts) == 4 and parts[3] == "board":
                self._json(self.bench.board(run_id))
                return
            if len(parts) == 4 and parts[3] == "file":
                rel = (query.get("path") or [""])[0]
                self._json(self.bench.file_view(run_id, rel))
                return
        except ValueError:
            self._json({"ok": False, "error": "not found"}, 404)
            return
        self.send_error(404)

    def _static(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def serve(bench: Workbench, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    Handler.bench = bench
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    store = Path(os.environ.get("WINGLINE_STORE", "runs"))
    port = int(os.environ.get("WINGLINE_WEB_PORT", "8765"))
    clear_session(store)
    bench = Workbench(store)
    bench.open()
    server = serve(bench, "127.0.0.1", port)
    print(f"Wingline workbench  http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        bench.close()


if __name__ == "__main__":
    main()
