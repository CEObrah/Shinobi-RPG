from __future__ import annotations

import pytest

from shinobi_runtime.api.models import validate_bounded_json


def test_bounded_json_rejects_aggregate_serialized_size() -> None:
    # Every individual string, the container fanout, node count, and depth are
    # legal.  The aggregate response is still too large for an MCP handoff.
    value = {"values": ["x" * 8192 for _ in range(40)]}

    with pytest.raises(ValueError, match="serialized UTF-8 bytes"):
        validate_bounded_json(value, label="context")


def test_bounded_json_accepts_compact_utf8() -> None:
    value = {"scene": "雨の匂い", "facts": ["quiet", "player-visible"]}

    assert validate_bounded_json(value, label="context") is value


def test_play_context_has_extra_node_headroom_without_relaxing_other_payloads() -> None:
    # The live command catalog is intentionally richer than an ordinary command
    # payload, but it remains subject to the same depth, fanout, string, and
    # serialized-byte limits. Keep the generic 2,048-node limit for everything
    # else while allowing the bounded play-context projection up to 4,096 nodes.
    value = {"rows": [{"values": [0] * 8} for _ in range(210)]}

    with pytest.raises(ValueError, match="2048 JSON nodes"):
        validate_bounded_json(value, label="payload")

    assert validate_bounded_json(value, label="play context") is value


def test_play_context_still_fails_closed_above_its_node_budget() -> None:
    value = {"rows": [{"values": [0] * 16} for _ in range(230)]}

    with pytest.raises(ValueError, match="4096 JSON nodes"):
        validate_bounded_json(value, label="play context")
