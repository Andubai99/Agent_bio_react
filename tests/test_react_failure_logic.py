from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from typing import Any

from agent.react_agent import ReActAgent, _local_decision_from_action_hints
from agent.types import ActionSequenceHint, AgentAction, ImageInfo, Observation, ReasonerDecision, TaskSpec, ToolResult, UIElement
from modules.vision.page_change_detector import PageChangeResult


class FakeDesktop:
    def __init__(
        self,
        *,
        window_title: str = "Main Window",
        sync_results: list[ToolResult] | None = None,
        execute_results: list[ToolResult] | None = None,
    ):
        self.window_title = window_title
        self.sync_results = list(sync_results or [])
        self.execute_results = list(execute_results or [])
        self.capture_count = 0
        self.executed_actions: list[AgentAction] = []

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
        self.executed_actions.append(action)
        if self.execute_results:
            return self.execute_results.pop(0)
        if action.type == "bad_action":
            return ToolResult(False, "UNKNOWN_ACTION", "Unsupported action type.")
        return ToolResult(True, "ACTION_OK", "Action executed.")


class FakeUiParser:
    def __init__(
        self,
        *,
        parse_failures: int = 0,
        elements: list[UIElement] | None = None,
        element_batches: list[list[UIElement]] | None = None,
    ):
        self.parse_failures = parse_failures
        self.elements = list(elements) if elements is not None else [
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
        self.element_batches = [list(batch) for batch in element_batches or []]

    def parse(self, screenshot: ImageInfo, *, task_instruction: str = "") -> list[UIElement]:
        if self.parse_failures > 0:
            self.parse_failures -= 1
            raise RuntimeError("temporary parse failure")
        if self.element_batches:
            return self.element_batches.pop(0)
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
        self.decide_count = 0

    def decide(self, context: Any) -> ReasonerDecision:
        self.decide_count += 1
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

    def test_local_resolver_selects_ellipsis_button(self) -> None:
        observation = Observation(
            summary="",
            elements=[
                UIElement(
                    idx=10,
                    type="unknown",
                    content="Experiment Name",
                    bbox=(0.01, 0.01, 0.2, 0.05),
                    pixel_bbox=(10, 10, 200, 50),
                    center=(105, 30),
                    interactive=False,
                ),
                UIElement(
                    idx=20,
                    type="unknown",
                    content="More",
                    bbox=(0.7, 0.2, 0.75, 0.25),
                    pixel_bbox=(700, 200, 750, 250),
                    center=(725, 225),
                    interactive=False,
                ),
            ],
        )

        decision = _local_decision_from_action_hints(
            [ActionSequenceHint(order=1, action="click", raw="点击 `...` 按钮", target_hint="...")],
            observation,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.actions[0].element_idx, 20)
        self.assertEqual(decision.actions[0].args["local_resolver"], "ellipsis_button")

    def test_home_page_memory_skips_idempotent_home_click(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_home_memory(root)
            desktop = FakeDesktop(window_title="Thermo BioPharma Finder 5.1")
            reasoner = FakeReasoner([RuntimeError("reasoner should not be called")])
            agent = ReActAgent(
                task=_home_task(root),
                desktop=desktop,
                ui_parser=FakeUiParser(elements=_home_elements()),
                reasoner=reasoner,
                vision_verifier=FakeVisionVerifier([]),
                page_change_detector=FakePageChangeDetector(),
                observation_cache=None,
                max_steps=3,
            )

            result = agent.run()

            self.assertTrue(result.ok)
            self.assertEqual(result.code, "OK")
            self.assertEqual(reasoner.decide_count, 0)
            self.assertEqual(desktop.executed_actions, [])
            self.assertEqual(result.steps[0].action_result.code, "ACTION_SKIPPED_PAGE_ALREADY_MATCHED")

    def test_home_page_memory_falls_back_when_vision_fails_after_click(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_home_memory(root)
            desktop = FakeDesktop(window_title="Thermo BioPharma Finder 5.1")
            agent = ReActAgent(
                task=_home_task(root),
                desktop=desktop,
                ui_parser=FakeUiParser(element_batches=[_not_home_elements(), _home_elements()]),
                reasoner=FakeReasoner([ReasonerDecision(thought="click home", actions=[AgentAction("click", 1)])]),
                vision_verifier=FakeVisionVerifier([ToolResult(False, "VERIFY_FAILED", "Temporary vision did not return post-action verification: post_action")]),
                page_change_detector=FakePageChangeDetector(),
                observation_cache=None,
                max_steps=3,
            )

            result = agent.run()

            self.assertTrue(result.ok)
            self.assertEqual(result.steps[0].verification.code, "PAGE_STATE_VERIFIED")


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


def _write_home_memory(root: Path) -> None:
    path = root / "tasks" / "biopharma_protein" / "memory" / "pages" / "home.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "page_id": "home",
                "title_keywords": ["Thermo BioPharma Finder 5.1"],
                "required_anchors": ["Experiment Types", "Sequence Manager", "Intact Mass Analysis"],
                "optional_anchors": ["Home", "Select an experiment type."],
                "verification": {"mode": "state_match", "page_change_required": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _home_task(root: Path) -> TaskSpec:
    return TaskSpec(
        id="biopharma_protein",
        title="Protein task",
        description="",
        path=root / "tasks" / "biopharma_protein" / "task.md",
        inputs={"window_title_keyword": "Thermo BioPharma Finder 5.1"},
        allowed_tools=[],
        start_conditions=[],
        success_criteria=[],
        safety_rules=[],
        body=(
            "窗口定义：\n"
            "- 主窗口：Thermo BioPharma Finder 5.1\n\n"
            "任务流程：\n"
            "0. 动作：确保位于 `Home` 界面；如果当前不在 `Home` 界面，则点击 `Home`。\n"
            "   窗口：主窗口\n"
            "   验证：处于 `Home` 界面。\n"
        ),
    )


def _home_elements() -> list[UIElement]:
    return [
        _element_with_content(1, "Home"),
        _element_with_content(2, "Select an experiment type."),
        _element_with_content(3, "Experiment Types"),
        _element_with_content(4, "Sequence Manager"),
        _element_with_content(5, "Intact Mass Analysis"),
    ]


def _not_home_elements() -> list[UIElement]:
    return [
        _element_with_content(1, "Home"),
        _element_with_content(2, "Intact Mass Analysis Definition"),
        _element_with_content(3, "Experiment Name"),
    ]


def _element_with_content(idx: int, content: str) -> UIElement:
    left = idx * 10
    return UIElement(
        idx=idx,
        type="unknown",
        content=content,
        bbox=(0.1, 0.1, 0.2, 0.2),
        pixel_bbox=(left, 10, left + 20, 30),
        center=(left + 10, 20),
        interactive=False,
    )


if __name__ == "__main__":
    unittest.main()
