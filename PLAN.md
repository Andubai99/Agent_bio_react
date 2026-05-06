# ReAct 智能体重构计划

## Summary
- 重做为通用 ReAct 多任务框架：任务流程从 Python 固定步骤迁移到 Markdown 任务文档，运行时由智能体检索任务说明、观察界面、选择工具、验证结果，直到任务完成或失败退出。
- 执行采用“受控自主”：智能体可决定下一步，但只能调用白名单工具；每个动作后必须截图验证，不能只相信模型返回坐标。
- 训练阶段只生成候选记忆；运行结束后批量人工审核，审核通过后才进入正式记忆库。正式运行时记忆只作为提示和约束，不能直接绕过当前截图验证。
- ReAct 推理模型首版不绑定具体供应商，只保留 `Reasoner` 接口；视觉理解工具继续使用现有 Qwen/GLM provider。

## Key Changes
- 新增通用任务文档格式：`YAML frontmatter + Markdown`。
  - YAML 固定字段：`id`、`title`、`description`、`inputs`、`allowed_tools`、`start_conditions`、`success_criteria`、`safety_rules`。
  - Markdown 固定章节：任务目标、关键界面状态、推荐里程碑、常见错误、完成判据。
  - 任务文档不得写死坐标，只能描述目标、约束、验证标准和允许动作。

- 新增 ReAct 引擎，而不是继续使用 `tasks/biopharma_protein.py` 的固定 `STEPS`。
  - 循环结构固定为：`Observe -> Retrieve Memory -> Reason -> Act -> Verify -> Record Candidate`。
  - `Observe` 负责截图、窗口信息、当前状态摘要。
  - `Reason` 输出结构化 JSON 决策：下一动作、目标描述、期望变化、失败回退策略。
  - `Act` 只能调用工具白名单：窗口连接、截图、视觉定位/验证、点击、双击、拖拽、输入文本、按键、滚动、等待。
  - `Verify` 必须基于新截图判断动作是否达到预期；验证失败时进入修正回合，而不是继续盲跑。

- 新增模型无关接口。
  - `Reasoner`：只定义输入输出协议，不绑定具体模型。
  - 默认实现为 `ManualReasoner` 或 `NullReasoner`：用于验证框架和接口；后续可接任意文本/多模态模型。
  - `VisionProvider` 继续作为工具存在，优先级沿用：Qwen3-VL-Flash -> GLM-4.6V-Flash -> 临时 YAML。

- 新增 SQLite 记忆库。
  - 表：`episodes`、`steps`、`memory_candidates`、`approved_memories`。
  - 训练运行写入候选记忆，包括截图路径、观察摘要、动作、动作结果、验证结果、错误原因。
  - 批量审核命令将候选记忆标记为 approved/rejected。
  - 正式运行只检索 approved 记忆，并要求当前截图验证通过后才能执行动作。

- 完全重写运行入口。
  - 新入口加载任务 md、创建 ReAct 引擎、连接工具、执行任务。
  - 旧固定流程不再作为运行 fallback；旧文件可暂时保留作参考和对照，但新入口不依赖 `tasks/biopharma_protein.py`。

## Test Plan
- 单元测试：
  - 任务 Markdown frontmatter 解析、必填字段校验、非法工具拒绝。
  - `Reasoner` 输出 JSON schema 校验：非法动作、缺少验证目标、越权工具调用必须失败。
  - 工具层 dry-run/mock：点击、输入、截图、视觉定位、验证动作均返回统一 `ToolResult`。
  - SQLite 记忆：候选写入、审核流转、approved 检索、rejected 不参与检索。

- 集成测试：
  - 使用 mock 界面截图和 mock reasoner 跑完整 ReAct loop。
  - 复现 Qwen 点错 `Oligonucleotide Analysis` 的案例：验证器必须识别页面错误，并要求修正。
  - 训练模式生成候选记忆，批量审核后正式模式能检索并使用该经验。
  - 正式模式中，即使记忆推荐旧坐标，也必须在当前截图验证目标文本/区域正确后才点击。

- 实机验收：
  - 用新的任务 md 执行 BioPharma 蛋白任务。
  - 成功条件：最终进入 Deconvoluted Spectrum 页面，并完成最高峰拖拽。
  - 失败条件：点错任务入口、文件窗口未出现、验证结果不一致时必须停止或修正，不能误报成功。

## Assumptions
- 首版目标是通用多任务框架，不只服务蛋白任务。
- 执行策略是受控自主，不允许模型自由调用任意 Win32/API。
- 推理模型暂不绑定，只保留接口；视觉理解仍可使用现有 Qwen/GLM。
- 记忆必须审核后固化；正式运行使用记忆作为提示和反例，不直接复用未验证坐标。
- 新 ReAct 入口完全重写，不以旧 Python 固定流程作为 fallback。
