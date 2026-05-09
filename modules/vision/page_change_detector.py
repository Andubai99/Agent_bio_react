from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from agent.types import AgentAction, ImageInfo


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PageChangeResult:
    page_changed: Optional[bool]
    confidence: float
    source: str
    reason: str
    metrics: dict[str, float] = field(default_factory=dict)
    local_decisive: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "page_changed": self.page_changed,
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
            "metrics": self.metrics,
            "local_decisive": self.local_decisive,
        }


class PageChangeDetector:
    def __init__(
        self,
        *,
        thumbnail_size: tuple[int, int] = (320, 180),
        diff_threshold: int = 25,
        comparison_dir: Path | None = None,
    ):
        self.thumbnail_size = thumbnail_size
        self.diff_threshold = diff_threshold
        self.comparison_dir = comparison_dir or ROOT / "data" / "page_change"

    def compare(
        self,
        before: ImageInfo,
        after: ImageInfo,
        *,
        action: AgentAction | None = None,
    ) -> PageChangeResult:
        try:
            before_array = self._load_gray_thumbnail(before)
            after_array = self._load_gray_thumbnail(after)
        except Exception as exc:
            return PageChangeResult(
                None,
                0.0,
                "local_diff",
                f"无法读取截图进行本地页面变化判断：{exc}",
            )

        diff = np.abs(after_array.astype(np.int16) - before_array.astype(np.int16))
        mask = diff > self.diff_threshold
        main_mask = _main_region(mask)
        global_ratio = float(mask.mean())
        main_ratio = float(main_mask.mean())
        largest_component_ratio = _largest_component_ratio(main_mask)
        action_type = (action.type if action is not None else "").strip().lower()

        metrics = {
            "global_changed_ratio": round(global_ratio, 6),
            "main_changed_ratio": round(main_ratio, 6),
            "largest_component_ratio": round(largest_component_ratio, 6),
        }

        if global_ratio <= 0.015 and main_ratio <= 0.025 and largest_component_ratio <= 0.01:
            return PageChangeResult(
                False,
                0.95,
                "local_diff",
                _reason("页面变化很小，判定整体页面未切换", metrics),
                metrics,
                True,
            )

        if main_ratio >= 0.20 or largest_component_ratio >= 0.12 or global_ratio >= 0.25:
            return PageChangeResult(
                True,
                0.9,
                "local_diff",
                _reason("主内容区域出现大面积变化，判定整体页面已切换", metrics),
                metrics,
                True,
            )

        if action_type == "input_text" and main_ratio <= 0.12 and largest_component_ratio <= 0.05:
            return PageChangeResult(
                False,
                0.78,
                "local_diff",
                _reason("动作是输入文本，且变化未形成大块页面替换，判定整体页面未切换", metrics),
                metrics,
                True,
            )

        return PageChangeResult(
            None,
            0.45,
            "local_diff",
            _reason("本地差异处于中间区间，需要视觉模型低清对比兜底", metrics),
            metrics,
        )

    def build_comparison_image(self, before: ImageInfo, after: ImageInfo) -> ImageInfo:
        if not before.path or not after.path:
            raise ValueError("Both before and after screenshots must have file paths.")

        before_image = Image.open(Path(before.path)).convert("RGB").resize(self.thumbnail_size)
        after_image = Image.open(Path(after.path)).convert("RGB").resize(self.thumbnail_size)
        diff_image = ImageChops.difference(before_image, after_image).convert("L")
        diff_rgb = Image.merge("RGB", (diff_image, diff_image, diff_image))

        width, height = self.thumbnail_size
        label_height = 24
        canvas = Image.new("RGB", (width * 3, height + label_height), "white")
        draw = ImageDraw.Draw(canvas)
        for index, (label, image) in enumerate(
            (("before", before_image), ("after", after_image), ("diff", diff_rgb))
        ):
            x = index * width
            canvas.paste(image, (x, label_height))
            draw.text((x + 8, 6), label, fill=(0, 0, 0))

        self.comparison_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.comparison_dir / f"page_change_compare_{stamp}.png"
        canvas.save(path)
        return ImageInfo(
            path=str(path),
            width=canvas.width,
            height=canvas.height,
            resolution=(canvas.width, canvas.height),
        )

    def _load_gray_thumbnail(self, screenshot: ImageInfo) -> np.ndarray:
        if not screenshot.path:
            raise ValueError("screenshot path is empty")
        image = Image.open(Path(screenshot.path)).convert("L").resize(self.thumbnail_size)
        return np.asarray(image, dtype=np.uint8)


def _main_region(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    top = int(height * 0.08)
    bottom = int(height * 0.95)
    left = int(width * 0.12)
    return mask[top:bottom, left:width]


def _largest_component_ratio(mask: np.ndarray) -> float:
    if mask.size == 0 or not bool(mask.any()):
        return 0.0

    visited = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    largest = 0
    for y in range(height):
        for x in range(width):
            if visited[y, x] or not mask[y, x]:
                continue
            area = _flood_fill_area(mask, visited, y, x)
            if area > largest:
                largest = area
    return float(largest / mask.size)


def _flood_fill_area(mask: np.ndarray, visited: np.ndarray, start_y: int, start_x: int) -> int:
    stack = [(start_y, start_x)]
    visited[start_y, start_x] = True
    area = 0
    height, width = mask.shape
    while stack:
        y, x = stack.pop()
        area += 1
        for next_y, next_x in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if next_y < 0 or next_y >= height or next_x < 0 or next_x >= width:
                continue
            if visited[next_y, next_x] or not mask[next_y, next_x]:
                continue
            visited[next_y, next_x] = True
            stack.append((next_y, next_x))
    return area


def _reason(prefix: str, metrics: dict[str, float]) -> str:
    return (
        f"{prefix}：全图变化 {metrics['global_changed_ratio']:.3%}，"
        f"主区域变化 {metrics['main_changed_ratio']:.3%}，"
        f"最大变化块 {metrics['largest_component_ratio']:.3%}。"
    )
