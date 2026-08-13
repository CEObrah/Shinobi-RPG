from __future__ import annotations

import json
from pathlib import Path

from shinobi_runtime.commands.campaign_intake_onboarding import _scaled_loadout_quantities


def test_sword_manor_intake_requires_capacity_and_conserved_standard_loadout() -> None:
    policies = json.loads(
        Path("game/rules/recruitment/policies.json").read_text(encoding="utf-8")
    )
    policy = policies["policies"]["recruitment.sword_manor_disciple"]
    loadout = json.loads(
        Path("game/data/loadout-records/load.ht.base.json").read_text(encoding="utf-8")
    )["loadout"]
    stock = json.loads(Path("state/house/stock.json").read_text(encoding="utf-8"))
    aliases = json.loads(
        Path("game/data/items/stock-aliases.json").read_text(encoding="utf-8")
    )["aliases"]

    assert policy["capacity_gate"] == {
        "module_kind": "training",
        "capacity_field": "capacity_slots",
    }
    assert policy["onboarding_loadout_ref"] == "load.ht.base"
    assert policy["onboarding_stock_ref"] == "stock.house.tang"
    assert policy["onboarding_require_full_loadout"] is True

    per_person = {row["item_id"]: row["quantity"] for row in loadout["items"]}
    totals = _scaled_loadout_quantities(per_person, policy["max_intake_per_batch"])
    for item_ref, required in totals.items():
        candidates = [item_ref, *aliases.get(item_ref, [])]
        tracked = next((key for key in candidates if key in stock["items"]), None)
        assert tracked is not None, item_ref
        assert stock["items"][tracked] >= required, item_ref


def test_scaled_onboarding_quantities_preserve_per_person_custody_totals() -> None:
    assert _scaled_loadout_quantities(
        {"armor_house_court": 1, "weapon_kunai": 4, "item_smoke_bomb": 2},
        12,
    ) == {
        "armor_house_court": 12,
        "item_smoke_bomb": 24,
        "weapon_kunai": 48,
    }
