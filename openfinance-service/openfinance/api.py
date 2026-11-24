import hmac
import json
import time
from datetime import date
from uuid import UUID, uuid4
from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from .store import Owner, Connection, Account, Movement, Event, RevokedItem
from .provider import ProviderError

EVENTS=['item/created','item/updated','item/error','item/deleted','item/waiting_user_input',
        'item/waiting_user_action','transactions/created','transactions/updated','transactions/deleted']


class OwnerInput(BaseModel):
    label: str=Field(min_length=1,max_length=150)
    kind: str=Field(pattern='^(PF|PJ)$')


class ItemInput(BaseModel):
    item_id: UUID


def create_app(sf, provider, webhook_secret, authenticate, sandbox_only=True,frontend_secret=''):
    if len(webhook_secret)<32: raise ValueError('Webhook secret must have at least 32 characters')
    app=FastAPI(title='ProntoGlosa Open Finance — saldos e extratos',docs_url=None,redoc_url=None,openapi_url=None)
    bearer=HTTPBearer(auto_error=False)

    def user(request:Request,auth: HTTPAuthorizationCredentials|None=Depends(bearer)):
        if not auth or auth.scheme.lower()!='bearer': raise HTTPException(401,'Autenticação necessária')
        try: uid=authenticate(auth.credentials)
        except SQLAlchemyError: raise HTTPException(503,'Autenticação temporariamente indisponível')
        if uid is None: raise HTTPException(401,'Usuário sem acesso')
        return uid

    def owner(s,id,uid):
        row=s.get(Owner,str(id))
        if row is None or row.user_id!=uid: raise HTTPException(404,'Titular não encontrado')
        return row

    @app.get('/health')
    def health():
        try:
            with sf() as s: s.execute(select(1))
        except SQLAlchemyError: raise HTTPException(503,'Armazenamento indisponível')
        return {'status':'ok','scope':['balances','transactions'],'sandbox_only':sandbox_only,
                'provider_configured':bool(provider.client_secret) if hasattr(provider,'client_secret') else True}

    @app.post('/webhook',status_code=202)
    async def webhook(request:Request):
        supplied=request.headers.get('X-ProntoGlosa-Webhook-Token','')
        if not hmac.compare_digest(supplied.encode(),webhook_secret.encode()): raise HTTPException(401,'Webhook não autorizado')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>65536: raise HTTPException(413,'Corpo excede o limite')
        try:
            payload=json.loads(body)
            if not isinstance(payload,dict): raise ValueError()
            if payload.get('event') not in EVENTS: return {'accepted':True,'ignored':True}
            event_id=payload['eventId']; item_id=str(UUID(payload['itemId']))
            if not isinstance(event_id,str) or not 1<=len(event_id)<=150: raise ValueError()
        except (ValueError,KeyError,TypeError): raise HTTPException(400,'Evento inválido')
        try:
            with sf.begin() as s:
                if s.get(Event,event_id): return {'accepted':True,'duplicate':True}
                s.add(Event(id=event_id,item_id=item_id,event=payload['event'],payload={},available_at=time.time()))
                if payload['event']=='item/deleted':
                    if not s.get(RevokedItem,item_id): s.add(RevokedItem(id=item_id))
                    connection=s.get(Connection,item_id)
                    if connection: connection.active=False; connection.status='DELETED'
        except IntegrityError: return {'accepted':True,'duplicate':True}
        except SQLAlchemyError: raise HTTPException(503,'Não foi possível persistir o evento')
        return {'accepted':True}

    @app.get('/owners')
    def owners(uid=Depends(user)):
        with sf() as s:
            return [{'id':r.id,'label':r.label,'kind':r.kind} for r in s.scalars(select(Owner).where(Owner.user_id==uid))]

    @app.post('/owners',status_code=201)
    def add_owner(data:OwnerInput,uid=Depends(user)):
        with sf.begin() as s:
            row=Owner(id=str(uuid4()),user_id=uid,label=data.label.strip(),kind=data.kind); s.add(row)
            return {'id':row.id,'label':row.label,'kind':row.kind}

    @app.post('/owners/{owner_id}/connect-token')
    def connect_token(owner_id:UUID,uid=Depends(user)):
        with sf() as s: titular=owner(s,owner_id,uid)
        try: result=provider.connect_token(str(owner_id))
        except ProviderError: raise HTTPException(503,'Conexão com Pluggy indisponível')
        connectors=[2] if sandbox_only else ([611,671,603,619,608,661,663] if titular.kind=='PF' else [662,672,609,616,621,627,670])
        return {'accessToken':result['accessToken'],'sandbox_only':sandbox_only,'products':['ACCOUNTS','TRANSACTIONS'],
                'widget_config':{'connectorIds':connectors,'includeSandbox':sandbox_only,'products':['ACCOUNTS','TRANSACTIONS']}}

    @app.post('/owners/{owner_id}/connections',status_code=202)
    def attach(owner_id:UUID,data:ItemInput,uid=Depends(user)):
        with sf() as s: owner(s,owner_id,uid)
        try: item=provider.item(str(data.item_id))
        except ProviderError: raise HTTPException(503,'Conexão com Pluggy indisponível')
        if item.get('id')!=str(data.item_id) or item.get('clientUserId')!=str(owner_id):
            raise HTTPException(409,'Conexão não corresponde ao titular')
        if sandbox_only and not item.get('connector',{}).get('isSandbox'): raise HTTPException(409,'Piloto somente sandbox')
        with sf.begin() as s:
            s.add(Event(id=str(uuid4()),item_id=str(data.item_id),event='item/updated',payload={},available_at=time.time()))
        return {'queued':True}

    @app.get('/owners/{owner_id}/accounts')
    def accounts(owner_id:UUID,uid=Depends(user)):
        with sf() as s:
            owner(s,owner_id,uid)
            connections=s.scalars(select(Connection).where(Connection.owner_id==str(owner_id))).all()
            rows=s.scalars(select(Account).join(Connection).where(Connection.owner_id==str(owner_id),Connection.active==True,Account.active==True)).all()
            return {'connections':[{'id':c.id,'status':c.status,'active':c.active,'sandbox':c.sandbox,
                    'last_sync':c.last_sync,'provider_updated':c.provider_updated,'last_error':c.last_error} for c in connections],
                    'accounts':[{'id':a.id,'connection_id':a.connection_id,'name':a.name,'number':a.number,
                        'currency':a.currency,'available_balance':str(a.balance) if a.balance is not None else None,
                        'closing_balance':str(a.closing_balance) if a.closing_balance is not None else None} for a in rows]}

    @app.get('/accounts/{account_id}/transactions')
    def transactions(account_id:UUID,date_from:date|None=None,date_to:date|None=None,
                     page:int=Query(1,ge=1,le=100000),page_size:int=Query(100,ge=1,le=500),uid=Depends(user)):
        if date_from and date_to and date_from>date_to: raise HTTPException(422,'Período inválido')
        with sf() as s:
            account=s.scalar(select(Account).join(Connection).join(Owner).where(Account.id==str(account_id),
                        Owner.user_id==uid,Connection.active==True,Account.active==True))
            if account is None: raise HTTPException(404,'Conta não encontrada')
            conditions=[Movement.account_id==str(account_id),Movement.active==True]
            if date_from: conditions.append(Movement.date>=date_from.isoformat())
            if date_to: conditions.append(func.substr(Movement.date,1,10)<=date_to.isoformat())
            total=s.scalar(select(func.count()).select_from(Movement).where(*conditions))
            rows=s.scalars(select(Movement).where(*conditions).order_by(Movement.date.desc(),Movement.id)
                           .offset((page-1)*page_size).limit(page_size)).all()
            return {'total':total,'page':page,'page_size':page_size,'results':[{'id':t.id,'date':t.date,
                'description':t.description,'amount':str(t.amount),'currency':t.currency,'type':t.type,'status':t.status} for t in rows]}

    @app.get('/status')
    def status(uid=Depends(user)):
        with sf() as s:
            jobs=s.execute(select(Event.state,func.count()).join(Connection,Event.item_id==Connection.id)
                           .join(Owner,Connection.owner_id==Owner.id).where(Owner.user_id==uid).group_by(Event.state)).all()
        return {'jobs':dict(jobs),'sandbox_only':sandbox_only}
    return app
