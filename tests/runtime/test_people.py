import json
import math
from collections import Counter
from pathlib import Path

import pytest

from shinobi_runtime.people import (
    CohortRoster,
    RepositoryPersonSheetResolver,
    assemble_sheet,
    cohort_slot_baseline,
    core_from_exact,
    core_from_person,
    core_from_registry,
)
from shinobi_runtime.sim import CounterRNG
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_existing_exact_and_person_owners_share_one_core_interface():
    zhu = core_from_exact(load("state/char/zhu.json"), component_ref="profile.exact", source_ref="state/char/zhu.json")
    toma = core_from_person(load("state/person/ht/023.json"), component_ref="profile.legacy", source_ref="state/person/ht/023.json")
    assert zhu.person_id == "char.zhu"
    assert toma.person_id == "ht.m023"
    assert zhu.representation == toma.representation == "exact"
    assert str(toma.birth_date) == "SE-0053-06-04"


@pytest.mark.parametrize("relative_path,raw_status,expected_birth_date", (("state/char/chiyo.json", "active", "SE--012-09-19"), ("state/char/kosuke-maruboshi.json", "active", "SE-0000-11-04"), ("state/char/kimimaro.json", "ill", "SE-0045-07-06")))
def test_exact_adapter_normalizes_operational_alive_status_without_mutating_owner(relative_path, raw_status, expected_birth_date):
    record = load(relative_path); original = json.loads(json.dumps(record))
    core = core_from_exact(record, component_ref="profile.exact", source_ref=relative_path)
    sheet = assemble_sheet(core, components={"profile.exact": record}).to_record()
    assert core.life_status == "alive"
    assert str(core.birth_date) == expected_birth_date
    assert sheet["components"]["profile.exact"]["life_status"] == raw_status
    assert record == original


def test_exact_adapter_rejects_unknown_life_status():
    record = load("state/char/zhu.json"); record["life_status"] = "retired"
    with pytest.raises(ValueError, match="unsupported exact life status"):
        core_from_exact(record, component_ref="profile.exact", source_ref="state/char/zhu.json")


def test_rostered_sheet_requires_baseline_and_keeps_namespaces_separate():
    core = core_from_registry(load("state/person-core/house-tang.json"), person_id="ht.core.001", source_ref="state/person-core/house-tang.json")
    sheet = assemble_sheet(core, cohort_baseline={"stats": {"sword": 80}}, components={})
    assert sheet.to_record()["core"]["person_id"] == "ht.core.001"
    assert sheet.to_record()["cohort_baseline"]["stats"]["sword"] == 80
    assert sheet.to_record()["core"]["identity_cues"]["doctrine_expression"]


def test_house_tang_roster_target_conserves_current_thirty_two_people():
    house = load("state/house/tang.json")
    rostered = tuple(person_id for cohort in house["cohorts"] for person_id in cohort["roster_refs"])
    exact = tuple(person_id for cohort in house["cohorts"] for person_id in cohort["members"]) + tuple(house["unassigned_members"]) + tuple(house["externally_assigned_members"])
    roster = CohortRoster("house.tang.all", exact, rostered)
    assert house["rostered_member_count"] == 27
    assert set(house["member_ids"]) == set(exact) | set(rostered)
    assert roster.represented_count == 32
    assert roster.cohort_backed_count == 27


def test_house_roster_registry_has_one_core_per_roster_reference():
    house = load("state/house/tang.json")
    registry = load("state/person-core/house-tang.json")
    scheduler = load("state/time/causal-scheduler.json")
    world_time = load("state/meta.json")["time"]
    refs = {person_id for cohort in house["cohorts"] for person_id in cohort["roster_refs"]}
    cohort_cursors = {person_id: cohort["cohort_profile"]["development"]["resolved_through"] for cohort in house["cohorts"] if isinstance(cohort.get("cohort_profile"), dict) for person_id in cohort["roster_refs"]}
    assert set(registry["people"]) == refs
    host_state = scheduler["hosts"]["host.house.house_tang"]["state"]
    assert host_state["safe_through"] >= world_time
    for person_id, record in registry["people"].items():
        assert record["id"] == person_id
        assert record["cohort_ref"] in {cohort["id"] for cohort in house["cohorts"]}
        assert "coverage_ref" not in record
        assert record["resolved_through"] <= cohort_cursors[person_id] <= world_time
        if cohort_cursors[person_id] < world_time:
            assert host_state["safe_through"] >= world_time


def test_roster_slot_baselines_recombine_to_every_saved_numeric_distribution():
    house = load("state/house/tang.json")
    for cohort in house["cohorts"]:
        refs = cohort["roster_refs"]
        if not refs: continue
        baselines = [cohort_slot_baseline(cohort_id=cohort["id"], profile=cohort["cohort_profile"], slot=slot, expected_count=len(refs)) for slot in range(len(refs))]
        for name, summary in cohort["cohort_profile"]["numeric_distributions"].items():
            values = [baseline["numeric_values"][name] for baseline in baselines]
            mean = sum(values) / len(values)
            spread = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
            assert abs(mean - summary["mean"]) <= 0.00001
            assert abs(spread - summary["sd"]) <= 0.00001
            assert abs(min(values) - summary["min"]) <= 0.00001
            assert abs(max(values) - summary["max"]) <= 0.00001


def test_roster_slot_categories_conserve_every_saved_count():
    house = load("state/house/tang.json")
    for cohort in house["cohorts"]:
        refs = cohort["roster_refs"]
        if not refs: continue
        baselines = [cohort_slot_baseline(cohort_id=cohort["id"], profile=cohort["cohort_profile"], slot=slot, expected_count=len(refs)) for slot in range(len(refs))]
        actual = {}
        for baseline in baselines:
            for group, values in baseline["category_values"].items():
                for value in values: actual[f"{group}:{value}"] = actual.get(f"{group}:{value}", 0) + 1
        assert actual == cohort["cohort_profile"]["category_counts"]


def test_deterministic_materialization_selects_real_ids_and_conserves_total():
    roster = CohortRoster("house.tang.instructors", (), tuple(f"ht.core.{index:03d}" for index in range(1, 6)))
    first_rng = CounterRNG(world_seed="seed", transaction_id="tx.casualty", stream="house.instructors")
    replay_rng = CounterRNG(world_seed="seed", transaction_id="tx.casualty", stream="house.instructors")
    selected = roster.select_rostered(2, first_rng)
    assert selected == roster.select_rostered(2, replay_rng)
    changed = roster.materialize(selected[0])
    assert changed.represented_count == roster.represented_count
    assert changed.cohort_backed_count == roster.cohort_backed_count - 1


def test_repository_resolver_loads_exact_person_and_rostered_full_sheets():
    resolver = RepositoryPersonSheetResolver(RepositoryStore(ROOT))
    exact = resolver("char.zhu"); person = resolver("ht.m023"); rostered = resolver("ht.core.001")
    assert exact["core"]["representation"] == "exact"
    assert exact["components"]["profile.exact"]["owner_id"] == "char.zhu"
    assert person["components"]["profile.person"]["schema"] == "person"
    assert rostered["core"]["representation"] == "rostered_cohort"
    assert rostered["cohort_baseline"]["cohort_ref"] == "cohort.house_tang.instructor_cadre"
    assert rostered["cohort_baseline"]["numeric_values"]["stats.martial_skills.sword"] >= 0
    assert resolver("ht.unknown") is None


def test_repository_resolver_reuses_owner_index_reads_within_one_call():
    class CountingRepository(RepositoryStore):
        def __init__(self, root):
            super().__init__(root); self.read_counts = Counter()
        def read_json(self, relative_path):
            self.read_counts[str(relative_path)] += 1
            return super().read_json(relative_path)
    repo = CountingRepository(ROOT)
    resolver = RepositoryPersonSheetResolver(repo)
    assert resolver("ht.core.001") is not None
    assert repo.read_counts["state/index/owners/person.json"] <= 1
