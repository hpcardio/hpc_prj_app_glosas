"""Synthetic integration check, run once inside the dedicated service."""
import json
import os
from pathlib import Path
import time
from uuid import uuid4
import httpx
import jwt
from sqlalchemy import text, select
from openfinance.runtime import resources
from openfinance.store import Owner, Connection, Account, Movement, Event

engine,sf,p=resources()
base='https://apihpc.hospitalprontocardio.com.br/app_glosas/openfinance'
with engine.connect() as c:
    principal=c.execute(text('SELECT id,email FROM api_prontocardio.usuarios_api WHERE ativo=true AND perfil=:p ORDER BY id LIMIT 1'),{'p':'ti'}).first()
if not principal: raise RuntimeError('No active TI principal available for authenticated validation')
# Short-lived test JWT uses the existing signing contract, never logged or saved.
token=jwt.encode({'sub':principal.email,'exp':int(time.time())+600},os.environ['SECRET_KEY'],algorithm=os.environ['ALGORITHM'])
h={'Authorization':'Bearer '+token}
client=httpx.Client(timeout=30,follow_redirects=False)
owner_id=os.environ.get('SANDBOX_TEST_OWNER')
if not owner_id:
    r=client.post(base+'/owners',json={'label':'DEMONSTRAÇÃO PLUGGY — DADOS FICTÍCIOS','kind':'PF'},headers=h)
    assert r.status_code==201, 'Owner API rejected validation'
    owner_id=r.json()['id']
r=client.post(base+f'/owners/{owner_id}/connect-token',headers=h)
assert r.status_code==200 and r.json().get('accessToken'), 'Connect Token failed'
# Synthetic bank connector 2 only; only requested products are collected.
p.connect_token(owner_id)  # initialize server-only API authentication
item_id=os.environ.get('SANDBOX_TEST_ITEM')
if not item_id:
    r=p.client.post(p.base+'/items',headers={'X-API-KEY':p.key},json={
        'connectorId':2,'parameters':{'user':'user-ok','password':'password-ok'},
        'clientUserId':owner_id,'products':['ACCOUNTS','TRANSACTIONS']})
    assert r.status_code in {200,201}, 'Sandbox connection creation failed: '+str(r.status_code)
    item_id=r.json()['id']
print(json.dumps({'stage':'sandbox_created','owner_id':owner_id,'item_id':item_id}),flush=True)
for attempt in range(40):
    with sf() as s:
        c=s.get(Connection,item_id)
        jobs=list(s.scalars(select(Event).where(Event.item_id==item_id)))
        if c and c.last_sync:
            rows=list(s.scalars(select(Account).where(Account.connection_id==item_id,Account.active==True)))
            counts=[len(list(s.scalars(select(Movement).where(Movement.account_id==a.id,Movement.active==True)))) for a in rows]
            assert c.sandbox and c.owner_id==owner_id
            assert any(j.state=='done' for j in jobs), 'No processed provider webhook'
            break
    time.sleep(3)
else: raise RuntimeError('Sandbox webhook/sync did not complete in validation window')
r=client.get(base+f'/owners/{owner_id}/accounts',headers=h)
assert r.status_code==200 and len(r.json()['accounts'])==len(rows)
for a in rows:
    r=client.get(base+f'/accounts/{a.id}/transactions?page_size=2',headers=h)
    assert r.status_code==200 and len(r.json()['results'])<=2
    actual=p.accounts(item_id)
    expected=next(x for x in actual if x['id']==a.id)
    from decimal import Decimal
    assert a.balance==Decimal(str(expected['balance']))
# Verify durable deduplication through public HTTPS; synthetic event shares a provider event ID.
event=next(j for j in jobs if j.state=='done')
r=client.post(base+'/webhook',headers={'X-ProntoGlosa-Webhook-Token':os.environ['WEBHOOK_SECRET']},
              json={'eventId':event.id,'event':event.event,'itemId':item_id})
assert r.status_code==202 and r.json().get('duplicate')
with engine.connect() as c:
    role=c.execute(text('SELECT current_user, has_table_privilege(current_user, :t, :p)'),
                   {'t':'api_prontocardio.usuarios_api','p':'UPDATE'}).first()
    assert role[0]=='prontoglosa_openfinance' and role[1] is False
print(json.dumps({'stage':'verified','accounts':len(rows),'transactions':sum(counts),
                  'provider_webhooks_received':len(jobs),'balances_match':True,'public_deduplication':True,
                  'restricted_database_role':True,'sandbox_only':True}),flush=True)
