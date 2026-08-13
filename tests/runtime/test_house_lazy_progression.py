"""Regression coverage for lazy House/cohort progression settlement."""

import copy
import json
from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.paths import DEVELOPMENT_BANK_PATH
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]
HOUSE_PATH = "state/house/tang.json"
START = CampaignTime.parse("SE-0061-02-06T21:15:00")
CURRENT = CampaignTime.parse("SE-0061-02-09T22:18:21")


def test_house_schedule_counts_only_overlapping_training_windows() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    policy = planner._house_training_policy("house.tang")
    assert policy is not None
    assert planner._scheduled_house_training_hours(START, CURRENT, policy) == 18


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
        assert profile["development"]["credits"]
        assert any(value.startswith("house_training_settled:") for value in profile["provenance"])
        assert profile["numeric_distributions"]["stats.martial_skills.sword"]["mean"] >= before[cohort["id"]]


def test_time_plan_settles_sparse_cohorts_and_exact_house_members() -> None:
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
    assert "state/char/zhu.json" in paths
    assert "state/char/linh.json" in paths
    assert "state/person/ht/023.json" in paths
    after = json.loads(enriched.writes[HOUSE_PATH].decode("utf-8"))
    assert after["cohorts"][1]["cohort_profile"]["development"]["resolved_through"] == str(CURRENT)
    assert enriched.result["house_progression_reviews"][0]["house_id"] == "house.tang"
    exact = {row["member_ref"]: row for row in enriched.result["house_exact_member_progression"]}
    assert {"char.zhu", "char.linh", "ht.m023"}.issubset(exact)
    assert all(row["active_hours"] != "0.000" for row in exact.values())
    bank = json.loads(enriched.writes[DEVELOPMENT_BANK_PATH].decode("utf-8"))
    for ref in ("char.zhu", "char.linh", "ht.m023"):
        assert bank["entries"][ref]["resolved_through"] == str(CURRENT)
        assert bank["entries"][ref]["credits"]
    zhu = json.loads(enriched.writes["state/char/zhu.json"].decode("utf-8"))
    linh = json.loads(enriched.writes["state/char/linh.json"].decode("utf-8"))
    toma = json.loads(enriched.writes["state/person/ht/023.json"].decode("utf-8"))
    assert "last_settled_at" not in zhu["development"]
    assert "last_settled_at" not in linh["development"]
    assert toma["resolved_through"] == str(CURRENT)
