"""Configuração por Convênio: seção de retenções (ISS, IR, PIS, COFINS, CSLL)."""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core import views

RETENCOES = [
    {"cd_convenio": 7, "convenio": "BACEN", "iss": 3.0, "ir": 1.2, "pis": 0.65, "cofins": 3.0,
     "csll": 1.0, "total": 8.85, "observacao": None, "configurado": True,
     "atualizado_por_nome": "Financeiro"},
    {"cd_convenio": 9, "convenio": "BRADESCO", "iss": 0, "ir": 0, "pis": 0, "cofins": 0,
     "csll": 0, "total": 0, "observacao": None, "configurado": False},
]


def _api_get(path, *args, **kwargs):
    if path == views.RETENCOES_CONVENIO_PATH:
        return {"convenios": RETENCOES}
    return {"convenios": [{"cd_convenio": 7, "convenio": "BACEN", "dias_para_recurso": 10,
                           "configurado": True, "habilitado": True}]}


class RetencoesConvenioTests(TestCase):
    def setUp(self):
        session = self.client.session
        session["api_access_token"] = "token-seguro"
        session["api_user"] = {"id": 7, "nome": "Financeiro", "email": "fin@teste.com",
                               "perfil": "usuario", "telas_permitidas": ["configuracao_convenio"]}
        session.save()
        self.url = reverse("prazos_recurso_convenio")

    @patch("core.views.api_get", side_effect=_api_get)
    def test_secao_mostra_percentuais_e_total(self, _api_get):
        response = self.client.get(self.url)
        self.assertContains(response, "Retenções por convênio")
        self.assertContains(response, 'name="iss_7" data-retencao-imposto')
        self.assertContains(response, 'value="1,20"')
        self.assertContains(response, "8,85%")
        self.assertContains(response, "1 de 2 convênios configurados")

    @patch("core.views.api_put", return_value={"alterados": 1})
    def test_salva_percentuais_com_virgula(self, api_put):
        response = self.client.post(self.url, {
            "form_action": "retencoes",
            "ret_cd_convenio": ["7", "9"],
            "ret_convenio_7": "BACEN", "iss_7": "3,00", "ir_7": "1,2", "pis_7": "0,65",
            "cofins_7": "3", "csll_7": "1", "ret_obs_7": "",
            "ret_convenio_9": "BRADESCO", "iss_9": "", "ir_9": "", "pis_9": "", "cofins_9": "",
            "csll_9": "", "ret_obs_9": "Sem retenção",
        })
        self.assertRedirects(response, f"{self.url}#retencoes", fetch_redirect_response=False)
        path, payload = api_put.call_args.args
        self.assertEqual(path, views.RETENCOES_CONVENIO_PATH)
        self.assertEqual(payload[0], {"cd_convenio": 7, "convenio": "BACEN", "iss": "3.00",
                                      "ir": "1.2", "pis": "0.65", "cofins": "3", "csll": "1",
                                      "observacao": None})
        self.assertEqual(payload[1]["iss"], "0")
        self.assertEqual(payload[1]["observacao"], "Sem retenção")

    @patch("core.views.api_put")
    def test_percentual_invalido_nao_salva(self, api_put):
        self.client.post(self.url, {
            "form_action": "retencoes", "ret_cd_convenio": ["7"], "ret_convenio_7": "BACEN",
            "iss_7": "150", "ir_7": "", "pis_7": "", "cofins_7": "", "csll_7": "",
        })
        api_put.assert_not_called()
