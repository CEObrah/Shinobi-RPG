import copy
import json
from pathlib import Path

from shinobi_runtime.martial_world.infrastructure import (
    estate_boundary_expansion_quote,
    estate_land_summary,
)
from shinobi_runtime.martial_world.rankings import (
    apply_faction_awareness_evidence,
    apply_faction_reputation_evidence,
    apply_personal_fame_evidence,
    baseline_faction_awareness,
    faction_awareness_score,
    personal_fame_score,
)
from shinobi_runtime.martial_world.relationships import apply_relationship_event
from shinobi_runtime.martial_world.social_presence import person_attends_site
from shinobi_runtime.martial_world.titles import derive_social_titles

ROOT = Path(__file__).resolve().parents[2]


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_relationship_events_require_knowledge_diminish_and_protect_wei_affection():
    base = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    unknown = apply_relationship_event(
        base,
        observer_ref="npc.a",
        subject_ref="npc.b",
        event_kind="rescue",
        observer_knows=False,
    )
    assert unknown["applied"] is False
    assert unknown["state_after"] == base

    first = apply_relationship_event(
        base,
        observer_ref="npc.a",
        subject_ref="npc.b",
        event_kind="rescue",
        observer_knows=True,
    )
    second = apply_relationship_event(
        first["state_after"],
        observer_ref="npc.a",
        subject_ref="npc.b",
        event_kind="rescue",
        observer_knows=True,
    )
    assert first["delta"]["trust"] > 0
    assert second["delta"]["trust"] <= first["delta"]["trust"]

    wei = apply_relationship_event(
        base,
        observer_ref="pc_wei_tang",
        subject_ref="npc.b",
        event_kind="shared_danger",
        observer_knows=True,
        protected_player_ref="pc_wei_tang",
    )
    assert wei["delta"]["affection"] == 0
    assert wei["delta"]["familiarity"] > 0


def test_awareness_fame_reputation_are_separate_and_evidence_scoped():
    state = {"schema": "jianghu-reputation-state-1.0", "audiences": {}, "rankings": {}}
    baseline = baseline_faction_awareness(
        faction_ref="house_tang",
        audience_kind="local_government",
        audience_place_ref="luoyang",
        faction_headquarters="luoyang",
        faction_type="martial_house",
    )
    assert baseline >= 900
    assert faction_awareness_score(
        state, audience_ref="government:central_plain", faction_ref="house_tang", baseline_milli=baseline
    ) == baseline

    unchanged = apply_faction_awareness_evidence(
        state,
        audience_ref="public:changan",
        faction_ref="house_tang",
        evidence_kind="public_contract",
        delivered=False,
    )
    assert unchanged == state

    aware = apply_faction_awareness_evidence(
        state,
        audience_ref="public:changan",
        faction_ref="house_tang",
        evidence_kind="public_contract",
        delivered=True,
    )
    famous = apply_personal_fame_evidence(
        aware,
        audience_ref="public:changan",
        person_ref="pc_wei_tang",
        evidence_kind="fulfilled_contract",
        delivered=True,
    )
    opinion = apply_faction_reputation_evidence(
        famous,
        audience_ref="public:changan",
        faction_ref="house_tang",
        axis_deltas={"reliability": 4},
        delivered=True,
    )
    assert faction_awareness_score(opinion, audience_ref="public:changan", faction_ref="house_tang") > 0
    assert personal_fame_score(opinion, audience_ref="public:changan", person_ref="pc_wei_tang") > 0
    assert opinion["faction_reputation"]["public:changan"]["house_tang"]["reliability"] == 4
    assert "pc_wei_tang" not in opinion["faction_awareness"]["public:changan"]


def test_social_titles_require_identity_knowledge():
    roster = load("state/martial-world/people/house_tang.json")["people"]
    wei = next(row for row in roster if row["person_id"] == "pc_wei_tang")
    identity = load("game/data/martial-world/faction-identities.json")["identities"]["house_tang"]
    family = load("state/martial-world/family.json")
    assert derive_social_titles(
        wei,
        faction_identity=identity,
        family_state=family,
        observer_knows_identity=False,
        observer_knows_office=False,
        observer_knows_faction=True,
    ) == []
    known = derive_social_titles(
        wei,
        faction_identity=identity,
        family_state=family,
        observer_knows_identity=True,
        observer_knows_office=True,
        observer_knows_faction=True,
    )
    assert "Young Master" in known


def test_civic_layer_is_compact_and_regional_without_joining_martial_factions():
    civic = load("state/martial-world/civic-people.json")["people"]
    assert len(civic) < 100
    offices = {office for row in civic for office in row.get("standing_offices", [])}
    assert {"emperor", "prince", "princess", "grand_minister", "magistrate"} <= offices
    assert all(not row.get("faction_ref") for row in civic)
    assert all(row.get("affiliation_ref") for row in civic)

    geography = load("game/data/martial-world/geography.json")["places"]
    major = {ref for ref, row in geography.items() if row.get("kind") == "major_city"}
    homes = {row.get("home_place_ref") for row in civic}
    assert major <= homes


def test_civic_social_presence_never_teleports_between_settlements():
    civic = load("state/martial-world/civic-people.json")["people"]
    emperor = next(row for row in civic if "emperor" in row.get("standing_offices", []))
    sites = load("game/data/martial-world/local-sites.json")["sites"]
    changan = sites["site.changan.tea_house"]
    from datetime import datetime
    assert person_attends_site(
        emperor,
        site_ref="site.changan.tea_house",
        site=changan,
        faction_headquarters="luoyang",
        sites=sites,
        at=datetime(61, 8, 14, 12, 0, 0),
    ) is False


def test_estate_boundary_expansion_is_one_costed_geometry_project():
    tang = load("state/martial-world/factions/house_tang.json")
    land = estate_land_summary(tang["infrastructure"])
    assert land == {"estate_area_m2": 108000, "used_footprint_m2": 76520, "remaining_land_m2": 31480}
    small = estate_boundary_expansion_quote(
        infrastructure=tang["infrastructure"],
        walls_level=tang["buildings"]["walls_gate"],
        additional_land_m2=2000,
        settlement_kind="imperial_capital",
    )
    large = estate_boundary_expansion_quote(
        infrastructure=tang["infrastructure"],
        walls_level=tang["buildings"]["walls_gate"],
        additional_land_m2=5000,
        settlement_kind="imperial_capital",
    )
    sr, lr = small["requirements"], large["requirements"]
    assert sr["new_estate_area_m2"] == 110000
    assert sr["new_perimeter_m"] > 1360
    assert large["total_economic_value_cash"] > small["total_economic_value_cash"]
    assert lr["land_purchase_cash"] > sr["land_purchase_cash"]
    assert "stone_kg" in lr["materials"] and lr["materials"]["stone_kg"] > 0


def test_settlement_tiers_have_distinct_service_depth_and_authored_major_sites():
    geography = load("game/data/martial-world/geography.json")["places"]
    sites = load("game/data/martial-world/local-sites.json")["sites"]

    def types(place):
        return {row.get("site_type") for row in sites.values() if row.get("parent_place_ref") == place}

    imperial = types("luoyang")
    major_city = types("changan")
    city = types("dengfeng")
    remote = types("wudangshan")
    assert {"government_office", "tournament_ground", "weapon_shop", "apothecary", "escort_agency"} <= imperial
    assert {"weapon_shop", "apothecary", "escort_agency"} <= major_city
    assert "weapon_shop" not in city and "escort_agency" not in city
    assert "magistrate_office" not in remote and "guild_hall" not in remote
    assert geography["luoyang"]["kind"] == "imperial_capital"
    assert sites["site.changan.market"]["name"] == "Western Caravan Market"
    assert sites["site.hangzhou.tea_house"]["name"] == "Dragon Well Teahouse"
