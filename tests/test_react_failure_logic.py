from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from agent.react_agent import ReActAgent
from agent.types import AgentAction, ImageInfo, ReasonerDecision, TaskSpec, ToolResult, UIElement
from modules.vision.page_change_detector import PageChangeResult


class FakeDesktop:
    def __init__(
        self,
        *,
        sync_results: list[ToolResult] | None = None,
        execute_results: list[ToolResult] | None = None,
    ):
        self.window_title = "Main Window"
        self.sync_results = list(sync_results or [])
        self.execute_results = list(execute_results or [])
        self.capture_count = 0

    def connect_window(self, title_keyword: str) -> ToolResult:
        return ToolResult(True, "WINDOW_CONNECTED", "Connected.", data={"window_title": title_keyword})

    def sync_after_action(self, **_: Any) -> ToolResult:
        if self.sync_results:
            return self.sync_results.pop(0)
        return ToolResult(True, "WINDOW_CONTEXT_UNCHANGED", "Window context unchanged.", data={"window_title": self.window_title})

    def capture(self, label: str) -> ImageInfo:
        self.capture_count += 1
        return ImageInfo(path=f"{label}.png", width=100, height=100, resolution=(100, 100))

    def execute_action(self, action: AgentAction, *, elements: list[UIElement], screenshot: ImageInfo) -> ToolResult:
        if self.execute_results:
            return self.execute_results.pop(0)
        if action.type == "bad_action":
            return ToolResult(False, "UNKNOWN_ACTION", "Unsupported action type.")
        return ToolResult(True, "ACTION_OK", "Action executed.")


class FakeUiParser:
    def __init__(self, *, parse_failures: int = 0):
        self.parse_failures = parse_failures
        self.elements = [
            UIElement(
                idx=1,
                type="unknown",
                content="Run",
                bbox=(0.1, 0.1, 0.2, 0.2),
                pixel_bbox=(10, 10, 20, 20),
                center=(15, 15),
                interactive=False,
            )
        ]

    def parse(self, screenshot: ImageInfo, *, task_instruction: str = "") -> list[UIElement]:
        if self.parse_failures > 0:
            self.parse_failures -= 1
            raise RuntimeError("temporary parse failure")
        return list(self.elements)

    def postprocess_existing(
        self,
        screenshot: ImageInfo,
        elements: list[UIElement],
        *,
        task_instruction: str = "",
    ) -> list[UIElement]:
        return list(elements)

    def synthetic_target_for_instruction(
        self,
        elements: list[UIElement],
        *,
        selected_idx: int | None,
        task_instruction: str,
    ) -> UIElement | None:
        return None


class FakeReasoner:
    def __init__(self, items: list[Any]):
        self.items = list(items)

    def decide(self, context: Any) -> ReasonerDecision:
        if not self.items:
            return _click_decision()
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeVisionVerifier:
    def __init__(self, results: list[ToolResult]):
        self.results = list(results)

    def verify_completion(self, expectation: str, screenshot: ImageInfo) -> ToolResult:
        if self.results:
            return self.results.pop(0)
        return ToolResult(True, "VERIFIED", "Verified.", data={"verified": True, "page_changed": False})

    def verify_post_action_comparison(self, expectation: str, comparison_screenshot: ImageInfo) -> ToolResult:
        return self.verify_completion(expectation, comparison_screenshot)


class FakePageChangeDetector:
    def compare(self, before: ImageInfo, after: ImageInfo, *, action: AgentAction | None = None) -> PageChangeResult:
        return PageChangeResult(False, 0.95, "local_diff", "unchanged", {}, True)


class ReactFailureLogicTests(unittest.TestCase):
    def test_verify_failed_once_retries_current_instruction(self) -> None:
        agent = _agent(
            vision=FakeVisionVerifier(
                [
                    ToolResult(False, "VERIFY_FAILED", "Vision verification failed.", data={"verified": False, "page_changed": False}),
                    ToolResult(True, "VERIFIED", "Verified.", data={"verified": True, "page_changed": False}),
                ]
            )
        )

        result = agent.run()

        self.assertTrue(result.ok)
        self.assertEqual(result.code, "OK")
        self.assertEqual(len(result.steps), 2)

    def test_verify_failed_twice_aborts(self) -> None:
        agent = _agent(
            vision=FakeVisionVerifier(
                [
                    ToolResult(False, "VERIFY_FAILED", "Vision verification failed.", data={"verified": False, "page_changed": False}),
                    ToolResult(False, "VERIFY_FAILED", "Vision verification failed.", data={"verified": False, "page_changed": False}),
                ]
            )
        )

        result = agent.run()

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "VERIFY_FAILED")

    def test_observe_failed_once_recovers(self) -> None:
        agent = _agent(ui_parser=FakeUiParser(parse_failures=1))

        result = agent.run()

        self.assertTrue(result.ok)
        self.assertEqual(result.code, "OK")

    def test_reasoner_failed_once_recovers(self) -> None:
        agent = _agent(reasoner=FakeReasoner([RuntimeError("temporary reasoner failure"), _click_decision()]))

        result = agent.run()

        self.assertTrue(result.ok)
        self.assertEqual(result.code, "OK")

    def test_post_window_sync_failed_once_recovers(self) -> None:
        agent = _agent(
            desktop=FakeDesktop(
                sync_results=[
                    ToolResult(True, "WINDOW_CONTEXT_UNCHANGED", "pre ok."),
                    ToolResult(False, "WINDOW_CONTEXT_EXPECTED_NOT_FOUND", "missing window."),
                    ToolResult(True, "WINDOW_CONTEXT_UNCHANGED", "pre ok."),
                    ToolResult(True, "WINDOW_CONTEXT_UNCHANGED", "post ok."),
                ]
            )
        )

        result = agent.run()

        self.assertTrue(result.ok)
        self.assertEqual(result.code, "OK")

    def test_unknown_action_does_not_retry(self) -> None:
        agent = _agent(reasoner=FakeReasoner([ReasonerDecision(thought="", actions=[AgentAction("bad_action", 1)])]))

        result = agent.run()

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "UNKNOWN_ACTION")


def _agent(
    *,
    desktop: FakeDesktop | None = None,
    ui_parser: FakeUiParser | None = None,
    reasoner: FakeReasoner | None = None,
    vision: FakeVisionVerifier | None = None,
) -> ReActAgent:
    return ReActAgent(
        task=_task(),
        desktop=desktop or FakeDesktop(),
        ui_parser=ui_parser or FakeUiParser(),
        reasoner=reasoner or FakeReasoner([_click_decision()]),
        vision_verifier=vision or FakeVisionVerifier([ToolResult(True, "VERIFIED", "Verified.", data={"verified": True, "page_changed": False})]),
        page_change_detector=FakePageChangeDetector(),
        max_steps=6,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        id="fake",
        title="Fake task",
        description="",
        path=Path("fake.md"),
        inputs={"window_title_keyword": "Main Window"},
        allowed_tools=[],
        start_conditions=[],
        success_criteria=[],
        safety_rules=[],
        body=(
            "窗口定义：\n"
            "- 主窗口：Main Window\n\n"
            "任务流程：\n"
            "0. 动作：点击 `Run`。\n"
            "   窗口：主窗口\n"
            "   验证：Run 已完成。\n"
        ),
    )


def _click_decision() -> ReasonerDecision:
    return ReasonerDecision(thought="click", actions=[AgentAction("click", 1)])


if __name__ == "__main__":
    unittest.main()
