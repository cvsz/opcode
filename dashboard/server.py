#!/usr/bin/env python3
from __future__ import annotations
import argparse,datetime as dt,http.server,json,os,pathlib,subprocess,threading,urllib.parse,uuid
ROOT=pathlib.Path(__file__).resolve().parents[1]; CONFIG=ROOT/'config'; STATE=ROOT/'state'
TASKS={'code','review','fast','vision','general'}; JOBS={}; LOCK=threading.Lock(); HTML_PATH=pathlib.Path(__file__).with_name('index.html')
def atomic_write(p,text):
 tmp=p.with_name(p.name+'.tmp');tmp.write_text(text,encoding='utf-8');tmp.replace(p)
def load(p,d):
 try:return json.loads(p.read_text(encoding='utf-8'))
 except (OSError,json.JSONDecodeError):return d
def key_status():
 cfg=load(CONFIG/'providers.json',{'providers':{}}); st=load(STATE/'key-pools.json',{}); out=[]; now=dt.datetime.now(dt.timezone.utc)
 for provider,c in cfg.get('providers',{}).items():
  ps=st.get(provider,{})
  for i,name in enumerate(c.get('key_slots',[]),1):
   until=ps.get('cooldowns',{}).get(str(i))
   try:cool=bool(until and dt.datetime.fromisoformat(until)>now)
   except (ValueError,TypeError):cool=False
   stats=ps.get('stats',{}).get(str(i),{})
   out.append({'provider':provider,'slot':i,'status':'COOLDOWN' if cool else ('READY' if os.getenv(name,'').strip() else 'NOT CONFIGURED'),'success':stats.get('success',0),'failure':stats.get('failure',0)})
 return out
def summary():
 ev=[]
 p=STATE/'events.jsonl'
 if p.exists():
  for line in p.read_text(encoding='utf-8').splitlines()[-300:]:
   try:ev.append(json.loads(line))
   except json.JSONDecodeError:pass
 circuits=load(STATE/'circuits.json',{}); groups={}
 for e in ev:
  m=e.get('model','unknown'); x=groups.setdefault(m,{'model':m,'runs':0,'ok':0,'lat':[],'err':None}); x['runs']+=1;x['ok']+=e.get('status')=='success'
  if isinstance(e.get('latency_seconds'),(int,float)):x['lat'].append(e['latency_seconds'])
  if e.get('error_category') not in (None,'success'):x['err']=e['error_category']
 models=[]
 for m,x in groups.items():
  until=circuits.get(m,{}).get('open_until')
  try:opened=bool(until and dt.datetime.fromisoformat(until)>dt.datetime.now(dt.timezone.utc))
  except (ValueError,TypeError):opened=False
  models.append({'model':m,'runs':x['runs'],'success_rate':x['ok']/x['runs'],'avg_latency':sum(x['lat'])/len(x['lat']) if x['lat'] else None,'last_error':x['err'],'circuit_open':opened})
 return {'events':ev,'models':sorted(models,key=lambda x:x['success_rate'],reverse=True),'open_circuits':sum(bool(c.get('open_until')) for c in circuits.values()),'keys':key_status(),'jobs':list(JOBS.values())[-20:]}
def start_job(d):
 jid=str(uuid.uuid4()); job={'id':jid,'task':d['task'],'status':'queued','output':''}; JOBS[jid]=job
 def worker():
  job['status']='running'; cmd=[os.getenv('PYTHON','python3'),str(ROOT/'scripts/router.py'),'route','--task',d['task'],'--prompt',d['prompt'],'--max-models',str(d['max_models']),'--timeout',str(d['timeout'])]
  if d['model']:cmd+=['--model',d['model']]
  try:
   p=subprocess.Popen(cmd,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=os.environ.copy()); lines=[]
   for line in p.stdout:lines.append(line.rstrip()[:1500]);job['output']='\n'.join(lines[-80:])
   code=p.wait();job.update(status='success' if code==0 else 'failed',exit_code=code)
  except Exception as e:job.update(status='failed',output=str(e))
  job['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat()
 threading.Thread(target=worker,daemon=True).start();return job
class H(http.server.BaseHTTPRequestHandler):
 def sendj(self,d,status=200):
  b=json.dumps(d).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def readbody(self):
  n=int(self.headers.get('Content-Length','0'))
  if n>131072:raise ValueError('Body too large')
  return json.loads(self.rfile.read(n) or b'{}')
 def host_ok(self):
  port=self.server.server_address[1]
  return self.headers.get('Host','') in {f'127.0.0.1:{port}',f'localhost:{port}',f'[::1]:{port}'}
 def do_GET(self):
  if not self.host_ok():self.sendj({'error':'Invalid Host header'},403);return
  u=urllib.parse.urlparse(self.path)
  try:
   if u.path=='/':
    b=HTML_PATH.read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
   elif u.path=='/api/summary':self.sendj(summary())
   elif u.path=='/api/health':self.sendj({'circuits':load(STATE/'circuits.json',{}),'keys':load(STATE/'key-pools.json',{})})
   elif u.path=='/api/task-config':
    t=urllib.parse.parse_qs(u.query).get('task',['code'])[0]
    if t not in TASKS:raise ValueError('Invalid task')
    self.sendj({'task':t,'models':[x.strip() for x in (CONFIG/(t+'.txt')).read_text().splitlines() if x.strip() and not x.lstrip().startswith('#')]})
   elif u.path=='/api/catalogue':
    p=subprocess.run([os.getenv('OPENCODE','opencode'),'models'],cwd=ROOT,capture_output=True,text=True,timeout=30);self.sendj({'output':p.stdout+'\n'+p.stderr,'exit_code':p.returncode})
   else:self.send_error(404)
  except Exception as e:self.sendj({'error':str(e)},400)
 def do_POST(self):
  if not self.host_ok():self.sendj({'error':'Invalid Host header'},403);return
  origin=self.headers.get('Origin')
  if origin and urllib.parse.urlparse(origin).netloc not in {f'{self.server.server_address[0]}:{self.server.server_address[1]}',f'localhost:{self.server.server_address[1]}'}:self.sendj({'error':'Cross-origin request rejected'},403);return
  try:
   d=self.readbody(); path=urllib.parse.urlparse(self.path).path
   if path=='/api/run':
    t=d.get('task');prompt=d.get('prompt')
    if t not in TASKS or not isinstance(prompt,str) or not prompt.strip() or len(prompt)>50000:raise ValueError('Invalid task or prompt')
    model=d.get('model','');mx=int(d.get('max_models',0));to=int(d.get('timeout',180))
    if not isinstance(model,str) or len(model)>200 or any(c.isspace() for c in model) or not 0<=mx<=100 or not 0<=to<=3600:raise ValueError('Invalid model or limits')
    self.sendj(start_job({'task':t,'prompt':prompt,'model':model,'max_models':mx,'timeout':to}),202)
   elif path=='/api/plan':
    t=d.get('task')
    if t not in TASKS:raise ValueError('Invalid task')
    p=subprocess.run([os.getenv('PYTHON','python3'),str(ROOT/'scripts/router.py'),'plan','--task',t],cwd=ROOT,capture_output=True,text=True,timeout=30);self.sendj({'output':p.stdout+p.stderr,'exit_code':p.returncode})
   elif path=='/api/task-config':
    t=d.get('task');ms=d.get('models')
    if t not in TASKS or not isinstance(ms,list) or len(ms)>100:raise ValueError('Invalid task or model list')
    clean=[]
    for m in ms:
     if not isinstance(m,str) or not m.strip() or len(m)>200 or any(c.isspace() for c in m):raise ValueError('Invalid model ID')
     if m not in clean:clean.append(m)
    atomic_write(CONFIG/(t+'.txt'),'\n'.join(clean)+('\n' if clean else ''));self.sendj({'saved':True})
   elif path in ('/api/reset-circuits','/api/reset-keys'):
    f=STATE/('circuits.json' if path.endswith('circuits') else 'key-pools.json');STATE.mkdir(exist_ok=True);atomic_write(f,'{}\n');self.sendj({'message':'State reset'})
   else:self.send_error(404)
  except Exception as e:self.sendj({'error':str(e)},400)
 def log_message(self,fmt,*args):print('[dashboard] '+fmt%args)
def main():
 p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8765);a=p.parse_args()
 if a.host not in {'127.0.0.1','localhost','::1'}:raise SystemExit('Loopback only; use authenticated reverse proxy for remote access')
 s=http.server.ThreadingHTTPServer((a.host,a.port),H);print(f'Control panel: http://{a.host}:{a.port}/')
 try:s.serve_forever()
 except KeyboardInterrupt:pass
 finally:s.server_close()
if __name__=='__main__':main()
