"""Activate credentials and webhook subscriptions; never output secrets."""
import json
import os
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parent
shared=Path('/home/admpronto/apps/prontoglosa-openfinance-shared')
pending=shared/'client-secret.pending'
env=shared/'service.env'
config=dict(line.split('=',1) for line in env.read_text().splitlines() if line)
secret=pending.read_text().strip()
if not 30<=len(secret)<=200 or any(c.isspace() for c in secret): raise RuntimeError('Invalid credential format')
# Validate inside the new container. Request and response stay in memory.
script='''import json,sys,httpx
data=json.load(sys.stdin)
try:
 r=httpx.post('https://api.pluggy.ai/auth',json=data,timeout=30)
 print(json.dumps({'status':r.status_code,'valid':r.status_code==200 and bool(r.json().get('apiKey'))}))
except Exception: print(json.dumps({'status':0,'valid':False}))
'''
check=subprocess.run(['docker','exec','-i','prontoglosa-openfinance-api','python','-c',script],input=json.dumps({
    'clientId':config['PLUGGY_CLIENT_ID'],'clientSecret':secret}),check=True,text=True,capture_output=True)
result=json.loads(check.stdout)
if not result['valid']: raise RuntimeError('Pluggy credentials not accepted: '+str(result['status']))
config['PLUGGY_CLIENT_SECRET']=secret
fd=os.open(env,os.O_WRONLY|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:f.write(''.join(f'{k}={v}\n' for k,v in config.items()))
pending.unlink()
cmd=['docker','compose','-p','prontoglosa-openfinance','-f',str(root/'compose.json'),'up','-d','--force-recreate']
subprocess.run(cmd,check=True,text=True,capture_output=True)
subprocess.run(['docker','exec','nginx','nginx','-s','reload'],check=True,text=True,capture_output=True)
register='''import json,os
from openfinance.runtime import resources
from openfinance.api import EVENTS
_,_,p=resources()
result=p.register_webhooks('https://apihpc.hospitalprontocardio.com.br/app_glosas/openfinance/webhook',os.environ['WEBHOOK_SECRET'],EVENTS)
print(json.dumps(result))
'''
r=subprocess.run(['docker','exec','-i','prontoglosa-openfinance-api','python','-c',register],check=True,text=True,capture_output=True)
registered=json.loads(r.stdout)
(shared/'webhook-registration.json').write_text(json.dumps(registered,indent=2))
print('Pluggy authenticated; credentials protected; subscriptions registered:',len(registered))
print('Events:',', '.join(w['event'] for w in registered))
