import json
from unittest.mock import Mock, patch
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.template.loader import render_to_string
from core import petrobras
from core.access import ROUTE_PERMISSIONS
from core.services import ApiError


@override_settings(PETROBRAS_ENABLED=True)
class PetrobrasTests(SimpleTestCase):
    def request(self, method="get", body=None):
        req = getattr(RequestFactory(), method)(
            "/follow-up-glosas/petrobras/glosas",
            data=json.dumps(body or {}) if method != "get" else {},
            content_type="application/json",
        )
        req.session = {"api_user": {"perfil": "usuario"}}
        return req

    def test_routes_use_followup_permission(self):
        self.assertEqual(ROUTE_PERMISSIONS["petrobras_bridge"], "follow_up_glosas")

    @patch("core.petrobras.api_request")
    def test_proxy_preserves_auth_context_and_query(self, api):
        upstream = Mock(
            content=b'{"total":0,"items":[]}',
            status_code=200,
            headers={"Content-Type": "application/json"},
        )
        api.return_value = upstream
        req = self.request()
        req.GET = req.GET.copy()
        req.GET["guia"] = "000123"
        response = petrobras.bridge(req, "glosas")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(api.call_args.kwargs["params"], {"guia": "000123"})
        self.assertEqual(
            api.call_args.args[:2], ("GET", "/app_glosas/petrobras/glosas")
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        upstream.close.assert_called_once()

    @patch("core.petrobras.api_request")
    def test_arbitrary_proxy_paths_blocked(self, api):
        for path in [
            "../usuarios",
            "glosas/1/../../usuarios",
            "https://example.com",
            "glosas?guia=x",
        ]:
            self.assertEqual(petrobras.bridge(self.request(), path).status_code, 404)
        api.assert_not_called()

    @patch("core.petrobras.api_request")
    def test_readonly_cannot_mutate(self, api):
        req = self.request("post")
        req.session["api_user"]["perfil"] = "leitura"
        self.assertEqual(petrobras.bridge(req, "protocolos").status_code, 403)
        api.assert_not_called()

    @override_settings(PETROBRAS_ENABLED=False)
    @patch("core.petrobras.api_request")
    def test_disabled_rollout_does_not_call_api(self, api):
        self.assertEqual(petrobras.bridge(self.request(), "status").status_code, 404)
        api.assert_not_called()

    @patch(
        "core.petrobras.api_request",
        side_effect=ApiError("<html>upstream traceback</html>", 502),
    )
    def test_upstream_html_is_not_exposed(self, api):
        response = petrobras.bridge(self.request(), "glosas")
        self.assertNotIn(b"traceback", response.content)
        self.assertEqual(response.status_code, 502)

    def test_template_escapes_convenio_and_uses_static_scripts(self):
        req = self.request()
        html = render_to_string(
            "partials/petrobras_followup.html",
            {"request": req, "filtros": {"convenio": "<script>alert(1)</script>"}},
        )
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("petrobras-followup.js", html)
        self.assertIn("petrobras-followup.css", html)


    @patch("core.petrobras.api_request")
    def test_empty_optional_filters_are_not_forwarded(self, api):
        api.return_value = Mock(
            content=b'{"total":242,"items":[]}', status_code=200,
            headers={"Content-Type": "application/json"},
        )
        req = self.request()
        req.GET = req.GET.copy()
        req.GET.update({"prazo_ate": "", "tuss": " ", "offset": "0"})
        self.assertEqual(petrobras.bridge(req, "glosas").status_code, 200)
        self.assertEqual(api.call_args.kwargs["params"], {"offset": "0"})

    @patch("core.petrobras.api_request")
    def test_valid_deadline_is_preserved(self, api):
        api.return_value = Mock(
            content=b'{"total":1,"items":[]}', status_code=200,
            headers={"Content-Type": "application/json"},
        )
        req = self.request()
        req.GET = req.GET.copy()
        req.GET["prazo_ate"] = "2026-10-30"
        petrobras.bridge(req, "glosas")
        self.assertEqual(
            api.call_args.kwargs["params"], {"prazo_ate": "2026-10-30"}
        )
