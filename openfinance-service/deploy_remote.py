"""Isolated deployment. Never prints environment or credentials."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

def run(args,**kw):
    return subprocess.run(args,check=True,text=True,capture_output=True,**kw).stdout

root=Path(__file__).resolve().parent
shared=Path('/home/admpronto/apps/prontoglosa-openfinance-shared')
shared.mkdir(mode=0o700,parents=True,exist_ok=True)
env=shared/'service.env'
main='api-prontocardio-api_prontocardio-1'
baseline=json.loads(run(['docker','inspect',main]))[0]
base=baseline['Image']
if not env.exists():
    payload=run(['docker','exec','-i',main,'python','-'],input=(root/'provision_inside.py').read_text())
    config=json.loads(payload)
    if any('\n' in str(v) or '\r' in str(v) for v in config.values()): raise RuntimeError('Invalid config value')
    fd=os.open(env,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f: f.write(''.join(f'{k}={v}\n' for k,v in config.items()))
    print('Restricted PostgreSQL role and schema created',flush=True)
else:
    print('Existing protected configuration retained',flush=True)
release=root.name
image='prontoglosa-openfinance:'+release
print('Building isolated service image',flush=True)
base_tag='prontoglosa-openfinance-base:'+base.split(':')[-1][:16]
run(['docker','tag',base,base_tag])
try:
    run(['docker','build','--build-arg','BASE_IMAGE='+base_tag,'-t',image,str(root)])
except subprocess.CalledProcessError as e:
    print(e.stderr[-2500:]); raise SystemExit(1)
common={'image':image,'restart':'unless-stopped','env_file':[{'path':str(env),'format':'raw'}],
        'read_only':True,'tmpfs':['/tmp:size=32M,noexec,nosuid'],'cap_drop':['ALL'],
        'security_opt':['no-new-privileges:true'],'mem_limit':'512m','cpus':'1.0',
        'networks':['existing'],'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}}
compose={'services':{
    'api':{**common,'container_name':'prontoglosa-openfinance-api',
           'healthcheck':{'test':['CMD','python','-c',"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5)"],
                          'interval':'30s','timeout':'10s','retries':3,'start_period':'20s'}},
    'worker':{**common,'container_name':'prontoglosa-openfinance-worker','command':['python','-m','openfinance.worker']}},
    'networks':{'existing':{'external':True,'name':'api-rede_default'}}}
path=root/'compose.json'; path.write_text(json.dumps(compose,indent=2))
# No schema changes are needed for this authentication-only deployment.
run(['docker','compose','-p','prontoglosa-openfinance','-f',str(path),'up','-d'])
for _ in range(12):
    try:
        health=run(['docker','exec','prontoglosa-openfinance-api','python','-c',
              "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).read().decode())"])
        print('Internal health:',health.strip(),flush=True); break
    except subprocess.CalledProcessError: time.sleep(2)
else: raise RuntimeError('New service did not become healthy')
after=json.loads(run(['docker','inspect',main]))[0]
if baseline['Id']!=after['Id'] or baseline['State']['StartedAt']!=after['State']['StartedAt']:
    raise RuntimeError('Main container changed during deployment; investigate before nginx change')
(root/'deployment.json').write_text(json.dumps({'base_image':base,'image':image,'main_id':baseline['Id'],
                'main_started':baseline['State']['StartedAt'],'stage':'internal-health-ok'},indent=2))
print('Deployment ready for separate HTTPS route validation',flush=True)
