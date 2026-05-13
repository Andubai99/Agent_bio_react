from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Mapping, Optional


class NullRunLogger:
    def log(self, message: str, data: Optional[Any] = None) -> None:
        return None


class ConsoleRunLogger:
    def __init__(self, *, max_chars: int = 4000, max_value_chars: int = 900):
        self.max_chars = max_chars
        self.max_value_chars = max_value_chars
        self._last_stamp: str | None = None

    def log(self, message: str, data: Optional[Any] = None) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        status = _status_label(data)
        header = f"[{stamp}] {status:<5} {message}"
        if self._last_stamp is not None and stamp != self._last_stamp:
            print("-----------", flush=True)
        self._last_stamp = stamp
        if data is None:
            print(header, flush=True)
            return
        print(f"{header}\n{self.format_data(data)}", flush=True)

    def format_data(self, data: Any) -> str:
        value = _redact(_compact_for_console(_to_jsonable(data)))
        text = _format_console_value(value, max_value_chars=self.max_value_chars)
        if len(text) > self.max_chars:
            return text[: self.max_chars].rstrip() + "\n  ...<已截断>"
        return text


def summarize_decision(decision: Any) -> dict[str, Any]:
    action = getattr(decision, "action", None)
    actions = _to_jsonable(getattr(decision, "actions", []) or [])
    payload = {
        "thought": getattr(decision, "thought", ""),
        "actions_count": len(actions),
        "actions": actions,
        "expected_observation": getattr(decision, "expected_observation", ""),
        "done": getattr(decision, "done", False),
        "failure": getattr(decision, "failure", None),
    }
    if action is not None and not actions:
        payload["action"] = _to_jsonable(action)
    return payload


def summarize_tool_result(result: Any) -> dict[str, Any]:
    return {
        "ok": getattr(result, "ok", None),
        "code": getattr(result, "code", ""),
        "message": getattr(result, "message", ""),
        "data": _summarize_result_data(getattr(result, "data", {})),
    }


def summarize_observation(observation: Any) -> dict[str, Any]:
    elements = list(getattr(observation, "elements", []) or [])
    interactive_elements = [item for item in elements if bool(getattr(item, "interactive", False))]
    return {
        "summary": getattr(observation, "summary", ""),
        "screenshot_path": getattr(observation, "screenshot_path", None),
        "window_title": getattr(observation, "window_title", None),
        "image_size": getattr(observation, "image_size", None),
        "resolution": getattr(observation, "resolution", None),
        "elements_count": len(elements),
        "interactive_count": len(interactive_elements),
        "elements_preview": [_summarize_element(item) for item in elements[:8]],
        "elements_omitted": max(0, len(elements) - 8),
        "interactive_preview": [_summarize_element(item) for item in interactive_elements[:6]],
        "interactive_omitted": max(0, len(interactive_elements) - 6),
        "state": getattr(observation, "state", {}),
    }


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in ("api_key", "apikey", "authorization", "token", "secret", "password")):
                redacted[key] = "<已隐藏>"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _status_label(data: Optional[Any]) -> str:
    if isinstance(data, Mapping):
        ok = data.get("ok")
        if ok is True:
            return "OK"
        if ok is False:
            return "FAIL"
    return "INFO"


def _format_console_value(value: Any, *, max_value_chars: int) -> str:
    return "\n".join(_format_lines(value, indent=2, max_value_chars=max_value_chars))


def _format_lines(value: Any, *, indent: int, max_value_chars: int) -> list[str]:
    prefix = " " * indent
    if _is_scalar(value):
        return [f"{prefix}{_format_scalar(value, max_value_chars=max_value_chars)}"]
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, item in value.items():
            key_text = str(key)
            if _can_inline(item, max_value_chars=max_value_chars):
                lines.append(f"{prefix}{key_text}: {_format_inline(item, max_value_chars=max_value_chars)}")
            else:
                lines.append(f"{prefix}{key_text}:")
                lines.extend(_format_lines(item, indent=indent + 2, max_value_chars=max_value_chars))
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]"]
        lines = []
        for item in value:
            if _can_inline(item, max_value_chars=max_value_chars):
                lines.append(f"{prefix}- {_format_inline(item, max_value_chars=max_value_chars)}")
            else:
                lines.append(f"{prefix}-")
                lines.extend(_format_lines(item, indent=indent + 2, max_value_chars=max_value_chars))
        return lines
    return [f"{prefix}{_format_inline(value, max_value_chars=max_value_chars)}"]


def _format_scalar(value: Any, *, max_value_chars: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > max_value_chars:
        return text[:max_value_chars].rstrip() + "...<已截断>"
    return text


def _format_inline(value: Any, *, max_value_chars: int) -> str:
    if _is_scalar(value):
        return _format_scalar(value, max_value_chars=max_value_chars)
    text = json.dumps(value, ensure_ascii=False, default=str, separators=(", ", ": "))
    if len(text) > max_value_chars:
        return text[:max_value_chars].rstrip() + "...<已截断>"
    return text


def _can_inline(value: Any, *, max_value_chars: int) -> bool:
    if _is_scalar(value):
        return True
    if isinstance(value, list):
        return not value or all(_is_scalar(item) for item in value)
    if isinstance(value, Mapping):
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(", ", ": "))
        return "\n" not in text and len(text) <= max_value_chars
    return True


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _compact_for_console(value: Any) -> Any:
    if isinstance(value, dict):
        compacted = {}
        for key, item in value.items():
            if key == "raw" and isinstance(item, dict):
                compacted[key] = _compact_raw_payload(item)
            elif key == "elements" and isinstance(item, list):
                compacted["elements_count"] = len(item)
                compacted["elements_preview"] = [_summarize_element(element) for element in item[:8]]
                compacted["elements_omitted"] = max(0, len(item) - 8)
            elif key == "element":
                compacted[key] = _summarize_element(item)
            else:
                compacted[key] = _compact_for_console(item)
        return compacted
    if isinstance(value, list):
        return [_compact_for_console(item) for item in value]
    return value


def _compact_raw_payload(value: dict[str, Any]) -> dict[str, Any]:
    keys_to_keep = ("provider", "parsed", "content")
    compacted: dict[str, Any] = {}
    for key in keys_to_keep:
        if key in value:
            compacted[key] = _compact_for_console(value[key])
    return compacted or {key: _compact_for_console(item) for key, item in value.items()}


def _summarize_result_data(data: Any) -> Any:
    if isinstance(data, Mapping):
        summarized = {}
        for key, item in data.items():
            if key == "element":
                summarized[key] = _summarize_element(item)
            elif key == "elements" and isinstance(item, list):
                summarized["elements_count"] = len(item)
                summarized["elements_preview"] = [_summarize_element(element) for element in item[:8]]
                summarized["elements_omitted"] = max(0, len(item) - 8)
            elif key == "action_results" and isinstance(item, list):
                summarized[key] = [_summarize_result_data(action_result) for action_result in item]
            else:
                summarized[key] = _summarize_result_data(item)
        return summarized
    if isinstance(data, list):
        return [_summarize_result_data(item) for item in data]
    return data


def _summarize_element(element: Any) -> Any:
    value = _to_jsonable(element)
    if not isinstance(value, Mapping):
        return value
    content = value.get("content", value.get("text", ""))
    return {
        "idx": value.get("idx"),
        "type": value.get("type"),
        "content": content,
        "pixel_bbox": value.get("pixel_bbox"),
        "center": value.get("center"),
        "interactive": value.get("interactive"),
        "source": value.get("source"),
    }
