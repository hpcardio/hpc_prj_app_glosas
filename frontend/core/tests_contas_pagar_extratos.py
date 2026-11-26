from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.services import ApiError

URL = '/financeiro/contas-a-pagar/extratos/'
PATH = '/app_glosas/financeiro/contas-a-pagar/extratos'
CONTAS = [
    {'cd_con_cor': 1, 'ds_con_cor': '08-BB C/C 211433-X', 'cd_multi_empresa': 1},
    {'cd_con_cor': 20, 'ds_con_cor': '05 BB', 'cd_multi_empresa': 2},
]
EXTRATOS = [{
    'id': 9, 'nome_arquivo': '08 BB.pdf', 'nome_banco': 'Banco do Brasil', 'banco': 1, 'agencia': '2917',
    'conta': '211433', 'conta_dv': 'X', 'cd_con_cor': 1, 'ds_con_cor': '08-BB C/C 211433-X',
    'periodo_inicio': '2026-11-19', 'periodo_fim': '2026-11-23', 'saldo_final': '19878.75', 'conferido': True,
    'avisos': [], 'qtd_linhas': 68, 'linhas_repetidas': 1, 'enviado_por_nome': 'Financeiro',
    'criado_em': '2026-11-23T12:00:00+00:00',
}]
SUGESTOES = {
    'cd_con_cor': 1, 'ds_con_cor': '08-BB C/C 211433-X', 'desde': '2026-11-16', 'ate': '2026-11-23',
    'janela_dias': {'antes': 5, 'depois': 10},
    'resumo': {'unico': 1, 'ambiguo': 1, 'sem_titulo': 1},
    'sugestoes': [
        {'linha_id': 5, 'data': '2026-11-18', 'historico': 'Pagamento de Boleto JB FARMA', 'documento': '811',
         'valor': '450.00', 'situacao': 'unico', 'titulos_em_outros_debitos': [],
         'candidatos': [{'cd_itcon_pag': 222704, 'cd_con_pag': 108001, 'nr_parcela': 1, 'nm_fornecedor': 'FORNECEDOR TESTE',
                         'nr_documento': '123', 'dt_vencimento': '2026-11-11', 'saldo': '450.00', 'dias': 7}]},
        {'linha_id': 6, 'data': '2026-11-18', 'historico': 'TED Transf', 'documento': None, 'valor': '252.00',
         'situacao': 'ambiguo', 'titulos_em_outros_debitos': [333],
         'candidatos': [{'cd_itcon_pag': 333, 'cd_con_pag': 1, 'nr_parcela': 1, 'nm_fornecedor': 'CLINICA A',
                         'nr_documento': '1', 'dt_vencimento': '2026-11-18', 'saldo': '252.00', 'dias': 0}]},
        {'linha_id': 7, 'data': '2026-11-18', 'historico': 'Tar DOC/TED', 'documento': None, 'valor': '13.60',
         'situacao': 'sem_titulo', 'titulos_em_outros_debitos': [], 'candidatos': []},
    ],
}
SIMULACAO = {
    'linha_id': 5, 'cd_itcon_pag': 222704, 'pode_baixar': True, 'pendencias': [],
    'avisos': ['A parcela está no lote de pagamento 743; confira antes de baixar.'], 'gravacao_habilitada': False,
    'titulo': {'cd_con_pag': 108001, 'nr_parcela': 1, 'nm_fornecedor': 'FORNECEDOR TESTE', 'dt_vencimento': '2026-11-11'},
    'pagcon_pag': {'cd_itcon_pag': 222704, 'vl_pago': '450.00', 'tp_pagamento': '5', 'cd_processo': 28},
    'mov_concor': {'cd_lan_concor': 14, 'nr_documento_identificacao': 'DEB C/C- 222704', 'cd_processo': 547},
    'efeitos_no_mv': ['TRG_PAGCON_PAG quita a parcela.'],
}


def _api_get(path, params=None):
    if path.endswith('/contas-mv'):
        return {'contas': CONTAS}
    if path.endswith('/baixas/sugestoes'):
        return SUGESTOES
    return {'extratos': EXTRATOS}


class ExtratosTests(TestCase):
    def setUp(self):
        session = self.client.session
        session['api_access_token'] = 'token'
        session['api_user'] = {'id': 4, 'nome': 'Amoras', 'perfil': 'usuario', 'telas_permitidas': ['contas_pagar_nfse_regras']}
        session.save()

    @patch('core.views.api_get', side_effect=_api_get)
    def test_lista_extratos_e_sugestoes_da_conta(self, api_get):
        response = self.client.get(URL, {'conta': 1, 'desde': '2026-11-16', 'ate': '2026-11-23'})

        self.assertEqual(response.status_code, 200)
        api_get.assert_any_call(f'{PATH}/baixas/sugestoes', {'cd_con_cor': 1, 'desde': '2026-11-16', 'ate': '2026-11-23'})
        self.assertContains(response, '4. Extratos e baixa')
        self.assertContains(response, '08 BB.pdf')
        self.assertContains(response, 'FORNECEDOR TESTE')
        self.assertContains(response, '7d depois')
        self.assertContains(response, 'também casa com outro débito')
        self.assertContains(response, 'pagamento em lote, tarifa ou transferência')
        self.assertContains(response, '1 repetidas')
        self.assertContains(response, 'simular=5&amp;titulo=222704')

    @patch('core.views.api_get', side_effect=_api_get)
    def test_filtra_situacao_sem_nova_consulta(self, api_get):
        response = self.client.get(URL, {'conta': 1, 'situacao': 'unico'})
        self.assertContains(response, 'Pagamento de Boleto')
        self.assertNotContains(response, 'Tar DOC/TED')

    @patch('core.views.api_post', return_value=SIMULACAO)
    @patch('core.views.api_get', side_effect=_api_get)
    def test_simulacao_mostra_o_que_seria_gravado_sem_gravar(self, api_get, api_post):
        response = self.client.get(URL, {'conta': 1, 'simular': 5, 'titulo': 222704})

        api_post.assert_called_once_with(f'{PATH}/linhas/5/simulacao', {'cd_itcon_pag': 222704})
        self.assertContains(response, 'Pode ser baixada')
        self.assertContains(response, 'lote de pagamento 743')
        self.assertContains(response, 'DEB C/C- 222704')
        self.assertContains(response, 'nada foi gravado no MV')

    @patch('core.views.api_post_files')
    def test_envio_de_varios_arquivos_mostra_resultado_de_cada_um(self, api_post_files):
        api_post_files.side_effect = [
            {'id': 9, 'nome_banco': 'Banco do Brasil', 'agencia': '2917', 'conta': '211433', 'conta_dv': 'X',
             'ds_con_cor': '08-BB C/C 211433-X', 'periodo_inicio': '2026-11-19', 'periodo_fim': '2026-11-23',
             'qtd_linhas': 68, 'linhas_repetidas': 1, 'conferido': True, 'avisos': []},
            ApiError('O extrato não fecha: a soma dos lançamentos difere.', status_code=422),
        ]
        response = self.client.post(URL, {
            'acao': 'enviar',
            'arquivos': [SimpleUploadedFile('08 BB.pdf', b'%PDF-1.4 a', 'application/pdf'),
                         SimpleUploadedFile('05 BB.pdf', b'%PDF-1.4 b', 'application/pdf')],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(api_post_files.call_count, 2)
        nome, conteudo, tipo = api_post_files.call_args_list[0].args[1]['arquivo']
        self.assertEqual((nome, conteudo, tipo), ('08 BB.pdf', b'%PDF-1.4 a', 'application/pdf'))

        with patch('core.views.api_get', side_effect=_api_get):
            pagina = self.client.get(response['Location'])
        self.assertContains(pagina, 'Recebido e conferido com o saldo')
        self.assertContains(pagina, 'O extrato não fecha')
        self.assertContains(pagina, '1 de 2 extrato(s) recebido(s).')
        # O resultado do envio aparece uma vez só.
        with patch('core.views.api_get', side_effect=_api_get):
            self.assertNotContains(self.client.get(URL), 'Recebido e conferido com o saldo')

    @patch('core.views.api_put', return_value={})
    def test_troca_a_conta_do_mv_do_extrato(self, api_put):
        response = self.client.post(URL, {'acao': 'conta', 'extrato_id': '9', 'cd_con_cor': '20'})
        self.assertEqual(response.status_code, 302)
        api_put.assert_called_once_with(f'{PATH}/9/conta', {'cd_con_cor': 20})

    @patch('core.views.api_put', return_value={})
    def test_extrato_pode_sair_da_baixa(self, api_put):
        self.client.post(URL, {'acao': 'conta', 'extrato_id': '9', 'cd_con_cor': ''})
        api_put.assert_called_once_with(f'{PATH}/9/conta', {'cd_con_cor': None})

    @patch('core.views.api_delete', return_value={})
    def test_exclui_extrato(self, api_delete):
        self.client.post(URL, {'acao': 'excluir', 'extrato_id': '9'})
        api_delete.assert_called_once_with(f'{PATH}/9')

    @patch('core.views.api_get', side_effect=_api_get)
    def test_sem_extratos_pede_o_envio(self, api_get):
        api_get.side_effect = lambda path, params=None: {'contas': CONTAS} if path.endswith('/contas-mv') else {'extratos': []}
        response = self.client.get(URL)
        self.assertContains(response, 'Envie os extratos e escolha a conta corrente')


class ExtratosAcessoTests(TestCase):
    def test_setor_nao_acessa(self):
        session = self.client.session
        session['api_access_token'] = 'token'
        session['api_user'] = {'id': 7, 'perfil': 'usuario', 'telas_permitidas': ['contas_pagar_nfse_setor']}
        session.save()
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn('acesso_negado=1', response['Location'])
