#!/usr/bin/env python3
"""Validate and summarize completed Agent strategy benchmark JSONL records."""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "artifacts" / "benchmarks" / "agent_strategy_trials.jsonl"
DEFAULT_DIR = ROOT / "artifacts" / "benchmarks"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    result = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(result, 3)


def average(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def load(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {number}: {exc}") from exc
    if not rows:
        raise ValueError("no benchmark records found")
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["task_id"], row["strategy"], int(row["repetition"]))].append(row)
    selected: list[dict[str, Any]] = []
    invalid: list[tuple[str, str, int]] = []
    for key, versions in grouped.items():
        if len(versions) == 1:
            selected.append(versions[0])
            continue
        prior = versions[:-1]
        if all(
            row.get("failure_type") == "environment_error"
            and row.get("run_status") is None
            for row in prior
        ):
            selected.append(versions[-1])
        else:
            invalid.append(key)
    if invalid:
        raise ValueError(f"duplicate completed task/strategy/repetition records: {invalid[:5]}")
    return selected


def numeric(rows: list[dict[str, Any]], name: str) -> list[float]:
    return [float(row[name]) for row in rows if row.get(name) is not None]


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluable = [row for row in rows if row.get("failure_type") != "environment_error"]
    result: dict[str, Any] = {
        "trials": len(rows),
        "successes": sum(bool(row["success"]) for row in rows),
        "evaluable_trials": len(evaluable),
        "evaluable_successes": sum(bool(row["success"]) for row in evaluable),
    }
    result["success_rate"] = result["successes"] / result["trials"] if rows else None
    result["evaluable_success_rate"] = (
        result["evaluable_successes"] / result["evaluable_trials"]
        if evaluable
        else None
    )
    latency = numeric(evaluable, "latency_ms")
    result["latency_ms"] = {
        "average": average(latency),
        "p50": percentile(latency, 0.5),
        "p95": percentile(latency, 0.95),
    }
    for name in ("model_calls", "tool_calls", "planning_calls", "turns", "total_tokens"):
        result[f"average_{name}"] = average(numeric(evaluable, name))
    result["failure_types"] = dict(
        sorted(Counter(str(row["failure_type"]) for row in rows if row.get("failure_type")).items())
    )
    return result


def validate_balance(rows: list[dict[str, Any]], allow_partial: bool) -> dict[str, Any]:
    coverage = Counter((row["level"], row["strategy"]) for row in rows)
    repetitions = sorted({int(row["repetition"]) for row in rows})
    expected_per_cell = 10 * len(repetitions)
    complete = all(
        coverage[(level, strategy)] == expected_per_cell
        for level in ("simple", "medium", "complex")
        for strategy in ("react", "plan_and_execute")
    )
    if not complete and not allow_partial:
        raise ValueError(
            f"incomplete/unbalanced experiment: coverage={dict(coverage)}, "
            "pass --allow-partial only for an explicitly preliminary report"
        )
    environment_keys = ("model", "provider", "temperature", "max_turns", "manifest_sha256")
    variants = {
        key: sorted({str(row.get("environment", {}).get(key)) for row in rows})
        for key in environment_keys
    }
    changed = {key: values for key, values in variants.items() if len(values) != 1}
    if changed:
        raise ValueError(f"fairness configuration changed across trials: {changed}")
    return {
        "complete_balanced_design": complete,
        "coverage": {f"{level}/{strategy}": count for (level, strategy), count in sorted(coverage.items())},
        "repetitions": repetitions,
        "fairness_config": {key: values[0] for key, values in variants.items()},
    }


def build_summary(rows: list[dict[str, Any]], allow_partial: bool) -> dict[str, Any]:
    design = validate_balance(rows, allow_partial)
    grouped: dict[str, dict[str, Any]] = defaultdict(dict)
    for level in ("simple", "medium", "complex"):
        for strategy in ("react", "plan_and_execute"):
            selected = [row for row in rows if row["level"] == level and row["strategy"] == strategy]
            if selected:
                grouped[level][strategy] = metrics(selected)
    paired: dict[str, dict[str, int]] = {}
    for level in ("simple", "medium", "complex"):
        by_key: dict[tuple[str, int], dict[str, bool]] = defaultdict(dict)
        for row in rows:
            if row["level"] == level and row.get("failure_type") != "environment_error":
                by_key[(row["task_id"], int(row["repetition"]))][row["strategy"]] = bool(row["success"])
        pairs = [value for value in by_key.values() if len(value) == 2]
        paired[level] = {
            "pairs": len(pairs),
            "both_success": sum(item["react"] and item["plan_and_execute"] for item in pairs),
            "react_only_success": sum(item["react"] and not item["plan_and_execute"] for item in pairs),
            "plan_only_success": sum(not item["react"] and item["plan_and_execute"] for item in pairs),
            "both_failed": sum(not item["react"] and not item["plan_and_execute"] for item in pairs),
        }
    return {
        "experiment": "agent_strategy",
        "design": design,
        "overall": {
            strategy: metrics([row for row in rows if row["strategy"] == strategy])
            for strategy in ("react", "plan_and_execute")
        },
        "by_level": dict(grouped),
        "paired_outcomes": paired,
        "environment_failure_records": sum(
            row.get("failure_type") == "environment_error" for row in rows
        ),
        "token_usage_available": any(row.get("total_tokens") is not None for row in rows),
    }


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "experiment", "run_group", "task_id", "level", "strategy", "repetition",
        "success", "latency_ms", "model_calls", "tool_calls", "planning_calls", "turns",
        "input_tokens", "output_tokens", "total_tokens", "failure_type", "failure_reason",
        "run_id", "workflow_id", "conversation_id", "run_status", "workspace_relative_path",
        "started_at", "completed_at",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "unavailable"
    return f"{value * 100:.2f}%" if percent else str(value)


def write_report(summary: dict[str, Any], path: Path) -> None:
    config = summary["design"]["fairness_config"]
    lines = [
        "# ReAct vs Plan-and-Execute Benchmark Report",
        "",
        "## Design",
        "",
        f"- Complete balanced design: **{summary['design']['complete_balanced_design']}**",
        f"- Repetitions: `{summary['design']['repetitions']}`",
        f"- Model: `{config['model']}`",
        f"- Provider: `{config['provider']}`",
        f"- Temperature: `{config['temperature']}`",
        f"- Max turns: `{config['max_turns']}`",
        f"- Manifest SHA-256: `{config['manifest_sha256']}`",
        "",
        "## Results by Difficulty",
        "",
        "| Level | Strategy | Recorded | Evaluable | Evaluable Success Rate | Avg Model Calls | Avg Tool Calls | Avg Turns | P50 Latency ms | P95 Latency ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for level in ("simple", "medium", "complex"):
        for strategy in ("react", "plan_and_execute"):
            item = summary["by_level"].get(level, {}).get(strategy)
            if not item:
                continue
            lines.append(
                f"| {level} | {strategy} | {item['trials']} | {item['evaluable_trials']} "
                f"| {fmt(item['evaluable_success_rate'], percent=True)} "
                f"| {fmt(item['average_model_calls'])} | {fmt(item['average_tool_calls'])} "
                f"| {fmt(item['average_turns'])} | {fmt(item['latency_ms']['p50'])} "
                f"| {fmt(item['latency_ms']['p95'])} |"
            )
    lines.extend(["", "## Failure Classification", ""])
    lines.append(
        f"- Environment-failure records excluded from evaluable metrics: "
        f"**{summary['environment_failure_records']}**"
    )
    for strategy, item in summary["overall"].items():
        lines.append(f"- `{strategy}`: `{json.dumps(item['failure_types'], ensure_ascii=False)}`")
    lines.extend([
        "",
        "## Interpretation Boundary",
        "",
        "This report contains deterministic grader outcomes and operation counts. It does not infer",
        "that either strategy is universally superior. Compare simple-task overhead, medium/complex",
        "paired outcomes, and failure classes together. Token usage remains unavailable unless the",
        "provider/runtime exposes reliable per-run usage.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    rows = load(args.input)
    summary = build_summary(rows, args.allow_partial)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "agent_strategy_trials.csv"
    json_path = args.output_dir / "agent_strategy_summary.json"
    report_path = args.output_dir / "agent_strategy_report.md"
    write_csv(rows, csv_path)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(summary, report_path)
    print(json.dumps({"csv": str(csv_path), "summary": str(json_path), "report": str(report_path)}, indent=2))


if __name__ == "__main__":
    main()
