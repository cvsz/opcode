#!/usr/bin/env python3
"""Rank models by observed success rate and latency using router JSONL events."""
from __future__ import annotations
import json, pathlib, statistics
ROOT = pathlib.Path(__file__).resolve().parents[1]
EVENTS = ROOT / "state" / "events.jsonl"
if not EVENTS.exists():
    print("No inference telemetry yet. Run routed tasks first.")
    raise SystemExit(0)
stats = {}
for line in EVENTS.read_text(encoding="utf-8").splitlines():
    try: event = json.loads(line)
    except json.JSONDecodeError: continue
    model = event.get("model")
    if not model: continue
    item = stats.setdefault(model, {"runs": 0, "success": 0, "latencies": [], "errors": {}})
    item["runs"] += 1
    item["success"] += int(event.get("status") == "success")
    if isinstance(event.get("latency_seconds"), (int, float)):
        item["latencies"].append(float(event["latency_seconds"]))
    category = event.get("error_category")
    if category and category != "success":
        item["errors"][category] = item["errors"].get(category, 0) + 1
rows = []
for model, item in stats.items():
    rate = item["success"] / item["runs"]
    latency = statistics.mean(item["latencies"]) if item["latencies"] else float("inf")
    # Reliability dominates; latency is a tie-breaker. Low sample counts are visible.
    score = rate * 100 - min(latency, 120) * 0.1
    rows.append((score, model, item, rate, latency))
rows.sort(reverse=True)
print(f"{'SCORE':>7} {'SUCCESS':>9} {'RUNS':>5} {'AVG_SEC':>8} MODEL")
for score, model, item, rate, latency in rows:
    avg = f"{latency:.2f}" if latency != float("inf") else "n/a"
    print(f"{score:7.2f} {rate:8.1%} {item['runs']:5d} {avg:>8} {model}")
print("\nNote: ranking is observational, task-mix dependent, and not a guarantee of future performance.")
