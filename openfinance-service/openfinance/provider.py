"""Only the Pluggy resources required for bank balances and statements."""
import threading
import time
from urllib.parse import parse_qs
import httpx


class ProviderError(Exception):
    pass


class Pluggy:
    base = 'https://api.pluggy.ai'

    def __init__(self, client_id, client_secret, client=None):
        self.client_id, self.client_secret = client_id, client_secret
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.key, self.expires = None, 0
        self.lock = threading.Lock()

    def request(self, method, path, **kwargs):
        allowed=(method,path) in {('POST','/connect_token'),('GET','/accounts'),('GET','/v2/transactions'),
                                  ('GET','/webhooks'),('POST','/webhooks')}
        allowed=allowed or (method=='GET' and path.startswith('/items/') and '/' not in path[7:] and '?' not in path)
        if not allowed:
            raise ProviderError('Resource outside read-only integration scope')
        for attempt in range(2):
            with self.lock:
                if not self.key or time.monotonic() >= self.expires:
                    if not self.client_secret:
                        raise ProviderError('Credentials not configured')
                    try:
                        r=self.client.post(self.base+'/auth',json={'clientId':self.client_id,'clientSecret':self.client_secret})
                    except httpx.HTTPError:
                        raise ProviderError('Pluggy authentication transport unavailable') from None
                    self.checked(r)
                    self.key=r.json()['apiKey']; self.expires=time.monotonic()+6900
                key=self.key
            try:
                r=self.client.request(method,self.base+path,headers={'X-API-KEY':key},**kwargs)
            except httpx.HTTPError:
                raise ProviderError('Pluggy transport unavailable') from None
            if r.status_code==401 and attempt==0:
                with self.lock: self.key=None
                continue
            self.checked(r)
            return r.json()
        raise ProviderError('Pluggy authentication unavailable')

    @staticmethod
    def checked(response):
        if not 200<=response.status_code<300:
            raise ProviderError(f'Pluggy HTTP {response.status_code}')

    def connect_token(self, owner_id):
        return self.request('POST','/connect_token',json={'options':{
            'clientUserId':owner_id, 'avoidDuplicates':True,
            'products':['ACCOUNTS','TRANSACTIONS']}})

    def item(self, item_id):
        from uuid import UUID
        return self.request('GET','/items/'+str(UUID(item_id)))

    def accounts(self, item_id):
        response=self.request('GET','/accounts',params={'itemId':item_id,'type':'BANK'})
        rows=response['results']
        if any(a.get('itemId')!=item_id for a in rows):
            raise ProviderError('Account belongs to another connection')
        if response.get('totalPages',1)>1:
            raise ProviderError('Unexpected accounts pagination')
        return [a for a in rows if a.get('type')=='BANK']

    def transactions(self, account_id):
        path='/v2/transactions'
        params={'accountId':account_id}
        rows=[]; seen=set()
        for _ in range(1000):
            response=self.request('GET',path,params=params)
            page=response['results']
            if any(t.get('accountId')!=account_id for t in page):
                raise ProviderError('Transaction belongs to another account')
            rows.extend(page)
            nxt=response.get('next')
            if not nxt: return rows
            if not isinstance(nxt,str) or not nxt.startswith('?') or nxt in seen:
                raise ProviderError('Invalid pagination cursor')
            seen.add(nxt)
            values=parse_qs(nxt[1:],keep_blank_values=True)
            if set(values)!={'accountId','after'} or values['accountId']!=[account_id] or len(values['after'])!=1 or not values['after'][0]:
                raise ProviderError('Unsafe pagination cursor')
            params={k:v[0] for k,v in values.items()}
        raise ProviderError('Pagination safety limit exceeded')

    def register_webhooks(self, url, secret, events):
        """Called explicitly at deployment, never from public API."""
        existing=self.request('GET','/webhooks').get('results',[])
        registered=[]
        for event in events:
            match=next((w for w in existing if w['url']==url and w['event']==event),None)
            if match:
                if (match.get('headers') or {}).get('X-ProntoGlosa-Webhook-Token')!=secret:
                    raise ProviderError('Existing webhook authentication must be verified explicitly before reuse')
                registered.append({'id':match['id'],'event':event,'existing':True})
                continue
            w=self.request('POST','/webhooks',json={'url':url,'event':event,
                'headers':{'X-ProntoGlosa-Webhook-Token':secret}})
            registered.append({'id':w['id'],'event':event,'existing':False})
        return registered
