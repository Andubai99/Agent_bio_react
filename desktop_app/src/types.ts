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
