from unittest.mock import patch

from django.test import TestCase

from core.services import ApiError

URL = '/financeiro/contas-a-pagar/conferencia/'
BOLETO = {'id': 4, 'vencimento': '2026-11-20', 'valor': '548.80', 'banco': '341', 'linha_digitavel': '34191790010104351004791020150008112340000054880',
          'enviado_em': '2026-11-18T09:15:00', 'enviado_por_nome': 'Setor TI', 'prazo_minimo': '2026-11-24', 'fora_do_prazo': True,
          'justificativa_atraso': 'O fornecedor só enviou o boleto hoje.', 'divergencias': []}
ITENS = [
    {'chave': '41644220000135-5744268', 'prestador_cnpj': '41644220000135', 'prestador': 'DB3 SERVICOS DE TELECOMUNICACOES S.A',
     'numero_nfse': '5744268', 'data_emissao': '2026-11-13', 'empresa': 'HOSPITAL PRONTOCARDIO', 'cd_multi_empresa': 1,
     'ds_oficina': 'TECNOLOGIA DA INFORMACAO', 'status_nfse': 'NORMAL', 'mv': None, 'etapa': 'a_importar',
     'valor_servicos': '560.00', 'valor_retido': '11.20', 'valor_liquido': '548.80', 'primeiro_vencimento': '2026-11-20',
     'fora_do_prazo': True, 'boletos': [BOLETO], 'pendencias': [], 'importacao': None, 'pode_desfazer': False},
    {'chave': '63554067000198-93125822', 'prestador_cnpj': '63554067000198', 'prestador': 'HAPVIDA ASSISTENCIA MEDICA S.A.',
     'numero_nfse': '93125822', 'data_emissao': '2026-11-01', 'empresa': 'PRONTOCARDIO PRONTO ATENDIMENTO', 'cd_multi_empresa': 2,
     'ds_oficina': 'CENTRAL DE RECEBIMENTO FISCAL', 'status_nfse': 'NORMAL', 'mv': {'cd_con_pag': 108679}, 'etapa': 'importada',
     'valor_servicos': '2708.73', 'valor_retido': '0.00', 'valor_liquido': '2708.73', 'primeiro_vencimento': '2026-11-25',
     'fora_do_prazo': False, 'boletos': [{**BOLETO, 'id': 5, 'vencimento': '2026-11-25', 'valor': '2708.73', 'fora_do_prazo': False, 'justificativa_atraso': None}],
     'pendencias': [], 'pode_desfazer': True,
     'importacao': {'status': 'importada', 'cd_con_pag': 108679, 'cd_ent_serv': 12463, 'cd_usuario_mv': 'RH000938',
                    'importado_por_nome': 'Amoras', 'concluido_em': '2026-11-18T14:24:18+00:00'}},
]
PAYLOAD = {'desde': '2026-10-01', 'prazo_minimo_hoje': '2026-11-24T10:00:00', 'fora_do_prazo': 1, 'itens': ITENS,
           'resumo': {'a_importar': {'quantidade': 1, 'valor_liquido': '548.80'}, 'pendencia': {'quantidade': 0, 'valor_liquido': '0'},
                      'importada': {'quantidade': 1, 'valor_liquido': '2708.73'}, 'lancada_mv': {'quantidade': 29, 'valor_liquido': '0'},
                      'cancelada': {'quantidade': 0, 'valor_liquido': '0'}}}
PREVIA = {'pode_desfazer': True, 'pendencias': [], 'gravacao_habilitada': True, 'usuario_mv': 'RH000938',
          'nota': {'numero_nfse': '93125822', 'prestador_razao_social': 'HAPVIDA ASSISTENCIA MEDICA S.A.'},
          'importacao': {'cd_usuario_mv': 'RH000938'},
          'titulo': {'cd_con_pag': 108679, 'vl_bruto_conta': '2708.73'}, 'impostos': [],
          'parcelas': [{'nr_parcela': 1, 'dt_vencimento': '2026-11-25T00:00:00', 'vl_duplicata': '2708.73'}],
          'entrada': {'cd_ent_serv': 12463}, 'itens': [{'cd_itent_serv': 12561}], 'qt_duplicatas': 1,
          'alteracoes_no_mv': [{'dt_modificacao': '2026-11-18T12:02:31', 'cd_usuario': 'RH000850',
                                'campos': 'Conta alterada. Campos: Observação: 05 / DP / PLANO DE SAUDE NOV/26; '}]}


class ConferenciaTests(TestCase):
    def setUp(self):
        session = self.client.session
        session['api_access_token'] = 'token'
        session['api_user'] = {'id': 4, 'nome': 'Amoras', 'perfil': 'usuario',
                               'telas_permitidas': ['contas_pagar_nfse_setor', 'contas_pagar_nfse_regras']}
        session.save()
        vinculo = patch('core.views._mv_vinculo', return_value={'pode_gravar_no_mv': True, 'cd_usuario_mv': 'RH000938', 'origem': 'cas'})
        vinculo.start()
        self.addCleanup(vinculo.stop)

    def _api_get(self, path, params=None, **kwargs):
        return PREVIA if path.endswith('/importacao/desfazer') else PAYLOAD

    @patch('core.views.api_get')
    def test_lista_notas_com_boletos_prazo_e_importacao(self, api_get):
        api_get.side_effect = self._api_get
        response = self.client.get(URL)
        api_get.assert_called_once_with('/app_glosas/financeiro/contas-a-pagar/nfse/financeiro/conferencia', {'etapa': 'todas'}, timeout=45)
        self.assertContains(response, 'Conferência do financeiro')
        self.assertContains(response, 'DB3 SERVICOS DE TELECOMUNICACOES S.A')
        self.assertContains(response, 'Fora do prazo')
        self.assertContains(response, '“O fornecedor só enviou o boleto hoje.”')
        self.assertContains(response, '<small>RETIDO</small><strong>R$ 11,20</strong>', html=False)
        self.assertContains(response, 'follow-up-glosa-card cf-card cf-card--a_importar')
        self.assertContains(response, '<span>A importar · 1</span><strong>R$ 548,80</strong>', html=False)
        self.assertContains(response, 'Simular e importar')
        self.assertContains(response, '?situacao=pronta&nota=41644220000135-5744268&simular=1#simulacao', html=False)
        self.assertContains(response, 'Desfazer importação')
        self.assertContains(response, 'boletos enviados hoje vencem a partir de 24/11/2026')
        self.assertNotContains(response, 'nfse-do-setor/?cadastrar=1#cadastro', html=False)
        # Login MV no topo da página (mesmo layout da NFS-e do setor), fora do painel da lista.
        html = response.content.decode()
        self.assertLess(html.index('mv-login-status--topo'), html.index('cf-resultados'))
        self.assertContains(response, '6. Conferência do financeiro')
        conteudo = response.content.decode()
        self.assertLess(conteudo.index('5744268'), conteudo.index('93125822'))  # mantém a ordem por vencimento

    @patch('core.views.api_get')
    def test_campo_setor_usa_o_setor_do_mv_e_mostra_a_oficina_no_titulo(self, api_get):
        itens = [{**ITENS[0], 'cd_setor': 12, 'nm_setor': 'TI'}, {**ITENS[1], 'cd_setor': None, 'nm_setor': None}]
        api_get.return_value = {**PAYLOAD, 'itens': itens}
        response = self.client.get(URL)
        self.assertContains(response, '<small>SETOR</small><strong title="Oficina: TECNOLOGIA DA INFORMACAO · HOSPITAL PRONTOCARDIO">TI</strong>', html=False)
        # Sem setor no MV, cai no nome da oficina, como antes.
        self.assertContains(response, '>CENTRAL DE RECEBIMENTO FISCAL</strong>', html=False)

    @patch('core.views.api_get')
    def test_busca_por_fornecedor_e_filtro_de_etapa(self, api_get):
        api_get.side_effect = self._api_get
        response = self.client.get(URL + '?busca=hapvida&etapa=importada')
        api_get.assert_called_once_with('/app_glosas/financeiro/contas-a-pagar/nfse/financeiro/conferencia', {'etapa': 'importada'}, timeout=45)
        self.assertContains(response, 'HAPVIDA ASSISTENCIA')
        self.assertNotContains(response, 'DB3 SERVICOS DE TELECOMUNICACOES S.A</span>')

    @patch('core.views.api_get')
    def test_previa_do_desfazer_mostra_o_que_sai_do_mv(self, api_get):
        api_get.side_effect = self._api_get
        response = self.client.get(URL + '?desfazer=63554067000198-93125822')
        self.assertContains(response, 'cf-card--desfazer')
        self.assertContains(response, '<small>NFS-E</small><strong>93125822</strong>', html=False)
        self.assertContains(response, '108679')
        self.assertContains(response, '12463')
        self.assertContains(response, 'Este título foi alterado no MV depois da importação.')
        self.assertContains(response, 'RH000850')
        self.assertContains(response, 'name="tem_alteracoes" value="1"', html=False)
        self.assertEqual(response.content.decode().count('name="confirmo"'), 2)

    @patch('core.views.api_get')
    def test_previa_com_bloqueio_nao_mostra_o_botao(self, api_get):
        bloqueada = {**PREVIA, 'pode_desfazer': False, 'pendencias': ['Existe pagamento para o título, parcela 1.']}
        api_get.side_effect = lambda path, params=None, **kwargs: bloqueada if path.endswith('/desfazer') else PAYLOAD
        response = self.client.get(URL + '?desfazer=63554067000198-93125822')
        self.assertContains(response, 'Não é possível desfazer pelo Receita Certa')
        self.assertContains(response, 'Existe pagamento para o título, parcela 1.')
        self.assertNotContains(response, 'name="motivo"')

    @patch('core.views.api_post', return_value={'desfeita': True, 'cd_usuario_mv': 'RH000938', 'cd_con_pag': 108679, 'cd_ent_serv': 12463, 'impostos': []})
    @patch('core.views.api_get')
    def test_desfaz_com_motivo_e_confirmacoes(self, api_get, api_post):
        api_get.side_effect = self._api_get
        response = self.client.post(URL, {'acao': 'desfazer', 'nota': '63554067000198-93125822', 'cd_con_pag': '108679',
                                          'tem_alteracoes': '1', 'confirmo': ['exclusao', 'alteracoes'],
                                          'motivo': '  Boleto anexado   errado pelo setor ', 'etapa': 'importada'}, follow=True)
        api_post.assert_called_once_with('/app_glosas/financeiro/contas-a-pagar/nfse/recebidas/63554067000198/93125822/importacao/desfazer',
                                         {'motivo': 'Boleto anexado errado pelo setor', 'cd_con_pag': 108679})
        self.assertContains(response, 'Importação da NFS-e 93125822 desfeita no MV por RH000938')
        self.assertEqual(response.redirect_chain[-1][0], URL + '?etapa=importada')

    @patch('core.views.api_post')
    @patch('core.views.api_get')
    def test_sem_a_segunda_confirmacao_nao_desfaz(self, api_get, api_post):
        api_get.side_effect = self._api_get
        response = self.client.post(URL, {'acao': 'desfazer', 'nota': '63554067000198-93125822', 'cd_con_pag': '108679',
                                          'tem_alteracoes': '1', 'confirmo': ['exclusao'], 'motivo': 'Boleto anexado errado'}, follow=True)
        api_post.assert_not_called()
        self.assertContains(response, 'marque as confirmações')

    @patch('core.views.api_post', side_effect=ApiError('O MV recusou; nada foi alterado. ORA-02292: integrity constraint', status_code=422))
    @patch('core.views.api_get')
    def test_recusa_do_mv_vira_mensagem(self, api_get, api_post):
        api_get.side_effect = self._api_get
        response = self.client.post(URL, {'acao': 'desfazer', 'nota': '63554067000198-93125822', 'cd_con_pag': '108679',
                                          'tem_alteracoes': '0', 'confirmo': ['exclusao'], 'motivo': 'Boleto anexado errado'}, follow=True)
        self.assertContains(response, 'A importação não foi desfeita')
        self.assertContains(response, 'ORA-02292')


class ConferenciaAcessoTests(TestCase):
    def test_setor_nao_acessa(self):
        session = self.client.session
        session['api_access_token'] = 'token'
        session['api_user'] = {'id': 7, 'perfil': 'usuario', 'telas_permitidas': ['contas_pagar_nfse_setor']}
        session.save()
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn('acesso_negado=1', response['Location'])
