import json
import math
from collections import Counter
from pathlib import Path

import pytest

from shinobi_runtime.people import (
    CohortRoster,
    RepositoryPersonSheetResolver,
    assemble_sheet,
    core_from_exact,
    core_from_registry,
)
from shinobi_runtime.people.profiles import category_counts, numeric_map, profile_entry_for
from shinobi_runtime.sim import CounterRNG
from shinobi_runtime.sim.scheduler_store import SchedulerStore
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_exact_and_rostered_people_share_one_core_interface():
    zhu = core_from_exact(
        load("state/char/zhu.json"),
        component_ref="profile.exact",
        source_ref="state/char/zhu.json",
    )
    registry = load("state/person-core/house-tang.json")
    toma = core_from_registry(
        registry,
        person_id="ht.m023",
        source_ref="state/person-core/house-tang.json",
    )
    assert zhu.person_id == "char.zhu"
    assert toma.person_id == "ht.m023"
    assert zhu.representation == "exact"
    assert toma.representation == "rostered_cohort"
    assert str(toma.birth_date) == "SE-0053-06-04"
    assert profile_entry_for(registry, "ht.m023")["person_ref"] == "ht.m023"


@pytest.mark.parametrize(
    "relative_path,raw_status,expected_birth_date",
    (
        ("state/char/chiyo.json", "active", "SE--012-09-19"),
        ("state/char/kosuke-maruboshi.json", "active", "SE-0000-11-04"),
        ("state/char/kimimaro.json", "ill", "SE-0045-07-06"),
    ),
)
def test_exact_adapter_normalizes_operational_alive_status_without_mutating_owner(
    relative_path, raw_status, expected_birth_date
):
    record = load(relative_path)
    original = json.loads(json.dumps(record))
    core = core_from_exact(record, component_ref="profile.exact", source_ref=relative_path)
    sheet = assemble_sheet(core, components={"profile.exact": record}).to_record()
    assert core.life_status == "alive"
    assert str(core.birth_date) == expected_birth_date
    assert sheet["components"]["profile.exact"]["life_status"] == raw_status
    assert record == original


def test_exact_adapter_rejects_unknown_life_status():
    record = load("state/char/zhu.json")
    record["life_status"] = "retired"
    with pytest.raises(ValueError, match="unsupported exact life status"):
        core_from_exact(
            record,
            component_ref="profile.exact",
            source_ref="state/char/zhu.json",
        )


def test_rostered_sheet_requires_baseline_and_keeps_namespaces_separate():
    core = core_from_registry(
        load("state/person-core/house-tang.json"),
        person_id="ht.core.001",
        source_ref="state/person-core/house-tang.json",
    )
    sheet = assemble_sheet(core, cohort_baseline={"stats": {"sword": 80}}, components={})
    assert sheet.to_record()["core"]["person_id"] == "ht.core.001"
    assert sheet.to_record()["cohort_baseline"]["stats"]["sword"] == 80
    assert sheet.to_record()["core"]["identity_cues"]["doctrine_expression"]


def test_house_tang_roster_conserves_current_thirty_two_people():
    house = load("state/house/tang.json")
    rostered = tuple(
        person_id for cohort in house["cohorts"] for person_id in cohort["roster_refs"]
    )
    exact = (
        tuple(person_id for cohort in house["cohorts"] for person_id in cohort["members"])
        + tuple(house["unassigned_members"])
        + tuple(house["externally_assigned_members"])
    )
    roster = CohortRoster("house.tang.all", exact, rostered)
    assert house["rostered_member_count"] == 28
    assert set(house["member_ids"]) == set(exact) | set(rostered)
    assert roster.represented_count == 32
    assert roster.cohort_backed_count == 28
    assert "ht.m023" in rostered


def test_house_roster_registry_has_one_core_and_profile_per_roster_reference():
    house = load("state/house/tang.json")
    registry = load("state/person-core/house-tang.json")
    scheduler = SchedulerStore(RepositoryStore(ROOT)).load(full=True)
    world_time = load("state/meta.json")["time"]
    refs = {person_id for cohort in house["cohorts"] for person_id in cohort["roster_refs"]}
    cohort_cursors = {
        person_id: cohort["cohort_profile"]["development"]["resolved_through"]
        for cohort in house["cohorts"]
        if isinstance(cohort.get("cohort_profile"), dict)
        for person_id in cohort["roster_refs"]
    }
    assert set(registry["people"]) == refs == set(registry["profiles"])
    host_state = scheduler.hosts["host.house.house_tang"].state
    assert str(host_state.safe_through) >= world_time
    for person_id, record in registry["people"].items():
        profile = registry["profiles"][person_id]
        assert record["id"] == profile["person_ref"] == person_id
        assert record["cohort_ref"] == profile["cohort_ref"]
        assert record["cohort_ref"] in {cohort["id"] for cohort in house["cohorts"]}
        assert "coverage_ref" not in record
        assert record["resolved_through"] <= cohort_cursors[person_id] <= world_time
        assert profile["institutional_progression"]["resolved_through"] <= world_time


def test_persistent_profiles_recombine_to_every_derived_cohort_numeric_summary():
    house = load("state/house/tang.json")
    registry = load("state/person-core/house-tang.json")
    for cohort in house["cohorts"]:
        refs = cohort["roster_refs"]
        if not refs:
            continue
        entries = [profile_entry_for(registry, ref) for ref in refs]
        for name, summary in cohort["cohort_profile"]["numeric_distributions"].items():
            values = [numeric_map(registry, entry)[name] for entry in entries]
            mean = sum(values) / len(values)
            spread = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
            assert abs(mean - summary["mean"]) <= 0.00001
            assert abs(spread - summary["sd"]) <= 0.00001
            assert abs(min(values) - summary["min"]) <= 0.00001
            assert abs(max(values) - summary["max"]) <= 0.00001


def test_persistent_profiles_recombine_to_every_derived_category_count():
    house = load("state/house/tang.json")
    registry = load("state/person-core/house-tang.json")
    for cohort in house["cohorts"]:
        refs = cohort["roster_refs"]
        if not refs:
            continue
        entries = [profile_entry_for(registry, ref) for ref in refs]
        assert category_counts(entries) == cohort["cohort_profile"]["category_counts"]


def test_saved_rostered_stats_do_not_change_when_cohort_summary_changes():
    registry = load("state/person-core/house-tang.json")
    before = numeric_map(registry, profile_entry_for(registry, "ht.core.001"))
    house = load("state/house/tang.json")
    cohort = next(row for row in house["cohorts"] if "ht.core.001" in row.get("roster_refs", []))
    cohort["cohort_profile"]["numeric_distributions"]["stats.martial_skills.sword"]["mean"] += 40

    class ModifiedHouseRepository(RepositoryStore):
        def read_json(self, relative_path):
            if str(relative_path) == "state/house/tang.json":
                return json.loads(json.dumps(house))
            return super().read_json(relative_path)

    resolved = RepositoryPersonSheetResolver(ModifiedHouseRepository(ROOT))("ht.core.001")
    assert resolved["cohort_baseline"]["numeric_values"]["stats.martial_skills.sword"] == before[
        "stats.martial_skills.sword"
    ]


def test_deterministic_materialization_selects_real_ids_and_conserves_total():
    roster = CohortRoster(
        "house.tang.instructors",
        (),
        tuple(f"ht.core.{index:03d}" for index in range(1, 6)),
    )
    first_rng = CounterRNG(world_seed="seed", transaction_id="tx.casualty", stream="house.instructors")
    replay_rng = CounterRNG(world_seed="seed", transaction_id="tx.casualty", stream="house.instructors")
    selected = roster.select_rostered(2, first_rng)
    assert selected == roster.select_rostered(2, replay_rng)
    changed = roster.materialize(selected[0])
    assert changed.represented_count == roster.represented_count
    assert changed.cohort_backed_count == roster.cohort_backed_count - 1


def test_repository_resolver_loads_exact_and_persistent_lite_sheets():
    resolver = RepositoryPersonSheetResolver(RepositoryStore(ROOT))
    exact = resolver("char.zhu")
    toma = resolver("ht.m023")
    rostered = resolver("ht.core.001")
    assert exact["core"]["representation"] == "exact"
    assert exact["components"]["profile.exact"]["owner_id"] == "char.zhu"
    for sheet in (toma, rostered):
        assert sheet["core"]["representation"] == "rostered_cohort"
        assert sheet["cohort_baseline"]["representation"] == "rostered_individual"
        assert sheet["cohort_baseline"]["numeric_values"]["stats.martial_skills.sword"] >= 0
        assert sheet["cohort_baseline"]["institutional_progression"]["standing"]
    assert resolver("ht.unknown") is None


def test_repository_resolver_reuses_owner_index_reads_within_one_call():
    class CountingRepository(RepositoryStore):
        def __init__(self, root):
            super().__init__(root)
            self.read_counts = Counter()

        def read_json(self, relative_path):
            self.read_counts[str(relative_path)] += 1
            return super().read_json(relative_path)

    repo = CountingRepository(ROOT)
    resolver = RepositoryPersonSheetResolver(repo)
    assert resolver("ht.core.001") is not None
    assert repo.read_counts["state/index/owners/ht.json"] <= 1
