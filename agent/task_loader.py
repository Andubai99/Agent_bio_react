from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from agent.types import TaskSpec


REQUIRED_FRONTMATTER_FIELDS = {
    "id",
    "title",
    "description",
    "inputs",
    "allowed_tools",
    "start_conditions",
    "success_criteria",
    "safety_rules",
}


class TaskSpecError(ValueError):
    pass


TASK_DOCUMENT_NAME = "task.md"


def load_task(path: Path) -> TaskSpec:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return _load_plain_task(path, text)
    frontmatter, body = _split_frontmatter(text)
    _validate_frontmatter(frontmatter, path)
    return TaskSpec(
        id=str(frontmatter["id"]),
        title=str(frontmatter["title"]),
        description=str(frontmatter["description"]),
        path=path,
        inputs=dict(frontmatter["inputs"] or {}),
        allowed_tools=[str(item) for item in frontmatter["allowed_tools"]],
        start_conditions=_as_string_list(frontmatter["start_conditions"]),
        success_criteria=_as_string_list(frontmatter["success_criteria"]),
        safety_rules=_as_string_list(frontmatter["safety_rules"]),
        body=body.strip(),
        frontmatter=frontmatter,
    )


def _load_plain_task(path: Path, text: str) -> TaskSpec:
    body = text.strip()
    title = _plain_title(body, path.stem)
    task_id = _plain_task_id(path)
    return TaskSpec(
        id=task_id,
        title=title,
        description=title,
        path=path,
        inputs={
            "window_title_keyword": "Thermo BioPharma Finder 5.1",
        },
        allowed_tools=[],
        start_conditions=[],
        success_criteria=[],
        safety_rules=[],
        body=body,
        frontmatter={},
    )


def iter_task_paths(tasks_dir: Path) -> list[Path]:
    paths = [*tasks_dir.glob("*.md"), *tasks_dir.glob(f"*/{TASK_DOCUMENT_NAME}")]
    return sorted(set(paths), key=lambda item: item.as_posix())


def load_task_by_id(tasks_dir: Path, task_id: str) -> TaskSpec:
    for path in iter_task_paths(tasks_dir):
        task = load_task(path)
        if task.id == task_id:
            return task
    raise TaskSpecError(f"Task not found: {task_id}")


def _split_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        raise TaskSpecError("Task document must start with YAML frontmatter")
    normalized = text.replace("\r\n", "\n")
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise TaskSpecError("Task document frontmatter is not closed")
    frontmatter_text = normalized[4:end]
    body = normalized[end + len("\n---\n") :]
    loaded = yaml.safe_load(frontmatter_text) or {}
    if not isinstance(loaded, dict):
        raise TaskSpecError("Task frontmatter must be a mapping")
    return loaded, body


def _plain_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped:
            return stripped
    return fallback


def _plain_task_id(path: Path) -> str:
    if path.name.lower() == TASK_DOCUMENT_NAME and path.parent.name:
        return path.parent.name
    return path.stem


def _validate_frontmatter(frontmatter: Dict[str, Any], path: Path) -> None:
    missing = sorted(REQUIRED_FRONTMATTER_FIELDS - set(frontmatter))
    if missing:
        raise TaskSpecError(f"{path} missing frontmatter fields: {', '.join(missing)}")
    if not isinstance(frontmatter["inputs"], dict):
        raise TaskSpecError("inputs must be a mapping")
    if not _is_list(frontmatter["allowed_tools"]):
        raise TaskSpecError("allowed_tools must be a list")
    if not frontmatter["allowed_tools"]:
        raise TaskSpecError("allowed_tools must not be empty")
    for field_name in ("start_conditions", "success_criteria", "safety_rules"):
        if not _is_list(frontmatter[field_name]):
            raise TaskSpecError(f"{field_name} must be a list")


def _as_string_list(value: Any) -> List[str]:
    return [str(item) for item in value]


def _is_list(value: Any) -> bool:
    return isinstance(value, list)
