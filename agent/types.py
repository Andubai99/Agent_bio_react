from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ImageInfo:
    path: Optional[str]
    width: int
    height: int
    resolution: tuple[int, int]
    image: Optional[Any] = None


@dataclass(frozen=True)
class RecognitionRequest:
    key: str
    kind: str
    prompt: str
    required: bool = True


@dataclass
class RecognitionResult:
    key: str
    kind: str
    point: Optional[tuple[int, int]] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    region: Optional[tuple[int, int, int, int]] = None
    verified: Optional[bool] = None
    page_changed: Optional[bool] = None
    raw: Any = None


@dataclass(frozen=True)
class UIElement:
    idx: int
    type: str
    content: Optional[str]
    bbox: tuple[float, float, float, float]
    pixel_bbox: tuple[int, int, int, int]
    center: tuple[int, int]
    interactive: bool
    source: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content or ""


@dataclass(frozen=True)
class TaskSpec:
    id: str
    title: str
    description: str
    path: Path
    inputs: Dict[str, Any]
    allowed_tools: List[str]
    start_conditions: List[str]
    success_criteria: List[str]
    safety_rules: List[str]
    body: str
    frontmatter: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskStepSpec:
    number: int
    action_text: str
    window_text: str
    verification_text: str
    raw: str


@dataclass(frozen=True)
class WindowTransition:
    source_alias: Optional[str]
    source_title: Optional[str]
    target_alias: Optional[str]
    target_title: Optional[str]
    raw: str


@dataclass(frozen=True)
class ParsedTaskBody:
    window_definitions: Dict[str, str]
    steps: List[TaskStepSpec]


@dataclass(frozen=True)
class Observation:
    summary: str
    screenshot_path: Optional[str] = None
    window_title: Optional[str] = None
    image_size: tuple[int, int] = (0, 0)
    resolution: tuple[int, int] = (0, 0)
    state: Dict[str, Any] = field(default_factory=dict)
    elements: List[UIElement] = field(default_factory=list)


@dataclass(frozen=True)
class AgentAction:
    type: str
    element_idx: Optional[int] = None
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    code: str = "OK"
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReasonerDecision:
    thought: str
    action: Optional[AgentAction] = None
    expected_observation: str = ""
    done: bool = False
    failure: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ReasonerDecision":
        action_value = value.get("action")
        action = None
        if action_value is not None:
            if not isinstance(action_value, dict):
                raise ValueError("decision.action must be an object")
            action_type = action_value.get("type")
            if not isinstance(action_type, str) or not action_type:
                raise ValueError("decision.action.type is required")
            raw_element_idx = action_value.get("element_idx")
            element_idx = None
            if raw_element_idx is not None:
                element_idx = int(raw_element_idx)
            args = action_value.get("args", {})
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise ValueError("decision.action.args must be an object")
            action = AgentAction(type=action_type, element_idx=element_idx, args=args)

        return cls(
            thought=str(value.get("thought", "")),
            action=action,
            expected_observation=str(value.get("expected_observation", "")),
            done=bool(value.get("done", False)),
            failure=value.get("failure"),
        )


@dataclass
class StepOutcome:
    index: int
    observation: Observation
    decision: ReasonerDecision
    action_result: Optional[ToolResult]
    verification: Optional[ToolResult]


@dataclass
class AgentRunResult:
    ok: bool
    code: str
    message: str
    steps: List[StepOutcome]
