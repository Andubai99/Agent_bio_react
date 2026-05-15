export type TaskStatus = "idle" | "ready" | "running" | "passed" | "failed";

export type TaskStep = {
  number: number;
  action: string;
  window: string;
  verification: string;
  status: TaskStatus;
};

export type TaskItem = {
  id: string;
  name: string;
  description: string;
  selected: boolean;
  status: TaskStatus;
  steps: TaskStep[];
};

export type ModelMode = "preset" | "custom";

export type ProviderConfig = {
  mode: ModelMode;
  preset: string;
  model: string;
  endpoint: string;
  apiKey: string;
  timeoutSeconds: number;
  maxTokens: number;
};

export type BackendState = "checking" | "connected" | "disconnected";

export type RunState = "idle" | "running" | "succeeded" | "failed" | "stopped";

export type RunStatus = {
  state: RunState;
  run_id: string | null;
  log_path: string | null;
  task: string | null;
  reasoner: string | null;
  pid: number | null;
  started_at: string | null;
  ended_at: string | null;
  exit_code: number | null;
  last_summary: Record<string, unknown> | null;
};

export type ServerTask = {
  id: string;
  title: string;
  description: string;
  path: string;
  steps: Array<{
    number: number;
    action: string;
    window: string;
    verification: string;
  }>;
};

export type LogEvent = {
  seq: number;
  timestamp: string;
  run_id?: string | null;
  type: "log" | "status" | "summary";
  stream?: "stdout" | "stderr";
  line?: string;
  state?: RunState;
  exit_code?: number;
  summary?: Record<string, unknown>;
};
