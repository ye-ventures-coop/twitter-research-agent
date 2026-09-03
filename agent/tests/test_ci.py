"""Тесты CI-провижининга grok2api с моком HTTP."""
from __future__ import annotations

import json
import unittest

import httpx

from app import ci


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )


class TestProvision(unittest.IsolatedAsyncioTestCase):
    async def test_login_ok(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/admin/v1/auth/login"
            body = json.loads(request.content)
            assert body == {"username": "admin", "password": "pw"}
            return httpx.Response(
                200, json={"data": {"tokens": {"accessToken": "tok-123"}}}
            )

        async with make_client(handler) as client:
            token = await ci.admin_login(client, "admin", "pw")
        self.assertEqual(token, "tok-123")

    async def test_login_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"code": "invalidCredentials"}})

        async with make_client(handler) as client:
            with self.assertRaises(ci.ProvisionError):
                await ci.admin_login(client, "admin", "wrong")

    async def test_import_sso_ok(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/admin/v1/accounts/web/import"
            assert request.headers["Authorization"] == "Bearer tok"
            assert b"sso-value-1" in request.content  # токен в multipart
            stream = (
                "event: progress\ndata: {}\n\n"
                'event: complete\ndata: {"created": 1, "updated": 0, '
                '"skipped": 0, "failed": 0, "synced": 1}\n\n'
            )
            return httpx.Response(200, text=stream)

        async with make_client(handler) as client:
            result = await ci.import_sso(client, "tok", "sso-value-1")
        self.assertEqual(result["created"], 1)

    async def test_import_sso_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            stream = 'event: complete\ndata: {"created": 0, "failed": 1}\n\n'
            return httpx.Response(200, text=stream)

        async with make_client(handler) as client:
            with self.assertRaises(ci.ProvisionError):
                await ci.import_sso(client, "tok", "bad-token")

    async def test_create_client_key(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/admin/v1/client-keys"
            return httpx.Response(
                201,
                json={"data": {"key": {"id": "1"}, "secret": "g2a_abc_secret"}},
            )

        async with make_client(handler) as client:
            secret = await ci.create_client_key(client, "tok")
        self.assertEqual(secret, "g2a_abc_secret")


if __name__ == "__main__":
    unittest.main()
