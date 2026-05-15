import type { LogEvent, OmniParserActionResponse, OmniParserStatus, RunStatus, ServerTask } from "../types";

export const API_BASE = "http://127.0.0.1:8765";
export const LOGS_WS_URL = "ws://127.0.0.1:8765/runs/logs";

type StartRunPayload = {
  task: string;
  reasoner: "deepseek";
  max_steps: number;
};

export async function getHealth(): Promise<boolean> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) {
    return false;
  }
  const payload = await response.json();
  return payload.ok === true;
}

export async function getTasks(): Promise<ServerTask[]> {
  const response = await fetch(`${API_BASE}/tasks`);
  if (!response.ok) {
    throw new Error(`Load tasks failed: ${response.status}`);
  }
  return response.json();
}

export async function getRunStatus(): Promise<RunStatus> {
  const response = await fetch(`${API_BASE}/runs/status`);
  if (!response.ok) {
    throw new Error(`Load run status failed: ${response.status}`);
  }
  return response.json();
}

export async function getRunEvents(after = 0): Promise<LogEvent[]> {
  const response = await fetch(`${API_BASE}/runs/events?after=${after}`);
  if (!response.ok) {
    throw new Error(`Load run events failed: ${response.status}`);
  }
  return response.json();
}

export async function startRun(payload: StartRunPayload): Promise<RunStatus> {
  const response = await fetch(`${API_BASE}/runs/start`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const errorPayload = await response.json().catch(() => null);
    const detail = errorPayload?.detail?.message ?? errorPayload?.message ?? `Start run failed: ${response.status}`;
    throw new Error(detail);
  }
  return response.json();
}

export async function stopRun(): Promise<RunStatus | null> {
  const response = await fetch(`${API_BASE}/runs/stop`, {method: "POST"});
  if (!response.ok) {
    throw new Error(`Stop run failed: ${response.status}`);
  }
  const payload = await response.json();
  return payload.status ?? null;
}

export async function getOmniParserStatus(): Promise<OmniParserStatus> {
  const response = await fetch(`${API_BASE}/omniparser/status`);
  if (!response.ok) {
    throw new Error(`Load OmniParser status failed: ${response.status}`);
  }
  return response.json();
}

export async function startOmniParser(): Promise<OmniParserActionResponse> {
  const response = await fetch(`${API_BASE}/omniparser/start`, {method: "POST"});
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail?.message ?? payload?.message ?? `Start OmniParser failed: ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

export async function stopOmniParser(): Promise<OmniParserActionResponse> {
  const response = await fetch(`${API_BASE}/omniparser/stop`, {method: "POST"});
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail?.message ?? payload?.message ?? `Stop OmniParser failed: ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

export function createLogsSocket(onEvent: (event: LogEvent) => void, onClose: () => void, onError: () => void): WebSocket {
  const socket = new WebSocket(LOGS_WS_URL);
  socket.onmessage = (message) => {
    const event = JSON.parse(message.data) as LogEvent;
    onEvent(event);
  };
  socket.onclose = onClose;
  socket.onerror = onError;
  return socket;
}
