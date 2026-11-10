#!/usr/bin/env python3
"""Provider-scoped API key pool. Secrets are read only from environment variables."""
from __future__ import annotations
import argparse, datetime as dt, json, os, pathlib, sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "providers.json"
STATE = ROOT / "state" / "key-pools.json"

def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

def load_json(path: pathlib.Path, default: Any) -> Any:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return default

def save_json(data: Any) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2)+"\n", encoding="utf-8")
    try: tmp.chmod(0o600)
    except OSError: pass
    tmp.replace(STATE)

def config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))

def provider_for_model(model: str) -> str:
    if "/" not in model: raise ValueError("Model must be provider/model")
    return model.split("/", 1)[0]

def available_slots(provider: str) -> list[dict]:
    cfg = config()["providers"].get(provider)
    if not cfg: return []
    result=[]
    for idx, env_name in enumerate(cfg.get("key_slots", []), 1):
        value=os.getenv(env_name, "").strip()
        if value:
            result.append({"slot": idx, "env_name": env_name, "secret": value})
    return result

def eligible_keys(provider: str) -> list[dict]:
    slots=available_slots(provider)
    state=load_json(STATE,{})
    pstate=state.get(provider,{})
    current=pstate.get("current_slot")
    cooldowns=pstate.get("cooldowns",{})
    ready=[]
    for item in slots:
        until=cooldowns.get(str(item["slot"]))
        if until:
            try:
                if dt.datetime.fromisoformat(until) > now(): continue
            except ValueError: pass
        ready.append(item)
    if current is not None:
        ready.sort(key=lambda x: (x["slot"] != current, x["slot"]))
    return ready

def mark(provider: str, slot: int, outcome: str, cooldown_seconds: int=300) -> None:
    state=load_json(STATE,{})
    pstate=state.setdefault(provider,{"current_slot":None,"cooldowns":{},"stats":{}})
    pstate["current_slot"]=slot
    stats=pstate.setdefault("stats",{}).setdefault(str(slot),{"success":0,"failure":0,"last_outcome":None})
    if outcome == "success":
        stats["success"] += 1
        stats["last_outcome"]="success"
        pstate.setdefault("cooldowns",{}).pop(str(slot),None)
    else:
        stats["failure"] += 1
        stats["last_outcome"]=outcome
        pstate.setdefault("cooldowns",{})[str(slot)]=(now()+dt.timedelta(seconds=cooldown_seconds)).isoformat()
    save_json(state)

def list_status() -> int:
    cfg=config()
    state=load_json(STATE,{})
    for provider, p in cfg["providers"].items():
        slots=available_slots(provider)
        print(f"{provider}: {len(slots)} configured environment key(s)")
        pstate=state.get(provider,{})
        for item in slots:
            slot=str(item["slot"])
            until=pstate.get("cooldowns",{}).get(slot)
            active=False
            if until:
                try: active=dt.datetime.fromisoformat(until)>now()
                except ValueError: pass
            stat=pstate.get("stats",{}).get(slot,{})
            print(f"  slot {slot}: {'COOLDOWN' if active else 'READY'} "
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
    state=load_json(STATE,{})
    if args.provider:
        state.pop(args.provider,None)
    else:
        state={}
    save_json(state); print("Key-pool state reset."); return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr); raise SystemExit(2)
