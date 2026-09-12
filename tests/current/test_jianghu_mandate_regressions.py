from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from shinobi_runtime.martial_world.appearance import appearance_profile
from shinobi_runtime.martial_world.equipment import carried_mass_kg, encumbrance_effects, load_support_kg
from shinobi_runtime.martial_world.infrastructure import (
    facility_physical_effects, infirmary_capacity, library_capacity,
    residential_capacity, storage_capacity_kg, transport_yard_capacity,
    workshop_capacity,
)
from shinobi_runtime.martial_world.medicine import (
    antidote_affinity_milli, diagnosis_score, poison_treatment_score,
    wound_treatment_score,
)
from shinobi_runtime.martial_world.poison import active_qi_purge, apply_poison
from shinobi_runtime.martial_world.property import (
    active_recovery_demands, clear_recovery_demand, issue_recovery_demand,
    move_claim_after_seizure, property_evidence_ref, provenance_claim,
    set_nonholder_claim, validate_property_evidence,
)
from shinobi_runtime.martial_world.equipment_state import compact_equipment_ledger, hydrate_equipment_ledger
from shinobi_runtime.martial_world.recognition import recognition_assessment
from shinobi_runtime.martial_world.security import select_watch_guards
from shinobi_runtime.martial_world.training import _apply_segment

ROOT = Path(__file__).resolve().parents[2]


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_tang_family_current_profiles_and_kai_child_body_are_exact():
    people = {p["person_id"]: p for p in load("state/martial-world/people/house_tang.json")["people"]}
    expected = {
        "pc_wei_tang": (44, 82, 90, 94, 84, 96, 115, 72, 150, 78),
        "char.zhu": (24, 87, 79, 86, 89, 90, 110, 72, 145, 88),
        "char.ling": (27, 70, 82, 90, 80, 98, 102, 58, 145, 92),
        "char.kai": (55, 22, 45, 48, 30, 70, 40, 25, 125, 35),
    }
    for ref, (birth, strength, speed, dex, endurance, perception, sword, unarmed, qi, qc) in expected.items():
        p = people[ref]
        assert p["birth_year"] == birth
        assert p["attributes"]["intelligence"] == 100
        assert set(p["aptitudes"].values()) == {200}
        assert p["attributes"]["strength"] == strength
        assert p["attributes"]["speed"] == speed
        assert p["attributes"]["dexterity"] == dex
        assert p["attributes"]["endurance"] == endurance
        assert p["attributes"]["perception"] == perception
        assert p["martial_skills"]["sword"] == sword
        assert p["martial_skills"]["unarmed"] == unarmed
        assert p["qi"] == qi and p["qi_control"] == qc
    kai = people["char.kai"]
    assert 15 <= kai["body_mass_kg"] <= 35
    assert not kai.get("standing_offices")
    assert max(kai.get("professional_skills", {}).values(), default=0) <= 5


def test_weapon_catalog_is_broad_disciplines_without_removed_duplicates():
    data = load("game/data/martial-world/equipment.json")
    weapons = data["weapon_catalog"]
    expected = {
        "weapon_jian": "sword", "weapon_dao": "sword", "weapon_long_dao": "sword",
        "weapon_short_sword": "sword", "weapon_dagger": "sword",
        "weapon_spear": "spear", "weapon_short_spear": "spear", "weapon_staff": "spear",
        "weapon_glaive": "spear", "weapon_bow": "bow",
        "weapon_throwing_knife": "hidden_weapons", "weapon_needle": "hidden_weapons",
    }
    assert {k: weapons[k]["discipline"] for k in expected} == expected
    assert len(weapons) == 12
    lowered = json.dumps(data).lower()
    for removed in ("dart", "hunting bow", "composite bow", "halberd"):
        assert removed not in lowered
    assert set(data["ammunition_catalog"]) == {"item_arrow"}


def test_faction_curricula_have_flavored_backups_and_house_tang_stays_sword_first_with_needles_auxiliary():
    identities = load("game/data/martial-world/faction-identities.json")["identities"]
    tang = identities["house_tang"]["training_curriculum"]
    assert tang["sword"] == 100 and tang["unarmed"] > 0
    assert tang["hidden_weapons"] > 0 and tang["hidden_weapons"] < tang["sword"]
    assert all(tang.get(k, 0) == 0 for k in ("spear", "bow"))
    assert "bow" in identities["house_tang"]["martial_neglects"]
    assert "weapon_needle" in identities["house_tang"]["weapons"]
    shaolin = identities["shaolin"]["training_curriculum"]
    assert shaolin["unarmed"] > shaolin["spear"] > 0 and shaolin.get("sword", 0) == 0
    bow = identities["faction.northern_bow_school"]["training_curriculum"]
    assert bow["bow"] > 0 and bow["sword"] > 0 and bow["unarmed"] > 0
    hidden = identities["silent_seal_hall"]["training_curriculum"]
    assert hidden["hidden_weapons"] > 0 and hidden["sword"] > 0 and hidden["unarmed"] > 0
    # The authored world uses more than one backup weapon family.
    backup_families = set()
    for row in identities.values():
        cur = row.get("training_curriculum", {})
        if cur.get("bow", 0) > 0 or cur.get("hidden_weapons", 0) > 0 or cur.get("unarmed", 0) >= max(cur.get("sword", 0), cur.get("spear", 0), cur.get("bow", 0), cur.get("hidden_weapons", 0)):
            if cur.get("sword", 0) > 0: backup_families.add("sword")
            if cur.get("spear", 0) > 0: backup_families.add("spear")
    assert backup_families == {"sword", "spear"}


def test_finite_curriculum_budget_dilutes_same_subject_when_third_discipline_added():
    person = {
        "person_id": "p", "birth_year": 20, "membership_grade": "full",
        "attributes": {k: 50 for k in ("strength", "speed", "dexterity", "endurance", "perception", "intelligence", "willpower")},
        "martial_skills": {"sword": 20, "unarmed": 20, "bow": 20}, "professional_skills": {},
        "aptitudes": {"physical": 100, "martial": 100, "qi": 100, "cognitive": 100, "leadership": 100},
        "qi": 20, "qi_control": 20, "health": {"status": "healthy", "consciousness": 100},
    }
    segment = {"started_at": "0061-01-01T00:00:00", "facilities": {"training_hall": 3, "training_grounds": 3}, "intensity_milli": 1000}
    two, three = deepcopy(person), deepcopy(person)
    s2, s3 = {}, {}
    _apply_segment(two, s2, {**segment, "curriculum": {"sword": 100, "unarmed": 100}}, days=3650)
    _apply_segment(three, s3, {**segment, "curriculum": {"sword": 100, "unarmed": 100, "bow": 100}}, days=3650)
    assert two["martial_skills"]["sword"] > three["martial_skills"]["sword"]
    assert three["martial_skills"]["bow"] > person["martial_skills"]["bow"]


def test_strength_relative_encumbrance_uses_mass_not_slots():
    catalog = load("game/data/martial-world/equipment.json")
    mass = carried_mass_kg({"weapon_needle": 100, "weapon_jian": 1}, catalog)
    assert mass > 0
    weak = encumbrance_effects(total_mass_kg=mass, strength=30, endurance=60)
    strong = encumbrance_effects(total_mass_kg=mass, strength=90, endurance=60)
    assert load_support_kg(strength=90, endurance=60) > load_support_kg(strength=30, endurance=60)
    assert strong["burden_ratio"] < weak["burden_ratio"]
    assert strong["movement_factor_milli"] > weak["movement_factor_milli"]
    assert strong["fatigue_cost_milli"] < weak["fatigue_cost_milli"]


def test_poison_can_be_fully_rejected_and_qi_purge_spends_qi():
    weak = apply_poison(poison_ref="sedative", current_burden=0, doses=1, endurance=100, qi=150, qi_control=100)
    assert weak["exposure_rejected"] is True and weak["burden_after"] == 0
    stronger = apply_poison(poison_ref="cardiotoxic", current_burden=0, doses=5, endurance=50, qi=50, qi_control=30)
    assert stronger["burden_after"] > 0
    purge = active_qi_purge(poison_ref="cardiotoxic", burden=stronger["burden_after"], current_qi=50, qi=50, qi_control=80, elapsed_minutes=180)
    assert purge["burden_after"] < purge["burden_before"]
    assert purge["qi_spent"] > 0 and purge["current_qi_after"] < purge["current_qi_before"]


def test_ling_medicine_is_mechanically_better_and_antidote_families_matter():
    people = {p["person_id"]: p for p in load("state/martial-world/people/house_tang.json")["people"]}
    ling = people["char.ling"]
    la, lattrs = ling["professional_skills"], ling["attributes"]
    ling_dx = diagnosis_score(medicine=la["medicine"], intelligence=lattrs["intelligence"], perception=lattrs["perception"], tool_available=True)
    ordinary_dx = diagnosis_score(medicine=55, intelligence=60, perception=60, tool_available=True)
    assert ling_dx["diagnosis_score"] > ordinary_dx["diagnosis_score"]
    ling_tx = wound_treatment_score(medicine=la["medicine"], dexterity=lattrs["dexterity"], intelligence=lattrs["intelligence"], perception=lattrs["perception"], physician_kit=True, medical_supply=True)
    ordinary_tx = wound_treatment_score(medicine=55, dexterity=60, intelligence=60, perception=60, physician_kit=True, medical_supply=True)
    assert ling_tx["treatment_score"] > ordinary_tx["treatment_score"]
    assert wound_treatment_score(medicine=120, dexterity=90, intelligence=100, perception=98, physician_kit=False, medical_supply=True)["advanced_procedure_enabled"] is False
    assert antidote_affinity_milli("nerve_antidote", "paralytic") > antidote_affinity_milli("blood_cardiac_antidote", "paralytic")
    assert antidote_affinity_milli("blood_cardiac_antidote", "cardiotoxic") > antidote_affinity_milli("nerve_antidote", "cardiotoxic")
    nerve = poison_treatment_score(medicine=120, intelligence=100, perception=98, poison_ref="paralytic", medicine_ref="nerve_antidote", burden=50, patient_endurance=80, patient_qi=100, patient_qi_control=80, facility_level=5)
    wrong = poison_treatment_score(medicine=120, intelligence=100, perception=98, poison_ref="paralytic", medicine_ref="blood_cardiac_antidote", burden=50, patient_endurance=80, patient_qi=100, patient_qi_control=80, facility_level=5)
    assert nerve["poison_treatment_score"] > wrong["poison_treatment_score"]


def test_clothing_variants_and_recognition_do_not_equate_clothes_with_membership():
    catalog = load("game/data/martial-world/equipment.json")
    identities = load("game/data/martial-world/faction-identities.json")["identities"]
    assert len(catalog["faction_clothing_variants"]) == len(identities) == 240
    assert catalog["faction_clothing_variants"]["clothing.faction.house_tang"] == {"base_ref": "clothing_faction_martial", "faction_ref": "house_tang"}
    observer = {"attributes": {"perception": 100, "intelligence": 100}}
    stranger = {"person_id": "stranger", "appearance": 60, "body_mass_kg": 70, "attributes": {"strength": 60, "endurance": 60}, "qi": 40, "qi_control": 30}
    disguised = recognition_assessment(observer=observer, target=stranger, target_items={"clothing.faction.house_tang": 1}, equipment_catalog=catalog, familiarity=0)
    assert disguised["faction_clothing_evidence_ref"] == "house_tang"
    assert disguised["faction_membership_proven_by_clothing"] is False
    assert disguised["personal_recognized"] is False


def test_wei_appearance_is_canonical_and_concealment_environment_reduce_evidence():
    roster = load("state/martial-world/people/house_tang.json")["people"]
    wei = next(p for p in roster if p["person_id"] == "pc_wei_tang")
    profile = appearance_profile(wei, current_year=61)
    assert profile["hair_color"] == "black" and profile["eye_color"] == "black"
    assert profile["hair_presentation"] == "short curly" and profile["build"] == "lean athletic"
    catalog = load("game/data/martial-world/equipment.json")
    observer = {"attributes": {"perception": 100, "intelligence": 100}}
    clear = recognition_assessment(observer=observer, target=wei, target_items={"clothing.faction.house_tang": 1}, equipment_catalog=catalog, familiarity=80, viewing_seconds=10)
    obscured = recognition_assessment(observer=observer, target=wei, target_items={"concealment_plain_full_mask": 1, "clothing.faction.house_tang": 1}, equipment_catalog=catalog, familiarity=80, viewing_seconds=2, smoke_visibility_milli=300, weather_visibility_milli=500, viewing_angle_milli=500, obstruction_milli=500)
    assert obscured["face_evidence_milli"] < clear["face_evidence_milli"]
    assert obscured["environment_visibility_milli"] < clear["environment_visibility_milli"]
    assert obscured["personal_recognition_confidence_milli"] < clear["personal_recognition_confidence_milli"]


def test_infrastructure_quality_does_not_create_physical_capacity_and_all_major_house_tang_capacities_are_live():
    tang = load("state/martial-world/factions/house_tang.json")
    infra = tang["infrastructure"]
    one = facility_physical_effects({"residential_compound": 1}, infra, "residential_compound")
    five = facility_physical_effects({"residential_compound": 5}, infra, "residential_compound")
    assert one["resident_capacity"] == five["resident_capacity"] == 204
    assert residential_capacity(tang["buildings"], infra) == 204
    assert workshop_capacity(tang["buildings"], infra)["craft_workstations"] > 0
    assert all(v > 0 for v in infirmary_capacity(tang["buildings"], infra).values())
    assert all(v > 0 for v in storage_capacity_kg(tang["buildings"], infra).values())
    assert library_capacity(tang["buildings"], infra) > 0
    assert all(v > 0 for v in transport_yard_capacity(tang["buildings"], infra).values())


def test_watch_positions_without_people_do_not_create_guards():
    tang = load("state/martial-world/factions/house_tang.json")
    assert select_watch_guards([], faction_ref="house_tang", at=datetime(61, 8, 14, 22), buildings=tang["buildings"], infrastructure=tang["infrastructure"]) == []
    roster = load("state/martial-world/people/house_tang.json")["people"]
    hydrated = []
    for p in roster[:20]:
        row = deepcopy(p); row.setdefault("health", {"status": "healthy", "consciousness": 100}); hydrated.append(row)
    guards = select_watch_guards(hydrated, faction_ref="house_tang", at=datetime(61, 8, 14, 22), buildings=tang["buildings"], infrastructure=tang["infrastructure"])
    assert guards and len(guards) <= 20


def test_property_provenance_generates_validatable_live_evidence_and_can_be_cleared_on_recovery():
    ledger = {"schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {}, "person_loadouts": {"victim": {"items": {"weapon_dao": 1}}, "taker": {"items": {"weapon_dao": 1}}}}
    seized = move_claim_after_seizure(ledger, from_holder="victim", to_holder="taker", item_ref="weapon_dao", quantity=1, original_owner_ref="victim")
    claim = provenance_claim(seized, "taker", "weapon_dao")
    assert claim["owner_ref"] == "victim" and claim["status"] == "seized"
    evidence = property_evidence_ref(seized, holder_ref="taker", item_ref="weapon_dao")
    assert evidence == "property_claim:taker:weapon_dao"
    assert validate_property_evidence(seized, evidence, holder_ref="taker")["owner_ref"] == "victim"
    recovered = set_nonholder_claim(seized, holder_ref="taker", item_ref="weapon_dao", owner_ref="victim", quantity=0)
    assert provenance_claim(recovered, "taker", "weapon_dao") is None
    assert validate_property_evidence(recovered, evidence, holder_ref="taker") is None


def test_property_provenance_survives_hydrate_compact_and_recovery_demand_is_current_not_history():
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {"taker": {"items": {"weapon_dao": 1}, "condition_milli": {}}},
    }
    seized = move_claim_after_seizure(
        ledger, from_holder="victim", to_holder="taker", item_ref="weapon_dao",
        quantity=1, original_owner_ref="house_victim",
    )
    evidence = property_evidence_ref(seized, holder_ref="taker", item_ref="weapon_dao")
    demanded = issue_recovery_demand(
        seized, owner_ref="house_victim", holder_ref="taker", item_ref="weapon_dao",
        quantity=1, issued_at="0061-08-21T12:00:00", evidence_ref=evidence,
    )
    hydrated = hydrate_equipment_ledger(compact_equipment_ledger(demanded))
    assert provenance_claim(hydrated, "taker", "weapon_dao")["owner_ref"] == "house_victim"
    rows = active_recovery_demands(hydrated, owner_ref="house_victim")
    assert len(rows) == 1 and rows[0]["holder_ref"] == "taker"
    cleared = clear_recovery_demand(
        hydrated, owner_ref="house_victim", holder_ref="taker", item_ref="weapon_dao",
    )
    assert active_recovery_demands(cleared, owner_ref="house_victim") == []
