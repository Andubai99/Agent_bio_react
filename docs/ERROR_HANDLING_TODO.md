# Error Handling Layer

The current loop uses a conservative failure handling layer. The goal is to
make every failure decision explicit and auditable while avoiding complex UI
rollback or free-form repair planning.

## Policy

- Retryable failures are retried at most once for the same instruction, stage,
  and error code.
- Non-retryable failures return an explicit error code and stop the run.
- Every failure path logs `错误处理决策` with stage, code, decision, attempt,
  max attempts, instruction index, and reason.

## Retryable failures

- `VERIFY_FAILED`: retry the current instruction once.
- `VISION_*_FORMAT_ERROR` / `VISION_*_FAILED`: retry visual verification once
  using the same screenshot.
- `OBSERVE_FAILED` / `OBSERVE_REUSE_FAILED`: take a fresh screenshot and parse
  again once.
- `REASONER_FAILED` / `NO_ACTION`: retry reasoning once using the current
  observation.
- `ELEMENT_NOT_FOUND`: take a fresh screenshot, parse again, and retry the
  current instruction once.
- `WINDOW_CONTEXT_EXPECTED_NOT_FOUND` after an action: retry the current
  instruction once with a fresh observation.
- `POST_ACTION_SCREENSHOT_FAILED`: retry post-action screenshot capture once.
- `PAGE_CHANGE_COMPARISON_FAILED`: fall back once to single-screenshot visual
  verification.

## Non-retryable examples

- Startup/window connection failures.
- Unsupported or unknown actions.
- Maa job failures.
- Success criteria failures.
- Max-step exhaustion.
