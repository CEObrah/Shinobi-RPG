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
