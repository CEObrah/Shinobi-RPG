from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")
pytest.importorskip("fastapi")
pytest.importorskip("jwt")

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.auth.middleware.auth_context import AuthenticatedUser, auth_context_var
from mcp.server.auth.provider import AccessToken

from shinobi_runtime.api.mcp import (
    JwtAccessTokenVerifier,
    McpOAuthSettings,
    create_mcp_server,
    mount_mcp,
)


class FakeOperations:
    def __init__(self) -> None:
        self.executed = []

    def play_context(self):
        return {
            "campaign": {
                "campaign_id": "shinobi-test",
                "revision": 18,
                "world_time": "SE-0061-02-06T21:15:00",
                "state_root": "a" * 64,
                "player_id": "pc_wei_tang",
            },
            "scene": {},
            "player": {},
            "commands": {},
            "context_policy": {},
        }

    def person_sheet(self, person_id):
        return {"person_id": person_id, "sheet": {"core": {"person_id": person_id}}}

    def inspect_game_object(self, object_ref, *, object_kind=None):
        return {"object_ref": object_ref, "object_kind": object_kind, "object": {}}

    def preview_command(self, command):
        return {
            "status": "ready",
            "code": "advance_time_ready",
            "target_revision": command.expected_revision + 1,
            "affected_refs": ["state/meta.json"],
        }

    def execute_command(self, command):
        self.executed.append(command)
        return {
            "status": "committed",
            "request_id": command.request_id,
            "transaction_id": "tx." + command.digest,
            "campaign_id": command.campaign_id,
            "committed_revision": command.expected_revision + 1,
            "committed_at": command.submitted_at,
            "result": {"command_type": command.command_type},
        }

    def lookup_command_receipt(self, command):
        for existing in self.executed:
            if existing.request_id != command.request_id:
                continue
            if existing.digest != command.digest:
                return None
            return {
                "status": "duplicate",
                "request_id": command.request_id,
                "transaction_id": "tx." + command.digest,
                "campaign_id": command.campaign_id,
                "committed_revision": command.expected_revision + 1,
                "committed_at": command.submitted_at,
                "result": {"command_type": command.command_type},
            }
        return None

    def ooc_audit(self, focus, observations):
        return {"diagnostics": [], "suggestions": []}


class FakeTokenVerifier:
    async def verify_token(self, token):
        if token != "valid-token":
            return None
        return AccessToken(
            token=token,
            client_id="chatgpt-test",
            scopes=["shinobi:read", "shinobi:write"],
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


def test_oauth_environment_is_complete_and_exact(monkeypatch):
    names = {
        "SHINOBI_MCP_PUBLIC_URL": "https://game.example/mcp",
        "SHINOBI_OAUTH_ISSUER_URL": "https://issuer.example/",
        "SHINOBI_OAUTH_JWKS_URL": "https://issuer.example/jwks",
        "SHINOBI_OAUTH_AUDIENCE": "shinobi-audience",
        "SHINOBI_OAUTH_ALLOWED_SUBJECTS": "player-test",
        "SHINOBI_MCP_PREVIEW_SECRET": "s" * 64,
    }
    for name, value in names.items():
        monkeypatch.setenv(name, value)
    settings = McpOAuthSettings.from_env()
    assert settings.public_url.endswith("/mcp")
    assert settings.issuer_url.endswith("/")
    assert settings.algorithms == ("RS256",)
    assert settings.read_scope == "shinobi:read"
    assert settings.write_scope == "shinobi:write"
    assert settings.allowed_subjects == ("player-test",)
    assert settings.preview_secret not in repr(settings)

    monkeypatch.delenv("SHINOBI_OAUTH_AUDIENCE")
    with pytest.raises(RuntimeError, match="SHINOBI_OAUTH_AUDIENCE"):
        McpOAuthSettings.optional_from_env()
    monkeypatch.setenv("SHINOBI_OAUTH_AUDIENCE", "shinobi-audience")
    monkeypatch.setenv("SHINOBI_MCP_PUBLIC_URL", "http://game.example/mcp")
    with pytest.raises(RuntimeError, match="public HTTPS"):
        McpOAuthSettings.from_env()


def test_jwt_verifier_checks_signature_issuer_audience_expiry_and_scopes():
    settings = oauth_settings()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = JwtAccessTokenVerifier(settings)
    verifier._jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(
            key=private_key.public_key()
        )
    )
    claims = {
        "iss": settings.issuer_url,
        "aud": settings.audience,
        "sub": "player-test",
        "azp": "chatgpt-test",
        "exp": int(time.time()) + 300,
        "nbf": int(time.time()) - 1,
        "scope": "shinobi:read shinobi:write",
    }
    token = jwt.encode(claims, private_key, algorithm="RS256")
    resolved = asyncio.run(verifier.verify_token(token))
    assert resolved is not None
    assert resolved.subject == "player-test"
    assert resolved.scopes == ["shinobi:read", "shinobi:write"]

    wrong = dict(claims, aud="another-resource")
    wrong_token = jwt.encode(wrong, private_key, algorithm="RS256")
    assert asyncio.run(verifier.verify_token(wrong_token)) is None
    wrong_subject = jwt.encode(
        dict(claims, sub="another-player"), private_key, algorithm="RS256"
    )
    assert asyncio.run(verifier.verify_token(wrong_subject)) is None
    wrong_client = jwt.encode(
        dict(claims, azp="another-client"), private_key, algorithm="RS256"
    )
    assert asyncio.run(verifier.verify_token(wrong_client)) is None
    assert asyncio.run(verifier.verify_token("x" * (16 * 1024 + 1))) is None


def test_mcp_tools_are_bounded_annotated_and_preview_exact_execution():
    operations = FakeOperations()
    oauth = oauth_settings()
    server = create_mcp_server(
        operations,
        oauth,
        token_verifier=FakeTokenVerifier(),
    )

    async def exercise():
        tools = await server.list_tools()
        assert [tool.name for tool in tools] == [
            "get_play_context",
            "get_person_sheet",
            "inspect_game_object",
            "preview_command",
            "execute_command",
            "ooc_audit",
        ]
        for tool in tools:
            assert tool.output_schema["additionalProperties"] is False
            expected_scopes = (
                ["shinobi:read", "shinobi:write"]
                if tool.name == "execute_command"
                else ["shinobi:read"]
            )
            assert tool.meta["securitySchemes"] == [
                {
                    "type": "oauth2",
                    "scopes": expected_scopes,
                }
            ]
        assert tools[0].annotations.read_only_hint is True
        execute_tool = next(tool for tool in tools if tool.name == "execute_command")
        assert execute_tool.annotations.read_only_hint is False
        assert execute_tool.annotations.destructive_hint is True

        context = await server.call_tool("get_play_context", {})
        assert context.structured_content["result"]["campaign"]["revision"] == 18
        preview = await server.call_tool(
            "preview_command",
            {
                "request_id": "request.mcp.001",
                "expected_revision": 18,
                "command_type": "advance_time",
                "payload": {"target_time": "SE-0061-02-06T21:16:00"},
            },
        )
        command = preview.structured_content["command"]
        attestation = preview.structured_content["preview_attestation"]
        assert preview.structured_content["preview"]["status"] == "ready"
        assert command["campaign_id"] == "shinobi-test"
        assert command["actor_id"] == "pc_wei_tang"

        missing_scope = await server.call_tool(
            "execute_command",
            {"command": command, "preview_attestation": attestation},
        )
        assert missing_scope.is_error is True
        assert "mcp/www_authenticate" in missing_scope.meta

        access = AccessToken(
            token="direct-test",
            client_id="chatgpt-test",
            scopes=["shinobi:read", "shinobi:write"],
            expires_at=4_102_444_800,
            resource=oauth.audience,
            subject="player-test",
        )
        marker = auth_context_var.set(AuthenticatedUser(access))
        try:
            executed = await server.call_tool(
                "execute_command",
                {"command": command, "preview_attestation": attestation},
            )
            tampered = dict(command)
            tampered["request_id"] = "request.mcp.tampered"
            rejected = await server.call_tool(
                "execute_command",
                {"command": tampered, "preview_attestation": attestation},
            )
            recovered = await server.call_tool(
                "execute_command",
                {"command": command},
            )
        finally:
            auth_context_var.reset(marker)
        assert executed.structured_content["receipt"]["status"] == "committed"
        assert rejected.structured_content["error"]["code"] == (
            "preview_attestation_invalid_or_expired"
        )
        assert recovered.structured_content["receipt"]["status"] == "duplicate"
        assert operations.executed[0].to_record() == command
        assert len(operations.executed) == 1

    asyncio.run(exercise())


def test_mcp_mount_exposes_protected_resource_and_accepts_initialize():
    oauth = oauth_settings()
    app = FastAPI()
    server = create_mcp_server(
        FakeOperations(),
        oauth,
        token_verifier=FakeTokenVerifier(),
    )
    mount_mcp(app, server, oauth, max_request_body_size=128 * 1024)

    with TestClient(app, base_url="https://game.example") as client:
        metadata = client.get("/.well-known/oauth-protected-resource/mcp")
        assert metadata.status_code == 200
        assert metadata.json() == {
            "resource": "https://game.example/mcp",
            "authorization_servers": ["https://issuer.example/"],
            "scopes_supported": ["shinobi:read", "shinobi:write"],
            "bearer_methods_supported": ["header"],
        }
        unauthorized = client.get("/mcp")
        assert unauthorized.status_code == 401
        assert "resource_metadata=" in unauthorized.headers["www-authenticate"]

        initialized = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer valid-token",
                "Accept": "application/json, text/event-stream",
                "Origin": "https://chatgpt.com",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200
        result = initialized.json()["result"]
        assert result["serverInfo"]["name"] == "shinobi-rpg"
        assert result["capabilities"]["tools"]["listChanged"] is False

        listed = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer valid-token",
                "Accept": "application/json, text/event-stream",
                "Origin": "https://chatgpt.com",
            },
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        assert listed.status_code == 200
        wire_tools = listed.json()["result"]["tools"]
        assert len(wire_tools) == 6
        for tool in wire_tools:
            expected_scopes = (
                ["shinobi:read", "shinobi:write"]
                if tool["name"] == "execute_command"
                else ["shinobi:read"]
            )
            expected_schemes = [{"type": "oauth2", "scopes": expected_scopes}]
            assert tool["securitySchemes"] == expected_schemes
            assert tool["_meta"]["securitySchemes"] == expected_schemes
