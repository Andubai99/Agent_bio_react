from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol

from agent.runtime_logger import NullRunLogger, summarize_decision
from agent.types import AgentAction, Observation, ReasonerDecision, StepOutcome, TaskSpec
from modules.api_keys import get_api_key


# API key fill-in area. Prefer environment variables or API.txt for normal use.
# If needed, paste the DeepSeek V4 Pro API key here.
DEEPSEEK_V4_PRO_API_KEY = ""

# Placeholder for future model providers. Not implemented.
OTHER_MODEL_API_KEY = ""

DEEPSEEK_V4_PRO_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEEPSEEK_V4_PRO_MODEL = "deepseek-v4-pro"
DEFAULT_THINKING = "disabled"
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_TOKENS = 1024


@dataclass(frozen=True)
class ReasonerContext:
    task: TaskSpec
    observation: Observation
    history: List[StepOutcome] = field(default_factory=list)


class Reasoner(Protocol):
    def decide(self, context: ReasonerContext) -> ReasonerDecision:
        ...


class NullReasoner:
    def decide(self, context: ReasonerContext) -> ReasonerDecision:
        raise RuntimeError("No reasoner configured. Use ManualReasoner, ScriptedReasoner, or DeepSeekReasoner.")


class ScriptedReasoner:
    def __init__(self, decisions: Iterable[ReasonerDecision]):
        self._decisions = list(decisions)
        self._index = 0

    def decide(self, context: ReasonerContext) -> ReasonerDecision:
        if self._index >= len(self._decisions):
            return ReasonerDecision(
                thought="Script exhausted.",
                done=True,
                failure="SCRIPT_EXHAUSTED",
            )
        decision = self._decisions[self._index]
        self._index += 1
        return decision


class ManualReasoner:
    def decide(self, context: ReasonerContext) -> ReasonerDecision:
        print("\n--- 任务 ---")
        print(context.task.body)
        print("\n--- OmniParser 元素 ---")
        for element in context.observation.elements[:120]:
            print(f"{element.idx}: content={element.text!r} center={element.center} bbox={element.pixel_bbox}")
        print(
            '\n请输入 JSON，例如：'
            '{"actions":[{"action":"click","index":9,"coordinate":[321,401],"text":null},'
            '{"action":"input_text","index":9,"coordinate":[321,401],"text":"BioPharma test demo1"}]}'
        )
        raw = input("> ")
        parsed = json.loads(raw)
        return _decision_from_index_coordinate(parsed, context, model_name="manual")


class DeepSeekReasoner:
    """Reasoner backed by DeepSeek V4 Pro.

    The model is constrained to return only the selected OmniParser element index
    and that element's action plus center coordinate.
    """

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = DEEPSEEK_V4_PRO_ENDPOINT,
        model: str = DEEPSEEK_V4_PRO_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        thinking: str = DEFAULT_THINKING,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        transport: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        logger=None,
    ):
        if not api_key:
            raise ValueError("Missing DeepSeek API key. Fill DEEPSEEK_V4_PRO_API_KEY or set DEEPSEEK_API_KEY.")
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        self.transport = transport
        self.logger = logger or NullRunLogger()

    @classmethod
    def from_env(cls, logger=None) -> "DeepSeekReasoner":
        api_key = DEEPSEEK_V4_PRO_API_KEY.strip() or get_api_key("DEEPSEEK_API_KEY", marker="DeepSeek")
        return cls(
            api_key,
            endpoint=os.environ.get("DEEPSEEK_REASONER_ENDPOINT", DEEPSEEK_V4_PRO_ENDPOINT).strip()
            or DEEPSEEK_V4_PRO_ENDPOINT,
            model=os.environ.get("DEEPSEEK_REASONER_MODEL", DEEPSEEK_V4_PRO_MODEL).strip()
            or DEEPSEEK_V4_PRO_MODEL,
            timeout_seconds=float(os.environ.get("DEEPSEEK_REASONER_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
            thinking=DEFAULT_THINKING,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
            max_tokens=int(os.environ.get("DEEPSEEK_REASONER_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
            logger=logger,
        )

    def decide(self, context: ReasonerContext) -> ReasonerDecision:
        payload = self._build_payload(context)
        self.logger.log(
            f"调用推理模型：{self.model}",
            {
                "endpoint": self.endpoint,
                "thinking": self.thinking,
                "reasoning_effort": self.reasoning_effort,
                "max_tokens": self.max_tokens,
                "任务": context.task.id,
                "截图": context.observation.screenshot_path,
                "元素数量": len(context.observation.elements),
            },
        )
        response = self.transport(payload) if self.transport else self._post(payload)
        content = self._extract_content(response)
        self.logger.log(f"推理模型原始输出：{self.model}", content)
        parsed = self._parse_json_object(content)
        decision = _decision_from_index_coordinate(parsed, context, model_name=self.model)
        self.logger.log(f"推理模型解析结果：{self.model}", summarize_decision(decision))
        return decision

    def _build_payload(self, context: ReasonerContext) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_prompt(context)},
            ],
            "temperature": 0.0,
            "stream": False,
            "response_format": {"type": "json_object"},
            "thinking": {"type": self.thinking},
        }
        if self.thinking != "disabled":
            payload["reasoning_effort"] = self.reasoning_effort
        if self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens
        return payload

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
            raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {error_body}") from exc
        return json.loads(body)

    @staticmethod
    def _extract_content(response: Dict[str, Any]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"DeepSeek response missing choices: {response}")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise RuntimeError(f"DeepSeek response missing message: {response}")
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        reasoning_content = message.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            extracted = _extract_last_json_object_text(reasoning_content)
            if extracted:
                return extracted
        raise RuntimeError(f"DeepSeek response missing content: {response}")

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
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise RuntimeError(f"DeepSeek content is not a JSON object: {content}")
        return parsed


def _system_prompt() -> str:
    return (
        "你是桌面智能体的元素选择器。"
        "你只能根据用户输入中的 task、action_sequence_hint 和 omniparser_json 选择下一步要交互的元素。"
        "不要使用其他信息，不要解释，不要输出 Markdown。"
        "严格只返回一个 JSON 对象，并且只能包含 actions 这一个顶层字段："
        '{"actions":[{"action":"表示下一步动作","index":序号,"coordinate":[x,y],"text":文本或null}]}。'
        "即使只有一个动作，也必须放在 actions 数组里。"
        "actions 可以包含任意数量的动作对象，必须按 task 原文语序和 action_sequence_hint 的 order 排列，执行器会按数组顺序逐个执行。"
        "如果 action_sequence_hint 不为空，通常 actions 的数量和顺序应与它一致。"
        "action_sequence_hint 是 Agent 根据任务语句解析出的参考动作序列；每个 hint 的 target_hint 用来帮助你在 omniparser_json 中选择对应元素。"
        "如果 target_hint 指向输入框、选择框、checkbox、input box，应优先选择 content 明确包含这些控件语义的 synthetic 元素。"
        "例如“点击某输入框并输入某文本”，必须返回两个对象：先 click 对应目标元素，再 input_text 对应同一目标元素和文本。"
        "例如“点击 A，然后点击 B，再输入 C”，必须返回三个对象。"
        "每个对象的 action 字段用于描述该步动作，不强制固定取值。"
        "当 action 表示 input_text 或输入文本时，text 必须返回需要输入的文本。"
        "当 action 不是 input_text 或输入文本时，text 必须返回 null。"
        "当前项目执行层支持的可选动作参考值包括："
        "click=单击目标元素；"
        "double_click=双击目标元素；"
        "input_text=向目标元素输入文本；"
        "drag=拖拽目标元素；"
        "press_key=按下键盘按键；"
        "noop=不执行任何界面动作。"
        "每个对象的 index 必须来自 omniparser_json[].idx。"
        "每个对象的 coordinate 必须逐字复制同一个元素的 center 字段。"
        "禁止编造、估算、修正或转换坐标。"
        "如果当前任务已经完成且无需再点任何元素，返回 "
        '{"actions":[]}。'
    )


def _user_prompt(context: ReasonerContext) -> str:
    payload = {
        "task": _task_payload(context.task),
        "omniparser_json": [
            {
                "idx": element.idx,
                "content": element.content,
                "center": element.center,
            }
            for element in context.observation.elements
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _task_payload(task: TaskSpec) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "current_instruction": task.inputs.get("current_instruction", task.body),
        "current_instruction_index": task.inputs.get("current_instruction_index"),
        "current_instruction_total": task.inputs.get("current_instruction_total"),
        "action_sequence_hint": task.inputs.get("action_sequence_hint", []),
        "raw_task_body": task.body,
        "return_format": {
            "top_level_fields": ["actions"],
            "action_item_fields": ["action", "index", "coordinate", "text"],
        },
    }


def _decision_from_index_coordinate(
    parsed: Dict[str, Any],
    context: ReasonerContext,
    *,
    model_name: str,
) -> ReasonerDecision:
    return _decision_from_actions(
        _normalize_model_response_to_actions(parsed, model_name=model_name),
        context,
        model_name=model_name,
    )


def _normalize_model_response_to_actions(parsed: Dict[str, Any], *, model_name: str) -> Dict[str, Any]:
    if set(parsed) == {"actions"}:
        return parsed

    legacy_fields = {"action", "index", "coordinate", "text"}
    if set(parsed) == legacy_fields:
        if parsed["index"] is None and parsed["coordinate"] is None:
            return {"actions": []}
        return {"actions": [parsed]}

    raise RuntimeError(
        f'{model_name} must return only {{"actions": [...]}}. '
        "Legacy single-action responses are accepted only as a compatibility fallback."
    )


def _decision_from_actions(
    parsed: Dict[str, Any],
    context: ReasonerContext,
    *,
    model_name: str,
) -> ReasonerDecision:
    extra_keys = set(parsed) - {"actions"}
    if extra_keys:
        raise RuntimeError(f"{model_name} returned extra fields: {sorted(extra_keys)}")
    raw_actions = parsed["actions"]
    if not isinstance(raw_actions, list):
        raise RuntimeError(f'{model_name} must return {{"actions": [...]}}')

    actions: list[AgentAction] = []
    for sequence_index, raw_action in enumerate(raw_actions, start=1):
        if not isinstance(raw_action, dict):
            raise RuntimeError(f"{model_name} actions[{sequence_index}] must be an object")
        action = _agent_action_from_model_item(
            raw_action,
            context,
            model_name=model_name,
            sequence_index=sequence_index,
        )
        if action is not None:
            actions.append(action)

    if not actions:
        return ReasonerDecision(
            thought=f"{model_name} returned no further element selection.",
            done=True,
        )

    return ReasonerDecision(
        thought=f"{model_name} selected {len(actions)} action(s).",
        action=actions[0],
        actions=actions,
        expected_observation="",
    )


def _agent_action_from_model_item(
    item: Dict[str, Any],
    context: ReasonerContext,
    *,
    model_name: str,
    sequence_index: int,
) -> AgentAction | None:
    extra_keys = set(item) - {"action", "index", "coordinate", "text"}
    if extra_keys:
        raise RuntimeError(f"{model_name} actions[{sequence_index}] returned extra fields: {sorted(extra_keys)}")
    if "action" not in item or "index" not in item or "coordinate" not in item or "text" not in item:
        raise RuntimeError(
            f'{model_name} action item must contain only {{"action": ..., "index": ..., "coordinate": ..., "text": ...}}'
        )
    raw_action = item["action"]
    if item["index"] is None and item["coordinate"] is None:
        return None

    action_type = _execution_action_from_model_action(raw_action)
    element_idx = int(item["index"])
    coordinate = item["coordinate"]
    element = next((item for item in context.observation.elements if item.idx == element_idx), None)
    if element is None:
        raise RuntimeError(f"{model_name} selected unknown index={element_idx}.")
    if not _same_coordinate(coordinate, element.center):
        raise RuntimeError(
            f"{model_name} coordinate does not match element center for index={element_idx}: "
            f"returned={coordinate}, expected={element.center}"
        )
    return AgentAction(
        type=action_type,
        element_idx=element_idx,
        args={
            "model_action": raw_action,
            "model_coordinate": list(element.center),
            "sequence_index": sequence_index,
            **({"text": str(item["text"])} if action_type == "input_text" and item["text"] is not None else {}),
        },
    )


def _execution_action_from_model_action(value: Any) -> str:
    if value is None:
        return "noop"
    action = str(value).strip().lower()
    if action in {"", "noop", "no_op", "none", "null", "do_nothing", "do nothing", "不操作", "无操作", "什么都不做"}:
        return "noop"
    if action in {"double_click", "double click", "双击"}:
        return "double_click"
    if action in {"input_text", "type", "输入", "输入文本"}:
        return "input_text"
    if action in {"drag", "拖拽", "拖动"}:
        return "drag"
    if action in {"press_key", "key", "按键"}:
        return "press_key"
    if action in {
        "click",
        "left_click",
        "single_click",
        "点击",
        "单击",
        "select",
        "check",
        "checkbox",
        "选择",
        "选中",
        "勾选",
        "选择框",
        "复选框",
    }:
        return "click"
    if "双击" in action or "double" in action:
        return "double_click"
    if "输入" in action or "type" in action:
        return "input_text"
    if "拖" in action or "drag" in action:
        return "drag"
    if "按键" in action or "press" in action:
        return "press_key"
    if (
        "点击" in action
        or "单击" in action
        or "select" in action
        or "check" in action
        or "选择" in action
        or "选中" in action
        or "勾选" in action
    ):
        return "click"
    return "noop"


def _has_required_decision_fields(parsed: Dict[str, Any]) -> bool:
    if "actions" in parsed and isinstance(parsed.get("actions"), list):
        return True
    return "action" in parsed and "index" in parsed and "coordinate" in parsed and "text" in parsed


def _same_coordinate(value: Any, expected: tuple[int, int]) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    try:
        return (int(value[0]), int(value[1])) == expected
    except (TypeError, ValueError):
        return False


def _extract_last_json_object_text(text: str) -> str:
    candidates: list[tuple[str, Dict[str, Any]]] = []
    start: int | None = None
    depth = 0
    in_string = False
    escape = False

    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char != "}" or depth == 0:
            continue

        depth -= 1
        if depth == 0 and start is not None:
            raw = text[start : index + 1]
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                start = None
                continue
            if isinstance(parsed, dict):
                candidates.append((raw, parsed))
            start = None

    for raw, parsed in reversed(candidates):
        if _has_required_decision_fields(parsed):
            return raw
    return candidates[-1][0] if candidates else ""
