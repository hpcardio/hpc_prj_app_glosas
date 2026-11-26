import json
from unittest.mock import patch
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile

BASE='/financeiro/contas-a-pagar/confirmacao-pagamentos/'
ITEM={'key':'mv:1:9:42','company':1,'company_name':'Hospital','supplier':'12','name':'Fornecedor',
      'document':'12345678000195','reference':'NF1/1','date':'2026-11-20','amount':'100.00','parcel':'9','payment_id':'42'}
PREVIEW={'items':[ITEM],'total':'100.00','fingerprint':'a'*64}
NOTICE={'confirmation':{'id':'notice-1','name':'Fornecedor','company':1,'document':'12345678000195',
    'items':[ITEM],'total':'100.00','confirmed_at':'2026-11-26T12:00:00Z'},'files':[{'id':'file-1','name':'comprovante.pdf'}],
    'events':[],'contact':{},'stage':'confirmado','active_link_ids':['link-1']}


class SupplierPaymentViewsTests(TestCase):
    def login(self):
        session=self.client.session
        session['api_access_token']='test'
        session['api_user']={'id':7,'perfil':'ti','nome':'Financeiro','telas_permitidas':[]}
        session.save()

    def prepare(self):
        session=self.client.session
        session['supplier_payment_selection']={'company':1,'supplier':'12','keys':['mv:1:9:42'],'preview':PREVIEW}
        session.save()

    @patch('core.supplier_payments.api_get')
    def test_payment_list_separate_from_registration_messages(self,api):
        self.login();api.return_value={'items':[ITEM],'total':1,'page':1,'pages':1,'can_confirm':True}
        response=self.client.get(BASE)
        self.assertContains(response,'Confirmação de pagamentos')
        self.assertContains(response,'NF1/1')
        self.assertNotContains(response,'Razão social')
        self.assertNotContains(response,'Gerar link individual')

    @patch('core.supplier_payments.api_post')
    def test_selection_opens_dedicated_confirmation(self,api):
        self.login();api.return_value=PREVIEW
        response=self.client.post(BASE,{'selected':['1|12|mv:1:9:42']})
        self.assertRedirects(response,BASE+'confirmar/',fetch_redirect_response=False)
        self.assertEqual(self.client.session['supplier_payment_selection']['keys'],['mv:1:9:42'])

    def test_confirmation_form_requires_receipt_and_financial_acknowledgment(self):
        self.login();self.prepare()
        response=self.client.get(BASE+'confirmar/')
        self.assertContains(response,'Comprovante bancário')
        self.assertContains(response,'Conferi a efetivação do pagamento')
        self.assertContains(response,'name="files"')
        self.assertNotContains(response,'Atualização cadastral')

    @patch('core.supplier_payments.api_post_files')
    def test_confirm_upload_creates_notice_without_sending(self,api):
        self.login();self.prepare()
        api.return_value={'id':'notice-1','token':'t'*43,'link_id':'link-1','link':'https://example.com/#token','message':'Aviso'}
        receipt=SimpleUploadedFile('comprovante.pdf',b'%PDF-1.4\nx',content_type='application/pdf')
        response=self.client.post(BASE+'confirmar/',{'fingerprint':'a'*64,'confirmed':'on','files':receipt,'note':'Conferido.'})
        self.assertRedirects(response,BASE+'avisos/notice-1/',fetch_redirect_response=False)
        self.assertEqual(api.call_args.args[0].split('/')[-1],'confirmar')

    @patch('core.supplier_payments.api_get',return_value=NOTICE)
    @patch('core.supplier_payments.api_post')
    def test_notice_get_never_sends_email(self,post,get):
        self.login()
        response=self.client.get(BASE+'avisos/notice-1/')
        self.assertContains(response,'Aviso de pagamento')
        self.assertContains(response,'Baixar aviso em PDF')
        self.assertContains(response,'comprovante.pdf')
        post.assert_not_called()

    @patch('core.supplier_payments.public_payment_request')
    def test_public_receipt_page_does_not_require_login(self,api):
        response=self.client.get('/fornecedor/pagamento/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Aviso e comprovantes de pagamento')
        api.assert_not_called()

    @patch('core.supplier_payments.public_payment_request')
    def test_public_receipt_query_uses_token_in_post(self,api):
        api.return_value={'name':'Fornecedor','total':'100.00','items':[ITEM],'files':[]}
        response=self.client.post('/fornecedor/pagamento/',{'action':'consult','token':'a'*43})
        self.assertEqual(response.json()['total'],'100.00')
        api.assert_called_once_with('/consultar',{'token':'a'*43})

    @patch('core.supplier_payments.api_post_files')
    def test_other_tab_selection_cannot_confirm_unintended_items(self,api):
        self.login();self.prepare()
        response=self.client.post(BASE+'confirmar/',{'fingerprint':'b'*64,'confirmed':'on'})
        self.assertContains(response,'seleção foi alterada')
        api.assert_not_called()
