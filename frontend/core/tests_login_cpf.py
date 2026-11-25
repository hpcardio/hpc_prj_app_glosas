from unittest.mock import patch

from django.test import TestCase

from core.services import ApiError


class LoginCpfLoginMvTests(TestCase):
    def test_campo_aceita_texto_livre(self):
        response = self.client.get('/login/')

        self.assertContains(response, 'type="text" name="email"', html=False)
        self.assertContains(response, 'E-mail, CPF ou login MV')

    @patch('core.views.api_get', return_value={'id': 9, 'nome': 'Setor', 'perfil': 'usuario',
                                               'telas_permitidas': ['contas_pagar_nfse_setor']})
    @patch('core.views.api_authenticate', return_value={'access_token': 't'})
    def test_cpf_e_login_mv_vao_para_a_api(self, api_authenticate, _):
        for login in ('529.982.247-25', 'RH000938'):
            self.client.post('/login/', {'email': login, 'password': 'prontocardio'})
            api_authenticate.assert_called_with(login.lower(), 'prontocardio')
            self.client.logout()

    @patch('core.views.api_authenticate', side_effect=ApiError('{"detail":"x"}', 401))
    def test_erro_fala_em_login(self, _):
        response = self.client.post('/login/', {'email': 'RH000938', 'password': 'x'})

        self.assertContains(response, 'Login ou senha incorretos.')
