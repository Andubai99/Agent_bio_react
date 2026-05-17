from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.types import Observation, TaskSpec


MEMORY_DIR_NAME = "memory"
PAGES_DIR_NAME = "pages"
TASK_DOCUMENT_NAME = "task.md"


@dataclass(frozen=True)
class PageDefinition:
    page_id: str
    title_keywords: list[str] = field(default_factory=list)
    required_anchors: list[str] = field(default_factory=list)
    optional_anchors: list[str] = field(default_factory=list)
    min_required_matches: int | None = None
    min_optional_matches: int = 0
    page_change_required: bool = False


@dataclass(frozen=True)
class PageMatchResult:
    page_id: str
    matched: bool
    confidence: float
    reason: str
    title_matched: bool
    matched_required: list[str]
    missing_required: list[str]
    matched_optional: list[str]
    missing_optional: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "matched": self.matched,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "title_matched": self.title_matched,
            "matched_required": self.matched_required,
            "missing_required": self.missing_required,
            "matched_optional": self.matched_optional,
            "missing_optional": self.missing_optional,
        }


class PageMemory:
    def __init__(self, pages_dir: Path):
        self.pages_dir = pages_dir

    @classmethod
    def for_task(cls, task: TaskSpec) -> "PageMemory":
        return cls(_task_pages_dir(task))

    def load(self, page_id: str) -> PageDefinition | None:
        path = self.pages_dir / f"{page_id}.json"
        if not path.exists():
            return None
        payload = _read_json(path)
        if not isinstance(payload, dict):
            return None
        return _definition_from_json(page_id, payload)

    def match(self, page_id: str, observation: Observation) -> PageMatchResult:
        definition = self.load(page_id)
        if definition is None:
            return PageMatchResult(
                page_id=page_id,
                matched=False,
                confidence=0.0,
                reason="page_definition_missing",
                title_matched=False,
                matched_required=[],
                missing_required=[],
                matched_optional=[],
                missing_optional=[],
            )
        return match_page_definition(definition, observation)


def match_page_definition(definition: PageDefinition, observation: Observation) -> PageMatchResult:
    title_text = _normalize_text(observation.window_title or "")
    title_matched = not definition.title_keywords or any(
        _normalize_text(keyword) in title_text for keyword in definition.title_keywords if _normalize_text(keyword)
    )

    element_texts = [
        _normalize_text(getattr(element, "content", "") or "")
        for element in observation.elements
        if _normalize_text(getattr(element, "content", "") or "")
    ]
    required = _match_anchors(definition.required_anchors, element_texts)
    optional = _match_anchors(definition.optional_anchors, element_texts)

    required_total = len(definition.required_anchors)
    min_required = definition.min_required_matches
    if min_required is None:
        min_required = required_total
    required_ok = len(required[0]) >= min_required
    optional_ok = len(optional[0]) >= definition.min_optional_matches
    matched = title_matched and required_ok and optional_ok

    required_score = 1.0 if required_total == 0 else len(required[0]) / required_total
    optional_score = 1.0 if not definition.optional_anchors else len(optional[0]) / len(definition.optional_anchors)
    title_score = 1.0 if title_matched else 0.0
    confidence = (required_score * 0.65) + (optional_score * 0.2) + (title_score * 0.15)

    if matched:
        reason = "page_state_matched"
    elif not title_matched:
        reason = "title_not_matched"
    elif not required_ok:
        reason = "required_anchors_missing"
    else:
        reason = "optional_anchors_below_threshold"

    return PageMatchResult(
        page_id=definition.page_id,
        matched=matched,
        confidence=confidence,
        reason=reason,
        title_matched=title_matched,
        matched_required=required[0],
        missing_required=required[1],
        matched_optional=optional[0],
        missing_optional=optional[1],
    )


def _task_pages_dir(task: TaskSpec) -> Path:
    task_path = task.path
    if task_path.name.lower() == TASK_DOCUMENT_NAME:
        return task_path.parent / MEMORY_DIR_NAME / PAGES_DIR_NAME
    return task_path.parent / task.id / MEMORY_DIR_NAME / PAGES_DIR_NAME


def _definition_from_json(page_id: str, payload: dict[str, Any]) -> PageDefinition:
    verification = payload.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}
    return PageDefinition(
        page_id=str(payload.get("page_id") or page_id),
        title_keywords=_string_list(payload.get("title_keywords")),
        required_anchors=_string_list(payload.get("required_anchors")),
        optional_anchors=_string_list(payload.get("optional_anchors")),
        min_required_matches=_optional_int(payload.get("min_required_matches")),
        min_optional_matches=int(payload.get("min_optional_matches") or 0),
        page_change_required=bool(verification.get("page_change_required", False)),
    )


def _match_anchors(anchors: list[str], element_texts: list[str]) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    for anchor in anchors:
        normalized_anchor = _normalize_text(anchor)
        if not normalized_anchor:
            continue
        if any(normalized_anchor in text or text in normalized_anchor for text in element_texts):
            matched.append(anchor)
        else:
            missing.append(anchor)
    return matched, missing


def _normalize_text(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
