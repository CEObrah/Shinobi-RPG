from __future__ import annotations

from pathlib import Path

import shinobi_runtime.martial_world.exact_combat as exact
from play_failure_fixture import ENEMY, HAN, PLAYER, YAO, played_medic_fixture
from skill_support import combat_contract_text

ROOT = Path(__file__).resolve().parents[2]


def test_played_compound_attack_and_medic_order_survive_same_resolution():
    """Stable regression for the played ``keep fighting; send Han to Yao`` failure.

    The former version was incorrectly gated to a mutable live-save revision.
    This immutable reconstruction keeps the actual semantic invariant
    executable forever: Wei's personal combat and a simultaneous retinue medic
    order must survive the same exact resolution.
    """
    combat, people, ledger = played_medic_fixture()
    result = exact.resolve_exchange(
        combat=combat,
        people=people,
        equipment_ledger=ledger,
        doctrines={},
        player_ref=PLAYER,
        player_action_kind="unarmed_strike",
        player_target_ref=ENEMY,
        player_weapon_ref="body_unarmed",
        player_hit_zone="chest",
        player_targeting_intent="disable",
        player_ally_orders=[{"actor_ref": HAN, "task": "reach", "target_ref": YAO}],
        player_retinue_context={"member_refs": [HAN, YAO]},
    )

    support = [
        event for event in result["events"]
        if event.get("actor_ref") == HAN
        and event.get("action_kind") == "ally_support"
        and event.get("intended_ref") == YAO
    ]
    assert support, "medic support disappeared from compound combat action"
    assert all(event.get("decision_origin") == "player_ally_order" for event in support)
    assert any(event.get("actor_ref") == PLAYER for event in result["events"]), (
        "player personal combat disappeared while resolving the ally order"
    )

    after = result["combat_after"]
    assert after["positions"][YAO]["stance"] == "fallen"
    assert int(after["positions"][YAO]["body_radius_mm"]) <= 140
    assert support[0]["result"] in {
        "support_approach", "support_reached", "support_protecting",
        "support_extraction_secured", "support_extract_target_mobile",
    }

    info = dict(result["combat_information"])
    assert info["scale"] == "exact_people"
    assert info["observed_hostiles_cumulative"] >= info["observed_active_engaged"]
    assert info["observed_active_engaged"] + info["observed_withdrawing"] >= 1
    assert info["observed_combat_capable_remaining"] == info["observed_active_engaged"] + info["observed_withdrawing"]


def test_skill_requires_scene_first_scale_appropriate_combat_accounting():
    text = combat_contract_text(ROOT)
    assert "When a committed exact-combat result exposes `combat_information`" in text
    assert "personal_tally_status" in text
    assert "misleading zero" in text
