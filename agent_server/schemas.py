from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RunState = Literal["idle", "running", "succeeded", "failed", "stopped"]
ReasonerName = Literal["manual", "script", "deepseek"]


class TaskStepInfo(BaseModel):
    number: int
    action: str
    window: str
    verification: str


class TaskInfo(BaseModel):
    id: str
    title: str
    description: str
    path: str
    steps: list[TaskStepInfo] = Field(default_factory=list)


class StartRunRequest(BaseModel):
    task: str = "biopharma_protein"
    reasoner: ReasonerName = "deepseek"
    max_steps: int = Field(default=30, ge=1, le=500)


class RunStatus(BaseModel):
    state: RunState
    run_id: str | None = None
    log_path: str | None = None
    task: str | None = None
    reasoner: str | None = None
    pid: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    last_summary: dict[str, Any] | None = None


class ApiMessage(BaseModel):
    ok: bool
    code: str
    message: str
    status: RunStatus | None = None
