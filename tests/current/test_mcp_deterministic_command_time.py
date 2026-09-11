from pathlib import Path

import pytest

from shinobi_runtime.api.command_time import command_submitted_at


def _mcp_source() -> str:
    return (Path(__file__).resolve().parents[2] / "runtime/shinobi_runtime/api/mcp.py").read_text(encoding="utf-8")


def test_campaign_world_time_maps_to_stable_command_clock_without_wall_time():
    assert command_submitted_at("SE-0061-09-27T21:15:54") == "0061-09-27T21:15:54Z"
    assert command_submitted_at("SE-0061-09-27T21:15:54") == command_submitted_at("SE-0061-09-27T21:15:54")
    with pytest.raises(ValueError):
        command_submitted_at("not-a-campaign-time")


def test_command_identity_source_keeps_world_time_in_preview_identity():
    source = _mcp_source()
    start = source.index("def _command_identity")
    end = source.index("\ndef _build_mcp_app", start) if "\ndef _build_mcp_app" in source[start:] else len(source)
    identity_source = source[start:end]
    assert 'world_time = identity.get("world_time")' in identity_source
    assert '"world_time": world_time' in identity_source


def test_mcp_preview_source_does_not_stamp_gameplay_commands_from_wall_clock():
    source = _mcp_source()
    assert "datetime.now" not in source
    assert 'submitted_at=command_submitted_at(campaign["world_time"])' in source
