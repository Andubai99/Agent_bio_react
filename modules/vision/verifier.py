from __future__ import annotations

from typing import Any

from agent.runtime_logger import NullRunLogger
from agent.types import ImageInfo, RecognitionRequest, ToolResult
from modules.vision.providers import VisionResponseFormatError
from modules.vision.schemas import return_fields, return_json


POST_ACTION_SYSTEM_PROMPT = f"""你是本项目的动作后统一视觉验证层，只负责返回当前任务是否完成以及页面是否整体切换。

返回格式必须严格为：
{return_json()}

字段含义：
- verified: 当前截图或 after 区域是否已经满足当前任务指示。
- page_changed: 如果输入是 before/after/diff 合成图，表示 after 相比 before 是否发生整体页面切换；如果输入是单张截图，则返回 false。

判断规则：
- 判断 verified 时，只看当前截图或 after 区域是否满足当前任务指示。
- 判断 page_changed 时，只在输入包含 before/after/diff 合成图时比较 before 和 after。
- 如果仍是同一页面，只是输入框内容、焦点、选中状态、按钮状态等局部小变化，则 page_changed=false。

严格要求：
- 只返回一个 JSON 对象。
- 不要返回 Markdown。
- 不要返回解释文字、reason 字段、confidence 字段或其他额外字段。
"""


class VisionVerifier:
    def __init__(self, vision_provider: Any, *, logger=None):
        self.vision_provider = vision_provider
        self.logger = logger or NullRunLogger()

    def verify(self, expectation: str, screenshot: ImageInfo) -> ToolResult:
        return self.verify_completion(expectation, screenshot)

    def verify_completion(self, expectation: str, screenshot: ImageInfo) -> ToolResult:
        return self._verify_post_action(
            expectation,
            screenshot,
            user_prompt=_build_single_screenshot_user_prompt(expectation, screenshot),
            error_code="VISION_RESPONSE_FORMAT_ERROR",
            failure_code="VISION_VERIFY_FAILED",
        )

    def verify_post_action_comparison(self, expectation: str, comparison_screenshot: ImageInfo) -> ToolResult:
        return self._verify_post_action(
            expectation,
            comparison_screenshot,
            user_prompt=_build_comparison_user_prompt(expectation, comparison_screenshot),
            error_code="VISION_POST_ACTION_FORMAT_ERROR",
            failure_code="VISION_POST_ACTION_FAILED",
        )

    def _verify_post_action(
        self,
        expectation: str,
        screenshot: ImageInfo,
        *,
        user_prompt: str,
        error_code: str,
        failure_code: str,
    ) -> ToolResult:
        if not expectation:
            return ToolResult(True, "NO_EXPECTATION", "No verification expectation provided.")
        if self.vision_provider is None:
            return ToolResult(False, "VISION_NOT_CONFIGURED", "No vision provider configured.")

        request = RecognitionRequest(
            key="post_action",
            kind="post_action",
            prompt=expectation,
        )
        try:
            result = self.vision_provider.resolve(
                "react",
                request,
                screenshot,
                {
                    "system_prompt": POST_ACTION_SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                },
            )
        except VisionResponseFormatError as exc:
            return ToolResult(
                False,
                error_code,
                str(exc),
                data={"screenshot_path": screenshot.path},
            )
        except Exception as exc:
            return ToolResult(
                False,
                failure_code,
                str(exc),
                data={"screenshot_path": screenshot.path},
            )

        parsed = _parsed_payload(result.raw)
        format_error = _format_error(parsed, result.verified, result.page_changed)
        if format_error is not None:
            return ToolResult(
                False,
                error_code,
                format_error,
                data={
                    "verified": result.verified,
                    "page_changed": result.page_changed,
                    "raw": result.raw,
                    "screenshot_path": screenshot.path,
                },
            )

        ok = bool(result.verified)
        return ToolResult(
            ok=ok,
            code="VERIFIED" if ok else "VERIFY_FAILED",
            message="Verified." if ok else "Vision verification failed.",
            data={
                "verified": result.verified,
                "page_changed": result.page_changed,
                "raw": result.raw,
                "screenshot_path": screenshot.path,
            },
        )


def _build_single_screenshot_user_prompt(expectation: str, screenshot: ImageInfo) -> str:
    return (
        f"截图尺寸：{screenshot.width}x{screenshot.height}。\n"
        f"当前任务指示：{expectation}\n"
        "请判断当前截图是否已经完成任务指示。此输入只有一张截图，没有 before/after 对比，因此 page_changed 必须返回 false。"
    )


def _build_comparison_user_prompt(expectation: str, screenshot: ImageInfo) -> str:
    return (
        f"合成图尺寸：{screenshot.width}x{screenshot.height}。\n"
        "合成图从左到右依次为 before、after、diff。\n"
        f"当前任务指示：{expectation}\n"
        "请用 after 区域判断当前任务指示是否完成，并比较 before/after 判断整体页面是否切换。"
    )


def _format_error(parsed: dict[str, Any], verified: bool | None, page_changed: bool | None) -> str | None:
    required_fields = return_fields()
    missing = [field for field in required_fields if field not in parsed]
    if missing:
        return f"Vision response missing required field(s): {', '.join(missing)}."
    extra = sorted(set(parsed) - set(required_fields))
    if extra:
        return f"Vision response returned unexpected field(s): {', '.join(extra)}."
    if verified is None:
        return "Vision response field 'verified' must be boolean."
    if page_changed is None:
        return "Vision response field 'page_changed' must be boolean."
    return None


def _parsed_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        parsed = raw.get("parsed")
        if isinstance(parsed, dict):
            return parsed
        fallback_raw = raw.get("fallback_raw")
        if isinstance(fallback_raw, dict):
            return _parsed_payload(fallback_raw)
        return raw
    return {}
