from __future__ import annotations

import ast
from pathlib import Path


def test_standing_manufacturing_uses_saved_authority_for_shared_stock() -> None:
    source = Path("runtime/shinobi_runtime/commands/campaign_manufacturing.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    advance = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_advance_time"
    )
    rendered = ast.unparse(advance)

    assert 'authority_ref = schedule.get("authority_ref")' in source
    assert "self._inventory_holder_authorized(authority_ref, stock_owner)" in rendered
    assert "institution_manufacturing_stock_not_owned" not in rendered
