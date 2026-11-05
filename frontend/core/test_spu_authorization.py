from unittest.mock import patch
from django.http import HttpResponse
from django.test import SimpleTestCase, RequestFactory
from .access import can_access_route, screen_access_context
from .services import ApiError
from .spu_access import profile_can_use_spu, require_spu_permission, token_can_use_spu


class SpuAuthorizationTests(SimpleTestCase):
    def test_any_active_user_can_solve_the_captcha(self):
        self.assertTrue(profile_can_use_spu({'ativo': True, 'perfil': 'usuario', 'telas_permitidas': []}))
        self.assertTrue(profile_can_use_spu({'ativo': True, 'perfil': 'ti'}))
        self.assertFalse(profile_can_use_spu({'ativo': False, 'perfil': 'ti'}))
        self.assertFalse(profile_can_use_spu({'perfil': 'ti'}))
        self.assertFalse(profile_can_use_spu(None))

    def test_modal_routes_do_not_require_a_screen(self):
        user = {'ativo': True, 'perfil': 'usuario', 'telas_permitidas': []}
        for route in ('spu_recaptcha_status', 'spu_recaptcha_viewer', 'spu_novnc_asset'):
            self.assertTrue(can_access_route(user, route))

    def test_modal_is_rendered_only_for_active_users(self):
        request = RequestFactory().get('/')
        request.session = {'api_user': {'ativo': True, 'perfil': 'usuario', 'telas_permitidas': []}}
        self.assertTrue(screen_access_context(request)['spu_access_allowed'])
        request.session = {'api_user': {'ativo': False, 'perfil': 'ti'}}
        self.assertFalse(screen_access_context(request)['spu_access_allowed'])
        request.session = {}
        self.assertFalse(screen_access_context(request)['spu_access_allowed'])

    @patch('core.spu_access.api_get')
    def test_revoked_session_and_api_outage_fail_closed(self, get):
        get.side_effect = ApiError('synthetic unavailable', 503)
        self.assertFalse(token_can_use_spu('synthetic'))
        get.assert_called_once_with('/usuarios/me', token='synthetic', timeout=5)
        get.reset_mock()
        self.assertFalse(token_can_use_spu(None))
        get.assert_not_called()

    @patch('core.spu_access.token_can_use_spu', return_value=False)
    def test_denied_http_proxy_does_not_execute_upstream(self, check):
        called = []
        request = RequestFactory().get('/spu')
        request.session = {'api_access_token': 'synthetic'}
        @require_spu_permission
        def view(request):
            called.append(True)
            return HttpResponse('private')
        result = view(request)
        self.assertEqual(result.status_code, 403)
        self.assertEqual(called, [])
        self.assertIn('no-store', result['Cache-Control'])
