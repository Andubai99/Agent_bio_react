from __future__ import annotations

import re

from agent.types import ActionSequenceHint, ParsedTaskBody, TaskStepSpec, WindowTransition


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


def parse_action_sequence(action_text: str) -> list[ActionSequenceHint]:
    text = action_text.strip()
    if not text:
        return []

    matches = list(_ACTION_PATTERN.finditer(text))
    if not matches:
        return [
            ActionSequenceHint(
                order=1,
                action="click",
                raw=text,
                target_hint=_clean_action_target(text),
            )
        ]

    hints: list[ActionSequenceHint] = []
    last_target = ""
    for index, match in enumerate(matches):
        raw_action = match.group(0)
        action = _normalize_action_verb(raw_action)
        start = match.start()
        if action == "input_text":
            start = matches[index - 1].end() if index > 0 else 0
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[start:end].strip(" ，,。；;")
        target_hint = _target_hint_from_segment(segment, action)
        text_value = _text_value_from_segment(segment, action)

        if action == "input_text":
            if _looks_like_text_value(target_hint, text_value):
                target_hint = ""
            if not target_hint:
                target_hint = last_target
        elif target_hint:
            last_target = target_hint

        hints.append(
            ActionSequenceHint(
                order=len(hints) + 1,
                action=action,
                raw=segment,
                target_hint=target_hint,
                text=text_value,
            )
        )

    return hints


def _strip_markdown_fences(body: str) -> str:
    return re.sub(r"```(?:\w+)?\s*([\s\S]*?)```", lambda match: match.group(1).strip(), body)


_ACTION_PATTERN = re.compile(
    r"双击|点击|单击|输入(?!框|栏|字段)|键入|填写|勾选|选中|选择(?!框)|按下|等待|拖拽|拖动",
    re.IGNORECASE,
)


def _normalize_action_verb(value: str) -> str:
    text = value.strip().lower()
    if text in {"双击"}:
        return "double_click"
    if text in {"输入", "键入", "填写"}:
        return "input_text"
    if text in {"拖拽", "拖动"}:
        return "drag"
    if text in {"按下"}:
        return "press_key"
    if text in {"等待"}:
        return "wait"
    return "click"


def _target_hint_from_segment(segment: str, action: str) -> str:
    text = segment.strip(" ，,。；;")
    text = _strip_leading_action_verb(text)
    if action == "input_text":
        in_match = re.search(
            r"(?:在|向)?(.+?)(?:中|内|里|处)?\s*(?:并|再)?\s*(?:输入(?!框|栏|字段)|键入|填写)\s*[`“\"']?(.+?)[`”\"']?$",
            segment,
        )
        if in_match:
            return _clean_action_target(in_match.group(1))
        return ""
    if action == "press_key":
        quoted = _quoted_phrases(text)
        return quoted[0] if quoted else _clean_action_target(text)
    if action == "wait":
        return _clean_action_target(text)
    return _clean_action_target(text)


def _text_value_from_segment(segment: str, action: str) -> str | None:
    if action != "input_text":
        return None
    text = segment.strip(" ，,。；;")
    quoted = _quoted_phrases(text)
    if quoted:
        return quoted[-1]
    match = re.search(r"(?:输入(?!框|栏|字段)|键入|填写)\s*(.+?)\s*$", text)
    if match:
        return match.group(1).strip(" `“”\"'。；;，,") or None
    return None


def _strip_leading_action_verb(text: str) -> str:
    return re.sub(r"^\s*(?:请|需要|将)?\s*(?:双击|点击|单击|输入|键入|填写|勾选|选中|选择|按下|等待|拖拽|拖动)\s*", "", text)


def _clean_action_target(value: str) -> str:
    text = value.strip()
    text = _strip_leading_action_verb(text)
    text = re.sub(r"^(?:在|向)\s*", "", text)
    text = re.sub(r"(?:，|,)?\s*(?:然后|再|并)\s*$", "", text)
    text = re.sub(r"\s*(?:中|内|里|处)$", "", text)
    text = re.sub(r"\s*(?:按钮|按键)$", "", text)
    text = text.strip(" 。；;，,")
    for char in ("`", "“", "”", '"', "'"):
        text = text.replace(char, "")
    return text.strip()


def _quoted_phrases(text: str) -> list[str]:
    values: list[str] = []
    for pattern in (r"`([^`]+)`", r"“([^”]+)”", r'"([^"]+)"', r"'([^']+)'"):
        values.extend(match.strip() for match in re.findall(pattern, text) if match.strip())
    return values


def _looks_like_text_value(target_hint: str, text_value: str | None) -> bool:
    if not target_hint or not text_value:
        return False
    return target_hint.strip("`“”\"'") == text_value


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
