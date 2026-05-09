from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Mapping, Optional


class NullRunLogger:
    def log(self, message: str, data: Optional[Any] = None) -> None:
        return None


class ConsoleRunLogger:
    def __init__(self, *, max_chars: int = 1600):
        self.max_chars = max_chars

    def log(self, message: str, data: Optional[Any] = None) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        if data is None:
            print(f"[{stamp}] {message}", flush=True)
            return
        print(f"[{stamp}] {message}：{self.format_data(data)}", flush=True)

    def format_data(self, data: Any) -> str:
        value = _redact(_to_jsonable(data))
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) > self.max_chars:
            return text[: self.max_chars] + "...<已截断>"
        return text


def summarize_decision(decision: Any) -> dict[str, Any]:
    action = getattr(decision, "action", None)
    return {
        "thought": getattr(decision, "thought", ""),
        "action": _to_jsonable(action),
        "expected_observation": getattr(decision, "expected_observation", ""),
        "done": getattr(decision, "done", False),
        "failure": getattr(decision, "failure", None),
    }


def summarize_tool_result(result: Any) -> dict[str, Any]:
    return {
        "ok": getattr(result, "ok", None),
        "code": getattr(result, "code", ""),
        "message": getattr(result, "message", ""),
        "data": getattr(result, "data", {}),
    }


def summarize_observation(observation: Any) -> dict[str, Any]:
    return {
        "summary": getattr(observation, "summary", ""),
        "screenshot_path": getattr(observation, "screenshot_path", None),
        "window_title": getattr(observation, "window_title", None),
        "image_size": getattr(observation, "image_size", None),
        "resolution": getattr(observation, "resolution", None),
        "elements": _to_jsonable(getattr(observation, "elements", [])),
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
