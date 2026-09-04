from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_generic_mcp_preview_routes_forward_repair_without_new_tool_discovery():
    source = (ROOT / "runtime/shinobi_runtime/api/mcp.py").read_text(encoding="utf-8")
    assert "is_repair = command_type == REPAIR_COMMAND_TYPE" in source
    assert 'mode=REPAIR_MODE if is_repair else "gameplay"' in source
    assert "repair_service.preview(command)" in source
    assert "else operations.preview_command(command)" in source


def test_generic_mcp_execute_routes_exact_repair_envelope_and_rejects_other_repair_modes():
    source = (ROOT / "runtime/shinobi_runtime/api/mcp.py").read_text(encoding="utf-8")
    assert "envelope.mode == REPAIR_MODE" in source
    assert "envelope.command_type == REPAIR_COMMAND_TYPE" in source
    assert 'raise ValueError("unsupported repair command")' in source
    assert "repair_service.lookup_receipt(envelope)" in source
    assert "repair_service.execute(envelope)" in source
    assert "else operations.execute_command(envelope)" in source
