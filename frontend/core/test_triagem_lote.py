"""Triagem: seleção limpa após recurso/acato em lote."""
from django.test import TestCase


class TriagemLoteTests(TestCase):
    def test_selecao_e_limpa_apos_tratamento_em_lote(self):
        from pathlib import Path

        base = Path(__file__).resolve().parent.parent / "templates"
        parcial = (base / "partials" / "triagem_atendimento.html").read_text()
        tela = (base / "conta_atendimento.html").read_text()
        layout = (base / "base.html").read_text()
        self.assertIn('@glosa-operacao-ok="concludeBatch()"', parcial)
        self.assertIn("concludeBatch() {\n          this.selectedForms = [];", tela)
        self.assertIn("new CustomEvent('glosa-operacao-ok', { bubbles: true })", layout)
