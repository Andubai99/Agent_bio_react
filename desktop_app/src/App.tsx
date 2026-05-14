import {
  Activity,
  Check,
  ChevronDown,
  Eye,
  FileText,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  Monitor,
  Play,
  RotateCcw,
  Settings,
  SlidersHorizontal,
  Square,
  Terminal,
  Zap
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  defaultReasonerConfig,
  defaultVisionConfig,
  initialTasks,
  runtimeEvents
} from "./data";
import type { ProviderConfig, TaskItem, TaskStatus } from "./types";

type TabId = "tasks" | "monitor" | "settings" | "logs";
type ConfigKind = "reasoner" | "vision";

const tabs: Array<{ id: TabId; label: string; icon: typeof ListChecks }> = [
  { id: "tasks", label: "任务", icon: ListChecks },
  { id: "monitor", label: "运行监控", icon: Monitor },
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

export function App() {
  const [activeTab, setActiveTab] = useState<TabId>("tasks");
  const [tasks, setTasks] = useState<TaskItem[]>(initialTasks);
  const [reasoner, setReasoner] = useState<ProviderConfig>(defaultReasonerConfig);
  const [vision, setVision] = useState<ProviderConfig>(defaultVisionConfig);

  const activeTask = useMemo(() => tasks.find((task) => task.selected) ?? tasks[0], [tasks]);

  function toggleTask(taskId: string) {
    setTasks((items) =>
      items.map((task) =>
        task.id === taskId
          ? {
              ...task,
              selected: !task.selected,
              status: !task.selected ? "ready" : "idle"
            }
          : task
      )
    );
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
        {activeTab === "tasks" && (
          <TaskWorkspace
            activeTask={activeTask}
            reasoner={reasoner}
            tasks={tasks}
            toggleTask={toggleTask}
            vision={vision}
          />
        )}
        {activeTab === "monitor" && <MonitorView activeTask={activeTask} />}
        {activeTab === "settings" && (
          <SettingsView
            reasoner={reasoner}
            updateProvider={updateProvider}
            vision={vision}
          />
        )}
        {activeTab === "logs" && <LogsView />}
      </main>
    </div>
  );
}

function TaskWorkspace({
  activeTask,
  reasoner,
  tasks,
  toggleTask,
  vision
}: {
  activeTask: TaskItem;
  reasoner: ProviderConfig;
  tasks: TaskItem[];
  toggleTask: (taskId: string) => void;
  vision: ProviderConfig;
}) {
  const selectedCount = tasks.filter((task) => task.selected).length;
  const currentStep = activeTask.steps.find((step) => step.status === "running") ?? activeTask.steps[0];

  return (
    <section className="workspace-grid">
      <aside className="task-panel">
        <div className="panel-heading">
          <div>
            <h2>任务列表</h2>
            <span>{selectedCount} 项已勾选</span>
          </div>
          <button className="icon-button" type="button" aria-label="刷新任务">
            <RotateCcw size={17} />
          </button>
        </div>

        <div className="task-list">
          {tasks.map((task) => (
            <label className={task.selected ? "task-row selected" : "task-row"} key={task.id}>
              <input
                checked={task.selected}
                onChange={() => toggleTask(task.id)}
                type="checkbox"
              />
              <span className="task-copy">
                <strong>{task.name}</strong>
                <small>{task.description}</small>
              </span>
              <StatusDot status={task.status} />
            </label>
          ))}
        </div>

        <div className="task-actions">
          <button className="secondary-button" type="button">
            <Square size={15} />
            停止
          </button>
          <button className="primary-button" type="button">
            <Play size={15} />
            开始运行
          </button>
        </div>
      </aside>

      <section className="live-panel">
        <div className="panel-heading">
          <div>
            <h2>当前任务窗口</h2>
            <span>{currentStep?.window ?? "未选择窗口"}</span>
          </div>
          <div className="runtime-pills">
            <span>
              <Zap size={14} />
              {reasoner.model}
            </span>
            <span>
              <Eye size={14} />
              {vision.model}
            </span>
          </div>
        </div>

        <div className="live-layout">
          <WindowPreview activeTask={activeTask} />
          <div className="step-panel">
            <div className="section-title">
              <Activity size={16} />
              执行进度
            </div>
            <div className="step-list">
              {activeTask.steps.map((step) => (
                <div className={step.status === "running" ? "step-row running" : "step-row"} key={step.number}>
                  <span className="step-number">{step.number}</span>
                  <span className="step-main">
                    <strong>{step.action}</strong>
                    <small>{step.verification}</small>
                  </span>
                  <StatusBadge status={step.status} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </section>
  );
}

function WindowPreview({ activeTask }: { activeTask: TaskItem }) {
  return (
    <div className="window-preview">
      <div className="mock-window-bar">
        <span>Thermo BioPharma Finder 5.1</span>
        <div className="window-controls">
          <i />
          <i />
          <i />
        </div>
      </div>
      <div className="mock-ribbon">
        <button className="ribbon-active" type="button">Home</button>
        <button type="button">Intact Mass Analysis</button>
        <button type="button">Load Results</button>
        <button type="button">Queue</button>
      </div>
      <div className="mock-body">
        <div className="form-column">
          <label>
            Experiment Name
            <input readOnly value="BioPharma test demo1" />
          </label>
          <label>
            Analysis File
            <div className="file-field">1#_20230714133351.raw</div>
          </label>
          <label className="checkbox-line">
            <input checked readOnly type="checkbox" />
            Default ReSpect
          </label>
          <button className="queue-button" type="button">Add To Queue</button>
        </div>
        <div className="queue-column">
          <div className="queue-header">Run Queue</div>
          <div className="queue-item active">
            <FileText size={15} />
            {activeTask.name}
          </div>
          <div className="queue-table">
            <span>状态</span>
            <strong>配置中</strong>
            <span>窗口</span>
            <strong>主窗口</strong>
            <span>验证</span>
            <strong>Qwen running</strong>
          </div>
        </div>
      </div>
    </div>
  );
}

function MonitorView({ activeTask }: { activeTask: TaskItem }) {
  return (
    <section className="monitor-grid">
      <div className="wide-panel">
        <div className="panel-heading">
          <div>
            <h2>运行监控</h2>
            <span>{activeTask.name}</span>
          </div>
          <StatusBadge status={activeTask.status} />
        </div>
        <div className="event-list">
          {runtimeEvents.map(([time, type, message]) => (
            <div className="event-row" key={`${time}-${message}`}>
              <span>{time}</span>
              <strong>{type}</strong>
              <p>{message}</p>
            </div>
          ))}
        </div>
      </div>
      <div className="side-panel">
        <div className="section-title">
          <Monitor size={16} />
          当前窗口
        </div>
        <WindowPreview activeTask={activeTask} />
      </div>
    </section>
  );
}

function SettingsView({
  reasoner,
  updateProvider,
  vision
}: {
  reasoner: ProviderConfig;
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
          <button
            className={config.mode === "preset" ? "active" : ""}
            onClick={() => patch({ mode: "preset" })}
            type="button"
          >
            内置
          </button>
          <button
            className={config.mode === "custom" ? "active" : ""}
            onClick={() => patch({ mode: "custom" })}
            type="button"
          >
            自定义
          </button>
        </div>
      </div>

      <div className="settings-form">
        <label>
          模型来源
          <div className="select-wrap">
            <select
              disabled={config.mode === "custom"}
              onChange={(event) => setPreset(event.target.value)}
              value={config.preset}
            >
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
          <input
            onChange={(event) => patch({ model: event.target.value })}
            readOnly={config.mode === "preset"}
            value={config.model}
          />
        </label>

        <label className="form-wide">
          Endpoint
          <input
            onChange={(event) => patch({ endpoint: event.target.value })}
            readOnly={config.mode === "preset"}
            value={config.endpoint}
          />
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
          <input
            min={1}
            onChange={(event) => patch({ timeoutSeconds: Number(event.target.value) })}
            type="number"
            value={config.timeoutSeconds}
          />
        </label>

        <label>
          Max Tokens
          <input
            min={1}
            onChange={(event) => patch({ maxTokens: Number(event.target.value) })}
            type="number"
            value={config.maxTokens}
          />
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

function LogsView() {
  return (
    <section className="wide-panel">
      <div className="panel-heading">
        <div>
          <h2>日志</h2>
          <span>logs/错误逻辑处理层工作日志.md</span>
        </div>
      </div>
      <div className="log-console">
        <p>[15:03:40] startup failure routed through FailurePolicy</p>
        <p>-----------</p>
        <p>[15:04:16] verification provider: qwen3-vl-flash</p>
        <p>-----------</p>
        <p>[15:04:18] current instruction retry budget: 1</p>
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
