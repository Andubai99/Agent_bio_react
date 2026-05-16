from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.types import ImageInfo, TaskSpec, UIElement


CACHE_DIR_NAME = "omniparser_cache"
MANIFEST_NAME = "manifest.json"
TASK_DOCUMENT_NAME = "task.md"
SCHEMA_VERSION = 1
ASPECT_RATIO_TOLERANCE = 0.015


@dataclass(frozen=True)
class ObservationCacheContext:
    task_id: str
    step_number: int
    window_title: str
    instruction: str
    resolution: tuple[int, int]
    parser_params: dict[str, Any]


@dataclass(frozen=True)
class ObservationCacheRecord:
    key: str
    entry: dict[str, Any]
    screenshot_path: Path
    elements_path: Path
    exact_resolution: bool


@dataclass(frozen=True)
class ObservationCacheLookup:
    record: ObservationCacheRecord | None
    reason: str

    @property
    def hit(self) -> bool:
        return self.record is not None


class ObservationCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.screenshots_dir = self.cache_dir / "screenshots"
        self.elements_dir = self.cache_dir / "elements"
        self.manifest_path = self.cache_dir / MANIFEST_NAME

    @classmethod
    def for_task(cls, task: TaskSpec) -> "ObservationCache":
        return cls(_task_cache_dir(task))

    def lookup(self, context: ObservationCacheContext) -> ObservationCacheLookup:
        manifest = self._read_manifest()
        entries = manifest.get("entries", {})
        if not isinstance(entries, dict) or not entries:
            return ObservationCacheLookup(None, "manifest_missing_or_empty")

        key = cache_key(context)
        entry = entries.get(key)
        if isinstance(entry, dict):
            record = self._record_from_entry(key, entry, exact_resolution=True)
            if record is not None:
                return ObservationCacheLookup(record, "exact_match")

        compatible = self._find_compatible_record(context, entries)
        if compatible is not None:
            return ObservationCacheLookup(compatible, "compatible_resolution_match")

        return ObservationCacheLookup(None, "entry_not_found")

    def load_elements(
        self,
        record: ObservationCacheRecord,
        *,
        current_resolution: tuple[int, int],
    ) -> list[UIElement]:
        payload = _read_json(record.elements_path)
        items = payload.get("elements", [])
        if not isinstance(items, list):
            raise ValueError(f"Cache elements must be a list: {record.elements_path}")
        elements = [_element_from_json(item) for item in items if isinstance(item, dict)]
        if not elements:
            return []
        cached_resolution = _coerce_resolution(record.entry.get("resolution"))
        if record.exact_resolution or cached_resolution == current_resolution:
            return elements
        return [_reproject_element(element, current_resolution) for element in elements]

    def write(
        self,
        context: ObservationCacheContext,
        *,
        screenshot: ImageInfo,
        elements: list[UIElement],
    ) -> ObservationCacheRecord:
        if not screenshot.path:
            raise ValueError("Cannot cache observation without screenshot path.")
        source = Path(screenshot.path)
        if not source.exists():
            raise FileNotFoundError(f"Screenshot does not exist: {source}")

        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.elements_dir.mkdir(parents=True, exist_ok=True)
        key = cache_key(context)
        screenshot_path = self.screenshots_dir / f"{key}{source.suffix or '.png'}"
        elements_path = self.elements_dir / f"{key}.json"

        shutil.copy2(source, screenshot_path)
        elements_payload = {
            "version": SCHEMA_VERSION,
            "key": key,
            "task_id": context.task_id,
            "step_number": context.step_number,
            "window_title": context.window_title,
            "instruction_summary": _instruction_summary(context.instruction),
            "resolution": list(context.resolution),
            "elements": [_element_to_json(element) for element in elements],
        }
        _write_json(elements_path, elements_payload)

        manifest = self._read_manifest()
        entries = manifest.setdefault("entries", {})
        if not isinstance(entries, dict):
            entries = {}
            manifest["entries"] = entries
        now = _now_iso()
        existing = entries.get(key) if isinstance(entries.get(key), dict) else {}
        created_at = existing.get("created_at") or now
        entry = {
            "key": key,
            "task_id": context.task_id,
            "step_number": context.step_number,
            "window_title": context.window_title,
            "instruction_hash": _instruction_hash(context.instruction),
            "instruction_summary": _instruction_summary(context.instruction),
            "resolution": list(context.resolution),
            "parser_params": _jsonable(context.parser_params),
            "screenshot": _relative_to_cache(self.cache_dir, screenshot_path),
            "elements": _relative_to_cache(self.cache_dir, elements_path),
            "element_count": len(elements),
            "created_at": created_at,
            "updated_at": now,
        }
        entries[key] = entry
        manifest["version"] = SCHEMA_VERSION
        manifest["updated_at"] = now
        _write_json(self.manifest_path, manifest)
        return ObservationCacheRecord(key, entry, screenshot_path, elements_path, exact_resolution=True)

    def _find_compatible_record(
        self,
        context: ObservationCacheContext,
        entries: dict[str, Any],
    ) -> ObservationCacheRecord | None:
        current_ratio = _aspect_ratio(context.resolution)
        candidates: list[ObservationCacheRecord] = []
        for key, entry in entries.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            if entry.get("task_id") != context.task_id:
                continue
            if int(entry.get("step_number", -1)) != context.step_number:
                continue
            if str(entry.get("window_title", "")) != context.window_title:
                continue
            if entry.get("instruction_hash") != _instruction_hash(context.instruction):
                continue
            if _canonical(entry.get("parser_params", {})) != _canonical(context.parser_params):
                continue
            cached_resolution = _coerce_resolution(entry.get("resolution"))
            if cached_resolution == context.resolution:
                continue
            if abs(_aspect_ratio(cached_resolution) - current_ratio) > ASPECT_RATIO_TOLERANCE:
                continue
            record = self._record_from_entry(key, entry, exact_resolution=False)
            if record is not None:
                candidates.append(record)
        if not candidates:
            return None
        return max(candidates, key=lambda item: str(item.entry.get("updated_at", "")))

    def _record_from_entry(
        self,
        key: str,
        entry: dict[str, Any],
        *,
        exact_resolution: bool,
    ) -> ObservationCacheRecord | None:
        screenshot = entry.get("screenshot")
        elements = entry.get("elements")
        if not isinstance(screenshot, str) or not isinstance(elements, str):
            return None
        screenshot_path = self.cache_dir / screenshot
        elements_path = self.cache_dir / elements
        if not screenshot_path.exists() or not elements_path.exists():
            return None
        if int(entry.get("element_count", 0) or 0) <= 0:
            return None
        return ObservationCacheRecord(key, entry, screenshot_path, elements_path, exact_resolution)

    def _read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"version": SCHEMA_VERSION, "entries": {}}
        try:
            value = _read_json(self.manifest_path)
        except Exception:
            return {"version": SCHEMA_VERSION, "entries": {}}
        if not isinstance(value, dict):
            return {"version": SCHEMA_VERSION, "entries": {}}
        value.setdefault("version", SCHEMA_VERSION)
        value.setdefault("entries", {})
        return value


def cache_key(context: ObservationCacheContext) -> str:
    payload = {
        "task_id": context.task_id,
        "step_number": context.step_number,
        "window_title": context.window_title,
        "instruction_hash": _instruction_hash(context.instruction),
        "resolution": list(context.resolution),
        "parser_params": _jsonable(context.parser_params),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:24]


def _task_cache_dir(task: TaskSpec) -> Path:
    task_path = task.path
    if task_path.name.lower() == TASK_DOCUMENT_NAME:
        return task_path.parent / CACHE_DIR_NAME
    return task_path.parent / task.id / CACHE_DIR_NAME


def _element_to_json(element: UIElement) -> dict[str, Any]:
    return {
        "idx": element.idx,
        "type": element.type,
        "content": element.content,
        "bbox": list(element.bbox),
        "pixel_bbox": list(element.pixel_bbox),
        "center": list(element.center),
        "interactive": element.interactive,
        "source": element.source,
        "raw": _jsonable(element.raw),
    }


def _element_from_json(value: dict[str, Any]) -> UIElement:
    return UIElement(
        idx=int(value["idx"]),
        type=str(value.get("type", "unknown")),
        content=value.get("content"),
        bbox=_coerce_float_box(value.get("bbox")),
        pixel_bbox=_coerce_int_box(value.get("pixel_bbox")),
        center=_coerce_point(value.get("center")),
        interactive=bool(value.get("interactive", False)),
        source=str(value.get("source", "")),
        raw=dict(value.get("raw") or {}),
    )


def _reproject_element(element: UIElement, resolution: tuple[int, int]) -> UIElement:
    width, height = resolution
    x1, y1, x2, y2 = element.bbox
    pixel_bbox = (
        round(x1 * width),
        round(y1 * height),
        round(x2 * width),
        round(y2 * height),
    )
    center = ((pixel_bbox[0] + pixel_bbox[2]) // 2, (pixel_bbox[1] + pixel_bbox[3]) // 2)
    return UIElement(
        idx=element.idx,
        type=element.type,
        content=element.content,
        bbox=element.bbox,
        pixel_bbox=pixel_bbox,
        center=center,
        interactive=element.interactive,
        source=element.source,
        raw=dict(element.raw),
    )


def _coerce_float_box(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"Invalid bbox in cache: {value!r}")
    return tuple(float(item) for item in value)  # type: ignore[return-value]


def _coerce_int_box(value: Any) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"Invalid pixel_bbox in cache: {value!r}")
    return tuple(int(item) for item in value)  # type: ignore[return-value]


def _coerce_point(value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"Invalid center in cache: {value!r}")
    return int(value[0]), int(value[1])


def _coerce_resolution(value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return 0, 0
    return int(value[0]), int(value[1])


def _aspect_ratio(resolution: tuple[int, int]) -> float:
    width, height = resolution
    if width <= 0 or height <= 0:
        return 0.0
    return width / height


def _instruction_hash(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _instruction_summary(value: str) -> str:
    text = " ".join(value.strip().split())
    return text[:180]


def _canonical(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _relative_to_cache(cache_dir: Path, path: Path) -> str:
    return path.relative_to(cache_dir).as_posix()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
