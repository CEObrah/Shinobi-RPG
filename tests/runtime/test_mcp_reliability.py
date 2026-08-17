from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("mcp")
pytest.importorskip("fastapi")
pytest.importorskip("jwt")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken

from shinobi_runtime.api.mcp import McpOAuthSettings, create_mcp_server, mount_mcp


class FakeTokenVerifier:
    async def verify_token(self, token):
        if token != "valid-token":
            return None
        return AccessToken(
            token=token,
            client_id="chatgpt-test",
            scopes=["shinobi:read"],
            expires_at=4_102_444_800,
            resource="shinobi-audience",
            subject="player-test",
        )


def oauth_settings() -> McpOAuthSettings:
    return McpOAuthSettings(
        public_url="https://game.example/mcp",
        issuer_url="https://issuer.example/",
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="shinobi-audience",
        algorithms=("RS256",),
        read_scope="shinobi:read",
        write_scope="shinobi:write",
        allowed_subjects=("player-test",),
        allowed_client_ids=("chatgpt-test",),
        preview_secret="s" * 64,
        allowed_origins=("https://chatgpt.com",),
    )


class ExplodingOperations:
    def play_context(self):
        raise RuntimeError("sensitive implementation detail")


def test_unexpected_read_failure_is_structured_and_does_not_escape() -> None:
    server = create_mcp_server(
        ExplodingOperations(),
        oauth_settings(),
        token_verifier=FakeTokenVerifier(),
    )

    async def exercise():
        result = await server.call_tool("get_play_context", {})
        assert result.structured_content == {
            "ok": False,
            "result": None,
            "error": {
                "code": "runtime_internal_error",
                "retryable": True,
                "refresh_context": False,
            },
        }

    asyncio.run(exercise())


class _TrackingSessionManager:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    @asynccontextmanager
    async def run(self):
        self.entered += 1
        try:
            yield
        finally:
            self.exited += 1


class _DummyMcpServer:
    def __init__(self) -> None:
        self.session_manager = _TrackingSessionManager()

    def streamable_http_app(self, **_kwargs):
        return FastAPI()


def test_mount_mcp_enters_session_manager_from_host_lifespan() -> None:
    app = FastAPI()
    server = _DummyMcpServer()

    mount_mcp(
        app,
        server,  # type: ignore[arg-type]
        oauth_settings(),
        max_request_body_size=128 * 1024,
    )

    assert server.session_manager.entered == 0
    with TestClient(app, base_url="https://game.example"):
        assert server.session_manager.entered == 1
        assert server.session_manager.exited == 0
    assert server.session_manager.exited == 1
