from shinobi_runtime.martial_world.escort_living_world import interception_decision


def test_outlaw_patrol_cannot_turn_risk_tolerance_and_numbers_into_a_motive():
    """A large patrol still needs a reason to attack an armed traveling party."""
    decision = interception_decision(
        attacker_faction_type="outlaw_faction",
        relation=None,
        own_available_martial=43,
        own_combat_index=70,
        observed_escort_count=12,
        observed_escort_combat_index=70,
        cargo_value_cash=0,
        ransom_value_cash=0,
        risk_tolerance=100,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
    )

    assert decision["attack"] is False
    assert decision["reason"] == "no_actionable_motive"
    assert decision["advantage_milli"] > 3000


def test_recognized_notable_target_can_supply_a_real_identity_motive():
    """Recognition may change the calculus, but strength still gates violence."""
    decision = interception_decision(
        attacker_faction_type="outlaw_faction",
        relation=None,
        own_available_martial=43,
        own_combat_index=70,
        observed_escort_count=12,
        observed_escort_combat_index=70,
        cargo_value_cash=0,
        ransom_value_cash=0,
        risk_tolerance=100,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
        recognized_target_motive_score=180,
    )

    assert decision["attack"] is True
    assert decision["intent"] == "hostile_interception"
    assert decision["motive_kind"] == "recognized_notable_target"
    assert decision["recognized_target_motive_score"] == 180


def test_identity_motive_does_not_override_bad_tactical_odds():
    decision = interception_decision(
        attacker_faction_type="outlaw_faction",
        relation=None,
        own_available_martial=2,
        own_combat_index=30,
        observed_escort_count=12,
        observed_escort_combat_index=90,
        cargo_value_cash=0,
        ransom_value_cash=0,
        risk_tolerance=100,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
        recognized_target_motive_score=320,
    )

    assert decision["attack"] is False
    assert decision["motive_kind"] == "recognized_notable_target"
    assert decision["advantage_milli"] < decision["required_advantage_milli"]


def test_serious_grievance_remains_an_actionable_noncriminal_motive():
    decision = interception_decision(
        attacker_faction_type="martial_house",
        relation={"hostility": 75, "trust": 0},
        own_available_martial=4,
        own_combat_index=50,
        observed_escort_count=4,
        observed_escort_combat_index=60,
        cargo_value_cash=0,
        ransom_value_cash=0,
        risk_tolerance=50,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
    )

    assert decision["attack"] is True
    assert decision["motive_kind"] == "grievance"
