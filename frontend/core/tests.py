from datetime import date
from unittest.mock import patch

from django.contrib.staticfiles import finders
from django.test import RequestFactory, TestCase

from core.views import (
    apply_dashboard_filters,
    build_recuperacao_indicators,
    get_dashboard_filters,
    subtract_months,
)


class DashboardIndicadoresTests(TestCase):
    def test_dashboard_filters_aplica_periodo_padrao_de_doze_meses(self):
        request = RequestFactory().get('/indicadores/')
        today = date.today()

        filtros = get_dashboard_filters(request)

        self.assertEqual(
            filtros['periodo_inicio'],
            subtract_months(today, 11).strftime('%Y-%m-%d'),
        )
        self.assertEqual(filtros['periodo_fim'], today.strftime('%Y-%m-%d'))

    def test_dashboard_filters_preserva_periodo_informado(self):
        request = RequestFactory().get(
            '/indicadores/',
            {
                'periodo_inicio': '2026-01-10',
                'periodo_fim': '2026-03-20',
            },
        )

        filtros = get_dashboard_filters(request)

        self.assertEqual(filtros['periodo_inicio'], '2026-01-10')
        self.assertEqual(filtros['periodo_fim'], '2026-03-20')

    def test_dashboard_filters_aceita_multiplos_valores(self):
        request = RequestFactory().get(
            '/indicadores/',
            {
                'convenio': ['AMIL', 'BACEN'],
                'prestador': ['Prestador A', 'Prestador B'],
                'tipo_atendimento': ['Urgência', 'Internação'],
                'motivo_glosa': ['Motivo A', 'Motivo B'],
            },
        )

        filtros = get_dashboard_filters(request)

        self.assertEqual(filtros['convenio'], ['AMIL', 'BACEN'])
        self.assertEqual(filtros['prestador'], ['Prestador A', 'Prestador B'])
        self.assertEqual(filtros['tipo_atendimento'], ['Urgência', 'Internação'])
        self.assertEqual(filtros['motivo_glosa'], ['Motivo A', 'Motivo B'])

    def test_dashboard_filters_aplica_multiplos_valores(self):
        rows = [
            {
                'data_glosa': '2026-01-10',
                'convenio': 'AMIL',
                'prestador': 'Prestador A',
                'tp_atendimento': 'Urgência',
                'motivo_glosa': 'Motivo A',
                'sn_glosado': 'true',
            },
            {
                'data_glosa': '2026-01-10',
                'convenio': 'BACEN',
                'prestador': 'Prestador B',
                'tp_atendimento': 'Internação',
                'motivo_glosa': 'Motivo B',
                'sn_glosado': 'true',
            },
            {
                'data_glosa': '2026-01-10',
                'convenio': 'BRADESCO',
                'prestador': 'Prestador C',
                'tp_atendimento': 'Externo',
                'motivo_glosa': 'Motivo C',
                'sn_glosado': 'true',
            },
        ]

        filtered = apply_dashboard_filters(
            rows,
            {
                'periodo_inicio': '2026-01-01',
                'periodo_fim': '2026-01-31',
                'convenio': ['AMIL', 'BACEN'],
                'prestador': ['Prestador A', 'Prestador B'],
                'tipo_atendimento': ['Urgência', 'Internação'],
                'motivo_glosa': ['Motivo A', 'Motivo B'],
                'tratativa': '',
            },
        )

        self.assertEqual(len(filtered), 2)
        self.assertEqual([row['convenio'] for row in filtered], ['AMIL', 'BACEN'])

    def test_recuperacao_exibe_todos_motivos_com_valor(self):
        rows = [
            {
                'sn_ativo': 'true',
                'sn_glosado': 'true',
                'processo_recurso': f'REC-{index}',
                'dt_recurso': '2026-01-10',
                'data_glosa': '2026-01-01',
                'motivo_glosa': f'Motivo {index:02d}',
                'convenio': 'Convenio A',
                'valor_glosado': 1000 + index,
                'valor_recebido': 500,
            }
            for index in range(13)
        ]

        indicadores = build_recuperacao_indicators(rows)

        self.assertEqual(indicadores['total_motivos'], 13)
        self.assertEqual(len(indicadores['scatter']), 13)

    def test_recuperacao_mensal_usa_periodo_informado(self):
        indicadores = build_recuperacao_indicators(
            [
                {
                    'sn_ativo': 'true',
                    'sn_glosado': 'true',
                    'processo_recurso': 'REC-1',
                    'dt_recurso': '2026-02-10',
                    'data_glosa': '2026-02-01',
                    'motivo_glosa': 'Motivo teste',
                    'convenio': 'Convenio A',
                    'valor_glosado': 1000,
                    'valor_recebido': 500,
                }
            ],
            '2026-01-10',
            '2026-03-20',
        )

        self.assertEqual(
            indicadores['mensal']['months'],
            ['01/2026', '02/2026', '03/2026'],
        )
        self.assertEqual(indicadores['mensal']['month_count'], 3)
        self.assertEqual(indicadores['mensal']['period_label'], '01/2026 a 03/2026')

    def test_recuperacao_tooltip_usa_legenda_de_eficiencia_operacional(self):
        indicadores = build_recuperacao_indicators(
            [
                {
                    'sn_ativo': 'true',
                    'sn_glosado': 'true',
                    'processo_recurso': 'REC-1',
                    'dt_recurso': '2026-01-10',
                    'data_glosa': '2026-01-01',
                    'motivo_glosa': 'Motivo teste',
                    'convenio': 'Convenio A',
                    'valor_glosado': 1000,
                    'valor_recebido': 500,
                }
            ]
        )

        tooltip = indicadores['scatter'][0]['tooltip']

        self.assertIn(
            'Taxa Eficiência Op. (vl. recuperado / vl. recursado): 50.0%',
            tooltip,
        )
        self.assertNotIn('Taxa de sucesso do recurso', tooltip)


class LoginFlowTests(TestCase):
    def test_renderiza_tela_de_login(self):
        response = self.client.get('/login/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gestão de Glosas')
        self.assertContains(response, 'login-brand-slogan')
        self.assertContains(response, 'Hospital Prontocardio')
        self.assertIsNotNone(finders.find('img/roger.jpeg'))

    def test_redireciona_visitante_para_login(self):
        response = self.client.get('/')

        self.assertRedirects(
            response,
            '/login/?next=%2F',
            fetch_redirect_response=False,
        )

    @patch('core.views.api_get')
    @patch('core.views.api_authenticate')
    def test_login_armazena_token_e_usuario(self, authenticate, api_get):
        authenticate.return_value = {
            'access_token': 'token-seguro',
            'token_type': 'Bearer',
        }
        api_get.return_value = {
            'id': 1,
            'nome': 'Usuário Teste',
            'email': 'usuario@teste.com',
        }

        response = self.client.post(
            '/login/',
            {
                'email': 'usuario@teste.com',
                'password': 'senha',
                'next': '/',
            },
        )

        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertEqual(
            self.client.session['api_access_token'],
            'token-seguro',
        )
        self.assertEqual(
            self.client.session['api_user']['nome'],
            'Usuário Teste',
        )
        api_get.assert_called_once_with('/usuarios/me', token='token-seguro')

    @patch('core.views.api_get')
    @patch('core.views.api_authenticate')
    def test_login_rejeita_redirecionamento_externo(self, authenticate, api_get):
        authenticate.return_value = {'access_token': 'token-seguro'}
        api_get.return_value = {
            'id': 1,
            'nome': 'Usuário Teste',
            'email': 'usuario@teste.com',
        }

        response = self.client.post(
            '/login/',
            {
                'email': 'usuario@teste.com',
                'password': 'senha',
                'next': 'https://site-malicioso.example',
            },
        )

        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_logout_limpa_sessao(self):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {'nome': 'Usuário Teste'}
        session.save()

        response = self.client.post('/logout/')

        self.assertRedirects(
            response,
            '/login/',
            fetch_redirect_response=False,
        )
        self.assertNotIn('api_access_token', self.client.session)

    @patch('core.views.api_post')
    def test_solicita_recuperacao_de_senha(self, api_post):
        response = self.client.post(
            '/esqueci-senha/', {'email': 'usuario@teste.com'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Verifique seu e-mail')
        api_post.assert_called_once_with(
            '/autenticacao/esqueci-senha',
            {'email': 'usuario@teste.com'},
        )

    @patch('core.views.api_post')
    def test_redefine_senha_com_token(self, api_post):
        response = self.client.post(
            '/redefinir-senha/',
            {
                'token': 'token-seguro-com-tamanho-suficiente',
                'password': 'nova-senha-segura',
                'password_confirmation': 'nova-senha-segura',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Senha atualizada')
        api_post.assert_called_once()

    def test_rota_redefinicao_compativel_com_api(self):
        response = self.client.get(
            '/autenticacao/redefinir-senha/',
            {'token': 'token-seguro-com-tamanho-suficiente'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Crie uma nova senha')

    def test_bloqueia_gestao_de_acessos_para_usuario_comum(self):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'nome': 'Usuário',
            'perfil': 'usuario',
        }
        session.save()

        response = self.client.get('/administrativo/acessos/')

        self.assertRedirects(response, '/', fetch_redirect_response=False)
