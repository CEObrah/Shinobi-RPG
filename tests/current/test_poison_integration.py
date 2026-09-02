from __future__ import annotations

from shinobi_runtime.martial_world.economy import base_value_cash
from shinobi_runtime.martial_world.enterprise_operations import operate_poison_apothecary_month
from shinobi_runtime.martial_world.exact_combat import (
    _commit_projectile_resources,
    _npc_poison_for,
    capability_from_person,
)
from shinobi_runtime.martial_world.health import settle_physiology
from shinobi_runtime.martial_world.poison import (
    activate_due_poison_exposures, poison_effects_for_burdens, poison_onset_seconds, poison_peak_seconds,
    queue_progressive_poison_exposure,
)


def _person(**overrides):
    row = {
        "person_id": "p",
        "attributes": {
            "strength": 80,
            "speed": 80,
            "dexterity": 80,
            "endurance": 80,
            "perception": 80,
            "intelligence": 80,
            "willpower": 80,
        },
        "martial_skills": {"sword": 80, "hidden_weapons": 80, "unarmed": 60},
        "qi": 20,
        "qi_control": 20,
        "health": {"status": "healthy", "injuries": []},
    }
    row.update(overrides)
    return row


def test_active_poison_penalties_reach_exact_combat_capability():
    healthy = capability_from_person(_person(), action_skill="hidden_weapon_throw")
    paralysed = capability_from_person(_person(poison_burdens={"paralytic": 100}), action_skill="hidden_weapon_throw")
    neuro = capability_from_person(_person(poison_burdens={"neurotoxic": 100}), action_skill="hidden_weapon_throw")
    cardio = capability_from_person(_person(poison_burdens={"cardiotoxic": 100}), action_skill="sword")

    assert paralysed.mobility < healthy.mobility
    assert paralysed.reaction < healthy.reaction
    assert neuro.perception < healthy.perception
    assert cardio.defense < capability_from_person(_person(), action_skill="sword").defense


def test_respiratory_poison_pressure_affects_physiology():
    base = settle_physiology(
        body_mass_kg=70, wounds=[], blood_lost_ml=0, elapsed_seconds=0,
        endurance=80, willpower=80, poison_effects={},
    )
    pressured = settle_physiology(
        body_mass_kg=70, wounds=[], blood_lost_ml=0, elapsed_seconds=0,
        endurance=80, willpower=80, poison_effects={"respiratory_pressure": 50},
    )
    assert pressured["shock"] > base["shock"]
    assert pressured["consciousness"] < base["consciousness"]


def test_anticoagulant_partial_burden_scales_from_neutral_bleeding_multiplier():
    partial = poison_effects_for_burdens({"anticoagulant": 30})
    full = poison_effects_for_burdens({"anticoagulant": 100})

    assert partial["bleeding_rate_multiplier_milli"] == 1150
    assert full["bleeding_rate_multiplier_milli"] == 1500


def test_plain_and_poisoned_needles_are_distinct_resource_choices():
    plain_ledger = {"person_loadouts": {"p": {"items": {"weapon_needle": 2, "poison_cardiotoxic": 1}}}}
    result = _commit_projectile_resources(
        plain_ledger, actor_ref="p", action_kind="hidden_weapon_throw",
        weapon_ref="weapon_needle", poison_ref=None,
    )
    assert result["ok"] is True
    assert plain_ledger["person_loadouts"]["p"]["items"] == {"weapon_needle": 1, "poison_cardiotoxic": 1}

    poisoned_ledger = {"person_loadouts": {"p": {"items": {"weapon_needle": 2, "poison_cardiotoxic": 1}}}}
    result = _commit_projectile_resources(
        poisoned_ledger, actor_ref="p", action_kind="hidden_weapon_throw",
        weapon_ref="weapon_needle", poison_ref="cardiotoxic",
    )
    assert result["ok"] is True
    assert result["poison_dose_consumed"] is True
    assert poisoned_ledger["person_loadouts"]["p"]["items"] == {"weapon_needle": 1}


def test_npc_poison_choice_is_contextual_and_conserves_saturated_targets():
    ledger = {"person_loadouts": {"p": {"items": {"weapon_needle": 3, "poison_cardiotoxic": 2}}}}
    target = _person(person_id="t")
    assert _npc_poison_for(
        "p", target, ledger, action_kind="hidden_weapon_throw", weapon_ref="weapon_needle", intent="lethal"
    ) == "cardiotoxic"
    assert _npc_poison_for(
        "p", target, ledger, action_kind="hidden_weapon_throw", weapon_ref="weapon_needle", intent="disable"
    ) is None
    target["pending_poison_burdens"] = {"cardiotoxic": {"burden": 100, "activates_at": "0061-09-14T10:00:00"}}
    assert _npc_poison_for(
        "p", target, ledger, action_kind="hidden_weapon_throw", weapon_ref="weapon_needle", intent="lethal"
    ) is None


def test_controlled_poison_reserve_production_consumes_real_reagents():
    inventory = {
        "raw_materials": {"toxin_reagent_unit": 6, "carrier_reagent_unit": 2},
        "poisons": {},
    }
    result = operate_poison_apothecary_month(
        inventory, recipe_ref="cardiotoxic", apothecary_level=5, medicine_skill=120,
        available_worker_hours=14, reserve_doses=12, max_batches=10,
    )
    assert result["batches"] == 2
    assert result["produced"] == 12
    assert result["inventory"]["poisons"]["poison_cardiotoxic"] == 12
    assert "toxin_reagent_unit" not in result["inventory"]["raw_materials"]
    assert "carrier_reagent_unit" not in result["inventory"]["raw_materials"]


def test_poison_reagents_have_canonical_market_values():
    assert base_value_cash("toxin_reagent_unit") == 18
    assert base_value_cash("carrier_reagent_unit") == 6

from shinobi_runtime.martial_world.poison import (
    activate_due_poison_exposures,
    poison_onset_seconds,
    poison_peak_seconds,
    queue_progressive_poison_exposure,
)


def test_combat_poisons_use_fast_progressive_onset_not_eight_minute_switch():
    assert poison_onset_seconds('cardiotoxic') == 25
    assert poison_peak_seconds('cardiotoxic') == 120
    assert poison_onset_seconds('paralytic') == 30
    assert poison_peak_seconds('paralytic') == 150


def test_progressive_poison_activates_onset_tranche_then_peak_remainder():
    queued = queue_progressive_poison_exposure(
        pending_burdens={}, poison_ref='cardiotoxic', burden_added=40,
        exposed_at='0061-09-14T10:00:00',
    )
    row = queued['pending_after']['cardiotoxic']
    assert row['activates_at'] == '0061-09-14T10:00:25'
    assert row['peaks_at'] == '0061-09-14T10:02:00'
    onset = activate_due_poison_exposures(
        active={}, pending=queued['pending_after'], at='0061-09-14T10:00:25',
    )
    assert onset['active_after']['cardiotoxic'] == 12
    assert onset['pending_after']['cardiotoxic']['burden'] == 28
    peak = activate_due_poison_exposures(
        active=onset['active_after'], pending=onset['pending_after'], at='0061-09-14T10:02:00',
    )
    assert peak['active_after']['cardiotoxic'] == 40
    assert 'cardiotoxic' not in peak['pending_after']


def test_legacy_single_stage_pending_poison_rows_still_activate():
    result = activate_due_poison_exposures(
        active={}, pending={'cardiotoxic': {'burden': 17, 'activates_at': '0061-09-14T10:00:10'}},
        at='0061-09-14T10:00:10',
    )
    assert result['active_after']['cardiotoxic'] == 17
    assert result['pending_after'] == {}


def test_combat_poisons_use_seconds_scale_progressive_onset():
    assert poison_onset_seconds("cardiotoxic") == 25
    assert poison_peak_seconds("cardiotoxic") == 120
    assert poison_onset_seconds("paralytic") == 30
    assert poison_peak_seconds("paralytic") == 150
    assert poison_onset_seconds("neurotoxic") == 45
    assert poison_peak_seconds("neurotoxic") == 180
    assert poison_onset_seconds("sedative") == 120
    assert poison_peak_seconds("sedative") == 480
    assert poison_onset_seconds("anticoagulant") == 180
    assert poison_peak_seconds("anticoagulant") == 600


def test_cardiotoxic_exposure_activates_initial_tranche_then_peak_without_duplication():
    queued = queue_progressive_poison_exposure(
        pending_burdens={}, poison_ref="cardiotoxic", burden_added=40,
        exposed_at="0061-09-14T10:00:00",
    )
    before = activate_due_poison_exposures(
        active={}, pending=queued["pending_after"], at="0061-09-14T10:00:24",
    )
    assert before["active_after"] == {}
    assert before["pending_after"]["cardiotoxic"]["burden"] == 40

    onset = activate_due_poison_exposures(
        active={}, pending=queued["pending_after"], at="0061-09-14T10:00:25",
    )
    assert onset["active_after"]["cardiotoxic"] == 12
    assert onset["pending_after"]["cardiotoxic"]["burden"] == 28
    assert onset["pending_after"]["cardiotoxic"]["stage"] == "peak"

    peak = activate_due_poison_exposures(
        active=onset["active_after"], pending=onset["pending_after"],
        at="0061-09-14T10:02:00",
    )
    assert peak["active_after"]["cardiotoxic"] == 40
    assert "cardiotoxic" not in peak["pending_after"]


def test_overlapping_same_poison_exposures_keep_independent_pending_clocks():
    first = queue_progressive_poison_exposure(
        pending_burdens={}, poison_ref="cardiotoxic", burden_added=40,
        exposed_at="0061-09-14T10:00:00",
    )
    second = queue_progressive_poison_exposure(
        pending_burdens=first["pending_after"], poison_ref="cardiotoxic", burden_added=40,
        exposed_at="0061-09-14T10:00:10",
    )
    pending = second["pending_after"]
    assert set(pending) == {"cardiotoxic", "cardiotoxic#2"}
    assert pending["cardiotoxic"]["activates_at"] == "0061-09-14T10:00:25"
    assert pending["cardiotoxic#2"]["activates_at"] == "0061-09-14T10:00:35"

    at_first_onset = activate_due_poison_exposures(
        active={}, pending=pending, at="0061-09-14T10:00:25",
    )
    assert at_first_onset["active_after"]["cardiotoxic"] == 12
    assert at_first_onset["pending_after"]["cardiotoxic"]["stage"] == "peak"
    assert at_first_onset["pending_after"]["cardiotoxic#2"]["stage"] == "onset"
    assert at_first_onset["pending_after"]["cardiotoxic#2"]["burden"] == 40

    at_second_onset = activate_due_poison_exposures(
        active=at_first_onset["active_after"], pending=at_first_onset["pending_after"],
        at="0061-09-14T10:00:35",
    )
    assert at_second_onset["active_after"]["cardiotoxic"] == 24
    assert at_second_onset["pending_after"]["cardiotoxic"]["burden"] == 28
    assert at_second_onset["pending_after"]["cardiotoxic#2"]["burden"] == 28


def test_progressive_pending_poison_metadata_survives_person_compaction():
    from shinobi_runtime.martial_world.person_state import compact_person_state

    person = _person(
        faction_ref="house_tang", birth_year=44, membership_grade="elite",
        pending_poison_burdens={
            "cardiotoxic#2": {
                "poison_ref": "cardiotoxic", "burden": 28,
                "activates_at": "0061-09-14T10:02:10",
                "peaks_at": "0061-09-14T10:02:10", "stage": "peak",
            }
        },
    )
    compacted = compact_person_state(person, faction_ref="house_tang")
    assert compacted["pending_poison_burdens"]["cardiotoxic#2"] == {
        "burden": 28,
        "activates_at": "0061-09-14T10:02:10",
        "poison_ref": "cardiotoxic",
        "peaks_at": "0061-09-14T10:02:10",
        "stage": "peak",
    }


def test_treatment_clearance_reduces_active_then_pending_without_collapsing_clocks():
    from shinobi_runtime.martial_world.poison import clear_poison_burden, combined_poison_burdens

    pending = {
        'cardiotoxic': {
            'poison_ref': 'cardiotoxic', 'burden': 20,
            'activates_at': '0061-09-14T10:00:25', 'peaks_at': '0061-09-14T10:02:00', 'stage': 'onset',
        },
        'cardiotoxic#2': {
            'poison_ref': 'cardiotoxic', 'burden': 30,
            'activates_at': '0061-09-14T10:00:35', 'peaks_at': '0061-09-14T10:02:10', 'stage': 'onset',
        },
    }
    result = clear_poison_burden(
        active={'cardiotoxic': 10}, pending=pending, poison_ref='cardiotoxic', amount=25,
    )

    assert result['burden_cleared'] == 25
    assert 'cardiotoxic' not in result['active_after']
    assert result['pending_after']['cardiotoxic']['burden'] == 5
    assert result['pending_after']['cardiotoxic']['activates_at'] == '0061-09-14T10:00:25'
    assert result['pending_after']['cardiotoxic#2']['burden'] == 30
    assert combined_poison_burdens(result['active_after'], result['pending_after'])['cardiotoxic'] == 35
