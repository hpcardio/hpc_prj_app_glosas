import json
import os
from pathlib import Path
import subprocess
import time

def run(args): return subprocess.run(args,check=True,capture_output=True,text=True).stdout

root=Path(__file__).resolve().parent
template=Path('/home/admpronto/hpc_projetos/api-rede/server/nginx.conf')
marker='# ProntoGlosa Open Finance — balances and statements only'
block='''    # ProntoGlosa Open Finance — balances and statements only
    location ^~ /app_glosas/openfinance/ {
        client_max_body_size 64k;
        proxy_read_timeout 45s;
        proxy_send_timeout 45s;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Host $http_host;
        proxy_redirect off;
        resolver 127.0.0.11 valid=10s ipv6=off;
        set $openfinance_service prontoglosa-openfinance-api;
        rewrite ^/app_glosas/openfinance/(.*)$ /$1 break;
        proxy_pass http://$openfinance_service:8000;
    }
'''
anchor='    server_name apihpc.hospitalprontocardio.com.br;'
live=run(['docker','exec','nginx','cat','/etc/nginx/conf.d/default.conf'])
original=template.read_text()
if marker in original or marker in live: raise SystemExit('Route already present; inspect instead of duplicating')
def insert(content):
    # HTTP and HTTPS both name this host: modify only its TLS server.
    parts=content.split('server {')
    hits=[i for i,p in enumerate(parts) if anchor in p and 'listen 443 ssl;' in p]
    if len(hits)!=1: raise RuntimeError('Ambiguous TLS server')
    n=hits[0]; parts[n]=parts[n].replace(anchor,anchor+'\n\n'+block,1)
    return 'server {'.join(parts)
new_template=insert(original); new_live=insert(live)
backup=root/'nginx-backup'; backup.mkdir(exist_ok=True)
(backup/'nginx.conf.before').write_text(original)
(backup/'default.conf.before').write_text(live)
new_path=root/'nginx.default.after.conf'; new_path.write_text(new_live)
if template.read_text()!=original: raise RuntimeError('Concurrent template change')
try:
    # Write in place to preserve bind-mount inode and permissions.
    template.write_text(new_template)
    run(['docker','cp',str(new_path),'nginx:/etc/nginx/conf.d/default.conf'])
    result=run(['docker','exec','nginx','nginx','-t'])
    run(['docker','exec','nginx','nginx','-s','reload'])
except Exception:
    template.write_text(original)
    run(['docker','cp',str(backup/'default.conf.before'),'nginx:/etc/nginx/conf.d/default.conf'])
    raise
print('HTTPS route added; nginx config validated and reloaded')
