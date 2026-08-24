from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from http.cookies import SimpleCookie
from importlib import import_module
from urllib.parse import urlsplit, urlunsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.asgi import get_asgi_application
from websockets import ConnectionClosed
from websockets.asyncio.client import connect


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "glosas_frontend.settings")
django_application = get_asgi_application()
LOGGER = logging.getLogger(__name__)


class AuthenticatedSpuProxy:
    websocket_path = "/automacao/spu/vnc/websockify"

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "websocket"
            and scope.get("path") == self.websocket_path
        ):
            await self._proxy_websocket(scope, receive, send)
            return
        await self.application(scope, receive, send)

    async def _proxy_websocket(self, scope, receive, send):
        if (
            not _same_origin(scope)
            or not await _has_api_session(scope)
            or not await _recaptcha_is_active()
        ):
            await send({"type": "websocket.close", "code": 4401})
            return

        requested_protocols = list(scope.get("subprotocols") or ())
        try:
            async with connect(
                _upstream_websocket_url(),
                subprotocols=requested_protocols or None,
                open_timeout=settings.SPU_NOVNC_TIMEOUT,
                close_timeout=3,
                max_size=None,
                compression=None,
            ) as upstream:
                await send(
                    {
                        "type": "websocket.accept",
                        "subprotocol": upstream.subprotocol,
                    }
                )
                await _relay_websocket(receive, send, upstream)
        except Exception:
            LOGGER.exception("Falha no proxy WebSocket do desktop SPU.")
            with suppress(Exception):
                await send({"type": "websocket.close", "code": 1011})


async def _relay_websocket(receive, send, upstream):
    async def client_to_upstream():
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                await upstream.close()
                return
            if message["type"] != "websocket.receive":
                continue
            data = message.get("bytes")
            if data is None:
                data = message.get("text", "")
            await upstream.send(data)

    async def upstream_to_client():
        try:
            async for data in upstream:
                if isinstance(data, bytes):
                    await send({"type": "websocket.send", "bytes": data})
                else:
                    await send({"type": "websocket.send", "text": data})
        except ConnectionClosed:
            return

    tasks = {
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    }
    done, pending = await asyncio.wait(
        tasks,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


async def _has_api_session(scope) -> bool:
    headers = _headers(scope)
    cookie = SimpleCookie()
    cookie.load(headers.get("cookie", ""))
    session_cookie = cookie.get(settings.SESSION_COOKIE_NAME)
    if session_cookie is None:
        return False

    engine = import_module(settings.SESSION_ENGINE)

    @sync_to_async(thread_sensitive=True)
    def session_is_authenticated():
        session = engine.SessionStore(session_key=session_cookie.value)
        return bool(session.get("api_access_token"))

    return await session_is_authenticated()


@sync_to_async(thread_sensitive=True)
def _recaptcha_is_active() -> bool:
    from core.spu_recaptcha import (
        SpuNovncUnavailable,
        get_spu_recaptcha_status,
    )

    try:
        return get_spu_recaptcha_status()["active"]
    except SpuNovncUnavailable:
        return False


def _same_origin(scope) -> bool:
    headers = _headers(scope)
    origin = urlsplit(headers.get("origin", ""))
    host = headers.get("host", "").lower()
    return origin.scheme in {"http", "https"} and origin.netloc.lower() == host


def _headers(scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", ())
    }


def _upstream_websocket_url() -> str:
    parsed = urlsplit(settings.SPU_NOVNC_INTERNAL_URL)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = f"{parsed.path.rstrip('/')}/websockify"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


application = AuthenticatedSpuProxy(django_application)

