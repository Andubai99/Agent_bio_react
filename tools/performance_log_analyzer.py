from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


TIMESTAMP_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]")


@dataclass(frozen=True)
class SpanSummary:
    name: str
    count: int
    total_seconds: int

    @property
    def average_seconds(self) -> float:
        return self.total_seconds / self.count if self.count else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Agent_bio_react runtime log latency.")
    parser.add_argument("log_path", type=Path)
    args = parser.parse_args()

    report = analyze_log(args.log_path)
    print(report)
    return 0


def analyze_log(log_path: Path) -> str:
    lines = log_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    timestamps = [(seconds, line) for line in lines if (seconds := _timestamp_seconds(line)) is not None]

    total_seconds = _total_runtime_seconds(timestamps)
    omni_startup = _paired_total(lines, "Starting OmniParser service", "OmniParser service started")
    omni_parse = _paired_total(lines, "Calling OmniParser UI parser", "OmniParser UI parse complete")
    reasoner = _paired_total(lines, "调用推理模型：", "推理模型原始输出：")
    vision = _paired_total(lines, "调用视觉模型：", "视觉模型原始输出：")
    cached_observations = sum("Screenshot parsed by cached observation" in line for line in lines)

    summaries = [
        SpanSummary("OmniParser 冷启动", omni_startup[0], omni_startup[1]),
        SpanSummary("OmniParser UI 解析", omni_parse[0], omni_parse[1]),
        SpanSummary("DeepSeek 推理", reasoner[0], reasoner[1]),
        SpanSummary("Qwen 视觉验证", vision[0], vision[1]),
    ]

    known_seconds = sum(item.total_seconds for item in summaries)
    other_seconds = max(total_seconds - known_seconds, 0)
    summaries.append(SpanSummary("其他流程", 1 if other_seconds else 0, other_seconds))

    output = [
        "# 性能日志统计",
        "",
        f"- 日志：`{log_path}`",
        f"- 总耗时：{_format_seconds(total_seconds)}",
        f"- 缓存 observation 复用次数：{cached_observations}",
        "",
        "| 阶段 | 次数 | 总耗时 | 平均耗时 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for item in summaries:
        average = f"{item.average_seconds:.1f}s" if item.count else "-"
        output.append(
            f"| {item.name} | {item.count} | {_format_seconds(item.total_seconds)} | {average} |"
        )
    return "\n".join(output)


def _timestamp_seconds(line: str) -> int | None:
    match = TIMESTAMP_RE.match(line)
    if not match:
        return None
    hours, minutes, seconds = (int(value) for value in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _total_runtime_seconds(timestamps: list[tuple[int, str]]) -> int:
    start = next((seconds for seconds, line in timestamps if "status  running" in line), None)
    end = None
    for seconds, line in timestamps:
        if any(status in line for status in ("status  succeeded", "status  failed", "status  stopped")):
            end = seconds
    if start is None and timestamps:
        start = timestamps[0][0]
    if end is None and timestamps:
        end = timestamps[-1][0]
    if start is None or end is None:
        return 0
    if end < start:
        end += 24 * 3600
    return end - start


def _paired_total(lines: list[str], start_marker: str, end_marker: str) -> tuple[int, int]:
    active_start: int | None = None
    count = 0
    total = 0
    for line in lines:
        timestamp = _timestamp_seconds(line)
        if timestamp is None:
            continue
        if start_marker in line and active_start is None:
            active_start = timestamp
            continue
        if end_marker in line and active_start is not None:
            end = timestamp
            if end < active_start:
                end += 24 * 3600
            total += max(end - active_start, 0)
            count += 1
            active_start = None
    return count, total


def _format_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, remaining = divmod(seconds, 60)
    return f"{minutes}m{remaining:02d}s"


if __name__ == "__main__":
    raise SystemExit(main())
