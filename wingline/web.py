"""Local workbench. One conversation, the OpenFOAM runs, and their files.

    python -m wingline.web
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from urllib.parse import parse_qs, unquote, urlparse

from wingline.engineer import Engineer
from wingline.files import list_files, list_runs, read_view
from wingline.kernel import Refusal
from wingline.meta import Study, resolve
from wingline.outer import explore
from wingline.steer import append_steer
from wingline.__main__ import panel_lines

_WEB = Path(__file__).resolve().parents[1] / "web"
_HINTS = {
    "idle": "Ask for a wing. A full NACA sentence starts immediately.",
    "asking": "The study needs an answer before the next solve.",
    "running": "A run is open. A message now steers the agent on its next turn.",
}


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
        self._lock = threading.Lock()
        self._load()

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
            pending = list(self.early)
            self.early.clear()
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
            for line in panel_lines(study):
                self._say("result", line)
            return
        for note in study.notes:
            self._say("meta", note)
        view = self.engineer.ask(study.sentence, on_run=self._note_run)
        for line in panel_lines(view.outcome):
            self._say("result", line)
        if os.environ.get("WINGLINE_EXPLORE", "1") == "0" or not view.run_id:
            return
        if isinstance(view.outcome, Refusal) and view.outcome.gate in {"parse", "bounds", "pilot", "meta"}:
            return
        self._say("outer", "Direct result is above. Searching nearby camber and thickness.")

        def run_case(sentence: str):
            assert self.engineer is not None
            return self.engineer.ask(sentence, on_run=self._note_run)

        for line in explore(self.store, study, view, run_case):
            self._say("outer", line)

    def snapshot(self) -> dict:
        with self._lock:
            phase = self.phase
            active = self.active_run
            messages = self.messages[-200:]
        return {
            "phase": phase,
            "active_run": active,
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
