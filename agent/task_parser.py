from __future__ import annotations

import re

from agent.types import ParsedTaskBody, TaskStepSpec, WindowTransition


def parse_task_body(body: str) -> ParsedTaskBody:
    cleaned = _strip_markdown_fences(body)
    return ParsedTaskBody(
        window_definitions=_parse_window_definitions(cleaned),
        steps=_parse_task_steps(cleaned),
    )


def parse_window_transition(window_text: str, window_definitions: dict[str, str]) -> WindowTransition:
    raw = window_text.strip()
    parts = [part.strip().strip("`") for part in re.split(r"\s*(?:->|→|=>|⇒)\s*", raw, maxsplit=1)]
    source_alias = parts[0] if parts and parts[0] else None
    target_alias = parts[1] if len(parts) > 1 and parts[1] else None
    return WindowTransition(
        source_alias=source_alias,
        source_title=_resolve_window_title(source_alias, window_definitions),
        target_alias=target_alias,
        target_title=_resolve_window_title(target_alias, window_definitions),
        raw=raw,
    )


def _strip_markdown_fences(body: str) -> str:
    return re.sub(r"```(?:\w+)?\s*([\s\S]*?)```", lambda match: match.group(1).strip(), body)


def _parse_window_definitions(body: str) -> dict[str, str]:
    lines = body.splitlines()
    definitions: dict[str, str] = {}
    in_section = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_section:
                continue
            continue
        if stripped.startswith("#"):
            continue
        if stripped.rstrip("：:") == "窗口定义":
            in_section = True
            continue
        if in_section and stripped.rstrip("：:") in {"任务流程", "步骤", "流程"}:
            break
        if not in_section:
            continue
        match = re.match(r"[-*]?\s*([^:：]+?)\s*[:：]\s*(.+?)\s*$", stripped)
        if match:
            alias = match.group(1).strip().strip("`")
            title = match.group(2).strip().strip("`")
            if alias and title:
                definitions[alias] = title
    return definitions


def _parse_task_steps(body: str) -> list[TaskStepSpec]:
    text = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#")).strip()
    if not text:
        return []

    pattern = re.compile(r"(?:^|\n|(?<=[。.!?；;])\s*)(\d+)\.\s+", re.MULTILINE)
    matches = list(pattern.finditer(text))
    steps: list[TaskStepSpec] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw = f"{match.group(1)}. {text[start:end].strip()}"
        action_text = _field_value(raw, "动作")
        window_text = _field_value(raw, "窗口")
        verification_text = _field_value(raw, "验证")
        if action_text:
            steps.append(
                TaskStepSpec(
                    number=int(match.group(1)),
                    action_text=action_text,
                    window_text=window_text,
                    verification_text=verification_text,
                    raw=raw,
                )
            )
    return steps


def _field_value(step_text: str, field_name: str) -> str:
    pattern = re.compile(
        rf"(?:^|\n)\s*(?:\d+\.\s*)?{re.escape(field_name)}\s*[:：]\s*(.*?)(?=\n\s*(?:动作|窗口|验证)\s*[:：]|\Z)",
        re.DOTALL,
    )
    match = pattern.search(step_text)
    if not match:
        return ""
    return " ".join(line.strip() for line in match.group(1).strip().splitlines()).strip()


def _resolve_window_title(alias: str | None, window_definitions: dict[str, str]) -> str | None:
    if not alias:
        return None
    return window_definitions.get(alias, alias)
