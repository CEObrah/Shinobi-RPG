from __future__ import annotations

from shinobi_runtime.commands import campaign_family_continuity_repair as repair


def test_parentage_repair_restores_shared_biological_parents() -> None:
    wei = repair._parentage_record("pc_wei_tang")
    kai = repair._parentage_record("char.kai")

    assert wei["authority"] is True
    assert kai["authority"] is True
    assert wei["child_id"] == "pc_wei_tang"
    assert kai["child_id"] == "char.kai"
    assert wei["parentage_id"] != kai["parentage_id"]

    expected = [
        {"parent_id": "char.zhu", "kind": "biological"},
        {"parent_id": "char.linh", "kind": "biological"},
    ]
    assert wei["parent_links"] == expected
    assert kai["parent_links"] == expected
    assert "Wei and Kai are brothers" in wei["provenance_note"]
