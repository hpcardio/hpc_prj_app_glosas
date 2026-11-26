import json
from unittest.mock import patch

from django.test import TestCase, Client

BASE = '/financeiro/contas-a-pagar/fornecedores/'
DETAIL = {'case': {'id': 1, 'company': 1, 'name': 'Fornecedor de teste', 'supplier': '12', 'document': '12345678000195',
    'contact': {}, 'stage': 'respondido', 'version': 2, 'validated_balance': None},
    'position': {'system_balance': '100.00', 'paid_period': '50.00', 'fingerprint': 'current', 'titles': []},
    'responses': [{'id': 'protocol', 'declared_balance': '80.00', 'payload': {'legal_name': 'Fornecedor', 'titles': [], 'agreements': [], 'as_of': '2026-11-26'}}],
    'events': [], 'attachments': [], 'reviews': [], 'can_review': True, 'comparison': 'divergencia', 'manual_payments': []}


class SupplierViewsTests(TestCase):
    def login(self, screens):
        session = self.client.session
        session['api_access_token'] = 'test'
        session['api_user'] = {'id': 7, 'nome': 'Financeiro', 'perfil': 'usuario', 'telas_permitidas': screens}
        session.save()

    @patch('core.supplier_updates.api_get')
    def test_private_screen_requires_access(self, api):
        self.login(['indicadores'])
        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 302)
        api.assert_not_called()

    @patch('core.supplier_updates.api_get')
    def test_native_list_and_navigation(self, api):
        self.login(['contas_pagar_gestao'])
        api.return_value = {'items': [], 'total': 0, 'counts': {}, 'companies': {}, 'page': 1, 'pages': 1}
        response = self.client.get(BASE)
        self.assertContains(response, 'Comunicações aos fornecedores')
        self.assertContains(response, 'Nova solicitação')
        self.assertNotContains(response, 'Saldo atual · MV')
        self.assertContains(response, 'css/app.css')

    @patch('core.supplier_updates.api_get', return_value=DETAIL)
    def test_three_positions_and_no_automatic_debt_update(self, api):
        self.login(['contas_pagar_gestao'])
        response = self.client.get(BASE + '1/?tab=conferencia')
        self.assertContains(response, 'Posição no MV')
        self.assertContains(response, 'Declarado pelo fornecedor')
        self.assertContains(response, 'Validado pelo financeiro')
        self.assertContains(response, 'Ainda não validado')
        self.assertContains(response, 'não altera títulos no MV')

    @patch('core.supplier_updates.api_get', return_value=DETAIL)
    def test_response_page_does_not_stack_message_and_payment_forms(self,api):
        self.login(['contas_pagar_gestao'])
        response=self.client.get(BASE+'1/')
        self.assertContains(response,'Dados enviados pelo fornecedor')
        self.assertNotContains(response,'Gerar link individual')
        self.assertNotContains(response,'Enviar e-mail agora')
        self.assertNotContains(response,'Salvar contato')

    @patch('core.supplier_updates.api_get')
    def test_read_only_response_does_not_claim_totals_were_compared(self,api):
        self.login(['contas_pagar_gestao'])
        api.return_value={k:v for k,v in DETAIL.items() if k not in ('position','comparison','reviews','manual_payments')}
        response=self.client.get(BASE+'1/')
        self.assertNotContains(response,'Totais iguais')

    @patch('core.supplier_updates.public_request')
    def test_public_portal_does_not_require_login_and_is_not_cached(self, api):
        api.return_value = {'name': 'Fornecedor', 'document': '12345678000195', 'company': 1}
        response = self.client.get('/fornecedor/atualizacao/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Atualização cadastral e conciliação financeira')
        self.assertNotContains(response, 'nav-group-toggle')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')

    @patch('core.supplier_updates.public_request')
    def test_other_paths_are_not_public(self, api):
        response = self.client.get('/fornecedor/atualizacao/admin/')
        self.assertEqual(response.status_code, 302)
        api.assert_not_called()

    @patch('core.supplier_updates.api_post')
    def test_open_whatsapp_only_prepares_message(self, api):
        self.login(['contas_pagar_gestao'])
        api.return_value = {'ok': True}
        response = self.client.post(BASE + '1/', {'action': 'prepare_whatsapp', 'destination': '85999999999'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(api.call_args.args[1]['result'], 'preparado')

    @patch('core.supplier_updates.api_post')
    def test_email_is_only_sent_on_explicit_post(self, api):
        self.login(['contas_pagar_gestao'])
        session = self.client.session
        session['supplier_invite_1'] = {'token': 'a'*43, 'message': 'Mensagem', 'link': 'https://example.com'}
        session.save()
        api.return_value = {'status': 'aceito_smtp'}
        response = self.client.post(BASE + '1/', {'action': 'send_email', 'destination': 'ana@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(api.call_args.args[0].endswith('/email'))

    @patch('core.supplier_updates.public_request')
    def test_public_response_receives_protocol_without_private_position(self, api):
        api.return_value = {'protocol': 'abc123', 'status': 'aguardando_validacao'}
        response = self.client.post('/fornecedor/atualizacao/', {'token': 'a'*43, 'payload': json.dumps({'no_debt': True})})
        self.assertContains(response, 'abc123')
        self.assertContains(response, 'aguardam validação')
        self.assertNotContains(response, 'system_balance')

    @patch('core.supplier_updates.public_request')
    def test_consultation_token_is_posted_without_exposing_staff_data(self, api):
        api.return_value = {'name': 'Fornecedor', 'document': '12345678000195', 'company': 1}
        response = self.client.post('/fornecedor/atualizacao/', {'action': 'consult', 'token': 'a'*43})
        self.assertEqual(response.json()['name'], 'Fornecedor')
        api.assert_called_once_with('/consultar', data={'token': 'a'*43})

    @patch('core.supplier_updates.public_request')
    def test_public_submission_keeps_csrf_protection(self, api):
        api.return_value = {'protocol':'csrf-test','status':'aguardando_validacao'}
        client = Client(enforce_csrf_checks=True)
        client.get('/fornecedor/atualizacao/')
        payload = {'token':'a'*43,'payload':'{}'}
        self.assertEqual(client.post('/fornecedor/atualizacao/', payload).status_code,403)
        payload['csrfmiddlewaretoken'] = client.cookies['csrftoken'].value
        self.assertEqual(client.post('/fornecedor/atualizacao/',payload,HTTP_ORIGIN='http://testserver').status_code,200)

    def test_oversized_public_post_is_rejected_before_csrf_parses_files(self):
        client = Client(enforce_csrf_checks=True)
        response = client.generic('POST','/fornecedor/atualizacao/',b'',CONTENT_LENGTH=str(27*1024*1024))
        self.assertEqual(response.status_code,413)
