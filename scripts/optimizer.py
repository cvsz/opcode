#!/usr/bin/env python3
"""Rank models by observed success rate and latency using router JSONL events."""
from __future__ import annotations

import json
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVENTS = ROOT / "state" / "events.jsonl"
LOW_SAMPLE = 5


def collect_stats(path: pathlib.Path) -> dict:
    """Aggregate per-model runs. Only success/failure events count as runs; bad lines are ignored."""
    stats: dict = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        model = event.get("model")
        if not model or event.get("status") not in ("success", "failure"):
            continue
        item = stats.setdefault(model, {"runs": 0, "success": 0, "latencies": [], "errors": {}})
        item["runs"] += 1
        item["success"] += int(event["status"] == "success")
        if isinstance(event.get("latency_seconds"), (int, float)):
            item["latencies"].append(float(event["latency_seconds"]))
        category = event.get("error_category")
        if category and category != "success":
            item["errors"][category] = item["errors"].get(category, 0) + 1
    return stats


def main(path: pathlib.Path = EVENTS) -> int:
    stats = collect_stats(path)
    if not stats:
        print("No inference telemetry yet. Run routed tasks first.")
        return 0
    rows = []
    for model, item in stats.items():
        rate = item["success"] / item["runs"]
        smooth = (item["success"] + 1) / (item["runs"] + 2)  # Laplace: 1/1 does not outrank 48/50
        latency = statistics.mean(item["latencies"]) if item["latencies"] else float("inf")
        # Reliability dominates; latency is a tie-breaker. Low sample counts are visible.
        score = smooth * 100 - min(latency, 120) * 0.1
        rows.append((score, model, item, rate, latency))
    rows.sort(key=lambda r: r[:2], reverse=True)
    print(f"{'SCORE':>7} {'SUCCESS':>9} {'RUNS':>5} {'AVG_SEC':>8} MODEL")
    for score, model, item, rate, latency in rows:
        avg = f"{latency:.2f}" if latency != float("inf") else "n/a"
        top = max(item["errors"].items(), key=lambda kv: kv[1])[0] if item["errors"] else ""
        flags = ("  (low sample)" if item["runs"] < LOW_SAMPLE else "") + (f"  top_error={top}" if top else "")
        print(f"{score:7.2f} {rate:8.1%} {item['runs']:5d} {avg:>8} {model}{flags}")
    print("\nNote: ranking is observational, task-mix dependent, and not a guarantee of future performance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
