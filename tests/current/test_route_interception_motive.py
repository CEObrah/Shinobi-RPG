from shinobi_runtime.martial_world.escort_living_world import interception_decision


def test_outlaw_patrol_can_make_an_opportunistic_attack_when_odds_are_favorable():
    """Random route predation remains possible without manifest cargo or grievance."""
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

    assert decision["attack"] is True
    assert decision["motive_kind"] == "opportunistic_predation"
    assert decision["expected_reward_score"] > 0
    assert decision["utility_score"] >= 0


def test_same_outlaw_patrol_backs_off_when_exposure_outweighs_the_payoff():
    decision = interception_decision(
        attacker_faction_type="outlaw_faction",
        relation=None,
        own_available_martial=43,
        own_combat_index=70,
        observed_escort_count=12,
        observed_escort_combat_index=70,
        cargo_value_cash=0,
        ransom_value_cash=0,
        risk_tolerance=40,
        government_risk_milli=800,
        minimum_attack_advantage_milli=1100,
    )

    assert decision["attack"] is False
    assert decision["reason"] == "expected_cost_exceeds_reward"
    assert decision["expected_cost_score"] > decision["expected_reward_score"]


def test_known_cargo_can_tip_a_borderline_outlaw_contact_into_attack():
    without_cargo = interception_decision(
        attacker_faction_type="outlaw_faction",
        relation=None,
        own_available_martial=12,
        own_combat_index=70,
        observed_escort_count=12,
        observed_escort_combat_index=70,
        cargo_value_cash=0,
        ransom_value_cash=0,
        risk_tolerance=50,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
    )
    with_cargo = interception_decision(
        attacker_faction_type="outlaw_faction",
        relation=None,
        own_available_martial=12,
        own_combat_index=70,
        observed_escort_count=12,
        observed_escort_combat_index=70,
        cargo_value_cash=50_000,
        ransom_value_cash=0,
        risk_tolerance=50,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
    )

    assert without_cargo["attack"] is False
    assert with_cargo["attack"] is True
    assert with_cargo["motive_kind"] == "loot"
    assert with_cargo["expected_reward_score"] > without_cargo["expected_reward_score"]


def test_recognized_notable_target_can_raise_reward_or_raise_risk():
    tempting_target = interception_decision(
        attacker_faction_type="outlaw_faction",
        relation=None,
        own_available_martial=12,
        own_combat_index=70,
        observed_escort_count=12,
        observed_escort_combat_index=70,
        cargo_value_cash=0,
        ransom_value_cash=0,
        risk_tolerance=50,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
        recognized_target_reward_score=300,
        recognized_target_risk_score=0,
    )
    dangerous_target = interception_decision(
        attacker_faction_type="outlaw_faction",
        relation=None,
        own_available_martial=12,
        own_combat_index=70,
        observed_escort_count=12,
        observed_escort_combat_index=70,
        cargo_value_cash=0,
        ransom_value_cash=0,
        risk_tolerance=50,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
        recognized_target_reward_score=300,
        recognized_target_risk_score=400,
    )

    assert tempting_target["attack"] is True
    assert tempting_target["motive_kind"] == "recognized_notable_target"
    assert dangerous_target["attack"] is False
    assert dangerous_target["recognized_target_risk_score"] == 400


def test_bad_tactical_odds_deter_opportunistic_attack_without_large_reward():
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
    )

    assert decision["attack"] is False
    assert decision["combat_risk_score"] > decision["expected_reward_score"]
    assert decision["tactical_caution_score"] > 0


def test_noncriminal_faction_still_needs_a_serious_grievance_to_start_road_violence():
    decision = interception_decision(
        attacker_faction_type="martial_house",
        relation={"hostility": 10, "trust": 0},
        own_available_martial=40,
        own_combat_index=90,
        observed_escort_count=4,
        observed_escort_combat_index=50,
        cargo_value_cash=100_000,
        ransom_value_cash=100_000,
        risk_tolerance=100,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
    )

    assert decision["attack"] is False
    assert decision["reason"] == "no_serious_grievance"
