import httpx
import pytest
from openfinance.provider import Pluggy, ProviderError


def provider(handler):
    return Pluggy('client', 'secret', httpx.Client(transport=httpx.MockTransport(handler)))


def test_token_scope_and_auth_stays_on_server():
    calls=[]
    def handler(r):
        import json
        calls.append((r.url.path, json.loads(r.content)))
        return httpx.Response(200,json={'apiKey':'private'} if r.url.path=='/auth' else {'accessToken':'limited'})
    p=provider(handler)
    assert p.connect_token('opaque')['accessToken']=='limited'
    assert calls[-1]==('/connect_token', {'options':{'clientUserId':'opaque','avoidDuplicates':True,'products':['ACCOUNTS','TRANSACTIONS']}})


def test_cursor_and_bank_only_no_unsupported_products():
    paths=[]
    def handler(r):
        paths.append(r.url.path)
        if r.url.path=='/auth': return httpx.Response(200,json={'apiKey':'private'})
        if r.url.path=='/accounts': return httpx.Response(200,json={'results':[{'id':'a','itemId':'i','type':'BANK'},{'id':'c','itemId':'i','type':'CREDIT'}]})
        if r.url.params.get('after'): return httpx.Response(200,json={'results':[{'id':'t2','accountId':'a'}],'next':None})
        return httpx.Response(200,json={'results':[{'id':'t1','accountId':'a'}],'next':'?accountId=a&after=opaque%2Bcursor'})
    p=provider(handler)
    assert [x['id'] for x in p.accounts('i')]==['a']
    assert [x['id'] for x in p.transactions('a')]==['t1','t2']
    assert set(paths)<={'/auth','/accounts','/v2/transactions'}


@pytest.mark.parametrize('next_value',['https://evil.test/?accountId=a','//evil.test','?accountId=other&after=x','?accountId=a&after=x&extra=y'])
def test_rejects_unsafe_or_cross_account_cursor(next_value):
    def handler(r):
        return httpx.Response(200,json={'apiKey':'key'} if r.url.path=='/auth' else {'results':[],'next':next_value})
    with pytest.raises(ProviderError): provider(handler).transactions('a')


def test_intermediate_page_failure_never_returns_partial_result():
    def handler(r):
        if r.url.path=='/auth': return httpx.Response(200,json={'apiKey':'key'})
        if r.url.params.get('after'): return httpx.Response(503,json={'message':'secret detail'})
        return httpx.Response(200,json={'results':[{'id':'t','accountId':'a'}],'next':'?accountId=a&after=x'})
    with pytest.raises(ProviderError,match='503'): provider(handler).transactions('a')


def test_rejects_transactions_from_other_account():
    def handler(r):
        return httpx.Response(200,json={'apiKey':'key'} if r.url.path=='/auth' else {'results':[{'id':'t','accountId':'other'}],'next':None})
    with pytest.raises(ProviderError): provider(handler).transactions('a')


def test_never_allows_modifying_or_payment_resources():
    p=provider(lambda r: httpx.Response(200,json={'apiKey':'key'}))
    for method,path in [('DELETE','/items/id'),('PATCH','/items/id'),('POST','/accounts'),('POST','/payments')]:
        with pytest.raises(ProviderError): p.request(method,path)


def test_existing_webhook_with_unknown_auth_is_not_silently_reused():
    def handler(r):
        return httpx.Response(200,json={'apiKey':'key'} if r.url.path=='/auth' else {
            'results':[{'id':'w','event':'item/created','url':'https://example.test/hook','headers':{}}]})
    with pytest.raises(ProviderError):
        provider(handler).register_webhooks('https://example.test/hook','expected-secret',['item/created'])
