from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from PIL import Image

from agent.react_agent import ReActAgent
from agent.types import ImageInfo, TaskSpec, UIElement
from modules.vision.page_change_detector import PageChangeResult
from tools.observation_cache import ObservationCache, ObservationCacheContext


class ObservationCacheTests(unittest.TestCase):
    def test_cache_roundtrip_loads_elements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            screenshot = _write_image(root / "screen.png", size=(100, 50))
            cache = ObservationCache(root / "cache")
            context = _context(resolution=(100, 50))

            cache.write(context, screenshot=screenshot, elements=[_element()])
            lookup = cache.lookup(context)

            self.assertTrue(lookup.hit)
            self.assertIsNotNone(lookup.record)
            elements = cache.load_elements(lookup.record, current_resolution=(100, 50))  # type: ignore[arg-type]
            self.assertEqual(len(elements), 1)
            self.assertEqual(elements[0].idx, 7)
            self.assertEqual(elements[0].pixel_bbox, (10, 5, 20, 10))

    def test_cache_reprojects_compatible_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            screenshot = _write_image(root / "screen.png", size=(100, 50))
            cache = ObservationCache(root / "cache")

            cache.write(_context(resolution=(100, 50)), screenshot=screenshot, elements=[_element()])
            lookup = cache.lookup(_context(resolution=(200, 100)))

            self.assertTrue(lookup.hit)
            self.assertIsNotNone(lookup.record)
            self.assertFalse(lookup.record.exact_resolution)  # type: ignore[union-attr]
            elements = cache.load_elements(lookup.record, current_resolution=(200, 100))  # type: ignore[arg-type]
            self.assertEqual(elements[0].pixel_bbox, (20, 10, 40, 20))
            self.assertEqual(elements[0].center, (30, 15))

    def test_agent_reuses_cache_on_second_observe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = ObservationCache(root / "tasks" / "sample" / "omniparser_cache")
            desktop = StaticImageDesktop(root)
            ui_parser = CountingUiParser()
            agent = ReActAgent(
                task=_task(root),
                desktop=desktop,
                ui_parser=ui_parser,  # type: ignore[arg-type]
                reasoner=object(),  # type: ignore[arg-type]
                vision_verifier=object(),  # type: ignore[arg-type]
                page_change_detector=UnchangedDetector(),
                observation_cache=cache,
            )

            first = agent._observe(1, step_number=0, current_instruction="click `Run`")
            second = agent._observe(2, step_number=0, current_instruction="click `Run`")

            self.assertFalse(hasattr(first, "ok"))
            self.assertFalse(hasattr(second, "ok"))
            self.assertEqual(ui_parser.parse_count, 1)
            self.assertEqual(ui_parser.postprocess_count, 1)
            self.assertEqual(second.state["observation_source"], "observation cache")  # type: ignore[union-attr]
            self.assertTrue(second.state["observation_cache"]["hit"])  # type: ignore[union-attr]


class StaticImageDesktop:
    def __init__(self, root: Path):
        self.root = root
        self.window_title = "Main Window"
        self.capture_count = 0

    def capture(self, label: str) -> ImageInfo:
        self.capture_count += 1
        return _write_image(self.root / f"{label}_{self.capture_count}.png", size=(100, 50))


class CountingUiParser:
    def __init__(self) -> None:
        self.parse_count = 0
        self.postprocess_count = 0

    def parse(self, screenshot: ImageInfo, *, task_instruction: str = "") -> list[UIElement]:
        self.parse_count += 1
        return [_element()]

    def postprocess_existing(
        self,
        screenshot: ImageInfo,
        elements: list[UIElement],
        *,
        task_instruction: str = "",
    ) -> list[UIElement]:
        self.postprocess_count += 1
        return list(elements)


class UnchangedDetector:
    def compare(self, before: ImageInfo, after: ImageInfo, *, action: Any = None) -> PageChangeResult:
        return PageChangeResult(False, 0.95, "local_diff", "unchanged", {}, True)


def _write_image(path: Path, *, size: tuple[int, int]) -> ImageInfo:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path)
    return ImageInfo(path=str(path), width=size[0], height=size[1], resolution=size)


def _context(*, resolution: tuple[int, int]) -> ObservationCacheContext:
    return ObservationCacheContext(
        task_id="sample",
        step_number=0,
        window_title="Main Window",
        instruction="click `Run`",
        resolution=resolution,
        parser_params={"box_threshold": 0.05, "iou_threshold": 0.7, "use_paddleocr": True, "imgsz": 640},
    )


def _element() -> UIElement:
    return UIElement(
        idx=7,
        type="unknown",
        content="Run",
        bbox=(0.1, 0.1, 0.2, 0.2),
        pixel_bbox=(10, 5, 20, 10),
        center=(15, 7),
        interactive=False,
        source="",
        raw={"idx": 7, "content": "Run"},
    )


def _task(root: Path) -> TaskSpec:
    return TaskSpec(
        id="sample",
        title="Sample",
        description="",
        path=root / "tasks" / "sample" / "task.md",
        inputs={},
        allowed_tools=[],
        start_conditions=[],
        success_criteria=[],
        safety_rules=[],
        body="",
    )


if __name__ == "__main__":
    unittest.main()
