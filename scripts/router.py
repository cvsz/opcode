#!/usr/bin/env python3
"""OpenCode router with provider-scoped multi-API-key rotation and circuit breakers."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid

ROOT=pathlib.Path(__file__).resolve().parents[1]
CONFIG=ROOT/"config"; STATE=ROOT/"state"
TASKS={"code","review","fast","vision","general"}
OPENCODE=os.getenv("OPENCODE","opencode")

def _int_env(name,default):
    try: value=int(os.getenv(name,str(default)))
    except (TypeError,ValueError): return default
    return value

FAIL_THRESHOLD=max(1,_int_env("CIRCUIT_FAILURE_THRESHOLD",2))
COOLDOWN=max(0,_int_env("CIRCUIT_COOLDOWN_SECONDS",300))
QUOTA_COOLDOWN=max(0,_int_env("CIRCUIT_QUOTA_COOLDOWN_SECONDS",3600))
KEY_COOLDOWN=max(0,_int_env("KEY_COOLDOWN_SECONDS",300))

def utcnow(): return dt.datetime.now(dt.timezone.utc)
def iso(): return utcnow().isoformat()
def load(path,default):
    """Load JSON, returning default when missing, invalid, or the wrong shape."""
    try: value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,UnicodeDecodeError): return default
    return value if isinstance(value,type(default)) else default
def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
    with contextlib.suppress(OSError): tmp.chmod(0o600)
    tmp.replace(path)
def emit(obj):
    STATE.mkdir(parents=True,exist_ok=True)
    obj=dict(obj)
    obj.setdefault("event_id",str(uuid.uuid4()))
    obj["timestamp"]=iso()
    with (STATE/"events.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(obj,separators=(",",":"))+"\n")
def config(): return json.loads((CONFIG/"providers.json").read_text(encoding="utf-8"))
def last_line(output,secret=None,limit=200):
    """Last non-empty output line, secret-redacted and truncated, for failure diagnostics."""
    lines=[l.strip() for l in str(output or "").splitlines() if l.strip()]
    line=lines[-1] if lines else "(no output)"
    if secret: line=line.replace(secret,"***")
    return line[:limit]
def classify(text,code):
    """Classify a failed attempt. Exit code 0 is always success, even when the
    model's answer text happens to mention quota/rate-limit/outage wording."""
    if not code: return "success"
    if code==124: return "timeout"
    s=(text or "")[-4000:].lower()   # errors print last; avoid matching echoed prompt text
    rules=[
      ("quota_exhausted",r"insufficient_quota|quota_exceeded|billing|credits exhausted|usage limit|resource_exhausted|insufficient balance|top up|pre-?paid credits|payment required|\b402\b"),   # last five seen in real opencode v2.2.7 output
      ("rate_limited",r"rate.?limit|too many requests|\b429\b|throttl"),
      ("authentication",r"unauthorized|invalid api key|authentication|forbidden|\b401\b|\b403\b|no (cookie )?auth credentials|credentials? (not found|missing)|not logged in"),
      ("provider_outage",r"service unavailable|bad gateway|gateway timeout|\b5\d\d\b|connection reset|connection refused|timed out|network"),
      ("model_unavailable",r"model not found|unknown model|unsupported model"),
    ]
    for name,pat in rules:
      if re.search(pat,s): return name
    return "execution_error"
def model_available(model,available):
    # Exact token match against `opencode models`; a `#variant` suffix is not part of the catalogue ID.
    return model.split("#",1)[0] in set(str(available).split())
def query_timeout():
    try: value=float(os.getenv("OPENCODE_QUERY_TIMEOUT","30"))
    except (TypeError,ValueError): return 30.0
    return value if value>0 else 30.0
def _opencode(args,env=None):
    """Run a short catalogue/help query. Raises RuntimeError on timeout."""
    timeout=query_timeout()
    try:
        return subprocess.run([OPENCODE,*args],capture_output=True,text=True,
                              env=env,timeout=timeout,check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"opencode {' '.join(args)} timed out after {timeout:g}s") from exc
_FLAGS={}
def supports_flag(sub_cmd,flag):
    key=(sub_cmd,flag)
    if key not in _FLAGS:
        try:
            p=_opencode([sub_cmd,"--help"])
            _FLAGS[key]=flag in (p.stdout+p.stderr)
        except (OSError,subprocess.SubprocessError,RuntimeError): _FLAGS[key]=False
    return _FLAGS[key]
def smoothed_rates():
    """Laplace-smoothed success rate per model from telemetry (new models start at 0.5)."""
    stats={}
    try: lines=(STATE/"events.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError,UnicodeDecodeError): return {}
    for line in lines:
        try: e=json.loads(line)
        except json.JSONDecodeError: continue
        if not isinstance(e,dict): continue
        if e.get("status") not in ("success","failure") or not e.get("model"): continue
        s=stats.setdefault(e["model"],[0,0]); s[1]+=1; s[0]+=e["status"]=="success"
    return {m:(ok+1)/(n+2) for m,(ok,n) in stats.items()}
def adaptive_order(models):
    if os.getenv("ADAPTIVE_ORDER","0")!="1": return models
    rates=smoothed_rates()
    return sorted(models,key=lambda m:-rates.get(m,0.5))  # stable: ties keep configured order
def task_models(task):
    if task not in TASKS: raise ValueError(f"Unsupported task: {task}")
    return [x.strip() for x in (CONFIG/f"{task}.txt").read_text().splitlines()
            if x.strip() and not x.lstrip().startswith("#")]
def get_available():
    try: p=_opencode(["models"])
    except OSError as exc: raise RuntimeError(f"cannot run {OPENCODE}: {exc}") from exc
    if p.returncode: raise RuntimeError(f"opencode models failed: {p.stderr.strip()}")
    return p.stdout+"\n"+p.stderr
def state_json(name,default): return load(STATE/name,default)
def circuit_is_open(model):
    state=state_json("circuits.json",{})
    item=state.get(model,{})
    if not isinstance(item,dict): return False
    until=item.get("open_until")
    if not until: return False
    try: return dt.datetime.fromisoformat(until)>utcnow()
    except (ValueError,TypeError): return False
def circuit_update(model,category,success):
    path=STATE/"circuits.json"; state=load(path,{})
    item=state.get(model)
    if not isinstance(item,dict):
        item={"failures":0,"successes":0}; state[model]=item
    if success:
      item["successes"]=item.get("successes",0)+1; item["failures"]=0; item.pop("open_until",None); item["last_error"]=None
    else:
      item["failures"]=item.get("failures",0)+1; item["last_error"]=category
      threshold=1 if category in {"authentication","model_unavailable","quota_exhausted"} else FAIL_THRESHOLD
      if item["failures"]>=threshold:
        item["open_until"]=(utcnow()+dt.timedelta(seconds=QUOTA_COOLDOWN if category=="quota_exhausted" else COOLDOWN)).isoformat()
    item["updated_at"]=iso(); atomic(path,state)

def key_candidates(provider):
    cfg=config()["providers"].get(provider)
    if not cfg: return [{"slot":0,"env_name":None,"secret":None,"auth_env":None}]
    auth_env=cfg["auth_env"]; state=load(STATE/"key-pools.json",{}).get(provider,{})
    if not isinstance(state,dict): state={}
    cooldowns=state.get("cooldowns",{})
    if not isinstance(cooldowns,dict): cooldowns={}
    items=[]
    for slot,env_name in enumerate(cfg.get("key_slots",[]),1):
      secret=os.getenv(env_name,"").strip()
      if not secret: continue
      until=cooldowns.get(str(slot))
      if until:
        try:
          if dt.datetime.fromisoformat(until)>utcnow(): continue
        except (ValueError,TypeError): pass
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
    p=state.get(provider)
    if not isinstance(p,dict): p={"current_slot":slot,"cooldowns":{},"stats":{}}; state[provider]=p
    p["current_slot"]=slot
    cooldowns=p.get("cooldowns")
    if not isinstance(cooldowns,dict): cooldowns={}; p["cooldowns"]=cooldowns
    stats=p.get("stats")
    if not isinstance(stats,dict): stats={}; p["stats"]=stats
    slot_stats=stats.get(str(slot))
    if not isinstance(slot_stats,dict): slot_stats={"success":0,"failure":0}; stats[str(slot)]=slot_stats
    if outcome=="success":
      slot_stats["success"]=slot_stats.get("success",0)+1; cooldowns.pop(str(slot),None)
    else:
      slot_stats["failure"]=slot_stats.get("failure",0)+1
      cooldowns[str(slot)]=(utcnow()+dt.timedelta(seconds=KEY_COOLDOWN)).isoformat()
    atomic(path,state)

def route(args):
    if args.max_models<0 or args.timeout<0: raise ValueError("limits must be non-negative")
    if not isinstance(args.prompt,str) or not args.prompt.strip(): raise ValueError("PROMPT is required")
    available=get_available()
    candidates=[args.model] if args.model else task_models(args.task)
    candidates=[m for m in candidates if model_available(m,available) and not circuit_is_open(m)]
    if not args.model: candidates=adaptive_order(candidates)
    if args.max_models>0: candidates=candidates[:args.max_models]
    if not candidates: print("No eligible models. Check catalogue/circuit state.",file=sys.stderr); return 3
    if args.dry_run:
      for m in candidates: print(m)
      return 0
    task_id=str(uuid.uuid4())
    for model in candidates:
      provider=model.split("/",1)[0]
      keys=key_candidates(provider)
      if not keys and os.getenv("REQUIRE_ENV_KEY","0")!="1":
        # No env keys: use credentials stored by `opencode auth login` (or key-less free models).
        keys=[{"slot":0,"env_name":None,"secret":None,"auth_env":None}]
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
          cmd=[OPENCODE,"run","--model",model]
          if getattr(args,"auto",False): cmd.append("--auto")   # opt-in only (ROUTER_AUTO=1 / --auto)
          if os.getenv("ROUTER_ARCHIVED","1")=="1" and supports_flag("run","--archived"): cmd.append("--archived")
          proc=subprocess.run(cmd+[args.prompt],
              capture_output=True,text=True,env=env,
              timeout=args.timeout if args.timeout>0 else None,check=False)
          output=(proc.stdout or "")+"\n"+(proc.stderr or ""); code=proc.returncode
        except subprocess.TimeoutExpired:
          output=f"timed out after {args.timeout}s"; code=124   # not str(exc): that embeds the whole prompt
        duration=round(time.monotonic()-start,3)
        category=classify(output,code); success=code==0
        circuit_update(model,category,success)
        if not success:
          print(f"[router] FAILED model={model} slot={key['slot']} category={category} exit={code}: {last_line(output,key['secret'])}",file=sys.stderr)
        if key["slot"]>0:
          # Rotate on authentication errors only.
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
        # Key-level retry only when classification suggests credential problem.
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
    r.add_argument("--auto",action="store_true",default=os.getenv("ROUTER_AUTO","0")=="1",help="pass --auto to opencode run (auto-approves permissions)")
    sub.add_parser("health"); sub.add_parser("reset")
    a=p.parse_args(); STATE.mkdir(parents=True,exist_ok=True)
    if a.cmd=="check":
      avail=get_available(); reg=[x.strip() for x in (CONFIG/"free-models.txt").read_text().splitlines() if x.strip() and not x.startswith("#")]
      found=0
      for m in reg:
        ok=model_available(m,avail); found+=int(ok); print(f"[{'AVAILABLE' if ok else 'MISSING'}] {m}")
      print(f"{found}/{len(reg)} found"); return 0
    if a.cmd=="plan":
      avail=get_available()
      for m in task_models(a.task):
        status="CIRCUIT_OPEN" if circuit_is_open(m) else ("READY" if model_available(m,avail) else "NOT_LISTED")
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
      print(f"ERROR: {e}",file=sys.stderr); raise SystemExit(2) from e
