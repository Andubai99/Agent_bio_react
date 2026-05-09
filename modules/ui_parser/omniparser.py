from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from agent.runtime_logger import NullRunLogger
from agent.types import ImageInfo, UIElement


DEFAULT_ROOT = Path(r"F:\OmniParser")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_BOX_THRESHOLD = 0.05
DEFAULT_IOU_THRESHOLD = 0.7
DEFAULT_USE_PADDLEOCR = True
DEFAULT_IMGSZ = 640


@dataclass(frozen=True)
class OmniParserConfig:
    root: Path = DEFAULT_ROOT
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    box_threshold: float = DEFAULT_BOX_THRESHOLD
    iou_threshold: float = DEFAULT_IOU_THRESHOLD
    use_paddleocr: bool = DEFAULT_USE_PADDLEOCR
    imgsz: int = DEFAULT_IMGSZ

    @classmethod
    def from_env(cls) -> "OmniParserConfig":
        return cls(
            root=Path(os.environ.get("OMNIPARSER_ROOT", str(DEFAULT_ROOT))),
            host=os.environ.get("OMNIPARSER_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST,
            port=int(os.environ.get("OMNIPARSER_PORT", str(DEFAULT_PORT))),
            timeout_seconds=float(os.environ.get("OMNIPARSER_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
            box_threshold=float(os.environ.get("OMNIPARSER_BOX_THRESHOLD", str(DEFAULT_BOX_THRESHOLD))),
            iou_threshold=float(os.environ.get("OMNIPARSER_IOU_THRESHOLD", str(DEFAULT_IOU_THRESHOLD))),
            use_paddleocr=_env_bool("OMNIPARSER_USE_PADDLEOCR", DEFAULT_USE_PADDLEOCR),
            imgsz=int(os.environ.get("OMNIPARSER_IMGSZ", str(DEFAULT_IMGSZ))),
        )

    @property
    def parse_url(self) -> str:
        return f"http://{self.host}:{self.port}/parse/"

    @property
    def probe_url(self) -> str:
        return f"http://{self.host}:{self.port}/probe/"


@dataclass(frozen=True)
class SyntheticTargetSpec:
    kind: str
    label: str
    side: str


class OmniParserService:
    def __init__(self, config: OmniParserConfig, *, logger=None):
        self.config = config
        self.logger = logger or NullRunLogger()
        self.process: Optional[subprocess.Popen] = None
        self.started_by_us = False

    def ensure_running(self) -> bool:
        if self.probe(timeout=2.0):
            self.logger.log("OmniParser service already running", {"probe_url": self.config.probe_url})
            return False

        command = self._command()
        self.logger.log(
            "Starting OmniParser service",
            {
                "cwd": str(self.server_dir),
                "command": command,
                "probe_url": self.config.probe_url,
            },
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            command,
            cwd=self.server_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self.started_by_us = True
        deadline = time.time() + self.config.timeout_seconds
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"OmniParser service exited with code {self.process.returncode}.")
            if self.probe(timeout=2.0):
                self.logger.log("OmniParser service started", {"probe_url": self.config.probe_url})
                return True
            time.sleep(2.0)
        raise TimeoutError(f"Timed out waiting for OmniParser service: {self.config.probe_url}")

    def probe(self, *, timeout: float) -> bool:
        request = urllib.request.Request(self.config.probe_url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return False
        return payload.get("message") == "Omniparser API ready"

    def shutdown_if_owned(self) -> None:
        if not self.started_by_us or self.process is None:
            return
        self.logger.log("Stopping owned OmniParser service")
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)

    @property
    def server_dir(self) -> Path:
        return self.config.root / "omnitool" / "omniparserserver"

    def _command(self) -> list[str]:
        root = self.config.root
        return [
            str(root / ".venv" / "Scripts" / "python.exe"),
            str(self.server_dir / "omniparserserver.py"),
            "--som_model_path",
            str(root / "weights" / "icon_detect" / "model.pt"),
            "--caption_model_name",
            "florence2",
            "--caption_model_path",
            str(root / "weights" / "icon_caption_florence"),
            "--device",
            "cuda",
            "--BOX_TRESHOLD",
            str(self.config.box_threshold),
            "--host",
            self.config.host,
            "--port",
            str(self.config.port),
        ]


class OmniParserClient:
    def __init__(self, config: OmniParserConfig, *, logger=None):
        self.config = config
        self.logger = logger or NullRunLogger()

    def parse(self, screenshot: ImageInfo, *, task_instruction: str = "") -> list[UIElement]:
        if not screenshot.path:
            raise RuntimeError("OmniParser requires a screenshot path.")
        payload = {
            "base64_image": _encode_image(Path(screenshot.path)),
            "box_threshold": self.config.box_threshold,
            "iou_threshold": self.config.iou_threshold,
            "use_paddleocr": self.config.use_paddleocr,
            "imgsz": self.config.imgsz,
        }
        self.logger.log(
            "Calling OmniParser UI parser",
            {
                "url": self.config.parse_url,
                "screenshot": screenshot.path,
                "image_size": [screenshot.width, screenshot.height],
                "use_paddleocr": self.config.use_paddleocr,
                "imgsz": self.config.imgsz,
            },
        )
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.config.parse_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OmniParser API HTTP {exc.code}: {error_body}") from exc
        response_payload = json.loads(body)
        items = response_payload.get("parsed_content_list")
        if not isinstance(items, list):
            raise RuntimeError(f"OmniParser response missing parsed_content_list: {response_payload}")

        elements = [_element_from_item(item, screenshot) for item in items if isinstance(item, dict)]
        elements = _add_synthetic_task_targets(
            elements,
            screenshot,
            task_instruction=task_instruction,
            logger=self.logger,
        )
        self.logger.log(
            "OmniParser UI parse complete",
            {"elements": len(elements), "latency": response_payload.get("latency")},
        )
        return elements

    def postprocess_existing(
        self,
        screenshot: ImageInfo,
        elements: list[UIElement],
        *,
        task_instruction: str = "",
    ) -> list[UIElement]:
        return _add_synthetic_task_targets(
            list(elements),
            screenshot,
            task_instruction=task_instruction,
            logger=self.logger,
        )

    def synthetic_target_for_instruction(
        self,
        elements: list[UIElement],
        *,
        selected_idx: int | None,
        task_instruction: str,
    ) -> UIElement | None:
        return synthetic_target_for_instruction(
            elements,
            selected_idx=selected_idx,
            task_instruction=task_instruction,
        )


def _element_from_item(item: dict[str, Any], screenshot: ImageInfo) -> UIElement:
    bbox = _normalized_bbox(item.get("bbox"))
    pixel_bbox = _bbox_to_pixels(bbox, screenshot.width, screenshot.height)
    center = ((pixel_bbox[0] + pixel_bbox[2]) // 2, (pixel_bbox[1] + pixel_bbox[3]) // 2)
    return UIElement(
        idx=int(item.get("idx", 0)),
        type=str(item.get("type", "unknown")),
        content=_optional_text(item.get("content")),
        bbox=bbox,
        pixel_bbox=pixel_bbox,
        center=center,
        interactive=bool(item.get("interactivity", False)),
        source=str(item.get("source", "")),
        raw=dict(item),
    )


def synthetic_target_for_instruction(
    elements: list[UIElement],
    *,
    selected_idx: int | None,
    task_instruction: str,
) -> UIElement | None:
    selected = next((element for element in elements if element.idx == selected_idx), None)
    if selected is None:
        return None

    specs = _synthetic_target_specs_from_instruction(task_instruction)
    if not specs:
        return None

    for spec in specs:
        if not _element_matches_label(selected, spec.label):
            continue
        synthetic_candidates = [
            element
            for element in elements
            if element.source == f"synthetic_{spec.kind}"
            and _normalize_text(element.raw.get("synthetic_label")) == _normalize_text(spec.label)
        ]
        if synthetic_candidates:
            return min(
                synthetic_candidates,
                key=lambda element: (
                    abs(_center_y(element.pixel_bbox) - _center_y(selected.pixel_bbox)),
                    abs(_center_x(element.pixel_bbox) - _center_x(selected.pixel_bbox)),
                ),
            )
    return None


def _add_synthetic_task_targets(
    elements: list[UIElement],
    screenshot: ImageInfo,
    *,
    task_instruction: str,
    logger=None,
) -> list[UIElement]:
    specs = _synthetic_target_specs_from_instruction(task_instruction)
    if not specs or not screenshot.path:
        return elements

    try:
        image = Image.open(Path(screenshot.path)).convert("RGB")
    except Exception as exc:
        if logger is not None:
            logger.log("Synthetic target postprocess skipped", {"reason": str(exc)})
        return elements

    base_elements = list(elements)
    output = list(elements)
    next_idx = max((element.idx for element in output), default=-1) + 1
    added: list[dict[str, Any]] = []

    for spec in specs:
        for label_element, label_text in _label_candidates(base_elements, [spec.label]):
            boxes = [
                box
                for box in _find_synthetic_target_boxes(image, label_element.pixel_bbox, spec)
                if not _overlaps_existing_element(box, output)
            ]
            boxes.sort(key=lambda box: (box[0], box[1], box[2], box[3]))
            multiple = len(boxes) > 1
            for rank, box in enumerate(boxes, start=1):
                synthetic = _synthetic_target_element(
                    box,
                    screenshot,
                    idx=next_idx,
                    label_element=label_element,
                    label_text=label_text,
                    spec=spec,
                    rank=rank,
                    multiple=multiple,
                )
                output.append(synthetic)
                added.append(
                    {
                        "idx": next_idx,
                        "kind": spec.kind,
                        "content": synthetic.content,
                        "label": label_text,
                        "side": spec.side,
                        "rank": rank,
                        "pixel_bbox": box,
                    }
                )
                next_idx += 1

    if logger is not None:
        logger.log(
            "Synthetic target postprocess",
            {
                "enabled": True,
                "specs": [
                    {"kind": spec.kind, "label": spec.label, "side": spec.side}
                    for spec in specs
                ],
                "added": added,
            },
        )
    return output


def _add_synthetic_input_boxes(
    elements: list[UIElement],
    screenshot: ImageInfo,
    *,
    task_instruction: str,
    logger=None,
) -> list[UIElement]:
    return _add_synthetic_task_targets(
        elements,
        screenshot,
        task_instruction=task_instruction,
        logger=logger,
    )


def _needs_input_box_postprocess(task_instruction: str) -> bool:
    return _needs_synthetic_target(task_instruction, "input_box")


def _labels_from_input_box_instruction(task_instruction: str) -> list[str]:
    return [
        spec.label
        for spec in _synthetic_target_specs_from_instruction(task_instruction)
        if spec.kind == "input_box"
    ]


def _synthetic_target_specs_from_instruction(task_instruction: str) -> list[SyntheticTargetSpec]:
    specs: list[SyntheticTargetSpec] = []
    for kind, default_side in (("input_box", "right"), ("checkbox", "left")):
        for segment in _target_segments(task_instruction, kind):
            side = _side_from_segment(segment, default=default_side)
            for label in _labels_from_target_segment(segment, kind):
                specs.append(SyntheticTargetSpec(kind=kind, label=label, side=side))

    normalized_instruction = _normalize_text(task_instruction)
    if _needs_synthetic_target(task_instruction, "input_box") and "experiment name" in normalized_instruction:
        specs.append(SyntheticTargetSpec(kind="input_box", label="Experiment Name", side="right"))

    return _dedupe_specs(specs)


def _target_segments(task_instruction: str, kind: str) -> list[str]:
    segments = re.split(r"[\n。；;]+", task_instruction)
    return [segment.strip() for segment in segments if _needs_synthetic_target(segment, kind)]


def _needs_synthetic_target(text: str, kind: str) -> bool:
    normalized = text.lower()
    return any(keyword in normalized for keyword in _target_keywords(kind))


def _target_keywords(kind: str) -> tuple[str, ...]:
    if kind == "input_box":
        return (
            "输入框",
            "文本框",
            "编辑框",
            "输入栏",
            "输入字段",
            "input box",
            "text box",
            "textbox",
            "input field",
        )
    if kind == "checkbox":
        return (
            "选择框",
            "复选框",
            "勾选框",
            "checkbox",
            "check box",
        )
    return ()


def _labels_from_target_segment(segment: str, kind: str) -> list[str]:
    labels: list[str] = []
    ignored_labels = _ignored_target_labels(kind)
    for label in _quoted_phrases(segment):
        normalized = _normalize_text(label)
        if normalized and normalized not in ignored_labels:
            labels.append(label)
    labels.extend(_unquoted_labels_near_target(segment, kind))
    return _dedupe_preserving_order(labels)


def _ignored_target_labels(kind: str) -> set[str]:
    values = {"...", "biopharma test demo1", *_target_keywords("input_box"), *_target_keywords("checkbox")}
    if kind == "input_box":
        values.update({"input", "text"})
    if kind == "checkbox":
        values.update({"check", "checked"})
    return {_normalize_text(value) for value in values}


def _unquoted_labels_near_target(segment: str, kind: str) -> list[str]:
    target_pattern = "|".join(re.escape(keyword) for keyword in _target_keywords(kind))
    labels: list[str] = []
    patterns = [
        rf"(?P<label>.+?)\s*(?P<side>左侧|右侧|left|right)\s*`?(?:{target_pattern})`?",
        rf"`?(?:{target_pattern})`?\s*(?P<side>左侧|右侧|left|right)\s*(?:的)?\s*(?P<label>.+?)$",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, segment, flags=re.IGNORECASE):
            label = _clean_extracted_label(match.group("label"))
            if label and _normalize_text(label) not in _ignored_target_labels(kind):
                labels.append(label)
    return labels


def _side_from_segment(segment: str, *, default: str) -> str:
    normalized = segment.lower()
    if "左侧" in segment or "left" in normalized:
        return "left"
    if "右侧" in segment or "right" in normalized:
        return "right"
    return default


def _clean_extracted_label(value: str) -> str:
    text = value.strip().strip("`'\"“”")
    text = re.sub(r"^\s*(?:动作|验证)\s*[:：]\s*", "", text)
    text = re.sub(r"^\s*(?:请|需要|将)?\s*(?:点击|单击|双击|勾选|选中|确认|输入)\s*", "", text)
    text = re.sub(r"\s*(?:处于|已经|已|中|内|里|处|的)?\s*$", "", text)
    return text.strip().strip("`'\"“”，,。；;：: ")


def _dedupe_specs(specs: list[SyntheticTargetSpec]) -> list[SyntheticTargetSpec]:
    output: list[SyntheticTargetSpec] = []
    seen: set[tuple[str, str, str]] = set()
    for spec in specs:
        key = (spec.kind, _normalize_text(spec.label), spec.side)
        if key[1] and key not in seen:
            output.append(spec)
            seen.add(key)
    return output


def _quoted_phrases(text: str) -> list[str]:
    values: list[str] = []
    patterns = [
        r"`([^`]+)`",
        r"“([^”]+)”",
        r'"([^"]+)"',
        r"'([^']+)'",
    ]
    for pattern in patterns:
        values.extend(match.strip() for match in re.findall(pattern, text) if match.strip())
    return values


def _label_candidates(elements: list[UIElement], labels: list[str]) -> list[tuple[UIElement, str]]:
    candidates: list[tuple[UIElement, str]] = []
    seen: set[int] = set()
    normalized_labels = [(label, _normalize_text(label)) for label in labels]
    for element in elements:
        content = _normalize_text(element.content)
        if not content:
            continue
        for label, normalized_label in normalized_labels:
            if normalized_label and (normalized_label in content or content in normalized_label):
                if element.idx not in seen:
                    candidates.append((element, label))
                    seen.add(element.idx)
                break
    return candidates


def _find_synthetic_target_boxes(
    image: Image.Image,
    label_bbox: tuple[int, int, int, int],
    spec: SyntheticTargetSpec,
) -> list[tuple[int, int, int, int]]:
    if spec.kind == "input_box":
        return _find_input_boxes_near_label(image, label_bbox, spec.side)
    if spec.kind == "checkbox":
        return _find_checkboxes_near_label(image, label_bbox, spec.side)
    return []


def _find_input_boxes_near_label(
    image: Image.Image,
    label_bbox: tuple[int, int, int, int],
    side: str,
) -> list[tuple[int, int, int, int]]:
    if side == "left":
        return _find_input_boxes_left_of_label(image, label_bbox)
    return _find_input_boxes_right_of_label(image, label_bbox)


def _find_input_boxes_right_of_label(
    image: Image.Image,
    label_bbox: tuple[int, int, int, int],
) -> list[tuple[int, int, int, int]]:
    width, height = image.size
    _, label_top, label_right, label_bottom = label_bbox
    search_left = max(0, label_right - 2)
    search_right = min(width - 1, max(label_right + 160, int(width * 0.55)))
    search_top = max(0, label_top - 14)
    search_bottom = min(height - 1, label_bottom + 18)

    runs_by_row: list[tuple[int, int, int]] = []
    for y in range(search_top, search_bottom + 1):
        for x1, x2 in _border_runs_on_row(image, y, search_left, search_right):
            if x2 - x1 + 1 >= 80 and x1 <= label_right + 30:
                runs_by_row.append((y, x1, x2))

    candidates: list[tuple[int, int, int, int]] = []
    for index, (top_y, top_x1, top_x2) in enumerate(runs_by_row):
        for bottom_y, bottom_x1, bottom_x2 in runs_by_row[index + 1 :]:
            box_height = bottom_y - top_y + 1
            if box_height < 12 or box_height > 45:
                continue
            x1 = max(top_x1, bottom_x1)
            x2 = min(top_x2, bottom_x2)
            box_width = x2 - x1 + 1
            if box_width < 80:
                continue
            box = (x1, top_y, x2, bottom_y)
            if _mostly_white_fill(image, box):
                candidates.append(box)

    return _merge_input_box_candidates(candidates)


def _find_input_boxes_left_of_label(
    image: Image.Image,
    label_bbox: tuple[int, int, int, int],
) -> list[tuple[int, int, int, int]]:
    width, height = image.size
    label_left, label_top, _, label_bottom = label_bbox
    search_left = max(0, min(label_left - 360, int(width * 0.45)))
    search_right = min(width - 1, label_left + 2)
    search_top = max(0, label_top - 14)
    search_bottom = min(height - 1, label_bottom + 18)

    runs_by_row: list[tuple[int, int, int]] = []
    for y in range(search_top, search_bottom + 1):
        for x1, x2 in _border_runs_on_row(image, y, search_left, search_right):
            if x2 - x1 + 1 >= 80 and x2 >= label_left - 320:
                runs_by_row.append((y, x1, x2))

    candidates: list[tuple[int, int, int, int]] = []
    for index, (top_y, top_x1, top_x2) in enumerate(runs_by_row):
        for bottom_y, bottom_x1, bottom_x2 in runs_by_row[index + 1 :]:
            box_height = bottom_y - top_y + 1
            if box_height < 12 or box_height > 45:
                continue
            x1 = max(top_x1, bottom_x1)
            x2 = min(top_x2, bottom_x2)
            if x2 - x1 + 1 < 80:
                continue
            box = (x1, top_y, x2, bottom_y)
            if _mostly_white_fill(image, box):
                candidates.append(box)

    return _merge_input_box_candidates(candidates)


def _find_checkboxes_near_label(
    image: Image.Image,
    label_bbox: tuple[int, int, int, int],
    side: str,
) -> list[tuple[int, int, int, int]]:
    width, height = image.size
    label_left, label_top, label_right, label_bottom = label_bbox
    label_center_y = _center_y(label_bbox)
    if side == "right":
        search_left = max(0, label_right - 6)
        search_right = min(width - 1, label_right + 150)
    else:
        search_left = max(0, label_left - 150)
        search_right = min(width - 1, label_left + 8)
    search_top = max(0, label_top - 10)
    search_bottom = min(height - 1, label_bottom + 10)
    roi = (search_left, search_top, search_right, search_bottom)

    component_candidates = [
        box
        for box in _checkbox_component_boxes(image, roi)
        if _is_checkbox_component_box(box)
        and abs(_center_y(box) - label_center_y) <= max(12, (label_bottom - label_top) // 2 + 6)
    ]
    if component_candidates:
        if side == "right":
            best = min(
                component_candidates,
                key=lambda box: (abs(_center_y(box) - label_center_y), abs(_center_x(box) - label_right)),
            )
        else:
            best = min(
                component_candidates,
                key=lambda box: (abs(_center_y(box) - label_center_y), abs(_center_x(box) - label_left)),
            )
        return [_expand_box(best, image.size, padding=1)]

    candidates: list[tuple[int, int, int, int]] = []
    for size in range(8, 25):
        max_y = search_bottom - size
        max_x = search_right - size
        if max_y < search_top or max_x < search_left:
            continue
        for y in range(search_top, max_y + 1):
            for x in range(search_left, max_x + 1):
                box = (x, y, x + size, y + size)
                if abs(_center_y(box) - label_center_y) > max(12, (label_bottom - label_top) // 2 + 6):
                    continue
                if _looks_like_checkbox(image, box):
                    candidates.append(box)

    merged = _merge_control_candidates(candidates)
    if not merged:
        return []
    if side == "right":
        best = min(
            merged,
            key=lambda box: (abs(_center_y(box) - label_center_y), abs(_center_x(box) - label_right)),
        )
        return [best]
    best = min(
        merged,
        key=lambda box: (abs(_center_y(box) - label_center_y), _center_x(box)),
    )
    return [best]


def _checkbox_component_boxes(
    image: Image.Image,
    roi: tuple[int, int, int, int],
) -> list[tuple[int, int, int, int]]:
    x1, y1, x2, y2 = roi
    width = max(0, x2 - x1 + 1)
    height = max(0, y2 - y1 + 1)
    if width <= 0 or height <= 0:
        return []

    mask = [
        [_is_checkbox_border_pixel(image.getpixel((x1 + x, y1 + y))) for x in range(width)]
        for y in range(height)
    ]
    seen = [[False] * width for _ in range(height)]
    boxes: list[tuple[int, int, int, int]] = []

    for start_y in range(height):
        for start_x in range(width):
            if not mask[start_y][start_x] or seen[start_y][start_x]:
                continue
            queue = deque([(start_x, start_y)])
            seen[start_y][start_x] = True
            min_x = max_x = start_x
            min_y = max_y = start_y
            count = 0
            while queue:
                x, y = queue.popleft()
                count += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx = x + dx
                        ny = y + dy
                        if 0 <= nx < width and 0 <= ny < height and mask[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            queue.append((nx, ny))
            if count >= 12:
                boxes.append((x1 + min_x, y1 + min_y, x1 + max_x, y1 + max_y))
    return boxes


def _is_checkbox_component_box(box: tuple[int, int, int, int]) -> bool:
    width = box[2] - box[0] + 1
    height = box[3] - box[1] + 1
    if width < 8 or height < 8 or width > 26 or height > 26:
        return False
    if abs(width - height) > 4:
        return False
    return True


def _looks_like_checkbox(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = box
    width = x2 - x1 + 1
    height = y2 - y1 + 1
    if width < 8 or height < 8 or width > 26 or height > 26 or abs(width - height) > 4:
        return False

    sides = [
        [image.getpixel((x, y1)) for x in range(x1, x2 + 1)],
        [image.getpixel((x, y2)) for x in range(x1, x2 + 1)],
        [image.getpixel((x1, y)) for y in range(y1, y2 + 1)],
        [image.getpixel((x2, y)) for y in range(y1, y2 + 1)],
    ]
    bordered_sides = sum(_border_ratio(side) >= 0.28 for side in sides)
    if bordered_sides < 3:
        return False

    inner_left = min(x2, x1 + 2)
    inner_right = max(inner_left, x2 - 2)
    inner_top = min(y2, y1 + 2)
    inner_bottom = max(inner_top, y2 - 2)
    light = 0
    total = 0
    for y in range(inner_top, inner_bottom + 1):
        for x in range(inner_left, inner_right + 1):
            total += 1
            if _is_near_white(image.getpixel((x, y))) or _is_light_gray(image.getpixel((x, y))):
                light += 1
    return total > 0 and light / total >= 0.45


def _border_ratio(pixels: list[tuple[int, int, int]]) -> float:
    if not pixels:
        return 0.0
    return sum(1 for pixel in pixels if _is_checkbox_border_pixel(pixel)) / len(pixels)


def _is_checkbox_border_pixel(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    if r > 248 and g > 248 and b > 248:
        return False
    if r < 90 and g < 90 and b < 90:
        return True
    return 90 <= r <= 235 and 90 <= g <= 235 and 90 <= b <= 235 and max(pixel) - min(pixel) <= 95


def _is_light_gray(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return r >= 210 and g >= 210 and b >= 210


def _merge_control_candidates(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    merged: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda item: ((item[2] - item[0]) * (item[3] - item[1])), reverse=True):
        if any(_iou(box, existing) > 0.25 or _overlap_ratio(box, existing) > 0.45 for existing in merged):
            continue
        merged.append(box)
    return merged


def _expand_box(
    box: tuple[int, int, int, int],
    image_size: tuple[int, int],
    *,
    padding: int,
) -> tuple[int, int, int, int]:
    width, height = image_size
    return (
        max(0, box[0] - padding),
        max(0, box[1] - padding),
        min(width - 1, box[2] + padding),
        min(height - 1, box[3] + padding),
    )


def _merge_input_box_candidates(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    merged: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda item: ((item[2] - item[0]) * (item[3] - item[1])), reverse=True):
        if any(_iou(box, existing) > 0.35 or _overlap_ratio(box, existing) > 0.6 for existing in merged):
            continue
        merged.append(box)
    return sorted(merged, key=lambda item: (item[0], item[1], item[2], item[3]))


def _border_runs_on_row(image: Image.Image, y: int, x1: int, x2: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    in_run = False
    start = x1
    for x in range(x1, x2 + 1):
        is_border = _is_input_border_pixel(image.getpixel((x, y)))
        if is_border and not in_run:
            start = x
            in_run = True
        elif in_run and not is_border:
            if x - start >= 3:
                runs.append((start, x - 1))
            in_run = False
    if in_run and x2 - start >= 2:
        runs.append((start, x2))
    return runs


def _is_input_border_pixel(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    if r > 250 and g > 250 and b > 250:
        return False
    if r < 120 or g < 120 or b < 120:
        return False
    return max(pixel) - min(pixel) <= 80


def _mostly_white_fill(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = box
    inner_left = min(x2, x1 + 3)
    inner_right = max(inner_left, x2 - 3)
    inner_top = min(y2, y1 + 2)
    inner_bottom = max(inner_top, y2 - 2)
    white = 0
    total = 0
    step_x = max(1, (inner_right - inner_left) // 60)
    step_y = max(1, (inner_bottom - inner_top) // 12)
    for y in range(inner_top, inner_bottom + 1, step_y):
        for x in range(inner_left, inner_right + 1, step_x):
            total += 1
            if _is_near_white(image.getpixel((x, y))):
                white += 1
    return total > 0 and white / total >= 0.75


def _is_near_white(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return r >= 240 and g >= 240 and b >= 240


def _overlaps_existing_element(box: tuple[int, int, int, int], elements: list[UIElement]) -> bool:
    for element in elements:
        if _iou(box, element.pixel_bbox) > 0.65:
            return True
    return False


def _synthetic_target_element(
    pixel_bbox: tuple[int, int, int, int],
    screenshot: ImageInfo,
    *,
    idx: int,
    label_element: UIElement,
    label_text: str,
    spec: SyntheticTargetSpec,
    rank: int,
    multiple: bool,
) -> UIElement:
    bbox = _pixels_to_normalized_bbox(pixel_bbox, screenshot.width, screenshot.height)
    center = ((pixel_bbox[0] + pixel_bbox[2]) // 2, (pixel_bbox[1] + pixel_bbox[3]) // 2)
    target_name = "input box" if spec.kind == "input_box" else "checkbox"
    content = f"{label_text} {target_name} {rank}" if multiple else f"{label_text} {target_name}"
    source = f"synthetic_{spec.kind}"
    return UIElement(
        idx=idx,
        type=source,
        content=content,
        bbox=bbox,
        pixel_bbox=pixel_bbox,
        center=center,
        interactive=True,
        source=source,
        raw={
            "content": content,
            "idx": idx,
            "source": source,
            "source_label_idx": label_element.idx,
            "source_label": label_element.content,
            "synthetic_kind": spec.kind,
            "synthetic_label": label_text,
            "synthetic_side": spec.side,
            "synthetic_rank": rank,
            "synthetic_multiple": multiple,
        },
    )


def _synthetic_input_box_element(
    pixel_bbox: tuple[int, int, int, int],
    screenshot: ImageInfo,
    *,
    idx: int,
    label_element: UIElement,
    label_text: str,
    rank: int,
    multiple: bool,
) -> UIElement:
    return _synthetic_target_element(
        pixel_bbox,
        screenshot,
        idx=idx,
        label_element=label_element,
        label_text=label_text,
        spec=SyntheticTargetSpec(kind="input_box", label=label_text, side="right"),
        rank=rank,
        multiple=multiple,
    )


def _element_matches_label(element: UIElement, label: str) -> bool:
    normalized_label = _normalize_text(label)
    normalized_content = _normalize_text(element.content)
    return bool(normalized_label and normalized_content) and (
        normalized_label in normalized_content or normalized_content in normalized_label
    )


def _center_x(box: tuple[int, int, int, int]) -> int:
    return (box[0] + box[2]) // 2


def _center_y(box: tuple[int, int, int, int]) -> int:
    return (box[1] + box[3]) // 2


def _normalized_bbox(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"Invalid OmniParser bbox: {value!r}")
    x1, y1, x2, y2 = (max(0.0, min(1.0, float(part))) for part in value)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Invalid OmniParser bbox: {value!r}")
    return x1, y1, x2, y2


def _bbox_to_pixels(
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    return (
        round(x1 * width),
        round(y1 * height),
        round(x2 * width),
        round(y2 * height),
    )


def _pixels_to_normalized_bbox(
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    safe_width = max(1, width)
    safe_height = max(1, height)
    return (
        max(0.0, min(1.0, x1 / safe_width)),
        max(0.0, min(1.0, y1 / safe_height)),
        max(0.0, min(1.0, x2 / safe_width)),
        max(0.0, min(1.0, y2 / safe_height)),
    )


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _normalize_text(value)
        if key and key not in seen:
            output.append(value)
            seen.add(key)
    return output


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    intersection = _intersection_area(a, b)
    if intersection <= 0:
        return 0.0
    area_a = _area(a)
    area_b = _area(b)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    intersection = _intersection_area(a, b)
    smaller = min(_area(a), _area(b))
    return intersection / smaller if smaller > 0 else 0.0


def _intersection_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0
    return (ix2 - ix1) * (iy2 - iy1)


def _area(box: tuple[int, int, int, int]) -> int:
    x1, y1, x2, y2 = box
    return max(0, x2 - x1) * max(0, y2 - y1)


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
