from unittest.mock import patch
from uuid import uuid4
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from .services import ApiError

O=str(uuid4()); A=str(uuid4()); OTHER=str(uuid4())
OWNERS=[{'id':O,'label':'Hospital demonstração','kind':'PJ'}]
ACCOUNTS={'accounts':[{'id':A,'connection_id':OTHER,'name':'Conta de teste','number':'1234',
    'currency':'BRL','available_balance':'123.450000','closing_balance':None}],
    'connections':[{'id':OTHER,'status':'UPDATED','active':True,'sandbox':True,'last_sync':1793226000,'last_error':None}]}


class OpenFinanceTests(TestCase):
    def setUp(self):
        self.client=Client()
        session=self.client.session
        session['api_access_token']='jwt-server-only'
        session['api_user']={'id':24,'perfil':'usuario','telas_permitidas':['contas_pagar_gestao','acompanhamento_particular']}
        session.save()
        self.url='/financeiro/saldos-extratos/'

    def api(self,path,**kwargs):
        if path.endswith('/owners'): return OWNERS
        if path.endswith('/health'):return {'sandbox_only':True,'provider_configured':True}
        if path.endswith('/accounts'):return ACCOUNTS
        if path.endswith('/transactions'):return {'total':1,'page':1,'page_size':100,'results':[{
            'id':OTHER,'description':'Teste <script>bad</script>','amount':'-10.550000','currency':'BRL',
            'date':'2026-10-28T12:00:00Z','type':'DEBIT','status':'PENDING'}]}
        raise AssertionError(path)

    @patch('core.openfinance.api_get')
    def test_page_shows_balance_pending_status_and_no_private_tokens(self,get):
        get.side_effect=self.api
        response=self.client.get(self.url,{'owner':O,'account':A})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'123,45')
        self.assertContains(response,'Não informado')
        self.assertContains(response,'Pendente')
        self.assertContains(response,'Dados fictícios')
        self.assertContains(response,'Teste &lt;script&gt;bad&lt;/script&gt;')
        self.assertNotContains(response,'jwt-server-only')
        self.assertContains(response,'Saldos e extratos')

    @patch('core.openfinance.api_get')
    def test_foreign_owner_and_account_not_forwarded_to_api(self,get):
        get.side_effect=self.api
        r=self.client.get(self.url,{'owner':OTHER,'account':A})
        self.assertEqual(r.status_code,404)
        get.assert_called_once_with('/app_glosas/openfinance/owners')
        get.reset_mock()
        r=self.client.get(self.url,{'owner':O,'account':OTHER})
        self.assertEqual(r.status_code,404)
        self.assertFalse(any('/transactions' in c.args[0] for c in get.call_args_list))

    @patch('core.openfinance.api_get')
    def test_invalid_dates_do_not_fetch_statement(self,get):
        get.side_effect=self.api
        r=self.client.get(self.url,{'owner':O,'account':A,'date_from':'2026-10-30','date_to':'2026-10-01'})
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'período')
        self.assertFalse(any('/transactions' in c.args[0] for c in get.call_args_list))

    @patch('core.openfinance.api_get')
    def test_errors_are_sanitized(self,get):
        get.side_effect=ApiError('postgres password=VERY_PRIVATE',503)
        r=self.client.get(self.url)
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'temporariamente indisponível')
        self.assertNotContains(r,'VERY_PRIVATE')

    @patch('core.openfinance.api_get')
    def test_operation_permission_does_not_grant_bank_data(self,get):
        session=self.client.session;session['api_user']['telas_permitidas']=['contas_pagar_operacao','acompanhamento_particular'];session.save()
        r=self.client.get(self.url)
        self.assertIn(r.status_code,[302,403])
        get.assert_not_called()

    @patch('core.openfinance.api_post')
    def test_connect_token_requires_post_and_csrf_and_forwards_only_owner(self,post):
        url=f'/financeiro/saldos-extratos/titulares/{O}/conectar/'
        self.assertEqual(self.client.get(url).status_code,405)
        strict=Client(enforce_csrf_checks=True); strict.cookies=self.client.cookies
        self.assertEqual(strict.post(url).status_code,403)
        post.return_value={'accessToken':'limited','sandbox_only':True,
                           'widget_config':{'connectorIds':[2],'products':['ACCOUNTS','TRANSACTIONS'],'includeSandbox':True}}
        r=self.client.post(url)
        self.assertEqual(r.status_code,200)
        self.assertEqual(r['Cache-Control'],'private, no-store')
        post.assert_called_once_with(f'/app_glosas/openfinance/owners/{O}/connect-token',{})

    @patch('core.openfinance.api_post')
    def test_owner_form_rejects_blank_label_and_wrong_kind(self,post):
        self.assertEqual(self.client.post(self.url,{'label':' ','kind':'PJ'}).status_code,200)
        self.assertEqual(self.client.post(self.url,{'label':'Hospital','kind':'unknown'}).status_code,200)
        post.assert_not_called()

    @patch('core.openfinance.api_post')
    def test_success_callback_passes_only_item_id(self,post):
        post.return_value={'queued':True}
        r=self.client.post(f'/financeiro/saldos-extratos/titulares/{O}/vincular/',
            data={'item_id':OTHER,'payments':True},content_type='application/json')
        self.assertEqual(r.status_code,202)
        post.assert_called_once_with(f'/app_glosas/openfinance/owners/{O}/connections',{'item_id':OTHER})

    @patch('core.openfinance.api_get')
    def test_no_owners_shows_empty_state(self,get):
        get.side_effect=lambda path,**kw: [] if path.endswith('/owners') else {'sandbox_only':True}
        r=self.client.get(self.url)
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'Cadastre um titular')

    @patch('core.openfinance.api_post')
    def test_live_connect_token_is_never_sent_over_http(self,post):
        post.return_value={'accessToken':'never-expose-this','sandbox_only':False,'widget_config':{}}
        r=self.client.post(f'/financeiro/saldos-extratos/titulares/{O}/conectar/')
        self.assertEqual(r.status_code,400)
        self.assertNotContains(r,'never-expose-this',status_code=400)

    @override_settings(SESSION_COOKIE_SECURE=True)
    @patch('core.openfinance.api_get')
    def test_existing_http_session_gets_secure_cookie_on_first_bank_page(self,get):
        get.side_effect=self.api
        r=self.client.get(self.url,secure=True)
        self.assertTrue(r.cookies['sessionid']['secure'])

    @patch('core.openfinance.api_get')
    def test_currency_label_is_preserved_when_not_brl(self,get):
        from copy import deepcopy
        def response(path,**kwargs):
            data=deepcopy(self.api(path,**kwargs))
            for row in data.get('accounts',[]) if isinstance(data,dict) else []:row['currency']='USD'
            for row in data.get('results',[]) if isinstance(data,dict) else []:row['currency']='USD'
            return data
        get.side_effect=response
        r=self.client.get(self.url,{'owner':O,'account':A})
        self.assertContains(r,'USD 123,45')
        self.assertNotContains(r,'R$ 123,45')
