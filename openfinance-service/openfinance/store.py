import time
from decimal import Decimal
from uuid import UUID
from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, Numeric, String, Text, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from .provider import ProviderError


class Base(DeclarativeBase): pass


class Owner(Base):
    __tablename__='owners'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    user_id: Mapped[int]=mapped_column(Integer,index=True)
    label: Mapped[str]=mapped_column(String(150))
    kind: Mapped[str]=mapped_column(String(2))


class Connection(Base):
    __tablename__='connections'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    owner_id: Mapped[str]=mapped_column(ForeignKey('owners.id'),index=True)
    connector_id: Mapped[int]=mapped_column(Integer)
    sandbox: Mapped[bool]=mapped_column(Boolean)
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    status: Mapped[str]=mapped_column(String(40),default='PENDING')
    last_sync: Mapped[float|None]=mapped_column(Float,nullable=True)
    provider_updated: Mapped[str|None]=mapped_column(String(80),nullable=True)
    last_error: Mapped[str|None]=mapped_column(String(80),nullable=True)


class Account(Base):
    __tablename__='accounts'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    connection_id: Mapped[str]=mapped_column(ForeignKey('connections.id'),index=True)
    name: Mapped[str]=mapped_column(String(200))
    number: Mapped[str]=mapped_column(String(100))
    currency: Mapped[str]=mapped_column(String(3))
    balance: Mapped[Decimal|None]=mapped_column(Numeric(24,6),nullable=True)
    closing_balance: Mapped[Decimal|None]=mapped_column(Numeric(24,6),nullable=True)
    active: Mapped[bool]=mapped_column(Boolean,default=True)


class Movement(Base):
    __tablename__='transactions'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    account_id: Mapped[str]=mapped_column(ForeignKey('accounts.id'),index=True)
    date: Mapped[str]=mapped_column(String(80),index=True)
    description: Mapped[str]=mapped_column(Text)
    amount: Mapped[Decimal]=mapped_column(Numeric(24,6))
    currency: Mapped[str]=mapped_column(String(3))
    type: Mapped[str]=mapped_column(String(30))
    status: Mapped[str]=mapped_column(String(30))
    active: Mapped[bool]=mapped_column(Boolean,default=True)


class Event(Base):
    __tablename__='webhook_events'
    id: Mapped[str]=mapped_column(String(150),primary_key=True)
    item_id: Mapped[str]=mapped_column(String(36),index=True)
    event: Mapped[str]=mapped_column(String(50))
    payload: Mapped[dict]=mapped_column(JSON)
    state: Mapped[str]=mapped_column(String(15),default='pending',index=True)
    attempts: Mapped[int]=mapped_column(Integer,default=0)
    available_at: Mapped[float]=mapped_column(Float,index=True)
    last_error: Mapped[str|None]=mapped_column(String(80),nullable=True)
    created_at: Mapped[float]=mapped_column(Float,default=time.time)


class RevokedItem(Base):
    __tablename__='revoked_items'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    revoked_at: Mapped[float]=mapped_column(Float,default=time.time)


REAL_CONNECTORS={611,671,603,619,608,661,663,662,672,609,616,621,627,670}


def decimal_value(value):
    if value is None: return None
    result=Decimal(str(value))
    if not result.is_finite(): raise ProviderError('Invalid amount')
    return result


def sync_item(sf, provider, item_id, sandbox_only):
    with sf() as s:
        if s.get(RevokedItem,item_id): return
    item=provider.item(item_id)
    if item.get('id')!=item_id: raise ProviderError('Unexpected connection')
    owner_id=item.get('clientUserId')
    with sf() as s:
        if not owner_id or not s.get(Owner,owner_id): raise ProviderError('Unregistered owner')
        previous=s.get(Connection,item_id)
        if previous and previous.owner_id!=owner_id: raise ProviderError('Connection owner mismatch')
        if previous and not previous.active: return  # tombstone: late events cannot reactivate consent
    connector=item['connector']; sandbox=connector.get('isSandbox') is True
    if sandbox_only and not sandbox: raise ProviderError('Sandbox only')
    if not sandbox and connector['id'] not in REAL_CONNECTORS: raise ProviderError('Connector not allowed')
    status=item.get('status','UNKNOWN')
    execution=item.get('executionStatus')
    usable=status=='UPDATED' and execution=='SUCCESS'
    # Partial responses must not replace complete snapshots.
    if not usable:
        with sf.begin() as s:
            if s.get(RevokedItem,item_id): return
            c=s.get(Connection,item_id)
            if c is None:
                c=Connection(id=item_id,owner_id=owner_id,connector_id=connector['id'],sandbox=sandbox)
                s.add(c)
            c.status=status; c.last_error=execution or status
        return
    accounts=provider.accounts(item_id)
    snapshots=[(a,provider.transactions(a['id'])) for a in accounts]
    with sf.begin() as s:
        if s.get(RevokedItem,item_id): return
        c=s.get(Connection,item_id)
        if c is not None and (c.owner_id!=owner_id or not c.active): raise ProviderError('Connection changed')
        if c is None:
            c=Connection(id=item_id,owner_id=owner_id,connector_id=connector['id'],sandbox=sandbox)
            s.add(c); s.flush()
        s.execute(update(Account).where(Account.connection_id==item_id).values(active=False))
        for a,txs in snapshots:
            if a['type']!='BANK' or a['itemId']!=item_id: raise ProviderError('Unexpected bank account')
            row=s.get(Account,a['id'])
            if row is not None and row.connection_id!=item_id: raise ProviderError('Account ownership mismatch')
            if row is None:
                row=Account(id=a['id'],connection_id=item_id); s.add(row)
            row.name=a.get('name') or 'Conta bancária'; row.number=a.get('number') or ''
            row.currency=a.get('currencyCode') or 'BRL'
            row.balance=decimal_value(a.get('balance'))
            row.closing_balance=decimal_value((a.get('bankData') or {}).get('closingBalance'))
            row.active=True
            s.flush()
            s.execute(update(Movement).where(Movement.account_id==a['id']).values(active=False))
            # Load by batches before changing rows. One remote lookup per movement
            # would dominate import latency when PostgreSQL is outside the LAN.
            ids=list({t['id'] for t in txs})
            existing={}
            for start in range(0,len(ids),1000):
                existing.update((m.id,m) for m in s.scalars(select(Movement).where(Movement.id.in_(ids[start:start+1000]))))
            for t in txs:
                if t['accountId']!=a['id']: raise ProviderError('Transaction ownership mismatch')
                movement=existing.get(t['id'])
                if movement is not None and movement.account_id!=a['id']: raise ProviderError('Movement ownership mismatch')
                if movement is None:
                    movement=Movement(id=t['id'],account_id=a['id']); s.add(movement)
                    existing[t['id']]=movement
                movement.date=t['date']; movement.description=t.get('description') or ''
                movement.amount=decimal_value(t['amount']); movement.currency=t.get('currencyCode') or row.currency
                movement.type=t.get('type') or 'UNKNOWN'; movement.status=t.get('status') or 'UNKNOWN'; movement.active=True
        c.status=status; c.last_sync=time.time(); c.provider_updated=item.get('lastUpdatedAt'); c.last_error=None


def run_one(sf, provider, sandbox_only):
    now=time.time()
    with sf.begin() as s:
        job=s.scalar(select(Event).where(Event.state.in_(['pending','processing']),Event.available_at<=now)
                     .order_by(Event.created_at).with_for_update(skip_locked=True).limit(1))
        if job is None: return False
        job.state='processing'; job.attempts+=1; job.available_at=now+3600
        event_id,item_id,event=job.id,job.item_id,job.event
    try:
        if event=='item/deleted':
            with sf.begin() as s:
                if not s.get(RevokedItem,item_id): s.add(RevokedItem(id=item_id))
                c=s.get(Connection,item_id)
                if c: c.active=False; c.status='DELETED'
        else:
            sync_item(sf,provider,item_id,sandbox_only)
        with sf.begin() as s:
            job=s.get(Event,event_id); job.state='done'; job.last_error=None
    except Exception as exc:
        # Do not persist raw exceptions: HTTP payloads / connection errors can contain secrets.
        error=type(exc).__name__
        with sf.begin() as s:
            job=s.get(Event,event_id); job.last_error=error
            job.state='failed' if job.attempts>=8 else 'pending'
            job.available_at=time.time()+min(60*2**job.attempts,7200)
            c=s.get(Connection,item_id)
            if c: c.last_error=error
    return True
