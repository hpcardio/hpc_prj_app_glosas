from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings

from glosas_frontend.asgi import (
    AuthenticatedSpuProxy,
    _same_origin,
    _upstream_websocket_url,
)


class SpuWebsocketProxyTests(SimpleTestCase):
    def test_same_origin_accepts_frontend_and_rejects_external_site(self):
        scope = {
            'headers': [
                (b'host', b'receita.example.com'),
                (b'origin', b'https://receita.example.com'),
            ]
        }
        self.assertTrue(_same_origin(scope))

        scope['headers'][1] = (b'origin', b'https://externo.example.com')
        self.assertFalse(_same_origin(scope))

    @override_settings(
        SPU_NOVNC_INTERNAL_URL='http://spu-novnc:6080/base'
    )
    def test_upstream_usa_websocket_da_rede_docker_interna(self):
        self.assertEqual(
            _upstream_websocket_url(),
            'ws://spu-novnc:6080/base/websockify',
        )

    def test_websocket_sem_origem_autenticada_e_recusado(self):
        sent = []

        async def receive():
            return {'type': 'websocket.connect'}

        async def send(message):
            sent.append(message)

        scope = {
            'type': 'websocket',
            'path': '/automacao/spu/vnc/websockify',
            'headers': [],
        }
        proxy = AuthenticatedSpuProxy(application=None)

        async_to_sync(proxy._proxy_websocket)(scope, receive, send)

        self.assertEqual(
            sent,
            [{'type': 'websocket.close', 'code': 4401}],
        )

