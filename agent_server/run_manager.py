from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.task_loader import iter_task_paths, load_task
from agent.task_parser import parse_task_body
from agent_server.schemas import RunStatus, StartRunRequest, TaskInfo, TaskStepInfo


class RunAlreadyActiveError(RuntimeError):
    pass


class RunManager:
    def __init__(self, *, root: Path, event_limit: int = 1000):
        self.root = root
        self.tasks_dir = root / "tasks"
        self.logs_dir = root / "logs"
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._state = "idle"
        self._run_id: str | None = None
        self._log_path: Path | None = None
        self._task: str | None = None
        self._reasoner: str | None = None
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._exit_code: int | None = None
        self._last_summary: dict[str, Any] | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=event_limit)
        self._next_seq = 1

    def list_tasks(self) -> list[TaskInfo]:
        tasks: list[TaskInfo] = []
        for path in iter_task_paths(self.tasks_dir):
            task = load_task(path)
            parsed_task = parse_task_body(task.body)
            tasks.append(
                TaskInfo(
                    id=task.id,
                    title=task.title,
                    description=task.description,
                    path=str(path.relative_to(self.root)),
                    steps=[
                        TaskStepInfo(
                            number=step.number,
                            action=step.action_text,
                            window=step.window_text,
                            verification=step.verification_text,
                        )
                        for step in parsed_task.steps
                    ],
                )
            )
        return tasks

    def start(self, request: StartRunRequest) -> RunStatus:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RunAlreadyActiveError("A run is already active.")

            self._process = None
            self._state = "running"
            self._run_id = uuid.uuid4().hex
            self._task = request.task
            self._reasoner = request.reasoner
            start_time = _local_now()
            self._started_at = _format_iso(start_time)
            self._ended_at = None
            self._exit_code = None
            self._last_summary = None
            self._log_path = self._build_log_path(start_time)
            self._events.clear()
            self._next_seq = 1

            command = [
                sys.executable,
                "run_agent.py",
                "run",
                "--task",
                request.task,
                "--reasoner",
                request.reasoner,
                "--max-steps",
                str(request.max_steps),
            ]
            self._write_log_header(request, command, start_time)
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(self.root),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except Exception as exc:
                self._state = "failed"
                self._ended_at = _now_iso()
                self._exit_code = None
                self._append_event({"type": "status", "state": "failed", "message": str(exc)})
                raise
            self._process = process
            self._append_event({"type": "status", "state": "running"})

            threading.Thread(target=self._read_stream, args=(process, "stdout"), daemon=True).start()
            threading.Thread(target=self._read_stream, args=(process, "stderr"), daemon=True).start()
            threading.Thread(target=self._wait_process, args=(process,), daemon=True).start()
            return self.status()

    def stop(self) -> RunStatus:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                if self._state == "running":
                    self._state = "stopped"
                    self._ended_at = _now_iso()
                return self.status()
            self._state = "stopped"
            self._append_event({"type": "status", "state": "stopped"})

        _terminate_process(process)
        return self.status()

    def status(self) -> RunStatus:
        with self._lock:
            pid = self._process.pid if self._process is not None and self._process.poll() is None else None
            return RunStatus(
                state=self._state,  # type: ignore[arg-type]
                run_id=self._run_id,
                log_path=str(self._log_path.relative_to(self.root)) if self._log_path else None,
                task=self._task,
                reasoner=self._reasoner,
                pid=pid,
                started_at=self._started_at,
                ended_at=self._ended_at,
                exit_code=self._exit_code,
                last_summary=self._last_summary,
            )

    def events_after(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [event for event in self._events if int(event["seq"]) > seq]

    def _read_stream(self, process: subprocess.Popen[str], stream_name: str) -> None:
        stream = process.stdout if stream_name == "stdout" else process.stderr
        if stream is None:
            return
        for raw_line in stream:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            self._append_event({"type": "log", "stream": stream_name, "line": line})
            if stream_name == "stdout":
                self._maybe_record_summary(line)

    def _wait_process(self, process: subprocess.Popen[str]) -> None:
        exit_code = process.wait()
        with self._lock:
            if process is not self._process:
                return
            self._exit_code = exit_code
            self._ended_at = _now_iso()
            if self._state != "stopped":
                self._state = "succeeded" if exit_code == 0 else "failed"
            self._append_event({"type": "status", "state": self._state, "exit_code": exit_code})
            if self._last_summary is not None:
                self._append_event({"type": "summary", "summary": self._last_summary})

    def _maybe_record_summary(self, line: str) -> None:
        stripped = line.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            return
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return
        if not isinstance(parsed, dict):
            return
        if {"ok", "code", "message", "steps"}.issubset(parsed):
            with self._lock:
                self._last_summary = parsed

    def _append_event(self, payload: dict[str, Any]) -> None:
        with self._lock:
            event = {"seq": self._next_seq, "timestamp": _now_iso(), "run_id": self._run_id, **payload}
            self._next_seq += 1
            self._events.append(event)
            self._append_event_to_log_file(event)

    def _build_log_path(self, start_time: datetime) -> Path:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stem = start_time.strftime("%Y%m%d_%H%M%S")
        path = self.logs_dir / f"{stem}.md"
        if not path.exists():
            return path
        suffix = 2
        while True:
            candidate = self.logs_dir / f"{stem}_{suffix}.md"
            if not candidate.exists():
                return candidate
            suffix += 1

    def _write_log_header(self, request: StartRunRequest, command: list[str], start_time: datetime) -> None:
        if self._log_path is None:
            return
        relative_path = self._log_path.relative_to(self.root)
        content = "\n".join(
            [
                "# Agent_bio_react 运行日志",
                "",
                f"- start_time: {_format_iso(start_time)}",
                f"- task: {request.task}",
                f"- reasoner: {request.reasoner}",
                f"- max_steps: {request.max_steps}",
                f"- run_id: {self._run_id}",
                f"- log_file: {relative_path}",
                f"- command: {json.dumps(command, ensure_ascii=False)}",
                "",
                "## Events",
                "",
            ]
        )
        self._log_path.write_text(content, encoding="utf-8")

    def _append_event_to_log_file(self, event: dict[str, Any]) -> None:
        if self._log_path is None:
            return
        line = _format_event_for_log_file(event)
        if line is None:
            return
        with self._log_path.open("a", encoding="utf-8") as file:
            file.write(line)
            file.write("\n")


def _local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _format_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _now_iso() -> str:
    return _format_iso(_local_now())


def _format_event_for_log_file(event: dict[str, Any]) -> str | None:
    timestamp = str(event.get("timestamp") or "")
    time_text = _format_time_for_log(timestamp)
    event_type = event.get("type")
    if event_type == "log":
        line = str(event.get("line") or "")
        if _parse_summary_line(line) is not None:
            return None
        stream = str(event.get("stream") or "log")
        return f"[{time_text}] {stream}  {line}"
    if event_type == "summary":
        return f"[{time_text}] summary  {_format_summary_for_log(event.get('summary'))}"
    if event_type == "status":
        exit_code = event.get("exit_code")
        suffix = "" if exit_code is None else f" exit={exit_code}"
        return f"[{time_text}] status  {event.get('state')}{suffix}"
    return f"[{time_text}] {event_type}  {json.dumps(event, ensure_ascii=False)}"


def _format_time_for_log(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return "--:--:--"
    return parsed.strftime("%H:%M:%S")


def _parse_summary_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if {"ok", "code", "message", "steps"}.issubset(parsed):
        return parsed
    return None


def _format_summary_for_log(summary: Any) -> str:
    if not isinstance(summary, dict):
        return ""
    message = summary.get("message")
    if isinstance(message, str) and len(message) > 500:
        message = f"{message[:500]}..."
    compact = {
        "ok": summary.get("ok"),
        "code": summary.get("code"),
        "message": message,
        "steps": summary.get("steps"),
    }
    return json.dumps(compact, ensure_ascii=False)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
