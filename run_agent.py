from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.failure_handler import (
    FailureContext,
    FailurePolicy,
    FailureStage,
    summarize_failure_decision,
)
from agent.react_agent import ReActAgent
from agent.runtime_logger import ConsoleRunLogger, NullRunLogger
from agent.task_loader import load_task_by_id
from agent.types import ReasonerDecision
from modules.reasoner import DeepSeekReasoner, ManualReasoner, ScriptedReasoner
from modules.ui_parser.omniparser import OmniParserClient, OmniParserConfig, OmniParserService
from modules.vision.providers import create_vision_provider
from modules.vision.verifier import VisionVerifier
from tools.maa_desktop import MaaDesktop


ROOT = Path(__file__).resolve().parent
TASKS_DIR = ROOT / "tasks"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Bio ReAct agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--task", default="biopharma_protein")
    run_parser.add_argument(
        "--reasoner",
        choices=["manual", "script", "deepseek"],
        default="manual",
    )
    run_parser.add_argument("--script", type=Path)
    run_parser.add_argument("--max-steps", type=int, default=30)
    run_parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Deprecated; final output is always a compact summary.",
    )
    run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable per-step runtime logs.",
    )

    args = parser.parse_args()
    if args.command == "run":
        return _run(args)
    raise RuntimeError(args.command)


def _run(args) -> int:
    logger = NullRunLogger() if args.quiet else ConsoleRunLogger()
    logger.log(
        "准备运行项目",
        {
            "root": str(ROOT),
            "task": args.task,
            "reasoner": args.reasoner,
        },
    )
    try:
        task = load_task_by_id(TASKS_DIR, args.task)
    except Exception as exc:
        return _handle_startup_failure(logger, args, "TASK_LOAD_FAILED", str(exc))
    logger.log("加载任务文档", {"path": str(task.path), "title": task.title})
    try:
        reasoner = _build_reasoner(args, logger)
    except Exception as exc:
        return _handle_startup_failure(logger, args, "REASONER_SETUP_FAILED", str(exc))
    logger.log("推理器已配置", {"reasoner": args.reasoner})

    try:
        omni_config = OmniParserConfig.from_env()
        service = OmniParserService(omni_config, logger=logger)
        service.ensure_running()
    except Exception as exc:
        return _handle_startup_failure(logger, args, "OMNIPARSER_UNAVAILABLE", str(exc))

    try:
        try:
            vision = create_vision_provider(logger=logger)
            vision_verifier = VisionVerifier(vision, logger=logger)
            desktop = MaaDesktop()
            ui_parser = OmniParserClient(omni_config, logger=logger)
            agent = ReActAgent(
                task=task,
                desktop=desktop,
                ui_parser=ui_parser,
                reasoner=reasoner,
                vision_verifier=vision_verifier,
                max_steps=args.max_steps,
                logger=logger,
            )
        except Exception as exc:
            return _handle_startup_failure(logger, args, "AGENT_SETUP_FAILED", str(exc))
        result = agent.run()
    finally:
        service.shutdown_if_owned()

    logger.log(
        "最终运行结果",
        {
            "ok": result.ok,
            "code": result.code,
            "message": result.message,
            "steps": len(result.steps),
        },
    )
    print(json.dumps(_result_summary_to_dict(result), ensure_ascii=False))
    return 0 if result.ok else 1


def _build_reasoner(args, logger=None):
    if args.reasoner == "manual":
        return ManualReasoner()
    if args.reasoner == "deepseek":
        return DeepSeekReasoner.from_env(logger=logger)
    if args.script is None:
        raise ValueError("--script is required when --reasoner script is used")
    values = json.loads(args.script.read_text(encoding="utf-8-sig"))
    if not isinstance(values, list):
        raise ValueError("script must be a JSON list of decisions")
    return ScriptedReasoner(ReasonerDecision.from_dict(item) for item in values)


def _handle_startup_failure(logger, args, code: str, message: str) -> int:
    failure = FailureContext(
        stage=FailureStage.STARTUP,
        code=code,
        message=message,
    )
    decision = FailurePolicy().decide(failure)
    logger.log("错误处理决策", summarize_failure_decision(failure, decision))
    return _print_startup_failure(args, code, message)


def _print_startup_failure(args, code: str, message: str) -> int:
    payload = {"ok": False, "code": code, "message": message, "steps": 0}
    print(json.dumps(payload, ensure_ascii=False))
    return 1


def _result_summary_to_dict(result) -> dict:
    return {
        "ok": result.ok,
        "code": result.code,
        "message": result.message,
        "steps": len(result.steps),
    }


if __name__ == "__main__":
    raise SystemExit(main())
