from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.types import ToolResult


class FailureStage(str, Enum):
    STARTUP = "startup"
    WINDOW_CONNECT = "window_connect"
    PRE_WINDOW_SYNC = "pre_window_sync"
    OBSERVE = "observe"
    REASONER = "reasoner"
    NO_ACTION = "no_action"
    ACTION = "action"
    POST_WINDOW_SYNC = "post_window_sync"
    POST_SCREENSHOT = "post_screenshot"
    PAGE_CHANGE = "page_change"
    VERIFICATION = "verification"
    SUCCESS_CRITERIA = "success_criteria"
    MAX_STEPS = "max_steps"


class FailureDecisionType(str, Enum):
    ABORT = "abort"
    RETRY_INSTRUCTION = "retry_instruction"
    RETRY_OBSERVE = "retry_observe"
    RETRY_REASONER = "retry_reasoner"
    RETRY_VERIFICATION = "retry_verification"
    RETRY_SCREENSHOT = "retry_screenshot"
    FALLBACK_SINGLE_SCREENSHOT_VERIFICATION = "fallback_single_screenshot_verification"


@dataclass(frozen=True)
class FailureContext:
    stage: FailureStage
    code: str
    message: str
    instruction_index: int | None = None
    loop_step: int | None = None
    page_changed: bool | None = None
    window_transition: Any = None
    result: ToolResult | None = None


@dataclass(frozen=True)
class FailureDecision:
    decision: FailureDecisionType
    attempt: int
    max_attempts: int
    reason: str

    @property
    def should_abort(self) -> bool:
        return self.decision == FailureDecisionType.ABORT


class FailurePolicy:
    def __init__(self, *, default_max_attempts: int = 1):
        self.default_max_attempts = default_max_attempts
        self._attempts: dict[tuple[int | None, str, str], int] = {}

    def decide(self, context: FailureContext) -> FailureDecision:
        retry_decision = self._retry_decision_for(context)
        if retry_decision is None:
            return FailureDecision(
                decision=FailureDecisionType.ABORT,
                attempt=0,
                max_attempts=0,
                reason=f"{context.code} is not recoverable in v1 policy.",
            )

        key = (context.instruction_index, context.stage.value, context.code)
        attempt = self._attempts.get(key, 0) + 1
        self._attempts[key] = attempt
        max_attempts = self.default_max_attempts
        if attempt > max_attempts:
            return FailureDecision(
                decision=FailureDecisionType.ABORT,
                attempt=attempt,
                max_attempts=max_attempts,
                reason=f"{context.code} exceeded retry limit for {context.stage.value}.",
            )

        return FailureDecision(
            decision=retry_decision,
            attempt=attempt,
            max_attempts=max_attempts,
            reason=self._reason_for(context, retry_decision),
        )

    def reset_instruction(self, instruction_index: int) -> None:
        for key in list(self._attempts):
            if key[0] == instruction_index:
                del self._attempts[key]

    def _retry_decision_for(self, context: FailureContext) -> FailureDecisionType | None:
        code = context.code
        stage = context.stage

        if code == "VERIFY_FAILED":
            return FailureDecisionType.RETRY_INSTRUCTION
        if code.startswith("VISION_") and (code.endswith("_FORMAT_ERROR") or code.endswith("_FAILED")):
            return FailureDecisionType.RETRY_VERIFICATION
        if code in {"OBSERVE_FAILED", "OBSERVE_REUSE_FAILED"}:
            return FailureDecisionType.RETRY_OBSERVE
        if code in {"REASONER_FAILED", "NO_ACTION"}:
            return FailureDecisionType.RETRY_REASONER
        if code == "ELEMENT_NOT_FOUND":
            return FailureDecisionType.RETRY_OBSERVE
        if code == "WINDOW_CONTEXT_EXPECTED_NOT_FOUND" and stage == FailureStage.POST_WINDOW_SYNC:
            return FailureDecisionType.RETRY_INSTRUCTION
        if code == "POST_ACTION_SCREENSHOT_FAILED":
            return FailureDecisionType.RETRY_SCREENSHOT
        if code == "PAGE_CHANGE_COMPARISON_FAILED":
            return FailureDecisionType.FALLBACK_SINGLE_SCREENSHOT_VERIFICATION
        return None

    def _reason_for(self, context: FailureContext, decision: FailureDecisionType) -> str:
        if decision == FailureDecisionType.RETRY_INSTRUCTION and context.code == "VERIFY_FAILED":
            if context.page_changed is False:
                return "Vision verification failed once; retry current instruction with cached observation."
            return "Vision verification failed once; retry current instruction with a fresh observation."
        if decision == FailureDecisionType.RETRY_VERIFICATION:
            return "Vision provider failed once; retry verification with the same screenshot."
        if decision == FailureDecisionType.RETRY_OBSERVE:
            return "Observation or element lookup failed once; take a fresh screenshot and parse again."
        if decision == FailureDecisionType.RETRY_REASONER:
            return "Reasoner failed once; retry with the current observation."
        if decision == FailureDecisionType.RETRY_SCREENSHOT:
            return "Post-action screenshot failed once; retry screenshot capture."
        if decision == FailureDecisionType.FALLBACK_SINGLE_SCREENSHOT_VERIFICATION:
            return "Comparison image failed; fall back to single after-screenshot verification."
        return "Retry allowed by v1 failure policy."


def summarize_failure_decision(context: FailureContext, decision: FailureDecision) -> dict[str, Any]:
    return {
        "stage": context.stage.value,
        "code": context.code,
        "decision": decision.decision.value,
        "attempt": decision.attempt,
        "max_attempts": decision.max_attempts,
        "instruction_index": context.instruction_index,
        "loop_step": context.loop_step,
        "page_changed": context.page_changed,
        "reason": decision.reason,
    }
