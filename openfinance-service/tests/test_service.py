import time
from uuid import uuid4
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from openfinance.store import Base, Owner, Connection, Account, Movement, Event, sync_item, run_one
from openfinance.api import create_app
from openfinance.provider import ProviderError

I=str(uuid4()); A=str(uuid4()); T=str(uuid4())

class FakeProvider:
    def __init__(self, owner):
        self.owner=owner; self.fail=False; self.empty=False; self.status='UPDATED'
    def item(self, id):
        return {'id':I,'clientUserId':self.owner,'status':self.status,'executionStatus':'SUCCESS',
                'connector':{'id':2,'isSandbox':True},'lastUpdatedAt':'2026-10-28T12:00:00Z'}
    def accounts(self, id):
        return [{'id':A,'itemId':I,'type':'BANK','name':'Teste','number':'123','balance':123.45,'currencyCode':'BRL','bankData':{'closingBalance':None}}]
    def transactions(self, id):
        if self.fail: raise ProviderError('Pluggy HTTP 503')
        return [] if self.empty else [{'id':T,'accountId':A,'amount':-10.55,'date':'2026-10-28T10:00:00Z','description':'Compra','type':'DEBIT','status':'POSTED','currencyCode':'BRL'}]
    def connect_token(self, owner): return {'accessToken':'limited'}

@pytest.fixture
def setup():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sf=sessionmaker(engine,expire_on_commit=False)
    with sf.begin() as s:
        own=Owner(id=str(uuid4()),user_id=1,label='Hospital',kind='PJ'); s.add(own)
        other=Owner(id=str(uuid4()),user_id=2,label='Outro',kind='PJ'); s.add(other)
    p=FakeProvider(own.id)
    return sf,own,other,p

def client(setup,user=1):
    sf,own,other,p=setup
    return TestClient(create_app(sf,p,'x'*48,lambda token: user if token=='valid' else None, sandbox_only=True))

def test_webhook_requires_secret_and_deduplicates_durably(setup):
    c=client(setup)
    payload={'eventId':'event1','event':'item/updated','itemId':I,'createdTransactionsLink':'https://evil.test'}
    assert c.post('/webhook',json=payload).status_code==401
    headers={'X-ProntoGlosa-Webhook-Token':'x'*48}
    assert c.post('/webhook',json=payload,headers=headers).status_code==202
    assert c.post('/webhook',json=payload,headers=headers).status_code==202
    with setup[0]() as s:
        rows=s.scalars(select(Event)).all()
        assert len(rows)==1 and 'evil' not in str(rows[0].payload)

def test_rejects_payment_event_and_large_body(setup):
    c=client(setup); h={'X-ProntoGlosa-Webhook-Token':'x'*48}
    assert c.post('/webhook',json={'event':'payment_intent/completed','eventId':'p','itemId':I},headers=h).status_code==202
    assert c.post('/webhook',content='x'*70000,headers=h).status_code==413
    with setup[0]() as s: assert not s.scalars(select(Event)).all()

def test_auth_and_owner_isolation(setup):
    c=client(setup); own,other=setup[1:3]
    assert c.get('/owners').status_code==401
    h={'Authorization':'Bearer valid'}
    assert c.get('/owners',headers=h).json()[0]['id']==own.id
    assert c.post(f'/owners/{other.id}/connect-token',headers=h).status_code==404
    data=c.post(f'/owners/{own.id}/connect-token',headers=h).json()
    assert data['accessToken']=='limited'
    assert data['widget_config']=={'connectorIds':[2],'products':['ACCOUNTS','TRANSACTIONS'],'includeSandbox':True}

def test_atomic_snapshot_money_and_statement_access(setup):
    sf,own,other,p=setup
    sync_item(sf,p,I,True)
    p.fail=True
    with pytest.raises(ProviderError): sync_item(sf,p,I,True)
    with sf() as s:
        assert s.get(Account,A).balance==Decimal('123.450000')
        assert s.get(Movement,T).amount==Decimal('-10.550000')
    h={'Authorization':'Bearer valid'}
    assert client(setup).get(f'/accounts/{A}/transactions',headers=h).json()['total']==1
    assert client(setup,user=2).get(f'/accounts/{A}/transactions',headers=h).status_code==404
    p.fail=False; p.empty=True
    sync_item(sf,p,I,True)
    with sf() as s: assert s.get(Movement,T).active is False

def test_rejects_unknown_titular_and_real_connection_in_pilot(setup):
    sf,own,other,p=setup
    p.owner=str(uuid4())
    with pytest.raises(ProviderError): sync_item(sf,p,I,True)
    p.owner=own.id
    original=p.item
    p.item=lambda id: {**original(id),'connector':{'id':611,'isSandbox':False}}
    with pytest.raises(ProviderError): sync_item(sf,p,I,True)
    with sf() as s: assert not s.scalars(select(Connection)).all()

def test_partial_status_never_overwrites_good_snapshot(setup):
    sf,own,other,p=setup
    sync_item(sf,p,I,True)
    p.status='OUTDATED'; p.empty=True
    sync_item(sf,p,I,True)
    with sf() as s:
        assert s.get(Connection,I).status=='OUTDATED'
        assert s.get(Movement,T).active

def test_retries_failed_job_preserves_event_and_prior_balance(setup):
    sf,own,other,p=setup
    p.fail=True
    with sf.begin() as s: s.add(Event(id='evt',item_id=I,event='item/updated',payload={},available_at=time.time()))
    assert run_one(sf,p,True)
    with sf() as s:
        e=s.get(Event,'evt'); assert e.state=='pending' and e.attempts==1 and e.available_at>time.time()
    p.fail=False
    with sf.begin() as s: s.get(Event,'evt').available_at=0
    assert run_one(sf,p,True)
    with sf() as s: assert s.get(Event,'evt').state=='done'

def test_deleted_connection_keeps_history_inactive(setup):
    sf,own,other,p=setup
    sync_item(sf,p,I,True)
    with sf.begin() as s: s.add(Event(id='del',item_id=I,event='item/deleted',payload={},available_at=0))
    run_one(sf,p,True)
    with sf() as s:
        assert not s.get(Connection,I).active
        assert s.get(Movement,T) is not None


def test_statement_import_batches_database_reads(setup):
    from sqlalchemy import event
    sf,own,other,p=setup
    p.transactions=lambda id: [{'id':str(uuid4()),'accountId':A,'amount':1,'date':'2026-10-28',
                                'description':'demo','status':'POSTED'} for _ in range(500)]
    calls=[]
    def capture(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('SELECT'): calls.append(statement)
    event.listen(sf.kw['bind'],'before_cursor_execute',capture)
    sync_item(sf,p,I,True)
    assert len(calls)<15, 'Statement import must not perform one database lookup per transaction'


def test_deleted_before_created_never_reactivates_connection(setup):
    sf,own,other,p=setup
    with sf.begin() as s:s.add(Event(id='early-delete',item_id=I,event='item/deleted',payload={},available_at=0))
    run_one(sf,p,True)
    sync_item(sf,p,I,True)
    with sf() as s:
        c=s.get(Connection,I)
        assert c is None or not c.active


def test_revocation_during_provider_call_blocks_partial_connection_creation(setup):
    from openfinance.store import RevokedItem
    sf,own,other,p=setup
    original=p.item
    def revoke(id):
        with sf.begin() as s:s.add(RevokedItem(id=id))
        return {**original(id),'status':'OUTDATED'}
    p.item=revoke
    sync_item(sf,p,I,True)
    with sf() as s: assert s.get(Connection,I) is None


def test_legacy_auth_only_with_server_frontend_secret(setup):
    sf,own,other,p=setup
    def auth(token,allow_legacy=False):return 1 if token=='legacy' and allow_legacy else None
    c=TestClient(create_app(sf,p,'x'*48,auth,frontend_secret='f'*48))
    h={'Authorization':'Bearer legacy'}
    assert c.get('/owners',headers=h).status_code==401
    assert c.get('/owners',headers={**h,'X-ProntoGlosa-Frontend-Key':'wrong'}).status_code==401
    assert c.get('/owners',headers={**h,'X-ProntoGlosa-Frontend-Key':'f'*48}).status_code==200
