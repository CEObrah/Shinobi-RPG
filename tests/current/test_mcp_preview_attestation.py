from shinobi_runtime.api.mcp import (
    McpOAuthSettings,
    _PREVIEW_ATTESTATION_CLOCK_SKEW_SECONDS,
    _PREVIEW_ATTESTATION_TTL_SECONDS,
    _preview_attestation,
    _verify_preview_attestation,
)
from shinobi_runtime.commands.envelope import CommandEnvelope


def _oauth():
    return McpOAuthSettings(
        public_url="https://runtime.example/mcp",
        issuer_url="https://issuer.example",
        jwks_url="https://issuer.example/jwks",
        audience="shinobi-runtime",
        algorithms=("RS256",),
        read_scope="shinobi:read",
        write_scope="shinobi:write",
        allowed_subjects=("test-user",),
        allowed_client_ids=(),
        preview_secret="A" * 43,
        allowed_origins=("https://chatgpt.com",),
    )


def _command(request_id="request.preview-clock-skew"):
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id=request_id,
        actor_id="pc.test",
        command_type="advance_time",
        expected_revision=1,
        submitted_at="2026-08-28T00:00:00Z",
        payload={"target_time": "SE-0061-01-02T00:00:00"},
    )


def test_preview_attestation_accepts_bounded_cross_instance_clock_skew():
    issued_at = 10_000
    token = _preview_attestation(_command(), _oauth(), now=issued_at)

    assert _verify_preview_attestation(
        _command(),
        token,
        _oauth(),
        now=issued_at - _PREVIEW_ATTESTATION_CLOCK_SKEW_SECONDS,
    )
    assert _verify_preview_attestation(
        _command(),
        token,
        _oauth(),
        now=(
            issued_at
            + _PREVIEW_ATTESTATION_TTL_SECONDS
            + _PREVIEW_ATTESTATION_CLOCK_SKEW_SECONDS
        ),
    )


def test_preview_attestation_rejects_clock_skew_outside_bound():
    issued_at = 10_000
    token = _preview_attestation(_command(), _oauth(), now=issued_at)

    assert not _verify_preview_attestation(
        _command(),
        token,
        _oauth(),
        now=issued_at - _PREVIEW_ATTESTATION_CLOCK_SKEW_SECONDS - 1,
    )
    assert not _verify_preview_attestation(
        _command(),
        token,
        _oauth(),
        now=(
            issued_at
            + _PREVIEW_ATTESTATION_TTL_SECONDS
            + _PREVIEW_ATTESTATION_CLOCK_SKEW_SECONDS
            + 1
        ),
    )


def test_preview_attestation_remains_bound_to_exact_command():
    issued_at = 10_000
    token = _preview_attestation(_command(), _oauth(), now=issued_at)

    assert not _verify_preview_attestation(
        _command("request.different"),
        token,
        _oauth(),
        now=issued_at,
    )
