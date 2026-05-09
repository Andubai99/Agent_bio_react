from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml

from agent.runtime_logger import NullRunLogger
from agent.types import ImageInfo, RecognitionRequest, RecognitionResult
from modules.api_keys import get_api_key
from modules.vision.schemas import return_json


QWEN_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_MODEL = "qwen3-vl-flash"


class VisionResponseFormatError(RuntimeError):
    pass


class TemporaryVisionProvider:
    def __init__(
        self,
        config_path: Optional[Path] = None,
        data: Optional[Dict[str, Any]] = None,
        logger=None,
    ):
        self.config_path = config_path
        self.data = data if data is not None else self._load_config(config_path)
        self.logger = logger or NullRunLogger()

    def resolve(
        self,
        task_id: str,
        request: RecognitionRequest,
        screenshot: Optional[ImageInfo],
        state: Dict[str, Any],
    ) -> RecognitionResult:
        if request.kind != "post_action":
            raise RuntimeError(f"Unsupported recognition kind: {request.kind}")

        self.logger.log(
            "调用临时视觉配置",
            {"task_id": task_id, "kind": request.kind, "key": request.key, "prompt": request.prompt},
        )
        task_data = self.data.get(task_id, {}) if isinstance(self.data, dict) else {}
        value = task_data.get(request.key)
        verified = _coerce_bool(value)
        page_changed = _coerce_page_changed(value)
        if request.required and (verified is None or page_changed is None):
            raise RuntimeError(f"Temporary vision did not return post-action verification: {request.key}")

        raw_value = value if isinstance(value, dict) else {"verified": verified, "page_changed": page_changed}
        result = RecognitionResult(
            request.key,
            request.kind,
            verified=verified,
            page_changed=page_changed,
            raw=raw_value,
        )
        self.logger.log(
            "临时视觉返回",
            {"verified": result.verified, "page_changed": result.page_changed, "raw": result.raw},
        )
        return result

    @staticmethod
    def _load_config(path: Optional[Path]) -> Dict[str, Any]:
        if path is None or not path.exists():
            return {}
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return loaded if isinstance(loaded, dict) else {}


class ChatVisionProvider:
    provider_label = "chat-vision"
    error_prefix = "Vision"
    api_key_env_name = ""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str,
        model: str,
        timeout_seconds: float = 60.0,
        fallback_provider: Optional[Any] = None,
        fallback_on_error: bool = True,
        max_tokens: int = 256,
        transport: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        logger=None,
    ):
        if not api_key:
            raise ValueError(f"Missing {self.error_prefix} API key: {self.api_key_env_name}")
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.fallback_provider = fallback_provider
        self.fallback_on_error = fallback_on_error
        self.max_tokens = max_tokens
        self.transport = transport
        self.logger = logger or NullRunLogger()

    def resolve(
        self,
        task_id: str,
        request: RecognitionRequest,
        screenshot: Optional[ImageInfo],
        state: Dict[str, Any],
    ) -> RecognitionResult:
        try:
            if request.kind != "post_action":
                raise RuntimeError(f"Unsupported recognition kind: {request.kind}")
            if screenshot is None or not screenshot.path:
                raise RuntimeError(f"{self.error_prefix} requires a screenshot file.")

            payload = self._build_payload(request, screenshot, state)
            self.logger.log(
                f"调用视觉模型：{self.model}",
                {
                    "endpoint": self.endpoint,
                    "kind": request.kind,
                    "key": request.key,
                    "prompt": request.prompt,
                    "screenshot": screenshot.path,
                    "resolution": screenshot.resolution,
                },
            )
            response = self.transport(payload) if self.transport else self._post(payload)
            content = self._extract_message_content(response)
            self.logger.log(f"视觉模型原始输出：{self.model}", content)
            try:
                parsed = self._parse_json_object(content)
            except Exception as exc:
                raise VisionResponseFormatError(
                    f"{self.error_prefix} response is not valid JSON: {exc}"
                ) from exc

            result = self._to_result(request, parsed, content)
            self.logger.log(
                f"视觉模型解析结果：{self.model}",
                {
                    "key": result.key,
                    "kind": result.kind,
                    "verified": result.verified,
                    "page_changed": result.page_changed,
                },
            )
            return result
        except Exception as exc:
            if isinstance(exc, VisionResponseFormatError):
                raise
            if self.fallback_on_error and self.fallback_provider is not None:
                self.logger.log(
                    f"视觉模型调用失败，准备切换备用模型：{self.model}",
                    {
                        "错误": str(exc),
                        "备用模型": type(self.fallback_provider).__name__,
                    },
                )
                fallback = self.fallback_provider.resolve(task_id, request, screenshot, state)
                fallback.raw = {
                    "provider": "fallback",
                    "fallback_provider": type(self.fallback_provider).__name__,
                    "provider_error": str(exc),
                    "fallback_raw": fallback.raw,
                }
                return fallback
            raise

    def _build_payload(
        self, request: RecognitionRequest, screenshot: ImageInfo, state: Dict[str, Any]
    ) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": self._build_messages(request, screenshot, state),
            "temperature": 0.1,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        if self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens
        return payload

    @staticmethod
    def _build_messages(
        request: RecognitionRequest,
        screenshot: ImageInfo,
        state: Dict[str, Any],
    ) -> list[dict[str, Any]]:
        if request.kind != "post_action":
            raise RuntimeError(f"Unsupported recognition kind: {request.kind}")

        image_content = {"type": "image_url", "image_url": {"url": _image_data_url(Path(str(screenshot.path)))}}
        system_prompt = str(state.get("system_prompt") or _default_post_action_system_prompt())
        user_prompt = str(state.get("user_prompt") or _default_post_action_user_prompt(request, screenshot))
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    image_content,
                    {"type": "text", "text": user_prompt},
                ],
            },
        ]

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{self.error_prefix} API HTTP {exc.code}: {error_body}") from exc
        return json.loads(body)

    @staticmethod
    def _extract_message_content(response: Dict[str, Any]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"Vision response missing choices: {response}")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise RuntimeError(f"Vision response missing message: {response}")
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [item.get("text") for item in content if isinstance(item, dict)]
            joined = "\n".join(text for text in texts if isinstance(text, str))
            if joined:
                return joined
        raise RuntimeError(f"Vision response missing text content: {response}")

    @staticmethod
    def _parse_json_object(content: str) -> Dict[str, Any]:
        text = content.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence:
            text = fence.group(1)
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        start = text.find("{")
        if start < 0:
            raise RuntimeError(f"Vision model did not return a JSON object: {content}")
        parsed, _ = json.JSONDecoder().raw_decode(text[start:])
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Vision model did not return a JSON object: {content}")
        return parsed

    def _to_result(
        self,
        request: RecognitionRequest,
        parsed: Dict[str, Any],
        content: str,
    ) -> RecognitionResult:
        if request.kind != "post_action":
            raise RuntimeError(f"Unsupported recognition kind: {request.kind}")
        raw = {"provider": self.model, "parsed": parsed, "content": content}
        return RecognitionResult(
            request.key,
            request.kind,
            verified=_coerce_bool(parsed.get("verified")),
            page_changed=_coerce_bool(parsed.get("page_changed")),
            raw=raw,
        )


class QwenVisionProvider(ChatVisionProvider):
    provider_label = QWEN_MODEL
    error_prefix = "Qwen"
    api_key_env_name = "DASHSCOPE_API_KEY"

    @classmethod
    def from_env(cls, *, fallback_provider: Optional[Any] = None, logger=None) -> "QwenVisionProvider":
        return cls(
            get_api_key("DASHSCOPE_API_KEY", marker="Qwen"),
            endpoint=os.environ.get("QWEN_VISION_ENDPOINT", QWEN_ENDPOINT),
            model=os.environ.get("QWEN_VISION_MODEL", QWEN_MODEL),
            timeout_seconds=float(os.environ.get("QWEN_VISION_TIMEOUT_SECONDS", "45")),
            fallback_provider=fallback_provider,
            fallback_on_error=_env_bool("QWEN_VISION_FALLBACK", True),
            max_tokens=int(os.environ.get("QWEN_VISION_MAX_TOKENS", "256")),
            logger=logger,
        )

    def _build_payload(
        self, request: RecognitionRequest, screenshot: ImageInfo, state: Dict[str, Any]
    ) -> Dict[str, Any]:
        payload = super()._build_payload(request, screenshot, state)
        payload["enable_thinking"] = _env_bool("QWEN_VISION_ENABLE_THINKING", False)
        return payload


def create_vision_provider(logger=None) -> Any:
    provider_name = os.environ.get("BIO_REACT_VISION_PROVIDER", "auto").strip().lower()
    temporary = TemporaryVisionProvider(logger=logger)

    if provider_name in {"auto", ""}:
        try:
            return QwenVisionProvider.from_env(fallback_provider=temporary, logger=logger)
        except ValueError:
            return temporary
    if provider_name in {"qwen", "qwen3-vl-flash"}:
        return QwenVisionProvider.from_env(fallback_provider=temporary, logger=logger)
    if provider_name in {"temporary", "mock", "yaml"}:
        return temporary
    raise RuntimeError(f"Unknown vision provider: {provider_name}")


def _default_post_action_system_prompt() -> str:
    return (
        "You are the unified post-action visual verification layer for a desktop UI agent. "
        "Return exactly one JSON object with no markdown or extra text. "
        f"The required schema is {return_json()}. "
        "Do not include confidence, reason, or any other fields. "
        "verified means whether the current image or after panel satisfies the current task instruction. "
        "page_changed means whether the overall page/screen switched from before to after. "
        "If there is no before/after comparison image, return page_changed=false. "
        "If the same page remains and only local state changed, return page_changed=false."
    )


def _default_post_action_user_prompt(request: RecognitionRequest, screenshot: ImageInfo) -> str:
    return (
        f"Image size: {screenshot.width}x{screenshot.height}.\n"
        f"Current task instruction: {request.prompt}\n"
        "If this is a before/after/diff comparison image, use the after panel to judge verified and "
        "compare before/after to judge page_changed. If this is a single screenshot, judge verified "
        "from that screenshot and return page_changed=false."
    )


def _image_data_url(path: Path) -> str:
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _coerce_page_changed(value: Any) -> Optional[bool]:
    if isinstance(value, dict):
        for key in (
            "page_changed",
            "pageChanged",
            "screen_changed",
            "screenChanged",
            "页面是否切换",
            "頁面是否切換",
        ):
            if key in value:
                return _coerce_bool(value.get(key))
        return None
    return _coerce_bool(value)


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, dict):
        value = value.get("verified")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "y", "on", "是", "有", "已切换", "切换", "changed"}:
            return True
        if lowered in {
            "false",
            "no",
            "0",
            "n",
            "off",
            "否",
            "无",
            "未切换",
            "没有切换",
            "未发生切换",
            "no change",
            "unchanged",
            "not changed",
        }:
            return False
    return None


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
