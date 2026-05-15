from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from agent_server.omniparser_manager import OmniParserManager
from agent_server.run_manager import RunAlreadyActiveError, RunManager
from agent_server.schemas import ApiMessage, OmniParserActionResponse, OmniParserStatus, RunStatus, StartRunRequest, TaskInfo


ROOT = Path(__file__).resolve().parents[1]
manager = RunManager(root=ROOT)
omniparser_manager = OmniParserManager()

app = FastAPI(title="Agent Bio React Local Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {"ok": True, "service": "agent_server"}


@app.get("/tasks", response_model=list[TaskInfo])
def list_tasks() -> list[TaskInfo]:
    return manager.list_tasks()


@app.post("/runs/start", response_model=RunStatus)
def start_run(request: StartRunRequest) -> RunStatus:
    try:
        return manager.start(request)
    except RunAlreadyActiveError as exc:
        raise HTTPException(
            status_code=409,
            detail={"ok": False, "code": "RUN_ALREADY_ACTIVE", "message": str(exc)},
        ) from exc


@app.post("/runs/stop", response_model=ApiMessage)
def stop_run() -> ApiMessage:
    status = manager.stop()
    return ApiMessage(ok=True, code="STOP_REQUESTED", message="Stop requested.", status=status)


@app.get("/runs/status", response_model=RunStatus)
def run_status() -> RunStatus:
    return manager.status()


@app.get("/runs/events")
def run_events(after: int = 0) -> list[dict[str, Any]]:
    return manager.events_after(after)


@app.get("/omniparser/status", response_model=OmniParserStatus)
def omniparser_status() -> OmniParserStatus:
    return omniparser_manager.status()


@app.post("/omniparser/start", response_model=OmniParserActionResponse)
def start_omniparser() -> OmniParserActionResponse:
    status = omniparser_manager.start()
    ok = status.state == "running" and status.probe_ok
    return OmniParserActionResponse(
        ok=ok,
        code="OMNIPARSER_RUNNING" if ok else "OMNIPARSER_START_FAILED",
        message=status.message,
        status=status,
    )


@app.post("/omniparser/stop", response_model=OmniParserActionResponse)
def stop_omniparser() -> OmniParserActionResponse:
    run_status = manager.status()
    if run_status.state == "running":
        status = omniparser_manager.status()
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "code": "AGENT_RUN_ACTIVE",
                "message": "Agent is running; OmniParser cannot be stopped.",
                "status": status.dict(),
            },
        )

    status = omniparser_manager.stop()
    ok = status.state == "stopped" and not status.probe_ok
    return OmniParserActionResponse(
        ok=ok,
        code="OMNIPARSER_STOPPED" if ok else "OMNIPARSER_STOP_SKIPPED",
        message=status.message,
        status=status,
    )


@app.websocket("/runs/logs")
async def run_logs(websocket: WebSocket) -> None:
    await websocket.accept()
    last_seq = 0
    try:
        while True:
            events = manager.events_after(last_seq)
            for event in events:
                last_seq = int(event["seq"])
                await websocket.send_json(event)
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        return


if __name__ == "__main__":
    uvicorn.run("agent_server.app:app", host="127.0.0.1", port=8765, reload=False)
