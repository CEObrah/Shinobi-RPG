"""Regression coverage for lazy House/cohort and institutional progression."""

import copy
import json
from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.paths import DEVELOPMENT_BANK_PATH
from shinobi_runtime.people.repertoire import technique_prerequisites_met
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]
HOUSE_PATH = "state/house/tang.json"
START = CampaignTime.parse("SE-0061-02-06T21:15:00")
WINDOW_END = CampaignTime.parse("SE-0061-02-09T22:18:21")
CURRENT = CampaignTime.parse(json.loads((ROOT / "state/meta.json").read_text(encoding="utf-8"))["time"])


def test_house_schedule_counts_only_overlapping_training_windows() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    policy = planner._house_training_policy("house.tang")
    assert policy is not None
    assert planner._scheduled_house_training_hours(START, WINDOW_END, policy) == 24
    total, shared, supplemental = planner._scheduled_house_training_components(START, WINDOW_END, policy)
    assert total == 24
    assert shared + supplemental == total
    assert shared > supplemental > 0


def test_house_cohorts_settle_progression_before_cursor_advances() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    house = copy.deepcopy(planner.repository.read_json(HOUSE_PATH))
    before = {
        cohort["id"]: cohort["cohort_profile"]["numeric_distributions"]["stats.martial_skills.sword"]["mean"]
        for cohort in house["cohorts"]
        if isinstance(cohort.get("cohort_profile"), dict)
    }
    result = planner._settle_house_progression(house, through=CURRENT)
    assert len(result["cohorts"]) == 4
    for cohort in house["cohorts"]:
        profile = cohort.get("cohort_profile")
        if not isinstance(profile, dict):
            continue
        assert profile["development"]["resolved_through"] == str(CURRENT)
        assert profile["numeric_distributions"]["stats.martial_skills.sword"]["mean"] >= before[cohort["id"]]


def test_time_plan_settles_exact_and_persistent_lite_house_members() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    house = copy.deepcopy(planner.repository.read_json(HOUSE_PATH))
    house["operating_process"]["last_review"] = str(CURRENT)
    base = _BuiltPlan(
        code="advance_time_ready",
        affected_refs=(HOUSE_PATH,),
        writes={HOUSE_PATH: _json_bytes(house)},
        result={"house_reviews": [f"{HOUSE_PATH}@{CURRENT}x1"]},
        validator=None,
    )
    enriched = planner._apply_house_progression_to_time_plan(base)
    paths = set(enriched.affected_refs)
    assert HOUSE_PATH in paths
    assert DEVELOPMENT_BANK_PATH in paths
    assert "state/person-core/house-tang.json" in paths
    assert "state/char/zhu.json" in paths
    assert "state/char/linh.json" in paths
    assert "state/person/ht/023.json" not in paths

    after = json.loads(enriched.writes[HOUSE_PATH].decode("utf-8"))
    assert all(
        cohort["cohort_profile"]["development"]["resolved_through"] == str(CURRENT)
        for cohort in after["cohorts"]
        if isinstance(cohort.get("cohort_profile"), dict)
    )
    assert enriched.result["house_progression_reviews"][0]["house_id"] == "house.tang"
    exact = {row["member_ref"]: row for row in enriched.result["house_exact_member_progression"]}
    assert {"char.zhu", "char.linh"}.issubset(exact)
    assert "ht.m023" not in exact

    lite = {row["member_ref"]: row for row in enriched.result["house_rostered_individual_progression"]}
    assert "ht.m023" in lite
    assert len(lite) == 28
    registry = json.loads(enriched.writes["state/person-core/house-tang.json"].decode("utf-8"))
    assert registry["profiles"]["ht.m023"]["institutional_progression"]["resolved_through"] == str(CURRENT)

    bank = json.loads(enriched.writes[DEVELOPMENT_BANK_PATH].decode("utf-8"))
    for ref in ("char.zhu", "char.linh"):
        assert bank["entries"][ref]["resolved_through"] == str(CURRENT)
        assert bank["entries"][ref]["credits"]


def test_house_has_three_technical_tiers_and_instructors_share_senior_access() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    institutions = planner._training_progression_institutions()
    tang = institutions["house.tang"]
    assert tang["technical_tier_order"] == ["junior", "senior", "master"]
    mapping = tang["standing_to_technical_tier"]
    assert mapping["junior_disciple"] == "junior"
    assert mapping["senior_disciple"] == "senior"
    assert mapping["assistant_instructor"] == "senior"
    assert mapping["senior_instructor"] == "senior"
    assert mapping["sword_master"] == "master"

    packages = planner._tech_package_registry()
    assert "PKG_HT_INSTRUCTOR" not in packages
    junior_ref = tang["technical_tiers"]["junior"]["package_ref"]
    senior_ref = tang["technical_tiers"]["senior"]["package_ref"]
    master_ref = tang["technical_tiers"]["master"]["package_ref"]
    junior_methods = set(planner._package_methods(packages, junior_ref))
    senior_methods = set(planner._package_methods(packages, senior_ref))
    master_methods = set(planner._package_methods(packages, master_ref))
    assert junior_methods
    assert senior_methods
    assert master_methods
    assert senior_methods.issubset(master_methods)

    protected = planner._house_protected_methods(institutions, packages)
    public_methods = set(planner._package_methods(packages, "PKG_KONOHA_ACADEMY_CORE"))
    assert protected
    assert protected.isdisjoint(public_methods)


def test_clan_curriculum_uses_package_order_not_village_rank() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    institutions = planner._training_progression_institutions()
    packages = planner._tech_package_registry()
    policy = institutions["clan.yamanaka"]
    package_ref = policy["membership_package_ref"]
    methods = planner._package_methods(packages, package_ref)
    assert len(methods) >= 2
    student = {
        "official_rank_or_status": "Jonin",
        "repertoire": {
            "packages": [package_ref],
            "method_mastery": {},
            "latent_or_locked_techniques": [],
        },
    }
    assert planner._clan_technique_allowed(student, methods[0], policy, packages)
    assert not planner._clan_technique_allowed(student, methods[1], policy, packages)
    student["repertoire"]["method_mastery"][methods[0]] = 50
    assert planner._clan_technique_allowed(student, methods[1], policy, packages)


def test_semantic_prerequisites_support_capability_and_clan_tokens() -> None:
    house_student = {
        "domain_proficiencies": {"wind": 40},
        "chakra_dimensions": {"control": 45},
        "repertoire": {
            "packages": ["PKG_HT_JUNIOR"],
            "method_mastery": {},
            "latent_or_locked_techniques": [],
        },
    }
    assert technique_prerequisites_met(
        house_student,
        {"prerequisites": ["wind_nature", "chakra_control"]},
    )

    clan_student = {
        "repertoire": {
            "packages": ["PKG_HYUGA_CLAN_CORE"],
            "method_mastery": {"byakugan_activation": 50},
            "latent_or_locked_techniques": [],
        },
    }
    assert technique_prerequisites_met(
        clan_student,
        {"prerequisites": ["hyuga_training", "chakra_network_perception"]},
    )
