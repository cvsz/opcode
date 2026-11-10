#!/usr/bin/env python3
"""OpenCode router with provider-scoped multi-API-key rotation and circuit breakers."""
from __future__ import annotations
import argparse, datetime as dt, json, os, pathlib, re, subprocess, sys, time, uuid
from typing import Any

ROOT=pathlib.Path(__file__).resolve().parents[1]
CONFIG=ROOT/"config"; STATE=ROOT/"state"
TASKS={"code","review","fast","vision","general"}
OPENCODE=os.getenv("OPENCODE","opencode")
FAIL_THRESHOLD=int(os.getenv("CIRCUIT_FAILURE_THRESHOLD","2"))
COOLDOWN=int(os.getenv("CIRCUIT_COOLDOWN_SECONDS","300"))
KEY_COOLDOWN=int(os.getenv("KEY_COOLDOWN_SECONDS","300"))

def utcnow(): return dt.datetime.now(dt.timezone.utc)
def iso(): return utcnow().isoformat()
def load(path,default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError): return default
def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
    try: tmp.chmod(0o600)
    except OSError: pass
    tmp.replace(path)
def emit(obj):
    STATE.mkdir(parents=True,exist_ok=True)
    obj["timestamp"]=iso()
    with (STATE/"events.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(obj,separators=(",",":"))+"\n")
def config(): return json.loads((CONFIG/"providers.json").read_text(encoding="utf-8"))
def classify(text,code):
    s=text.lower()
    rules=[
      ("quota_exhausted",r"insufficient_quota|quota_exceeded|billing|credits exhausted|usage limit|resource_exhausted"),
      ("rate_limited",r"rate.?limit|too many requests|\b429\b|throttl"),
      ("authentication",r"unauthorized|invalid api key|authentication|forbidden|\b401\b|\b403\b"),
      ("provider_outage",r"service unavailable|bad gateway|gateway timeout|\b5\d\d\b|connection reset|connection refused|timed out|network"),
      ("model_unavailable",r"model not found|unknown model|unsupported model"),
    ]
    for name,pat in rules:
      if re.search(pat,s): return name
    return "execution_error" if code else "success"
def model_available(model,available): return model in available
def task_models(task):
    if task not in TASKS: raise ValueError(f"Unsupported task: {task}")
    return [x.strip() for x in (CONFIG/f"{task}.txt").read_text().splitlines()
            if x.strip() and not x.lstrip().startswith("#")]
def get_available():
    p=subprocess.run([OPENCODE,"models"],capture_output=True,text=True)
    if p.returncode: raise RuntimeError(f"opencode models failed: {p.stderr.strip()}")
    return p.stdout+"\n"+p.stderr
def state_json(name,default): return load(STATE/name,default)
def circuit_is_open(model):
    item=state_json("circuits.json",{}).get(model,{})
    until=item.get("open_until")
    if not until: return False
    try: return dt.datetime.fromisoformat(until)>utcnow()
    except ValueError: return False
def circuit_update(model,category,success):
    path=STATE/"circuits.json"; state=load(path,{})
    item=state.setdefault(model,{"failures":0,"successes":0})
    if success:
      item["successes"]+=1; item["failures"]=0; item.pop("open_until",None); item["last_error"]=None
    else:
      item["failures"]+=1; item["last_error"]=category
      threshold=1 if category in {"authentication","model_unavailable"} else FAIL_THRESHOLD
      if item["failures"]>=threshold:
        item["open_until"]=(utcnow()+dt.timedelta(seconds=COOLDOWN)).isoformat()
    item["updated_at"]=iso(); atomic(path,state)

def key_candidates(provider):
    cfg=config()["providers"].get(provider)
    if not cfg: return [{"slot":0,"env_name":None,"secret":None,"auth_env":None}]
    auth_env=cfg["auth_env"]; state=load(STATE/"key-pools.json",{}).get(provider,{})
    cooldowns=state.get("cooldowns",{}); items=[]
    for slot,env_name in enumerate(cfg.get("key_slots",[]),1):
      secret=os.getenv(env_name,"").strip()
      if not secret: continue
      until=cooldowns.get(str(slot))
      if until:
        try:
          if dt.datetime.fromisoformat(until)>utcnow(): continue
        except ValueError: pass
      items.append({"slot":slot,"env_name":env_name,"secret":secret,"auth_env":auth_env})
    current=state.get("current_slot")
    items.sort(key=lambda x:(x["slot"]!=current,x["slot"]))
    if items: return items
    # If no pooled slots configured, preserve a normal provider env variable.
    if os.getenv(auth_env,"").strip():
      return [{"slot":0,"env_name":auth_env,"secret":os.environ[auth_env],"auth_env":auth_env}]
    return []

def key_mark(provider,slot,outcome):
    if slot==0: return
    path=STATE/"key-pools.json"; state=load(path,{})
    p=state.setdefault(provider,{"current_slot":slot,"cooldowns":{},"stats":{}})
    p["current_slot"]=slot
    stats=p.setdefault("stats",{}).setdefault(str(slot),{"success":0,"failure":0})
    if outcome=="success":
      stats["success"]+=1; p.setdefault("cooldowns",{}).pop(str(slot),None)
    else:
      stats["failure"]+=1
      p.setdefault("cooldowns",{})[str(slot)]=(utcnow()+dt.timedelta(seconds=KEY_COOLDOWN)).isoformat()
    atomic(path,state)

def route(args):
    if not args.prompt.strip(): raise ValueError("PROMPT is required")
    available=get_available()
    candidates=[args.model] if args.model else task_models(args.task)
    candidates=[m for m in candidates if model_available(m,available) and not circuit_is_open(m)]
    if args.max_models>0: candidates=candidates[:args.max_models]
    if not candidates: print("No eligible models. Check catalogue/circuit state.",file=sys.stderr); return 3
    if args.dry_run:
      for m in candidates: print(m)
      return 0
    task_id=str(uuid.uuid4())
    for model in candidates:
      provider=model.split("/",1)[0]
      keys=key_candidates(provider)
      if not keys:
        print(f"[router] no API key configured for provider={provider}; skipping {model}",file=sys.stderr)
        emit({"task_id":task_id,"task":args.task,"model":model,"status":"skipped","error_category":"missing_credentials"})
        continue
      for key in keys:
        env=os.environ.copy()
        if key["auth_env"] and key["secret"]:
          env[key["auth_env"]]=key["secret"]
        # Never print or log key material or the secret-bearing environment.
        print(f"[router] model={model} provider={provider} key_slot={key['slot']}",file=sys.stderr)
        start=time.monotonic()
        try:
          proc=subprocess.run([OPENCODE,"run","--model",model,args.prompt],
              capture_output=True,text=True,env=env,
              timeout=args.timeout if args.timeout>0 else None,check=False)
          output=(proc.stdout or "")+"\n"+(proc.stderr or ""); code=proc.returncode
        except subprocess.TimeoutExpired as exc:
          output=str(exc); code=124
        duration=round(time.monotonic()-start,3)
        category=classify(output,code); success=code==0
        circuit_update(model,category,success)
        if key["slot"]>0:
          # Rotate on provider/key-scoped transient/auth/quota errors only.
          if success: key_mark(provider,key["slot"],"success")
          elif category == "authentication":
            # Rotate only for invalid/expired credentials. Quota and rate-limit
            # responses are provider usage controls, not reasons to hop accounts.
            key_mark(provider,key["slot"],category)
        emit({"task_id":task_id,"task":args.task,"model":model,"provider":provider,
              "key_slot":key["slot"],"status":"success" if success else "failure",
              "error_category":category,"exit_code":code,"latency_seconds":duration})
        if success:
          if proc.stdout: sys.stdout.write(proc.stdout)
          if proc.stderr: sys.stderr.write(proc.stderr)
          print(f"\n[router] success model={model} latency={duration}s",file=sys.stderr)
          return 0
        # Key-level retry only when classification suggests credential/quota/provider problem.
        # If model-specific error, stop cycling keys and continue next model.
        if category != "authentication":
          break
        print(f"[router] {category}; switching provider key",file=sys.stderr)
      # Continue to next model after key pool exhausted.
    print("All model/key attempts failed.",file=sys.stderr); return 1

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("check")
    plan=sub.add_parser("plan"); plan.add_argument("--task",default=os.getenv("TASK","code"))
    r=sub.add_parser("route"); r.add_argument("--task",default=os.getenv("TASK","code"))
    r.add_argument("--prompt",default=os.getenv("PROMPT","")); r.add_argument("--model",default=os.getenv("MODEL",""))
    r.add_argument("--dry-run",action="store_true",default=os.getenv("DRY_RUN","0")=="1")
    r.add_argument("--max-models",type=int,default=int(os.getenv("MAX_MODELS","0")))
    r.add_argument("--timeout",type=int,default=int(os.getenv("TIMEOUT_SECONDS","180")))
    sub.add_parser("health"); sub.add_parser("reset")
    a=p.parse_args(); STATE.mkdir(parents=True,exist_ok=True)
    if a.cmd=="check":
      avail=get_available(); reg=[x.strip() for x in (CONFIG/"free-models.txt").read_text().splitlines() if x.strip() and not x.startswith("#")]
      found=0
      for m in reg:
        ok=m in avail; found+=int(ok); print(f"[{'AVAILABLE' if ok else 'MISSING'}] {m}")
      print(f"{found}/{len(reg)} found"); return 0
    if a.cmd=="plan":
      avail=get_available()
      for m in task_models(a.task):
        status="CIRCUIT_OPEN" if circuit_is_open(m) else ("READY" if m in avail else "NOT_LISTED")
        print(f"[{status}] {m}")
      return 0
    if a.cmd=="route":
      if a.max_models<0 or a.timeout<0: raise ValueError("limits must be non-negative")
      return route(a)
    if a.cmd=="health":
      print(json.dumps({"circuits":state_json("circuits.json",{}),"keys":state_json("key-pools.json",{})},indent=2)); return 0
    if a.cmd=="reset":
      atomic(STATE/"circuits.json",{}); atomic(STATE/"key-pools.json",{}); print("Circuit and key-pool state reset."); return 0
    return 2
if __name__=="__main__":
    try: raise SystemExit(main())
    except (OSError,ValueError,RuntimeError,KeyError,json.JSONDecodeError) as e:
      print(f"ERROR: {e}",file=sys.stderr); raise SystemExit(2)
