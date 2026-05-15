# ReAct 循环重构计划

## Summary

- 重构为新循环：启动/探活 OmniParser -> 连接并置前窗口 -> 截图 -> OmniParser UI 解析 -> Reasoner 选择动作和元素序号 -> 执行层按元素坐标交互 -> 视觉模型验证 -> 回到截图。
- 删除现有 memory 机制、mock 模式、旧 UI 解析逻辑和全部 tests；`tasks/` 内容保持不变。
- Agent 负责启动 OmniParser FastAPI 服务；如果已有可用服务则复用，如果本次启动则运行结束后关闭。

## Key Changes

- OmniParser 服务管理：
  - 新增服务管理层，启动前先请求 `GET /probe/`；若不可用，用 `OmniParser/.venv/Scripts/python.exe` 启动 `OmniParser/omnitool/omniparserserver/omniparserserver.py`。
  - 默认启动参数：`--som_model_path OmniParser/weights/icon_detect/model.pt`、`--caption_model_name florence2`、`--caption_model_path OmniParser/weights/icon_caption_florence`、`--device cuda`、`--BOX_TRESHOLD 0.05`、`--host 127.0.0.1`、`--port 8001`。
  - Agent 只用 `/probe/` 判定服务可用；`OmniParser/imgs/ScreenShot_2026-05-06_192141_030.png` 作为手动/开发验证图片，不纳入每次运行自检。
- Agent 循环：
  - `connect_window` 只在启动时执行一次，并激活置前。
  - 每轮先截图，再调用 OmniParser `/parse/` 得到元素列表。
  - Reasoner 输入包含任务、截图路径、OmniParser 元素列表、历史步骤。
  - Reasoner 输出新 schema：`thought`、`action.type`、`action.element_idx`、`action.args`、`expected_observation`、`done`、`failure`。
  - 执行层不接受模型坐标；只接受元素序号，坐标由当前截图的 OmniParser bbox 中心换算。
- UI 解析和执行：
  - 删除 `tools/ui_elements.py` 及 Maa/mock 中所有旧 UI 解析逻辑。
  - 将 OmniParser `idx/type/bbox/content/interactivity/source` 映射为项目内 UI 元素；`idx` 保持 0 基，仅在当前截图有效。
  - bbox 为归一化 `xyxy`，先转截图像素中心，再映射到 Maa 控制器坐标。
  - 删除 mock desktop 支持，`run_agent.py --tools mock` 和相关脚本一起移除。
  - 支持动作：`click`、`double_click`、`input_text`、`drag`、`press_key`、`wait`；元素动作必须带 `element_idx`。
- Memory 和错误处理：
  - 删除 `MemoryStore`、候选记忆、审核命令、`--mode`、`--db`、`list-candidates`、`approve`、`reject`。
  - `memory/` 改为 README 文档占位，不提供可调用实现。
  - 新增错误处理 TODO 文档，列出 connect/screenshot/parse/reason/execute/verify 的未来接入点；本轮不写运行时代码。

## Public Interfaces

- `run_agent.py run` 保留核心参数：`--task`、`--reasoner manual|script|deepseek`、`--max-steps`、`--summary-only`、`--quiet`。
- 新增 OmniParser 配置默认值：
  - `OMNIPARSER_HOST=127.0.0.1`
  - `OMNIPARSER_PORT=8001`
  - `OMNIPARSER_TIMEOUT_SECONDS=300`
  - `OMNIPARSER_BOX_THRESHOLD=0.05`
  - `OMNIPARSER_IOU_THRESHOLD=0.7`
  - `OMNIPARSER_USE_PADDLEOCR=true`
  - `OMNIPARSER_IMGSZ=640`
- Scripted reasoner JSON 改用新动作 schema；旧 `{"action":{"name":...}}` 不再兼容。

## Test Plan

- 删除现有 tests，不新增自动化测试文件。
- 实施后做手动验证：
  - 无服务时运行 Agent，确认会自动启动 OmniParser 并通过 `/probe/`。
  - 已有服务时运行 Agent，确认复用服务且结束时不关闭外部服务。
  - 本次启动的服务在 Agent 结束后被关闭。
  - 用 `OmniParser/imgs/ScreenShot_2026-05-06_192141_030.png` 手动调用解析客户端，确认能返回 `parsed_content_list`。
  - Scripted reasoner 使用 `element_idx` 能执行 click/input_text。
  - DeepSeek reasoner 不输出坐标，只输出动作类型和元素序号。
  - 动作后视觉验证失败时，运行结果返回失败，不写 memory。

## Assumptions

- 默认端口固定为 `8001`；如果该端口已有可用 OmniParser 服务，Agent 复用它。
- 本轮只保证 Maa + OmniParser 新循环；mock、memory、旧测试链路全部退出。
- `tasks/biopharma_protein.md` 不改，Reasoner system prompt 会覆盖为新动作协议。
- 错误处理层本轮仅文档占位，不接入运行时。
