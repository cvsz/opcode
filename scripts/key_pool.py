#!/usr/bin/env python3
"""Provider-scoped API key pool. Secrets are read only from environment variables."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "providers.json"
STATE = ROOT / "state" / "key-pools.json"

def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

def load_json(path: pathlib.Path, default: Any) -> Any:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError): return default
    return value if isinstance(value, type(default)) else default

def load_state() -> dict:
    state = load_json(STATE, {})
    return state if isinstance(state, dict) else {}

def save_json(data: Any) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2)+"\n", encoding="utf-8")
    with contextlib.suppress(OSError): tmp.chmod(0o600)
    tmp.replace(STATE)

def config() -> dict:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}

def provider_for_model(model: str) -> str:
    if "/" not in model: raise ValueError("Model must be provider/model")
    return model.split("/", 1)[0]

def available_slots(provider: str) -> list[dict]:
    cfg = config().get("providers", {}).get(provider)
    if not isinstance(cfg, dict): return []
    result=[]
    for idx, env_name in enumerate(cfg.get("key_slots", []), 1):
        value=os.getenv(env_name, "").strip()
        if value:
            result.append({"slot": idx, "env_name": env_name, "secret": value})
    return result

def _cooldown_active(until: Any) -> bool:
    if not until: return False
    try: return dt.datetime.fromisoformat(until) > now()
    except (ValueError, TypeError): return False

def eligible_keys(provider: str) -> list[dict]:
    slots=available_slots(provider)
    state=load_state()
    pstate=state.get(provider, {})
    if not isinstance(pstate, dict): pstate = {}
    current=pstate.get("current_slot")
    cooldowns=pstate.get("cooldowns", {})
    if not isinstance(cooldowns, dict): cooldowns = {}
    ready=[]
    for item in slots:
        if _cooldown_active(cooldowns.get(str(item["slot"]))): continue
        ready.append(item)
    if current is not None:
        ready.sort(key=lambda x: (x["slot"] != current, x["slot"]))
    return ready

def mark(provider: str, slot: int, outcome: str, cooldown_seconds: int=300) -> None:
    try: cooldown_seconds = max(0, int(cooldown_seconds))
    except (TypeError, ValueError): cooldown_seconds = 300
    state=load_state()
    pstate=state.get(provider)
    if not isinstance(pstate, dict):
        pstate={"current_slot": None, "cooldowns": {}, "stats": {}}
        state[provider]=pstate
    pstate["current_slot"]=slot
    cooldowns=pstate.get("cooldowns")
    if not isinstance(cooldowns, dict): cooldowns={}; pstate["cooldowns"]=cooldowns
    stats=pstate.get("stats")
    if not isinstance(stats, dict): stats={}; pstate["stats"]=stats
    slot_stats=stats.get(str(slot))
    if not isinstance(slot_stats, dict):
        slot_stats={"success": 0, "failure": 0, "last_outcome": None}
        stats[str(slot)]=slot_stats
    if outcome == "success":
        slot_stats["success"] = slot_stats.get("success", 0) + 1
        slot_stats["last_outcome"]="success"
        cooldowns.pop(str(slot), None)
    else:
        slot_stats["failure"] = slot_stats.get("failure", 0) + 1
        slot_stats["last_outcome"]=outcome
        cooldowns[str(slot)]=(now()+dt.timedelta(seconds=cooldown_seconds)).isoformat()
    save_json(state)

def list_status() -> int:
    cfg=config()
    state=load_state()
    for provider in cfg.get("providers", {}):
        slots=available_slots(provider)
        print(f"{provider}: {len(slots)} configured environment key(s)")
        pstate=state.get(provider, {})
        if not isinstance(pstate, dict): pstate = {}
        cooldowns=pstate.get("cooldowns", {})
        if not isinstance(cooldowns, dict): cooldowns = {}
        stats=pstate.get("stats", {})
        if not isinstance(stats, dict): stats = {}
        for item in slots:
            slot=str(item["slot"])
            stat=stats.get(slot, {})
            if not isinstance(stat, dict): stat = {}
            print(f"  slot {slot}: {'COOLDOWN' if _cooldown_active(cooldowns.get(slot)) else 'READY'} "
                  f"success={stat.get('success',0)} failure={stat.get('failure',0)} "
                  f"source={item['env_name']}")
    return 0

def main() -> int:
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("status")
    reset=sub.add_parser("reset"); reset.add_argument("--provider")
    args=p.parse_args()
    if args.cmd=="status": return list_status()
    state=load_state()
    if args.provider:
        state.pop(args.provider,None)
    else:
        state={}
    save_json(state); print("Key-pool state reset."); return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr); raise SystemExit(2) from exc
