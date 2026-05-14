import type { ProviderConfig, TaskItem } from "./types";

export const initialTasks: TaskItem[] = [
  {
    id: "biopharma_protein",
    name: "任务：蛋白",
    description: "BioPharma Finder 5.1 完整队列流程",
    selected: true,
    status: "running",
    steps: [
      {
        number: 0,
        action: "点击 Home",
        window: "主窗口",
        verification: "回到 Home 界面",
        status: "passed"
      },
      {
        number: 1,
        action: "点击 Intact Mass Analysis",
        window: "主窗口",
        verification: "进入实验配置界面",
        status: "passed"
      },
      {
        number: 2,
        action: "输入 Experiment Name",
        window: "主窗口",
        verification: "输入框中有输入",
        status: "running"
      },
      {
        number: 3,
        action: "点击 ...",
        window: "主窗口 -> 添加分析文件窗口",
        verification: "打开 Add Analysis File(s)",
        status: "ready"
      },
      {
        number: 4,
        action: "双击 1#_20230714133351.raw",
        window: "添加分析文件窗口 -> 主窗口",
        verification: "RAW 文件加入列表",
        status: "idle"
      },
      {
        number: 5,
        action: "选择 Default ReSpect",
        window: "主窗口",
        verification: "选择框处于选中状态",
        status: "idle"
      },
      {
        number: 6,
        action: "点击 Add To Queue",
        window: "主窗口",
        verification: "任务加入队列",
        status: "idle"
      }
    ]
  },
  {
    id: "intact_mass_check",
    name: "Intact Mass 快速检查",
    description: "只验证主窗口和实验配置页",
    selected: false,
    status: "idle",
    steps: []
  },
  {
    id: "load_raw_file",
    name: "RAW 文件加载",
    description: "文件选择窗口与回主窗口链路",
    selected: false,
    status: "idle",
    steps: []
  },
  {
    id: "queue_submit",
    name: "队列提交",
    description: "选择算法并加入运行队列",
    selected: false,
    status: "idle",
    steps: []
  }
];

export const defaultReasonerConfig: ProviderConfig = {
  mode: "preset",
  preset: "deepseek",
  model: "deepseek-v4-pro",
  endpoint: "https://api.deepseek.com/chat/completions",
  apiKey: "",
  timeoutSeconds: 60,
  maxTokens: 512
};

export const defaultVisionConfig: ProviderConfig = {
  mode: "preset",
  preset: "qwen",
  model: "qwen3-vl-flash",
  endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
  apiKey: "",
  timeoutSeconds: 45,
  maxTokens: 256
};

export const runtimeEvents = [
  ["15:04:08", "观察", "解析到 128 个 UI 元素"],
  ["15:04:10", "推理", "DeepSeek 返回 2 个动作"],
  ["15:04:12", "执行", "点击 Experiment Name input box"],
  ["15:04:13", "执行", "输入 BioPharma test demo1"],
  ["15:04:16", "验证", "Qwen 视觉验证进行中"]
];
