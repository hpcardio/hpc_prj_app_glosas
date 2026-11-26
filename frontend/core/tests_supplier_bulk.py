from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from html.parser import HTMLParser

BASE='/financeiro/contas-a-pagar/envios-em-massa/'
JOB={'id':'job-1','kind':'payments','channel':'email','status':'draft','version':1,'total':1,'page':1,'pages':1,
     'configuration':{'available':True,'enabled':True,'reason':'','body':'Aviso de pagamento e comprovantes.'},
     'counts':{'ready':1,'blocked':0,'accepted':0,'failed':0,'unknown':0,'queued':0,'preparing':0,'sending':0,'cancelled':0},
     'recipients':[{'id':'r1','target':'notice-1','destination':'fornecedor@example.com','status':'ready','error':'',
        'snapshot':{'name':'Fornecedor','company':1,'total':'100.00','documents':[{'id':'aviso','name':'Aviso PDF'},{'id':'bank','name':'comprovante.pdf'}]}}],
     'attempts':[],'needs_resend_confirmation':False}


class BulkViewTests(TestCase):
    def login(self,permissions=None):
        session=self.client.session;session['api_access_token']='test'
        session['api_user']={'id':7,'perfil':'usuario','nome':'Financeiro','telas_permitidas':permissions or ['contas_pagar_gestao','contas_pagar_nfse_regras']};session.save()

    @patch('core.supplier_bulk.api_post')
    def test_selected_suppliers_open_preview_without_dispatching(self,post):
        self.login();post.return_value=JOB
        response=self.client.post(BASE+'preparar/',{'kind':'updates','channel':'email','selected':['1','2'],'request_key':'request-test-1'})
        self.assertRedirects(response,BASE+'job-1/',fetch_redirect_response=False)
        self.assertEqual(post.call_args.args[1]['ids'],['1','2'])
        self.assertTrue(post.call_args.args[0].endswith('/prever'))

    @patch('core.supplier_bulk.api_get',return_value=JOB)
    @patch('core.supplier_bulk.api_post')
    def test_preview_get_shows_recipient_docs_and_never_sends(self,post,get):
        self.login();response=self.client.get(BASE+'job-1/')
        self.assertContains(response,'Conferir envio em massa')
        self.assertContains(response,'fornecedor@example.com')
        self.assertContains(response,'comprovante.pdf')
        self.assertContains(response,'Autorizar envio do lote')
        post.assert_not_called()

    @patch('core.supplier_bulk.api_get',return_value=JOB)
    @patch('core.supplier_bulk.api_post')
    def test_approval_requires_explicit_confirmation(self,post,get):
        self.login();response=self.client.post(BASE+'job-1/',{'action':'approve','version':'1'})
        self.assertContains(response,'Confirme a revisão')
        post.assert_not_called()

    @patch('core.supplier_bulk.api_post')
    def test_retry_targets_only_failed_rows(self,post):
        self.login();post.return_value=JOB
        response=self.client.post(BASE+'job-1/',{'action':'retry'})
        self.assertEqual(response.status_code,302)
        self.assertTrue(post.call_args.args[0].endswith('/acao/repetir-falhas'))

    @patch('core.supplier_bulk.api_post')
    def test_all_filtered_selection_is_explicit(self,post):
        self.login();post.return_value=JOB
        self.client.post(BASE+'preparar/',{'kind':'payments','channel':'email','all_filtered':'on','filters':'{"company":"1"}','request_key':'request-test-2'})
        self.assertTrue(post.call_args.args[1]['all_filtered'])
        self.assertEqual(post.call_args.args[1]['filters'],{'company':'1'})

    @patch('core.supplier_bulk.api_get')
    def test_restricted_staff_cannot_open_bulk_routes(self,get):
        self.login(['indicadores'])
        self.assertEqual(self.client.get(BASE+'job-1/').status_code,302)
        get.assert_not_called()

    @patch('core.supplier_updates.api_get')
    def test_supplier_selection_loads_script_and_does_not_nest_forms(self,get):
        self.login();get.return_value={'items':[{'id':1,'name':'Fornecedor','company':1,'sent_count':1,'has_response':False}],
            'total':1,'page':1,'pages':1,'companies':{}}
        response=self.client.get('/financeiro/contas-a-pagar/fornecedores/')
        self.assertContains(response,'js/supplier-updates.js')
        class Forms(HTMLParser):
            depth=0;nested=False
            def handle_starttag(self,tag,attrs):
                if tag=='form':self.depth+=1;self.nested=self.nested or self.depth>1
            def handle_endtag(self,tag):
                if tag=='form':self.depth-=1
        parser=Forms();parser.feed(response.content.decode())
        self.assertFalse(parser.nested)
