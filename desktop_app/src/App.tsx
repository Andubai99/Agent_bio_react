import {
  Activity,
  Check,
  ChevronDown,
  Eye,
  KeyRound,
  ListChecks,
  Play,
  RotateCcw,
  Settings,
  SlidersHorizontal,
  Square,
  Terminal,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  createLogsSocket,
  getOmniParserStatus,
  getRunEvents,
  getHealth,
  getRunStatus,
  getTasks,
  startOmniParser,
  startRun,
  stopOmniParser,
  stopRun
} from "./api/client";
import { defaultReasonerConfig, defaultVisionConfig } from "./data";
import type {
  BackendState,
  LogEvent,
  OmniParserStatus,
  ProviderConfig,
  RunStatus,
  ServerTask,
  TaskItem,
  TaskStatus
} from "./types";

type TabId = "tasks" | "settings" | "logs";
type ConfigKind = "reasoner" | "vision";

const tabs: Array<{ id: TabId; label: string; icon: typeof ListChecks }> = [
  { id: "tasks", label: "任务", icon: ListChecks },
  { id: "settings", label: "设置", icon: Settings },
  { id: "logs", label: "日志", icon: Terminal }
];

const reasonerPresets = [
  {
    id: "deepseek",
    label: "DeepSeek",
    model: "deepseek-v4-pro",
    endpoint: "https://api.deepseek.com/chat/completions"
  },
  {
    id: "manual",
    label: "Manual",
    model: "manual",
    endpoint: "local://manual"
  },
  {
    id: "scripted",
    label: "Scripted",
    model: "scripted",
    endpoint: "local://scripted"
  }
];

const visionPresets = [
  {
    id: "qwen",
    label: "Qwen",
    model: "qwen3-vl-flash",
    endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
  },
  {
    id: "temporary",
    label: "Temporary",
    model: "temporary-yaml",
    endpoint: "local://temporary"
  }
];

const idleStatus: RunStatus = {
  state: "idle",
  run_id: null,
  log_path: null,
  task: null,
  reasoner: null,
  pid: null,
  started_at: null,
  ended_at: null,
  exit_code: null,
  last_summary: null
};

const idleOmniParserStatus: OmniParserStatus = {
  state: "unknown",
  probe_ok: false,
  owned: false,
  pid: null,
  host: "127.0.0.1",
  port: 8001,
  root: "",
  last_probe_at: null,
  message: "等待连接本地后端"
};

export function App() {
  const [activeTab, setActiveTab] = useState<TabId>("tasks");
  const [backendState, setBackendState] = useState<BackendState>("checking");
  const [backendMessage, setBackendMessage] = useState("正在连接本地后端");
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<RunStatus>(idleStatus);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [reasoner, setReasoner] = useState<ProviderConfig>(defaultReasonerConfig);
  const [vision, setVision] = useState<ProviderConfig>(defaultVisionConfig);
  const [omniParserStatus, setOmniParserStatus] = useState<OmniParserStatus>(idleOmniParserStatus);
  const [omniParserBusy, setOmniParserBusy] = useState(false);
  const [omniParserError, setOmniParserError] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const activeLogRunIdRef = useRef<string | null>(null);

  useEffect(() => {
    void refreshBackend();
    const interval = window.setInterval(() => {
      void refreshStatus();
    }, 1000);
    const logInterval = window.setInterval(() => {
      void syncLogEvents();
    }, 1000);
    const omniParserInterval = window.setInterval(() => {
      void refreshOmniParserStatus();
    }, 2000);
    return () => {
      window.clearInterval(interval);
      window.clearInterval(logInterval);
      window.clearInterval(omniParserInterval);
    };
  }, []);

  useEffect(() => {
    if (backendState !== "connected") {
      socketRef.current?.close();
      socketRef.current = null;
      return;
    }
    socketRef.current?.close();
    socketRef.current = createLogsSocket(
      (event) => {
        appendLogEvent(event);
        if (event.type === "status" && event.state) {
          setRunStatus((status) => ({
            ...status,
            run_id: event.run_id ?? status.run_id,
            state: event.state ?? status.state,
            exit_code: event.exit_code ?? status.exit_code
          }));
        }
        if (event.type === "summary" && event.summary) {
          setRunStatus((status) => ({ ...status, last_summary: event.summary ?? status.last_summary }));
        }
      },
      () => setBackendMessage("日志连接已断开，状态轮询仍在继续"),
      () => setBackendMessage("日志连接异常，状态轮询仍在继续")
    );
    return () => {
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [backendState]);

  const activeTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) ?? tasks[0] ?? emptyTask(),
    [selectedTaskId, tasks]
  );
  const currentInstructionIndex = useMemo(() => latestInstructionIndex(logs, runStatus), [logs, runStatus]);
  const decoratedTasks = useMemo(
    () => tasks.map((task) => decorateTask({ ...task, selected: task.id === selectedTaskId }, runStatus, currentInstructionIndex)),
    [currentInstructionIndex, runStatus, selectedTaskId, tasks]
  );
  const decoratedActiveTask = useMemo(
    () => decorateTask({ ...activeTask, selected: true }, runStatus, currentInstructionIndex),
    [activeTask, currentInstructionIndex, runStatus]
  );
  const isRunning = runStatus.state === "running";
  const backendConnected = backendState === "connected";

  async function refreshBackend() {
    try {
      setBackendState("checking");
      const ok = await getHealth();
      if (!ok) {
        throw new Error("Health check failed.");
      }
      setBackendState("connected");
      setBackendMessage("本地后端已连接");
      setOperationError(null);
      await Promise.all([loadTasks(), refreshStatus(), refreshOmniParserStatus()]);
    } catch (error) {
      setBackendState("disconnected");
      setBackendMessage("本地后端未连接，请先启动 agent_server");
      setOperationError(error instanceof Error ? error.message : String(error));
    }
  }

  async function refreshOmniParserStatus() {
    if (backendState === "disconnected") {
      return;
    }
    try {
      const status = await getOmniParserStatus();
      setOmniParserStatus(status);
      setOmniParserError(null);
    } catch (error) {
      setOmniParserStatus((status) => ({
        ...status,
        state: "unknown",
        probe_ok: false,
        message: "无法读取 OmniParser 状态"
      }));
      setOmniParserError(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleStartOmniParser() {
    if (!backendConnected || omniParserBusy) {
      return;
    }
    setOmniParserBusy(true);
    setOmniParserError(null);
    setOmniParserStatus((status) => ({ ...status, state: "starting", message: "正在启动 OmniParser" }));
    try {
      const response = await startOmniParser();
      setOmniParserStatus(response.status);
      if (!response.ok) {
        setOmniParserError(response.message);
      }
    } catch (error) {
      setOmniParserError(error instanceof Error ? error.message : String(error));
      await refreshOmniParserStatus();
    } finally {
      setOmniParserBusy(false);
    }
  }

  async function handleStopOmniParser() {
    if (!backendConnected || omniParserBusy || isRunning) {
      return;
    }
    setOmniParserBusy(true);
    setOmniParserError(null);
    setOmniParserStatus((status) => ({ ...status, state: "stopping", message: "正在停止 OmniParser" }));
    try {
      const response = await stopOmniParser();
      setOmniParserStatus(response.status);
      if (!response.ok) {
        setOmniParserError(response.message);
      }
    } catch (error) {
      setOmniParserError(error instanceof Error ? error.message : String(error));
      await refreshOmniParserStatus();
    } finally {
      setOmniParserBusy(false);
    }
  }

  async function loadTasks() {
    const serverTasks = await getTasks();
    const nextTasks = serverTasks.map(mapServerTask);
    setTasks(nextTasks);
    setSelectedTaskId((current) => current ?? nextTasks[0]?.id ?? null);
  }

  async function refreshStatus() {
    try {
      const status = await getRunStatus();
      setRunStatus(status);
      setBackendState("connected");
      setBackendMessage("本地后端已连接");
      setOperationError(null);
    } catch {
      setBackendState("disconnected");
      setBackendMessage("本地后端未连接，请先启动 agent_server");
    }
  }

  async function syncLogEvents() {
    try {
      const events = await getRunEvents();
      events.forEach(handleLogEvent);
    } catch {
      return;
    }
  }

  function handleLogEvent(event: LogEvent) {
    appendLogEvent(event);
    if (event.type === "status" && event.state) {
      setRunStatus((status) => ({
        ...status,
        run_id: event.run_id ?? status.run_id,
        state: event.state ?? status.state,
        exit_code: event.exit_code ?? status.exit_code
      }));
    }
    if (event.type === "summary" && event.summary) {
      setRunStatus((status) => ({ ...status, last_summary: event.summary ?? status.last_summary }));
    }
  }

  function appendLogEvent(event: LogEvent) {
    const eventRunId = event.run_id ?? null;
    const shouldReset = Boolean(eventRunId && activeLogRunIdRef.current !== eventRunId);
    if (shouldReset) {
      activeLogRunIdRef.current = eventRunId;
    }
    setLogs((items) => {
      const base = shouldReset ? [] : items;
      const eventKey = logEventKey(event);
      if (base.some((item) => logEventKey(item) === eventKey)) {
        return base;
      }
      return [...base, event].slice(-1000);
    });
  }

  async function handleStartRun() {
    if (!backendConnected || !activeTask.id || isRunning) {
      return;
    }
    setOperationError(null);
    if (reasoner.preset !== "deepseek") {
      setOperationError("当前最小闭环只支持使用 DeepSeek 启动 Agent，其他模型配置暂未接入后端。");
      return;
    }
    activeLogRunIdRef.current = null;
    setLogs([]);
    try {
      const status = await startRun({
        task: activeTask.id,
        reasoner: "deepseek",
        max_steps: 30
      });
      setRunStatus(status);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleStopRun() {
    if (!backendConnected || !isRunning) {
      return;
    }
    setOperationError(null);
    try {
      const status = await stopRun();
      if (status) {
        setRunStatus(status);
      }
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : String(error));
    }
  }

  function toggleTask(taskId: string) {
    setSelectedTaskId(taskId);
  }

  function updateProvider(kind: ConfigKind, next: ProviderConfig) {
    if (kind === "reasoner") {
      setReasoner(next);
      return;
    }
    setVision(next);
  }

  return (
    <div className="app-shell">
      <header className="titlebar">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <h1>Agent_bio_react</h1>
            <span>Thermo BioPharma Finder desktop agent</span>
          </div>
        </div>
        <nav className="top-tabs" aria-label="Primary">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                className={activeTab === tab.id ? "top-tab active" : "top-tab"}
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                type="button"
              >
                <Icon size={16} />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </header>

      <main className="content">
        <BackendBanner
          backendMessage={backendMessage}
          backendState={backendState}
          operationError={operationError}
          refreshBackend={refreshBackend}
        />
        {activeTab === "tasks" && (
          <TaskWorkspace
            activeTask={decoratedActiveTask}
            backendConnected={backendConnected}
            isRunning={isRunning}
            reasoner={reasoner}
            refreshTasks={loadTasks}
            runStatus={runStatus}
            startRun={handleStartRun}
            stopRun={handleStopRun}
            tasks={decoratedTasks}
            toggleTask={toggleTask}
            vision={vision}
          />
        )}
        {activeTab === "settings" && (
          <SettingsView
            backendConnected={backendConnected}
            isRunning={isRunning}
            omniParserBusy={omniParserBusy}
            omniParserError={omniParserError}
            omniParserStatus={omniParserStatus}
            reasoner={reasoner}
            refreshOmniParserStatus={refreshOmniParserStatus}
            startOmniParser={handleStartOmniParser}
            stopOmniParser={handleStopOmniParser}
            updateProvider={updateProvider}
            vision={vision}
          />
        )}
        {activeTab === "logs" && <LogsView logs={logs} runStatus={runStatus} />}
      </main>
    </div>
  );
}

function BackendBanner({
  backendMessage,
  backendState,
  operationError,
  refreshBackend
}: {
  backendMessage: string;
  backendState: BackendState;
  operationError: string | null;
  refreshBackend: () => Promise<void>;
}) {
  return (
    <div className={`backend-banner ${backendState}`}>
      <span>{backendMessage}</span>
      {operationError && <strong>{operationError}</strong>}
      <button onClick={() => void refreshBackend()} type="button">
        重新连接
      </button>
    </div>
  );
}

function TaskWorkspace({
  activeTask,
  backendConnected,
  isRunning,
  reasoner,
  refreshTasks,
  runStatus,
  startRun,
  stopRun,
  tasks,
  toggleTask,
  vision
}: {
  activeTask: TaskItem;
  backendConnected: boolean;
  isRunning: boolean;
  reasoner: ProviderConfig;
  refreshTasks: () => Promise<void>;
  runStatus: RunStatus;
  startRun: () => Promise<void>;
  stopRun: () => Promise<void>;
  tasks: TaskItem[];
  toggleTask: (taskId: string) => void;
  vision: ProviderConfig;
}) {
  const selectedCount = tasks.filter((task) => task.selected).length;
  const currentStep = activeTask.steps.find((step) => step.status === "running");
  const completedCount = activeTask.steps.filter((step) => step.status === "passed").length;

  return (
    <section className="workspace-grid">
      <aside className="task-panel">
        <div className="panel-heading">
          <div>
            <h2>任务列表</h2>
            <span>{selectedCount} 项已勾选</span>
          </div>
          <button className="icon-button" onClick={() => void refreshTasks()} type="button" aria-label="刷新任务">
            <RotateCcw size={17} />
          </button>
        </div>

        <div className="task-list">
          {tasks.length === 0 && <div className="empty-state">未加载到任务</div>}
          {tasks.map((task) => (
            <label className={task.selected ? "task-row selected" : "task-row"} key={task.id}>
              <input checked={task.selected} onChange={() => toggleTask(task.id)} type="checkbox" />
              <span className="task-copy">
                <strong>{task.name}</strong>
                <small>{task.description}</small>
              </span>
              <StatusDot status={task.status} />
            </label>
          ))}
        </div>

        <div className="task-actions">
          <button className="secondary-button" disabled={!backendConnected || !isRunning} onClick={() => void stopRun()} type="button">
            <Square size={15} />
            停止
          </button>
          <button className="primary-button" disabled={!backendConnected || isRunning || !activeTask.id} onClick={() => void startRun()} type="button">
            <Play size={15} />
            开始运行
          </button>
        </div>
      </aside>

      <section className="run-panel">
        <div className="panel-heading">
          <div>
            <h2>{activeTask.name || "未选择任务"}</h2>
            <span>{runStateLabel(runStatus.state)} · {completedCount}/{activeTask.steps.length || 0} 步</span>
          </div>
          <StatusBadge status={activeTask.status} />
        </div>

        <div className="run-overview">
          <div className="model-strip" aria-label="当前任务窗口">
            <ModelPill icon={<Zap size={17} />} label="推理层模型" value={runStatus.reasoner ?? reasoner.model} />
            <ModelPill icon={<Eye size={17} />} label="视觉模型" value={vision.model} />
          </div>

          <div className="run-metrics">
            <div>
              <span>当前步骤</span>
              <strong>{currentStep ? `${currentStep.number}. ${currentStep.action}` : activeTask.steps.length ? "等待运行" : "无任务步骤"}</strong>
            </div>
            <div>
              <span>运行日志</span>
              <strong>{runStatus.log_path ?? "-"}</strong>
            </div>
            <div>
              <span>PID</span>
              <strong>{runStatus.pid ?? "-"}</strong>
            </div>
          </div>
        </div>

        <div className="step-panel">
          <div className="section-title">
            <Activity size={16} />
            执行进度
          </div>
          <div className="step-list">
            {activeTask.steps.length === 0 && <div className="empty-state">该任务暂无结构化步骤</div>}
            {activeTask.steps.map((step) => (
              <div className={`step-row ${step.status}`} key={step.number}>
                <span className="step-number">{step.number}</span>
                <span className="step-main">
                  <strong>{step.action}</strong>
                  <small>{step.window} · {step.verification}</small>
                </span>
                <StatusBadge status={step.status} />
              </div>
            ))}
          </div>
        </div>
      </section>
    </section>
  );
}

function ModelPill({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="model-pill">
      <span className="model-icon">{icon}</span>
      <span className="model-copy">
        <small>{label}</small>
        <strong>{value}</strong>
      </span>
    </div>
  );
}

function SettingsView({
  backendConnected,
  isRunning,
  omniParserBusy,
  omniParserError,
  omniParserStatus,
  reasoner,
  refreshOmniParserStatus,
  startOmniParser,
  stopOmniParser,
  updateProvider,
  vision
}: {
  backendConnected: boolean;
  isRunning: boolean;
  omniParserBusy: boolean;
  omniParserError: string | null;
  omniParserStatus: OmniParserStatus;
  reasoner: ProviderConfig;
  refreshOmniParserStatus: () => Promise<void>;
  startOmniParser: () => Promise<void>;
  stopOmniParser: () => Promise<void>;
  updateProvider: (kind: ConfigKind, next: ProviderConfig) => void;
  vision: ProviderConfig;
}) {
  const [activeMenu, setActiveMenu] = useState("model");
  const menu = ["切换模型", "运行设置", "连接设置", "界面设置", "日志设置", "关于项目"];

  return (
    <section className="settings-layout">
      <aside className="settings-menu">
        {menu.map((item) => (
          <button
            className={activeMenu === "model" && item === "切换模型" ? "menu-item active" : "menu-item"}
            key={item}
            onClick={() => setActiveMenu(item === "切换模型" ? "model" : item)}
            type="button"
          >
            {item}
          </button>
        ))}
      </aside>

      <div className="settings-content">
        <OmniParserSection
          backendConnected={backendConnected}
          isRunning={isRunning}
          omniParserBusy={omniParserBusy}
          omniParserError={omniParserError}
          refreshStatus={refreshOmniParserStatus}
          startService={startOmniParser}
          status={omniParserStatus}
          stopService={stopOmniParser}
        />
        <ModelSection
          config={reasoner}
          icon={<Zap size={17} />}
          kind="reasoner"
          presets={reasonerPresets}
          title="推理模型"
          updateProvider={updateProvider}
        />
        <ModelSection
          config={vision}
          icon={<Eye size={17} />}
          kind="vision"
          presets={visionPresets}
          title="视觉模型"
          updateProvider={updateProvider}
        />
      </div>
    </section>
  );
}

function OmniParserSection({
  backendConnected,
  isRunning,
  omniParserBusy,
  omniParserError,
  refreshStatus,
  startService,
  status,
  stopService
}: {
  backendConnected: boolean;
  isRunning: boolean;
  omniParserBusy: boolean;
  omniParserError: string | null;
  refreshStatus: () => Promise<void>;
  startService: () => Promise<void>;
  status: OmniParserStatus;
  stopService: () => Promise<void>;
}) {
  const running = status.state === "running" && status.probe_ok;
  const statusText = omniParserStateLabel(status.state);
  const address = `${status.host}:${status.port}`;

  return (
    <section className="settings-section omniparser-section">
      <div className="section-heading">
        <div className="section-title">
          <Activity size={17} />
          OmniParser 服务
        </div>
        <span className={`service-state ${status.state}`}>{statusText}</span>
      </div>

      <div className="service-overview">
        <div>
          <span>服务地址</span>
          <strong>{address}</strong>
        </div>
        <div>
          <span>PID</span>
          <strong>{status.pid ?? "-"}</strong>
        </div>
        <div>
          <span>启动来源</span>
          <strong>{status.owned ? "本项目启动" : running ? "外部服务" : "-"}</strong>
        </div>
        <div>
          <span>最近探活</span>
          <strong>{status.last_probe_at ? formatDateTime(status.last_probe_at) : "-"}</strong>
        </div>
      </div>

      <div className="service-path">
        <span>根目录</span>
        <strong>{status.root || "OmniParser/"}</strong>
      </div>

      <div className="service-message">
        <span>{status.message}</span>
        {omniParserError && <strong>{omniParserError}</strong>}
      </div>

      <div className="section-actions">
        <button className="secondary-button" disabled={!backendConnected || omniParserBusy} onClick={() => void refreshStatus()} type="button">
          <RotateCcw size={15} />
          刷新状态
        </button>
        <button className="secondary-button" disabled={!backendConnected || omniParserBusy || running} onClick={() => void startService()} type="button">
          <Play size={15} />
          启动
        </button>
        <button
          className="danger-button"
          disabled={!backendConnected || omniParserBusy || isRunning || !running || !status.owned}
          onClick={() => void stopService()}
          type="button"
          title={!status.owned && running ? "只能停止由本项目启动的 OmniParser" : undefined}
        >
          <Square size={15} />
          停止
        </button>
      </div>
    </section>
  );
}

function ModelSection({
  config,
  icon,
  kind,
  presets,
  title,
  updateProvider
}: {
  config: ProviderConfig;
  icon: React.ReactNode;
  kind: ConfigKind;
  presets: Array<{ id: string; label: string; model: string; endpoint: string }>;
  title: string;
  updateProvider: (kind: ConfigKind, next: ProviderConfig) => void;
}) {
  function patch(next: Partial<ProviderConfig>) {
    updateProvider(kind, { ...config, ...next });
  }

  function setPreset(presetId: string) {
    const preset = presets.find((item) => item.id === presetId);
    patch({
      preset: presetId,
      model: preset?.model ?? config.model,
      endpoint: preset?.endpoint ?? config.endpoint
    });
  }

  return (
    <section className="settings-section">
      <div className="section-heading">
        <div className="section-title">
          {icon}
          {title}
        </div>
        <div className="segmented">
          <button className={config.mode === "preset" ? "active" : ""} onClick={() => patch({ mode: "preset" })} type="button">
            内置
          </button>
          <button className={config.mode === "custom" ? "active" : ""} onClick={() => patch({ mode: "custom" })} type="button">
            自定义
          </button>
        </div>
      </div>

      <div className="settings-form">
        <label>
          模型来源
          <div className="select-wrap">
            <select disabled={config.mode === "custom"} onChange={(event) => setPreset(event.target.value)} value={config.preset}>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </select>
            <ChevronDown size={16} />
          </div>
        </label>

        <label>
          Model
          <input onChange={(event) => patch({ model: event.target.value })} readOnly={config.mode === "preset"} value={config.model} />
        </label>

        <label className="form-wide">
          Endpoint
          <input onChange={(event) => patch({ endpoint: event.target.value })} readOnly={config.mode === "preset"} value={config.endpoint} />
        </label>

        <label className="form-wide">
          API Key
          <div className="key-input">
            <KeyRound size={16} />
            <input
              onChange={(event) => patch({ apiKey: event.target.value })}
              placeholder={config.mode === "custom" ? "输入 API Key" : "使用项目环境变量或 API.txt"}
              type="password"
              value={config.apiKey}
            />
          </div>
        </label>

        <label>
          Timeout
          <input min={1} onChange={(event) => patch({ timeoutSeconds: Number(event.target.value) })} type="number" value={config.timeoutSeconds} />
        </label>

        <label>
          Max Tokens
          <input min={1} onChange={(event) => patch({ maxTokens: Number(event.target.value) })} type="number" value={config.maxTokens} />
        </label>
      </div>

      <div className="section-actions">
        <button className="secondary-button" type="button">
          <SlidersHorizontal size={15} />
          测试连接
        </button>
        <button className="primary-button" type="button">
          <Check size={15} />
          保存配置
        </button>
      </div>
    </section>
  );
}

function LogsView({ logs, runStatus }: { logs: LogEvent[]; runStatus: RunStatus }) {
  const logEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [logs.length]);

  return (
    <section className="wide-panel">
      <div className="panel-heading">
        <div>
          <h2>日志</h2>
          <span>
            当前状态：{runStateLabel(runStatus.state)}
            {runStatus.log_path ? ` · ${runStatus.log_path}` : ""}
          </span>
        </div>
      </div>
      <div className="log-console">
        {logs.length === 0 && <p>等待运行日志...</p>}
        {logs.map((event) => (
          <p className={event.stream === "stderr" ? "log-error" : ""} key={logEventKey(event)}>
            {formatLogEvent(event)}
          </p>
        ))}
        <div ref={logEndRef} />
      </div>
    </section>
  );
}

function StatusDot({ status }: { status: TaskStatus }) {
  return <span className={`status-dot ${status}`} aria-label={status} />;
}

function StatusBadge({ status }: { status: TaskStatus }) {
  const label: Record<TaskStatus, string> = {
    idle: "空闲",
    ready: "待运行",
    running: "运行中",
    passed: "通过",
    failed: "失败"
  };

  return <span className={`status-badge ${status}`}>{label[status]}</span>;
}

function mapServerTask(task: ServerTask): TaskItem {
  return {
    id: task.id,
    name: task.title,
    description: task.description || task.path,
    selected: false,
    status: "idle",
    steps: task.steps.map((step) => ({
      number: step.number,
      action: step.action,
      window: step.window,
      verification: step.verification,
      status: "idle"
    }))
  };
}

function decorateTask(task: TaskItem, status: RunStatus, currentInstructionIndex: number | null): TaskItem {
  const selected = status.task === task.id || task.selected;
  if (status.task !== task.id) {
    return { ...task, selected, status: selected ? "ready" : "idle" };
  }
  const activeIndex = clampStepIndex(currentInstructionIndex ?? 0, task.steps.length);
  if (status.state === "running") {
    return {
      ...task,
      selected: true,
      status: "running",
      steps: task.steps.map((step, index) => ({
        ...step,
        status: index < activeIndex ? "passed" : index === activeIndex ? "running" : "idle"
      }))
    };
  }
  if (status.state === "succeeded") {
    return {
      ...task,
      selected: true,
      status: "passed",
      steps: task.steps.map((step) => ({ ...step, status: "passed" }))
    };
  }
  if (status.state === "failed") {
    return {
      ...task,
      selected: true,
      status: "failed",
      steps: task.steps.map((step, index) => ({
        ...step,
        status: index < activeIndex ? "passed" : index === activeIndex ? "failed" : "idle"
      }))
    };
  }
  return { ...task, selected: true, status: "ready" };
}

function latestInstructionIndex(logs: LogEvent[], status: RunStatus) {
  if (!status.run_id) {
    return null;
  }
  for (let index = logs.length - 1; index >= 0; index -= 1) {
    const event = logs[index];
    if (event.run_id && event.run_id !== status.run_id) {
      continue;
    }
    if (event.type !== "log" || !event.line) {
      continue;
    }
    const match = event.line.match(/^\s*index:\s*(\d+)\s*$/);
    if (!match) {
      continue;
    }
    const value = Number(match[1]);
    if (Number.isFinite(value) && value > 0) {
      return value - 1;
    }
  }
  return null;
}

function clampStepIndex(index: number, length: number) {
  if (length <= 0) {
    return 0;
  }
  return Math.min(Math.max(index, 0), length - 1);
}

function emptyTask(): TaskItem {
  return {
    id: "",
    name: "未加载任务",
    description: "请连接本地后端",
    selected: false,
    status: "idle",
    steps: []
  };
}

function runStateLabel(state: RunStatus["state"]) {
  const label: Record<RunStatus["state"], string> = {
    idle: "空闲",
    running: "运行中",
    succeeded: "成功",
    failed: "失败",
    stopped: "已停止"
  };
  return label[state];
}

function omniParserStateLabel(state: OmniParserStatus["state"]) {
  const label: Record<OmniParserStatus["state"], string> = {
    unknown: "未知",
    starting: "启动中",
    running: "运行中",
    stopping: "停止中",
    stopped: "未启动",
    failed: "失败"
  };
  return label[state];
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--:--:--";
  }
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function logEventKey(event: LogEvent) {
  return `${event.run_id ?? "run"}:${event.seq}:${event.type}`;
}

function formatLogEvent(event: LogEvent) {
  if (event.type === "log") {
    return `[${formatTime(event.timestamp)}] ${event.stream ?? "log"}  ${event.line ?? ""}`;
  }
  if (event.type === "summary") {
    return `[${formatTime(event.timestamp)}] summary  ${formatSummary(event.summary)}`;
  }
  return `[${formatTime(event.timestamp)}] status  ${event.state}${event.exit_code === undefined ? "" : ` exit=${event.exit_code}`}`;
}

function formatSummary(summary: Record<string, unknown> | undefined) {
  if (!summary) {
    return "";
  }
  const code = typeof summary.code === "string" ? summary.code : "";
  const ok = typeof summary.ok === "boolean" ? String(summary.ok) : "";
  const steps = typeof summary.steps === "number" ? String(summary.steps) : "";
  const message = typeof summary.message === "string" ? summary.message : "";
  const shortenedMessage = message.length > 240 ? `${message.slice(0, 240)}...` : message;
  return JSON.stringify({ ok, code, message: shortenedMessage, steps });
}
