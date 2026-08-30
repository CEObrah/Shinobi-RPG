from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tests/current/test_combat_geometry.py"
text = PATH.read_text(encoding="utf-8")


def replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"expected one match, found {count}: {old[:120]!r}")
    return source.replace(old, new, 1)


text = replace_once(
    text,
    '''    assert trace['moved'] is False\n    assert trace['reason']=='target_moved_beyond_committed_approach'\n    assert moved.x_mm==combat['positions'][attacker['person_id']]['x_mm']\n''',
    '''    assert trace['moved'] is True\n    assert trace['reason']=='partial_committed_approach'\n    assert int(trace['distance_mm'])==int(params['approach_distance_mm'])\n    assert int(trace['remaining_mm'])>0\n    assert moved.x_mm>combat['positions'][attacker['person_id']]['x_mm']\n''',
)

text = replace_once(
    text,
    '''    assert c.offense==0 and c.control==0 and c.defense==0 and c.mobility==0 and c.reaction==0\n''',
    '''    assert 0<c.offense<b.offense and 0<c.control<b.control and 0<c.defense<b.defense\n    assert 0<c.mobility<b.mobility and 0<c.reaction<b.reaction\n    assert 0<c.capture<b.capture and 0<c.escape<b.escape\n''',
)

text = replace_once(
    text,
    '''    assert sum(c.values()) == 0\n''',
    '''    assert 0 < sum(c.values()) < sum(b.values())\n''',
)

PATH.write_text(text, encoding="utf-8")

wrapper_path = ROOT / "tests/current/test_combat_command_wrapper.py"
wrapper = wrapper_path.read_text(encoding="utf-8")
wrapper = replace_once(
    wrapper,
    '''from shinobi_runtime.martial_world.social_causality import add_vow\n''',
    '''from shinobi_runtime.martial_world.social_causality import add_vow\nfrom shinobi_runtime.martial_world.equipment_state import effective_person_loadout\n''',
)
wrapper = replace_once(
    wrapper,
    '''    assert repo.read_json("state/martial-world/equipment-ledger.json") == before_equipment\n    assert "state/martial-world/equipment-ledger.json" not in plan.writes or json.loads(plan.writes["state/martial-world/equipment-ledger.json"].decode("utf-8")) == before_equipment\n''',
    '''    # Planning the exchange must not mutate the repository before commit.\n    assert repo.read_json("state/martial-world/equipment-ledger.json") == before_equipment\n    # The improvised scene prop is combat-local and must never become durable\n    # inventory. The exchange may still legitimately wear real weapons carried\n    # by either participant, so do not require the entire equipment ledger to\n    # remain byte-for-byte unchanged.\n    planned_equipment = (\n        json.loads(plan.writes["state/martial-world/equipment-ledger.json"].decode("utf-8"))\n        if "state/martial-world/equipment-ledger.json" in plan.writes\n        else before_equipment\n    )\n    before_player_items = effective_person_loadout(before_equipment, player_ref).get("items", {})\n    after_player_items = effective_person_loadout(planned_equipment, player_ref).get("items", {})\n    assert after_player_items == before_player_items\n    assert prop_ref not in json.dumps(planned_equipment, sort_keys=True)\n''',
)
wrapper_path.write_text(wrapper, encoding="utf-8")

print("stale combat regressions and scene-prop inventory invariant corrected")
