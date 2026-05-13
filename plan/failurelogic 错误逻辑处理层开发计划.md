# failurelogic 错误逻辑处理层开发计划

## 目标

- 在 `failurelogic` 分支实现统一错误处理层，把当前散落在 `ReActAgent` 主循环里的“失败即退出 / 视觉失败重试一次”收敛为可审计、可测试、可扩展的失败决策机制。
- v1 目标是“保守恢复”：只做低风险、次数受限的重试或重新观察，不做复杂回滚、不引入 memory、不让模型自由规划修复动作。
- 成功路径行为保持不变：截图、OmniParser、DeepSeek `actions[]`、Maa 执行、窗口转场、本地页面变化、视觉验证仍按当前链路运行。
- 失败输出必须更明确：控制台记录失败阶段、错误码、处理决策、重试次数；最终摘要仍保持紧凑，不输出全量 JSON。

## Key Changes

- 新增 `agent/failure_handler.py`，集中定义：
  - `FailureStage`：`startup`、`window_connect`、`pre_window_sync`、`observe`、`reasoner`、`no_action`、`action`、`post_window_sync`、`post_screenshot`、`page_change`、`verification`、`success_criteria`、`max_steps`。
  - `FailureDecision`：`abort`、`retry_instruction`、`retry_observe`、`retry_reasoner`、`retry_verification`、`retry_screenshot`、`fallback_single_screenshot_verification`。
  - `FailureContext`：携带 stage、code、message、当前任务序号、循环 step、page_changed、窗口转场、相关 `ToolResult` 摘要。
  - `FailurePolicy`：纯函数策略表，输入 context 和历史计数，输出 decision。
- 在 `ReActAgent` 中接入失败层：
  - 所有当前 `return AgentRunResult(False, ...)` 的分支先调用 failure handler。
  - handler 决定可恢复时，主循环只做对应的保守动作；不可恢复时再退出。
  - 每个任务指示按 `(instruction_index, stage, code)` 计数，默认每类恢复最多 1 次。
- v1 策略表固定为：
  - `VERIFY_FAILED`：当前任务指示重试一次；`page_changed=false` 时复用观察，`true/null` 时重新截图观察。
  - `VISION_*_FORMAT_ERROR` / `VISION_*_FAILED`：同一截图重新调用视觉验证一次，仍失败则退出。
  - `OBSERVE_FAILED`：重新截图并重新调用 OmniParser 一次，仍失败则退出。
  - `REASONER_FAILED` / `NO_ACTION`：同一观察重新调用推理器一次，仍失败则退出。
  - `ELEMENT_NOT_FOUND`：重新观察并重新推理一次，仍失败则退出。
  - `WINDOW_CONTEXT_EXPECTED_NOT_FOUND` 发生在动作后：回到当前任务指示，重新观察并重新执行该指示一次，仍失败则退出。
  - `POST_ACTION_SCREENSHOT_FAILED`：重新截图一次，仍失败则退出。
  - `PAGE_CHANGE_COMPARISON_FAILED`：降级为只用 after screenshot 做单图视觉验证一次。
  - `WINDOW_NOT_FOUND`、`WINDOW_CONNECT_FAILED`、`WINDOW_ACTIVATE_FAILED`、`UNKNOWN_ACTION`、`MAA_JOB_FAILED`、`SUCCESS_CRITERIA_NOT_MET`、`MAX_STEPS_EXCEEDED`：直接退出。
- 运行日志增加统一记录：
  - 日志名：`错误处理决策`
  - 字段：`stage`、`code`、`decision`、`attempt`、`max_attempts`、`instruction_index`、`reason`
  - 保持现有 `INFO/OK/FAIL` 和时间戳分隔线格式。
- 同步修正一个已暴露的动作提示解析边界：
  - `点击 ... 按钮` 不应解析出 `press_key`。
  - `parse_action_sequence()` 中 `按` 只在独立动词或明确按键语境下匹配，不匹配“按钮”。

## Test Plan

- 纯单元测试：
  - `FailurePolicy` 对每个错误码返回预期 decision。
  - 同一 `(instruction, stage, code)` 第二次失败会从 retry 变为 abort。
  - `VERIFY_FAILED` 根据 `page_changed` 返回复用观察或重新观察策略。
  - `PAGE_CHANGE_COMPARISON_FAILED` 返回单图视觉验证 fallback。
  - `点击 ... 按钮` 只解析为一个 click hint。
- 主循环 fake 集成测试：
  - 视觉第一次失败、第二次成功，最终任务继续推进。
  - 视觉连续两次失败，最终返回 `VERIFY_FAILED`。
  - OmniParser 观察第一次失败、第二次成功，最终继续推进。
  - Reasoner 第一次异常、第二次返回动作，最终继续推进。
  - 动作后窗口同步第一次失败、重试当前指示后成功。
  - `ELEMENT_NOT_FOUND` 后重新观察并重新推理一次。
  - `UNKNOWN_ACTION` / `MAA_JOB_FAILED` 不重试，直接失败。
- 回归检查：
  - 当前 `biopharma_protein` 正常成功路径仍返回 `{"ok": true, "code": "OK", "message": "Task complete.", "steps": 7}`。
  - 控制台不输出全量 step JSON。
  - OmniParser 服务仍按“已有则复用，本次启动则结束时关闭”的现有行为运行。

## Assumptions

- v1 不实现复杂自动修复动作，例如让模型生成“纠错计划”、自动关闭弹窗、撤销输入、清空队列或恢复应用初始状态。
- v1 不引入 memory，不改任务文件格式，不改 DeepSeek 标准 `actions[]` 协议。
- 所有恢复动作都必须受限、可计数、可日志审计；同一失败点默认最多恢复一次。
- 错误处理层只协调主循环，不把恢复逻辑塞进 Maa、OmniParser、Reasoner、Vision provider 内部。
