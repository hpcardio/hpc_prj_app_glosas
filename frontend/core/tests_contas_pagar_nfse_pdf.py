from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.services import ApiError

URL = '/financeiro/contas-a-pagar/nfse-do-setor/'
RASCUNHO = {
    'id': 41, 'status': 'rascunho', 'nome_arquivo': 'nf-sp.pdf', 'reconhecido': True,
    'avisos': ['As retenções lidas (R$ 10,00) não fecham com o total do DANFSe (R$ 12,00).'],
    'informativos': {'municipio_emissor': 'SÃO PAULO/SP'},
    'dados': {'numero_nfse': '6631', 'data_emissao': '2026-11-06T12:07:03', 'competencia_nfse': '2026-11-01',
              'chave_acesso': '35503081207595068000100000000000663126110386618210', 'prestador_cnpj': '07595068000100',
              'prestador_razao_social': 'MULTIMEDICOS SERVICOS MEDICOS LTDA', 'prestador_municipio': 'SÃO PAULO/SP',
              'tomador_cnpj': '08711085000128', 'tomador_razao_social': 'HOSPITAL', 'valor_servicos': '18551.40',
              'iss_retido': '1', 'valor_iss': '556.54', 'valor_ir': '278.27', 'valor_csll': '862.64',
              'valor_liquido_nfse': '16853.95', 'discriminacao': 'Serviços médicos', 'aliquota': '3.00'},
}


class CadastroPdfTests(TestCase):
    def setUp(self):
        session = self.client.session
        session['api_access_token'] = 'token'
        session['api_user'] = {'id': 7, 'nome': 'Setor', 'perfil': 'usuario', 'telas_permitidas': ['contas_pagar_nfse_setor']}
        session.save()
        vinculo = patch('core.views._mv_vinculo', return_value=None)
        vinculo.start()
        self.addCleanup(vinculo.stop)

    def _api_get(self, path, params=None, **kwargs):
        return RASCUNHO if '/recebidas/manual/' in path else {'oficinas': [], 'itens': []}

    @patch('core.views.api_get')
    def test_botao_e_card_de_envio(self, api_get):
        api_get.side_effect = self._api_get
        response = self.client.get(URL)
        self.assertContains(response, 'Cadastro de NFS-e de outro município')
        self.assertContains(response, '?cadastrar=1#cadastro', html=False)
        response = self.client.get(URL + '?cadastrar=1')
        self.assertContains(response, '1 de 2 · Enviar o PDF')
        self.assertContains(response, 'name="arquivo_nfse"', html=False)
        html = response.content.decode()
        # Seção própria, antes e fora do painel da lista de notas.
        self.assertLess(html.index('name="arquivo_nfse"'), html.index('cf-resultados'))

    @patch('core.views.api_post_files', return_value={'id': 41, 'reconhecido': True})
    @patch('core.views.api_get')
    def test_envio_le_o_pdf_e_abre_a_conferencia(self, api_get, api_post_files):
        api_get.side_effect = self._api_get
        arquivo = SimpleUploadedFile('nf.pdf', b'%PDF-1.4', content_type='application/pdf')
        response = self.client.post(URL, {'acao': 'ler_nfse_pdf', 'arquivo_nfse': arquivo})
        self.assertEqual(api_post_files.call_args.args[0], '/app_glosas/financeiro/contas-a-pagar/nfse/recebidas/manual/leitura')
        self.assertEqual(response['Location'], URL + '?conferir=41#cadastro')

    @patch('core.views.api_get')
    def test_conferencia_mostra_dados_lidos_editaveis_e_avisos(self, api_get):
        api_get.side_effect = self._api_get
        response = self.client.get(URL + '?conferir=41')
        self.assertContains(response, '2 de 2 · Conferir os dados')
        self.assertContains(response, 'nf-sp.pdf · SÃO PAULO/SP')
        self.assertContains(response, 'name="valor_servicos" value="18.551,40"', html=False)
        self.assertContains(response, 'name="data_emissao" value="2026-11-06T12:07"', html=False)
        self.assertContains(response, 'não fecham com o total do DANFSe')
        self.assertContains(response, 'issRetido: true')

    @patch('core.views.api_post', return_value={'confirmado': True, 'chave': '07595068000100-6631', 'numero_nfse': '6631'})
    @patch('core.views.api_get')
    def test_confirmar_envia_os_dados_e_abre_a_nota_na_fila(self, api_get, api_post):
        api_get.side_effect = self._api_get
        response = self.client.post(URL, {'acao': 'confirmar_nfse_pdf', 'manual_id': '41', 'numero_nfse': '6631',
                                          'valor_servicos': '18.551,40', 'iss_retido': '1', 'discriminacao': 'Serviços'})
        path, corpo = api_post.call_args.args
        self.assertEqual(path, '/app_glosas/financeiro/contas-a-pagar/nfse/recebidas/manual/41/confirmar')
        self.assertEqual(corpo['dados']['valor_servicos'], '18.551,40')
        self.assertEqual(corpo['dados']['iss_retido'], '1')
        self.assertEqual(response['Location'], URL + '?situacao=todas&nota=07595068000100-6631#nota-07595068000100-6631')

    @patch('core.views.api_post', side_effect=ApiError('{"detail": {"erros": ["O tomador não é uma empresa do grupo no MV (confira o CNPJ do tomador)."]}}', status_code=422))
    @patch('core.views.api_get')
    def test_erro_de_validacao_mantem_o_que_o_usuario_digitou(self, api_get, api_post):
        api_get.side_effect = self._api_get
        response = self.client.post(URL, {'acao': 'confirmar_nfse_pdf', 'manual_id': '41', 'tomador_cnpj': '11222333000181',
                                          'valor_servicos': '100,00'}, follow=True)
        self.assertContains(response, 'O tomador não é uma empresa do grupo no MV')
        self.assertContains(response, 'name="tomador_cnpj" value="11222333000181"', html=False)
        self.assertContains(response, 'name="valor_servicos" value="100,00"', html=False)

    @patch('core.views.api_delete')
    @patch('core.views.api_get')
    def test_descartar(self, api_get, api_delete):
        api_get.side_effect = self._api_get
        self.client.post(URL, {'acao': 'descartar_nfse_pdf', 'manual_id': '41'})
        api_delete.assert_called_once_with('/app_glosas/financeiro/contas-a-pagar/nfse/recebidas/manual/41')

    @patch('core.views.api_get')
    def test_card_da_nota_por_pdf_mostra_origem_e_excluir(self, api_get):
        nota = {'chave': '07595068000100-6631', 'prestador_cnpj': '07595068000100', 'prestador': 'MULTIMEDICOS', 'numero_nfse': '6631',
                'data_emissao': '2026-11-06', 'valor_servicos': '18551.40', 'valor_liquido': '16853.95', 'situacao': 'pendente',
                'origem_nfse': 'pdf', 'nfse_manual_id': 41, 'status_nfse': None, 'boletos': None, 'pronta': False,
                'ds_oficina': 'CENTRAL', 'cd_oficina': 5, 'empresa': 'HOSPITAL', 'tomador': 'HOSPITAL', 'mv': None}
        api_get.side_effect = lambda path, params=None, **kwargs: {'oficinas': [], 'itens': [nota]}
        response = self.client.get(URL)
        self.assertContains(response, 'Cadastrada por PDF')
        self.assertContains(response, 'name="acao" value="excluir_nfse_pdf"', html=False)

    @patch('core.views.api_post', return_value={'excluido': True})
    @patch('core.views.api_get', return_value={'oficinas': [], 'itens': []})
    def test_excluir_cadastro_com_motivo(self, api_get, api_post):
        self.client.post(URL, {'acao': 'excluir_nfse_pdf', 'manual_id': '41', 'motivo': 'PDF errado'})
        api_post.assert_called_once_with('/app_glosas/financeiro/contas-a-pagar/nfse/recebidas/manual/41/excluir', {'motivo': 'PDF errado'})
