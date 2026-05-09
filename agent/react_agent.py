from __future__ import annotations

import re
from dataclasses import replace
from typing import List

from agent.runtime_logger import (
    NullRunLogger,
    summarize_decision,
    summarize_observation,
    summarize_tool_result,
)
from agent.task_parser import parse_task_body, parse_window_transition
from agent.types import AgentRunResult, ImageInfo, Observation, StepOutcome, TaskSpec, ToolResult
from modules.reasoner import Reasoner, ReasonerContext
from modules.ui_parser.omniparser import OmniParserClient
from modules.vision.page_change_detector import PageChangeDetector, PageChangeResult
from modules.vision.verifier import VisionVerifier
from tools.maa_desktop import MaaDesktop


class ReActAgent:
    def __init__(
        self,
        *,
        task: TaskSpec,
        desktop: MaaDesktop,
        ui_parser: OmniParserClient,
        reasoner: Reasoner,
        vision_verifier: VisionVerifier,
        page_change_detector: PageChangeDetector | None = None,
        max_steps: int = 30,
        logger=None,
    ):
        self.task = task
        self.desktop = desktop
        self.ui_parser = ui_parser
        self.reasoner = reasoner
        self.vision_verifier = vision_verifier
        self.page_change_detector = page_change_detector or PageChangeDetector()
        self.max_steps = max_steps
        self.logger = logger or NullRunLogger()

    def run(self) -> AgentRunResult:
        self.logger.log(
            "启动智能体",
            {
                "任务": self.task.id,
                "标题": self.task.title,
                "最大步数": self.max_steps,
            },
        )
        parsed_task = parse_task_body(self.task.body)
        title_keyword = _initial_window_title(self.task, parsed_task.window_definitions)
        self.logger.log("连接并置前目标窗口", {"title_keyword": title_keyword})
        connected = self.desktop.connect_window(title_keyword)
        self.logger.log("窗口连接结果", summarize_tool_result(connected))
        if not connected.ok:
            return AgentRunResult(False, connected.code, connected.message, [])

        task_steps = parsed_task.steps
        if not task_steps:
            return AgentRunResult(False, "NO_TASK_INSTRUCTIONS", "Task has no executable instructions.", [])
        self.logger.log(
            "任务结构化解析结果",
            {
                "window_definitions": parsed_task.window_definitions,
                "steps": [
                    {
                        "number": step.number,
                        "action": step.action_text,
                        "window": step.window_text,
                        "verification": step.verification_text,
                    }
                    for step in task_steps
                ],
            },
        )

        steps: List[StepOutcome] = []
        current_instruction_index = 0
        current_instruction_verification_failures = 0
        cached_observation: Observation | None = None
        reuse_cached_observation = False
        for index in range(1, self.max_steps + 1):
            completion = self._finish_if_all_instructions_verified(
                current_instruction_index,
                total=len(task_steps),
                steps=steps,
            )
            if completion is not None:
                return completion

            current_step = task_steps[current_instruction_index]
            window_transition = parse_window_transition(current_step.window_text, parsed_task.window_definitions)
            current_task = _task_with_current_instruction(
                self.task,
                current_instruction=current_step.action_text,
                current_index=current_instruction_index,
                total=len(task_steps),
            )
            self.logger.log(f"第 {index} 步开始")
            self.logger.log(
                "当前任务指示",
                {
                    "index": current_instruction_index + 1,
                    "total": len(task_steps),
                    "action": current_step.action_text,
                    "window": current_step.window_text,
                    "verification": current_step.verification_text,
                    "window_transition": window_transition,
                },
            )
            window_ready = self.desktop.sync_after_action(
                expected_title_keywords=_source_window_titles(window_transition),
                settle_seconds=0.0,
                allow_foreground_switch=False,
            )
            self.logger.log("观察前窗口上下文检查结果", summarize_tool_result(window_ready))
            if not window_ready.ok:
                return AgentRunResult(False, window_ready.code, window_ready.message, steps)

            observation_result = self._observe(
                index,
                current_instruction=_ui_parser_context(current_step.action_text, current_step.verification_text),
                reuse_observation=cached_observation if reuse_cached_observation else None,
            )
            reuse_cached_observation = False
            if isinstance(observation_result, ToolResult):
                return AgentRunResult(False, observation_result.code, observation_result.message, steps)
            observation = observation_result
            cached_observation = observation
            self.logger.log("观察结果", summarize_observation(observation))

            self.logger.log("调用推理器生成下一步动作")
            try:
                decision = self.reasoner.decide(
                    ReasonerContext(
                        task=current_task,
                        observation=observation,
                        history=steps,
                    )
                )
            except Exception as exc:
                return AgentRunResult(False, "REASONER_FAILED", str(exc), steps)
            self.logger.log("推理器输出", summarize_decision(decision))

            if decision.done:
                if decision.failure is not None:
                    return AgentRunResult(False, str(decision.failure), str(decision.failure), steps)
                verification = self.vision_verifier.verify_completion(
                    _current_instruction_verification_prompt(
                        current_step.verification_text,
                        current_index=current_instruction_index,
                        total=len(task_steps),
                    ),
                    ImageInfo(
                        path=observation.screenshot_path,
                        width=observation.image_size[0],
                        height=observation.image_size[1],
                        resolution=observation.resolution,
                    ),
                )
                self.logger.log("当前任务指示验证结果", summarize_tool_result(verification))
                if verification.ok:
                    current_instruction_verification_failures = 0
                    current_instruction_index += 1
                    completion = self._finish_if_all_instructions_verified(
                        current_instruction_index,
                        total=len(task_steps),
                        steps=steps,
                    )
                    if completion is not None:
                        return completion
                    continue
                if verification.code == "VERIFY_FAILED":
                    current_instruction_verification_failures += 1
                    if current_instruction_verification_failures == 1:
                        self.logger.log(
                            "Vision verification failed; retrying current instruction once",
                            {
                                "index": current_instruction_index + 1,
                                "total": len(task_steps),
                                "action": current_step.action_text,
                                "verification": current_step.verification_text,
                                "page_changed": None,
                            },
                        )
                        continue
                return AgentRunResult(False, verification.code, verification.message, steps)

            if decision.action is None:
                return AgentRunResult(
                    False,
                    "NO_ACTION",
                    "Reasoner did not provide an action.",
                    steps,
                )

            before_screenshot = ImageInfo(
                path=observation.screenshot_path,
                width=observation.image_size[0],
                height=observation.image_size[1],
                resolution=observation.resolution,
            )
            decision = self._redirect_to_synthetic_target_if_needed(
                decision,
                observation=observation,
                task_instruction=_ui_parser_context(current_step.action_text, current_step.verification_text),
            )
            self.logger.log("准备执行动作", decision.action)
            action_result = self.desktop.execute_action(
                decision.action,
                elements=observation.elements,
                screenshot=before_screenshot,
            )
            self.logger.log("动作执行结果", summarize_tool_result(action_result))
            if not action_result.ok:
                outcome = StepOutcome(index, observation, decision, action_result, None)
                steps.append(outcome)
                return AgentRunResult(False, action_result.code, action_result.message, steps)

            window_sync = self.desktop.sync_after_action(
                expected_title_keywords=_after_action_window_titles(window_transition),
                allow_foreground_switch=False,
            )
            self.logger.log("动作后窗口上下文同步结果", summarize_tool_result(window_sync))
            if not window_sync.ok:
                outcome = StepOutcome(index, observation, decision, window_sync, None)
                steps.append(outcome)
                return AgentRunResult(False, window_sync.code, window_sync.message, steps)

            try:
                post_screenshot = self.desktop.capture(f"step_{index}_after_action")
            except Exception as exc:
                outcome = StepOutcome(index, observation, decision, action_result, None)
                steps.append(outcome)
                return AgentRunResult(False, "POST_ACTION_SCREENSHOT_FAILED", str(exc), steps)

            verification_prompt = _current_instruction_verification_prompt(
                current_step.verification_text,
                current_index=current_instruction_index,
                total=len(task_steps),
            )
            page_change = self._detect_page_change(before_screenshot, post_screenshot, action=decision.action)
            self.logger.log("页面变化判断结果", page_change.to_dict())
            verification = _local_window_transition_verification(
                window_transition,
                window_sync=window_sync,
                after_screenshot=post_screenshot,
                page_change=page_change,
            )
            if verification is not None:
                self.logger.log("窗口转场本地验证结果", summarize_tool_result(verification))
            else:
                verification = self._verify_after_action(
                    verification_prompt,
                    before_screenshot=before_screenshot,
                    after_screenshot=post_screenshot,
                    page_change=page_change,
                )
            self.logger.log("当前任务指示验证结果", summarize_tool_result(verification))
            outcome = StepOutcome(
                index=index,
                observation=observation,
                decision=decision,
                action_result=action_result,
                verification=verification,
            )
            steps.append(outcome)
            page_changed = _verification_page_changed(verification)
            reuse_cached_observation = page_changed is False
            if verification.ok:
                current_instruction_verification_failures = 0
                current_instruction_index += 1
                completion = self._finish_if_all_instructions_verified(
                    current_instruction_index,
                    total=len(task_steps),
                    steps=steps,
                )
                if completion is not None:
                    return completion
                continue
            if verification.code == "VERIFY_FAILED":
                current_instruction_verification_failures += 1
                if current_instruction_verification_failures == 1:
                    self.logger.log(
                        "Vision verification failed; retrying current instruction once",
                        {
                            "index": current_instruction_index + 1,
                            "total": len(task_steps),
                            "action": current_step.action_text,
                            "verification": current_step.verification_text,
                            "page_changed": page_changed,
                        },
                    )
                    continue
                self.logger.log(
                    "当前任务指示验证不通过，按要求中断退出",
                    {
                        "index": current_instruction_index + 1,
                        "total": len(task_steps),
                        "action": current_step.action_text,
                        "verification": current_step.verification_text,
                    },
                )
                return AgentRunResult(False, verification.code, verification.message, steps)
            return AgentRunResult(False, verification.code, verification.message, steps)

        return AgentRunResult(
            False,
            "MAX_STEPS_EXCEEDED",
            f"Exceeded max_steps={self.max_steps}.",
            steps,
        )

    def _detect_page_change(
        self,
        before_screenshot: ImageInfo,
        after_screenshot: ImageInfo,
        *,
        action,
    ) -> PageChangeResult:
        local_result = self.page_change_detector.compare(
            before_screenshot,
            after_screenshot,
            action=action,
        )
        return local_result

    def _redirect_to_synthetic_target_if_needed(
        self,
        decision,
        *,
        observation: Observation,
        task_instruction: str,
    ):
        action = decision.action
        if action is None or action.type.strip().lower() not in {"click", "double_click", "input_text"}:
            return decision

        synthetic = self.ui_parser.synthetic_target_for_instruction(
            observation.elements,
            selected_idx=action.element_idx,
            task_instruction=task_instruction,
        )
        if synthetic is None or synthetic.idx == action.element_idx:
            return decision

        redirected_action = replace(
            action,
            element_idx=synthetic.idx,
            args={
                **action.args,
                "redirected_from_element_idx": action.element_idx,
                "redirected_to_synthetic_idx": synthetic.idx,
                "redirected_to_synthetic_content": synthetic.content,
                "synthetic_coordinate": list(synthetic.center),
            },
        )
        self.logger.log(
            "执行目标重定向到合成控件",
            {
                "from_element_idx": action.element_idx,
                "to_element_idx": synthetic.idx,
                "to_content": synthetic.content,
                "to_center": synthetic.center,
            },
        )
        return replace(
            decision,
            thought=f"{decision.thought} Synthetic target redirect applied to {synthetic.content}.",
            action=redirected_action,
        )

    def _verify_after_action(
        self,
        verification_prompt: str,
        *,
        before_screenshot: ImageInfo,
        after_screenshot: ImageInfo,
        page_change: PageChangeResult,
    ) -> ToolResult:
        if page_change.local_decisive:
            self.logger.log(
                "本地页面变化判断已确定，视觉模型只验证当前任务指示",
                {
                    "page_changed": page_change.page_changed,
                    "page_change_source": page_change.source,
                    "page_change_local_decisive": page_change.local_decisive,
                    "期望状态": verification_prompt,
                },
            )
            verification = self.vision_verifier.verify_completion(verification_prompt, after_screenshot)
            _attach_page_change_result(verification, page_change)
            return verification

        try:
            comparison = self.page_change_detector.build_comparison_image(
                before_screenshot,
                after_screenshot,
            )
        except Exception as exc:
            failed_page_change = PageChangeResult(
                None,
                0.0,
                "local_diff",
                f"{page_change.reason} 生成低清对比图失败：{exc}",
                page_change.metrics,
            )
            verification = ToolResult(
                False,
                "PAGE_CHANGE_COMPARISON_FAILED",
                failed_page_change.reason,
                data={"screenshot_path": after_screenshot.path},
            )
            _attach_page_change_result(verification, failed_page_change)
            return verification

        self.logger.log(
            "本地页面变化判断不确定，调用统一视觉验证",
            {
                "comparison_image": comparison.path,
                "local_result": page_change.to_dict(),
                "期望状态": verification_prompt,
            },
        )
        verification = self.vision_verifier.verify_post_action_comparison(verification_prompt, comparison)
        model_page_changed = _verification_page_changed(verification)
        page_change_from_vision = PageChangeResult(
            model_page_changed,
            0.6 if model_page_changed is not None else 0.0,
            "vision_compare" if model_page_changed is not None else "vision_compare_failed",
            verification.message,
            {
                **page_change.metrics,
                "local_confidence": page_change.confidence,
            },
        )
        _attach_page_change_result(verification, page_change_from_vision)
        return verification

    def _observe(
        self,
        index: int,
        *,
        current_instruction: str = "",
        reuse_observation: Observation | None = None,
    ) -> Observation | ToolResult:
        try:
            reused = reuse_observation is not None
            if reuse_observation is None:
                screenshot = self.desktop.capture(f"step_{index}_screenshot")
                elements = self.ui_parser.parse(screenshot, task_instruction=current_instruction)
            else:
                if not reuse_observation.screenshot_path:
                    return ToolResult(False, "OBSERVE_REUSE_FAILED", "Cached observation has no screenshot path.")
                screenshot = ImageInfo(
                    path=reuse_observation.screenshot_path,
                    width=reuse_observation.image_size[0],
                    height=reuse_observation.image_size[1],
                    resolution=reuse_observation.resolution,
                )
                elements = self.ui_parser.postprocess_existing(
                    screenshot,
                    reuse_observation.elements,
                    task_instruction=current_instruction,
                )
        except Exception as exc:
            return ToolResult(False, "OBSERVE_FAILED", str(exc))
        interactive_count = sum(1 for element in elements if element.interactive)
        source = "cached observation" if reused else "OmniParser"
        return Observation(
            summary=(
                f"Screenshot parsed by {source}: {len(elements)} elements, "
                f"{interactive_count} interactive."
            ),
            screenshot_path=screenshot.path,
            window_title=self.desktop.window_title,
            image_size=(screenshot.width, screenshot.height),
            resolution=screenshot.resolution,
            elements=elements,
            state={
                "element_count": len(elements),
                "interactive_element_count": interactive_count,
                "reused_observation": reused,
            },
        )

    def _verify_success_criteria(self) -> ToolResult:
        try:
            screenshot = self.desktop.capture("success_check")
        except Exception as exc:
            return ToolResult(False, "SUCCESS_SCREENSHOT_FAILED", str(exc))
        failed = []
        for criterion in self.task.success_criteria:
            self.logger.log("验证完成判据", {"判据": criterion})
            result = self.vision_verifier.verify_completion(criterion, screenshot)
            self.logger.log("单条完成判据结果", summarize_tool_result(result))
            if not result.ok:
                failed.append(f"{criterion}: {result.message}")
        if failed:
            return ToolResult(
                False,
                "SUCCESS_CRITERIA_NOT_MET",
                "; ".join(failed),
            )
        return ToolResult(True, "SUCCESS_CRITERIA_MET", "Task success verified.")

    def _finish_if_all_instructions_verified(
        self,
        current_instruction_index: int,
        *,
        total: int,
        steps: List[StepOutcome],
    ) -> AgentRunResult | None:
        if current_instruction_index < total:
            return None
        success_check = self._verify_success_criteria()
        self.logger.log("完成判据验证结果", summarize_tool_result(success_check))
        if not success_check.ok:
            return AgentRunResult(False, success_check.code, success_check.message, steps)
        return AgentRunResult(True, "OK", "Task complete.", steps)


def _attach_page_change_result(verification: ToolResult, page_change: PageChangeResult) -> None:
    verification.data["page_changed"] = page_change.page_changed
    verification.data["page_change_confidence"] = page_change.confidence
    verification.data["page_change_source"] = page_change.source
    verification.data["page_change_reason"] = page_change.reason
    verification.data["page_change_metrics"] = page_change.metrics
    verification.data["page_change_local_decisive"] = page_change.local_decisive


def _local_window_transition_verification(
    window_transition,
    *,
    window_sync: ToolResult,
    after_screenshot: ImageInfo,
    page_change: PageChangeResult,
) -> ToolResult | None:
    if not (getattr(window_transition, "target_title", None) or getattr(window_transition, "target_alias", None)):
        return None

    expected_titles = _after_action_window_titles(window_transition)
    actual_title = str(window_sync.data.get("window_title") or "")
    if not _title_matches_any(actual_title, expected_titles):
        return None

    result = ToolResult(
        True,
        "WINDOW_TRANSITION_VERIFIED",
        "Expected window transition is verified by current bound window title.",
        data={
            "verified": True,
            "page_changed": True,
            "screenshot_path": after_screenshot.path,
            "expected_window_titles": expected_titles,
            "actual_window_title": actual_title,
            "window_transition": {
                "source_alias": getattr(window_transition, "source_alias", None),
                "source_title": getattr(window_transition, "source_title", None),
                "target_alias": getattr(window_transition, "target_alias", None),
                "target_title": getattr(window_transition, "target_title", None),
                "raw": getattr(window_transition, "raw", ""),
            },
            "window_sync": window_sync.data,
        },
    )
    _attach_page_change_result(result, page_change)
    result.data["page_changed"] = True
    result.data["page_change_source"] = "window_context"
    result.data["page_change_reason"] = (
        f"当前 Maa 绑定窗口标题为 {actual_title!r}，匹配任务窗口转场目标。"
    )
    return result


def _verification_page_changed(result: ToolResult) -> bool | None:
    value = result.data.get("page_changed")
    parsed = _optional_bool(value)
    if parsed is not None:
        return parsed

    raw = result.data.get("raw")
    if isinstance(raw, dict):
        raw_parsed = raw.get("parsed")
        if isinstance(raw_parsed, dict):
            for key in ("page_changed", "pageChanged", "screen_changed", "screenChanged", "页面是否切换"):
                parsed = _optional_bool(raw_parsed.get(key))
                if parsed is not None:
                    return parsed
    return None


def _optional_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "y", "on", "是", "有", "已切换", "切换", "changed"}:
            return True
        if text in {
            "false",
            "no",
            "0",
            "n",
            "off",
            "否",
            "无",
            "未切换",
            "没有切换",
            "未发生切换",
            "no change",
            "unchanged",
            "not changed",
        }:
            return False
    return None


def _task_instructions(body: str) -> list[str]:
    text = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#")).strip()
    if not text:
        text = body.strip()
    if not text:
        return []

    pattern = re.compile(r"(?:^|\n|(?<=[。.!?；;])\s*)(\d+)\.\s+", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [text]

    instructions: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        instruction = text[start:end].strip()
        if instruction:
            instructions.append(f"{match.group(1)}. {instruction}")
    return instructions or [text]


def _task_with_current_instruction(
    task: TaskSpec,
    *,
    current_instruction: str,
    current_index: int,
    total: int,
) -> TaskSpec:
    position = current_index + 1
    return replace(
        task,
        id=f"{task.id}:step_{position}",
        title=f"{task.title} ({position}/{total})",
        body=f"当前任务指示（{position}/{total}）：{current_instruction}",
    )


def _initial_window_title(task: TaskSpec, window_definitions: dict[str, str]) -> str:
    configured = task.inputs.get("window_title_keyword")
    if configured:
        return str(configured)
    return window_definitions.get("主窗口") or next(iter(window_definitions.values()), "Thermo BioPharma Finder 5.1")


def _source_window_titles(window_transition) -> list[str]:
    return _compact_titles(window_transition.source_title, window_transition.source_alias)


def _after_action_window_titles(window_transition) -> list[str]:
    if window_transition.target_title or window_transition.target_alias:
        return _compact_titles(window_transition.target_title, window_transition.target_alias)
    return _source_window_titles(window_transition)


def _compact_titles(*values: str | None) -> list[str]:
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in output:
            output.append(text)
    return output


def _title_matches_any(title: str | None, expected_titles: list[str]) -> bool:
    actual = str(title or "").lower()
    return bool(actual) and any(str(expected or "").lower() in actual for expected in expected_titles)


def _ui_parser_context(action_text: str, verification_text: str) -> str:
    parts = []
    if action_text.strip():
        parts.append(f"动作：{action_text.strip()}")
    if verification_text.strip():
        parts.append(f"验证：{verification_text.strip()}")
    return "\n".join(parts)


def _current_instruction_verification_prompt(
    instruction: str,
    *,
    current_index: int,
    total: int,
) -> str:
    clean_instruction = instruction.rstrip("。.!?；; ")
    return (
        f"请判断交互后的页面是否已经正确完成当前任务指示（{current_index + 1}/{total}）："
        f"{clean_instruction}。如果正确完成则 verified=true，否则 verified=false。"
    )


def _window_title_candidates(instruction: str) -> list[str]:
    candidates: list[str] = []
    for pattern in (r"`([^`]+)`", r"\"([^\"]+)\"", r"'([^']+)'", r"“([^”]+)”", r"‘([^’]+)’"):
        for match in re.findall(pattern, instruction):
            value = match.strip()
            if value and value not in candidates:
                candidates.append(value)
    return candidates
