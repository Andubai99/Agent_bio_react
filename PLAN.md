# F:\Agent_bio_react 全新 ReAct 智能体计划

## Summary
- 新智能体根目录固定为 `F:\Agent_bio_react`，该文件夹已存在。
- 新智能体在 `F:\Agent_bio_react` 中从零实现，不放在旧项目目录下，不改造旧固定流程代码。
- 当前仓库必须自包含运行逻辑；不导入旧项目代码，不读取旧项目配置或 API 文件。

## Key Changes
- 新目录结构：
  - `F:\Agent_bio_react\tasks\`：任务 Markdown 文档，采用 `YAML frontmatter + Markdown`。
  - `F:\Agent_bio_react\agent\`：ReAct loop、状态机、Reasoner 接口、任务加载器。
  - `F:\Agent_bio_react\tools\`：受控工具白名单，封装截图、窗口连接、点击、输入、拖拽、等待、视觉定位/验证。
  - `F:\Agent_bio_react\modules\vision\`：视觉验证层在本仓库内独立实现，优先使用当前仓库的 API key 配置。
  - `F:\Agent_bio_react\memory\`：SQLite 记忆库、候选记忆、审核和检索逻辑。
  - `F:\Agent_bio_react\data\`：运行数据，默认存放 `memory.sqlite3`。
  - `F:\Agent_bio_react\tests\`：单元测试和 mock 集成测试。
  - `F:\Agent_bio_react\run_agent.py`：新入口。

- 独立运行边界：
  - 桌面执行层直接使用当前环境安装的 `maa` 包。
  - 桌面执行配置使用本仓库 `config/settings.yaml`。
  - API key 只来自环境变量或本仓库 `API.txt`。
  - 不导入旧项目模块，不把旧项目路径加入 `sys.path`，不读取旧项目配置。

- 新任务文档格式：
  - YAML 字段：`id`、`title`、`description`、`inputs`、`allowed_tools`、`start_conditions`、`success_criteria`、`safety_rules`。
  - Markdown 章节：任务目标、关键界面状态、推荐里程碑、常见错误、完成判据。
  - 不允许写死坐标；只能描述目标、约束、验证标准和允许动作。
  - 首个任务文档放在 `F:\Agent_bio_react\tasks\biopharma_protein.md`。

- ReAct loop：
  - 固定循环：`Observe -> Retrieve Memory -> Reason -> Act -> Verify -> Record Candidate`。
  - `Reasoner` 首版只定义接口，不绑定具体模型；可先用 `ManualReasoner` 或 mock reasoner 验证框架。
  - `Act` 只能调用任务文档 `allowed_tools` 中声明的工具。
  - 每个动作后必须截图并验证状态变化；验证失败进入修正回合或安全退出。

- 记忆系统：
  - SQLite 文件默认放在 `F:\Agent_bio_react\data\memory.sqlite3`。
  - 训练模式写入候选记忆，不直接固化。
  - 审核命令批量批准/拒绝候选记忆。
  - 正式模式只使用 approved 记忆，并且必须结合当前截图验证，不能直接复用坐标。

## Test Plan
- 单元测试：
  - 任务 Markdown 解析和必填字段校验。
  - 工具白名单权限校验。
  - `Reasoner` 输出 schema 校验。
  - SQLite 候选记忆写入、审核、正式检索。
  - Qwen provider mock 调用和 fallback 顺序。

- 集成测试：
  - 使用 mock screenshot + mock reasoner 跑完整 ReAct loop。
  - 模拟点错 `Oligonucleotide Analysis`：验证器必须发现页面错误，不允许继续任务。
  - 训练模式生成候选记忆，审核后正式模式可检索使用。
  - 正式模式中，记忆推荐动作仍必须通过当前截图验证。

- 实机验收：
  - 从 `F:\Agent_bio_react\run_agent.py` 启动 BioPharma 蛋白任务。
  - 成功条件：最终进入 Deconvoluted Spectrum 页面，并完成最高峰拖拽。
  - 失败条件：目标窗口不存在、点错入口、文件窗口未出现、验证不一致时必须明确报错，不能误报成功。

## Assumptions
- 新智能体根路径固定为 `F:\Agent_bio_react`。
- 新智能体完全独立于旧固定流程入口和旧项目配置。
- 不复用旧项目中配置的 API Key 或旧桌面适配器。
- 推理模型暂时空置，只留接口；视觉模型继续优先使用 Qwen。
