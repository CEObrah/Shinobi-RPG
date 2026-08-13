"""Regression coverage for mixed aggregate/exact Academy graduation."""
from __future__ import annotations

import copy
from pathlib import Path

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]
POPULATION_PATH = "state/population/registry.json"
GRADUATION_AT = CampaignTime.parse("SE-0061-03-01T07:00:00")
EXPECTED_KONOHA_EXACT = {
    "canon_choji",
    "canon_hinata",
    "canon_ino",
    "canon_kiba",
    "canon_naruto",
    "canon_sakura",
    "canon_sasuke",
    "canon_shikamaru",
    "canon_shino",
}
UNDERAGE_KONOHA = {"canon_hanabi", "canon_konohamaru"}


def _command(repo: RepositoryStore, suffix: str) -> CommandEnvelope:
    meta = repo.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id="shinobi-wei-main",
        request_id=f"academy-graduation-{suffix}",
        actor_id="pc_wei_tang",
        command_type="advance_time",
        expected_revision=meta["revision"],
        submitted_at="2026-08-11T00:00:00Z",
        payload={"target_time": str(GRADUATION_AT)},
        mode="gameplay",
    )


def _konoha_academy(repo: RepositoryStore) -> dict:
    registry = repo.read_json("state/world/institutions-konoha.json")
    return copy.deepcopy(
        next(
            row
            for row in registry["payload"]["institutions"]
            if row["id"] == "institution.konoha.academy"
        )
    )


def _run_review(suffix: str):
    repo = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repo)
    writes: dict[str, dict] = {}
    world_events = copy.deepcopy(planner._world_events())
    result = planner._apply_institution_autonomy_review(
        institution=_konoha_academy(repo),
        at=GRADUATION_AT,
        compacted=1,
        command=_command(repo, suffix),
        policy_book=planner._autonomy_policy_book(),
        world_events=world_events,
        record_writes=writes,
    )
    return repo, planner, writes, world_events, result


def test_academy_graduation_consumes_shared_slots_without_creating_people() -> None:
    repo, planner, writes, world_events, result = _run_review("conservation")
    before = repo.read_json(POPULATION_PATH)
    after = writes[POPULATION_PATH]
    pipeline = result["population_pipeline"]

    assert pipeline["graduates"] == 12
    assert set(pipeline["exact_graduate_refs"]) == EXPECTED_KONOHA_EXACT
    assert pipeline["anonymous_graduates"] == 3
    assert len(pipeline["exact_graduate_refs"]) + pipeline["anonymous_graduates"] == pipeline["graduates"]

    before_total = sum(pool["count"] for pool in before["pools"].values())
    after_total = sum(pool["count"] for pool in after["pools"].values())
    assert after_total == before_total

    academy_before = before["pools"]["pool.konoha.academy"]
    service_before = before["pools"]["pool.konoha.shinobi_service"]
    academy_after = after["pools"]["pool.konoha.academy"]
    service_after = after["pools"]["pool.konoha.shinobi_service"]

    assert academy_after["count"] == academy_before["count"] + 18 - 12
    assert service_after["count"] == service_before["count"] + 12
    assert set(academy_after["representation"]["rostered_person_refs"]) == UNDERAGE_KONOHA
    assert EXPECTED_KONOHA_EXACT.issubset(service_after["representation"]["rostered_person_refs"])
    assert academy_after["representation"]["anonymous_count"] + academy_after["representation"]["rostered_count"] == academy_after["count"]
    assert service_after["representation"]["anonymous_count"] + service_after["representation"]["rostered_count"] == service_after["count"]
    assert service_after["representation"]["anonymous_count"] == service_before["representation"]["anonymous_count"] + 3

    graduation = next(
        row
        for row in reversed(after["transfers"])
        if row["id"] == pipeline["graduation_transfer_id"]
    )
    assert graduation["source_removed"] == graduation["destination_added"] == 12
    assert set(graduation["materialized_person_ids"]) == EXPECTED_KONOHA_EXACT
    assert graduation["method"] == "neutral_proportional_with_rostered_identity_sync"

    force_path, _digest, force_before = planner._resolve_covered_owner_view(
        "force.konoha.shinobi", cache=__import__("shinobi_runtime.commands.core", fromlist=["_OwnerResolutionCache"])._OwnerResolutionCache()
    )
    assert writes[force_path]["total"] == force_before["total"] + 12
    assert sum(writes[force_path]["availability"].values()) == writes[force_path]["total"]
    assert any(event["kind"] == "academy_exact_graduation_recorded" for event in world_events["events"])
    assert not any(path.startswith("state/team/") for path in writes)


def test_exact_graduates_receive_normal_genin_career_fields_and_underage_students_do_not() -> None:
    repo, _planner, writes, _world_events, result = _run_review("career")
    index = repo.read_json("state/index/owners/canon.json")["owners"]
    for person_ref in result["population_pipeline"]["exact_graduate_refs"]:
        subject = writes[index[person_ref]]
        assert subject["official_rank_or_status"] == "Genin"
        assert subject["career_state"]["rank"] == "Genin"
        assert subject["career_state"]["current_rank_or_status"] == "Genin"
        assert subject["career_state"]["promotion_eligible"] is False
        assert subject["life_course_state"]["rank_history"][-1]["rank"] == "Genin"
        assert "graduate: Genin" in subject["life_course_state"]["status_history"][-1]

    for person_ref in UNDERAGE_KONOHA:
        path = index[person_ref]
        assert path not in writes
        assert repo.read_json(path)["official_rank_or_status"] == "academy"


def test_exact_graduate_selection_is_deterministic() -> None:
    first = _run_review("determinism-a")[-1]["population_pipeline"]["exact_graduate_refs"]
    second = _run_review("determinism-b")[-1]["population_pipeline"]["exact_graduate_refs"]
    assert first == second
    assert set(first) == EXPECTED_KONOHA_EXACT
