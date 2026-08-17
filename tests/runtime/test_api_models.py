from __future__ import annotations

import pytest

from shinobi_runtime.api.models import validate_bounded_json


def test_bounded_json_rejects_aggregate_serialized_size() -> None:
    # Every individual string, the container fanout, node count, and depth are
    # legal. The aggregate response is still too large for an ordinary payload.
    value = {"values": ["x" * 8192 for _ in range(40)]}

    with pytest.raises(ValueError, match="serialized UTF-8 bytes"):
        validate_bounded_json(value, label="context")


def test_bounded_json_accepts_compact_utf8() -> None:
    value = {"scene": "雨の匂い", "facts": ["quiet", "player-visible"]}

    assert validate_bounded_json(value, label="context") is value


def test_play_context_assembly_can_exceed_wire_budget_without_relaxing_payloads() -> None:
    # Campaign projections compose internally before the public wire handoff is
    # compacted. The assembly envelope therefore has more headroom than both an
    # ordinary payload and the final MCP play-context response.
    value = {"rows": [{"values": [0] * 16} for _ in range(230)]}

    with pytest.raises(ValueError, match="2048 JSON nodes"):
        validate_bounded_json(value, label="payload")

    with pytest.raises(ValueError, match="4096 JSON nodes"):
        validate_bounded_json(value, label="compact play context")

    assert validate_bounded_json(value, label="play context") is value


def test_play_context_assembly_still_fails_closed_above_internal_budget() -> None:
    value = {"rows": [{"values": [0] * 150} for _ in range(220)]}

    with pytest.raises(ValueError, match="32768 JSON nodes"):
        validate_bounded_json(value, label="play context")
