from __future__ import annotations

import copy
import json
from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.commands.promotion_exam_delegation_materialization import exactify_home_village_genin
from shinobi_runtime.commands.promotion_exam_hosted_policy import minimum_route_days, required_arrival_lead_days
from shinobi_runtime.commands.promotion_exam_pacing import _effective_phase_offsets
from shinobi_runtime.commands.promotion_exam_scheduler import promotion_exam_profiles
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.store.template_validation import RegisteredTemplateValidator

ROOT = Path(__file__).resolve().parents[2]
VILLAGES = ("konoha", "suna", "iwa", "kumo", "kiri", "ame", "kusa", "taki", "oto", "yuga")
FOREIGN = tuple(v for v in VILLAGES if v != "konoha")
FORCES = {
    "konoha": "force.konoha.shinobi", "suna": "force.suna.shinobi", "iwa": "force.iwa.shinobi",
    "kumo": "force.kumo.shinobi", "kiri": "force.kiri.shinobi", "ame": "force.ame.shinobi",
    "kusa": "force.kusa.shinobi", "taki": "force.taki.shinobi", "oto": "force.oto.network", "yuga": "force.yuga.service",
}
HOMES = {
    "suna": "place.suna", "iwa": "place.iwa", "kumo": "place.kumo", "kiri": "place.kiri",
    "ame": "place.ame", "kusa": "place.kusa", "taki": "place.taki", "oto": "place.oto.network", "yuga": "place.yuga",
}
CYCLE = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"


def test_current_exam_snapshot_preserves_registration_and_stage_integrity() -> None:
    repo = RepositoryStore(ROOT)
    meta = repo.read_json("state/meta.json")
    assert meta["revision"] >= meta["last_checkpoint_revision"] >= 1
    CampaignTime.parse(meta["time"])
    career = repo.read_json("state/reg/shinobi-career-pipeline.json")
    rows = [r for r in career["history"] if isinstance(r, dict) and r.get("cycle_id") == CYCLE]
    phases = [r for r in rows if r.get("kind") == "promotion_exam_cycle_phase"]
    assert phases
    assert phases[-1]["phase"] in {"registration", "qualification", "field_evaluation", "finals", "promotion_review", "closed"}
    regs = [r for r in rows if r.get("kind") == "promotion_exam_registration"]
    assert any(r.get("team_ref") == "team.konoha.fujin" for r in regs)
    foreign = {r["team_ref"]: r for r in regs if str(r.get("team_ref", "")).startswith("promotion_exam_delegation.")}
    assert set(foreign) == {f"promotion_exam_delegation.{v}.common" for v in FOREIGN}
    assert all(len(row["candidate_refs"]) == 3 for row in foreign.values())
    registered = {ref for row in regs for ref in row["candidate_refs"]}
    seen = set()
    for row in rows:
        if row.get("kind") != "promotion_exam_evaluation":
            continue
        key = (row.get("phase"), row.get("candidate_ref"))
        assert key not in seen
        seen.add(key)
        assert row.get("candidate_ref") in registered


def test_every_participating_village_has_population_academy_service_and_career_authority() -> None:
    repo = RepositoryStore(ROOT)
    population = repo.read_json("state/population/registry.json")
    career = repo.read_json("state/reg/shinobi-career-pipeline.json")
    force_index = repo.read_json("state/index/owners/force.json")["owners"]
    policy = repo.read_json("game/rules/autonomy/policies.json")["institution_assignments"]
    assignments_by_service = {
        row.get("service_pool_id"): row
        for row in policy.values()
        if isinstance(row, dict) and row.get("kind") == "academy_pipeline"
    }
    for village in VILLAGES:
        for category in ("civilian_general", "support_service", "youth_candidate", "academy", "shinobi_service"):
            assert f"pool.{village}.{category}" in population["pools"]
        service = population["pools"][f"pool.{village}.shinobi_service"]
        force = repo.read_json(force_index[FORCES[village]])
        assert service["count"] == force["total"]
        assert service["linked_force_ref"] == FORCES[village]
        assert force["population_pool_id"] == f"pool.{village}.shinobi_service"
        assert service["representation"]["anonymous_count"] + service["representation"]["rostered_count"] == service["count"]
        ranks = career["villages"][village]["rank_counts"]
        assert sum(ranks.values()) == service["count"]
        assignment = assignments_by_service[f"pool.{village}.shinobi_service"]
        assert assignment["source_pool_id"] == f"pool.{village}.youth_candidate"
        assert assignment["academy_pool_id"] == f"pool.{village}.academy"
        assert assignment["candidate_source_pool_id"] == f"pool.{village}.civilian_general"


def test_common_exam_invites_all_villages_and_calendar_fits_every_route() -> None:
    repo = RepositoryStore(ROOT)
    profile = promotion_exam_profiles(repo)[0]
    hosted = profile["hosted_exam"]
    assert tuple(hosted["participating_villages"]) == VILLAGES
    delegations = {row["service_village"]: row for row in hosted["foreign_delegations"]}
    assert set(delegations) == set(FOREIGN)
    required = required_arrival_lead_days(repo, profile)
    schedule = dict(_effective_phase_offsets(repo, profile))
    lead = schedule["qualification"] - schedule["registration"]
    assert lead >= required
    for village in FOREIGN:
        row = delegations[village]
        assert row["force_ref"] == FORCES[village]
        route = minimum_route_days(repo, HOMES[village], hosted["host_arrival_place_ref"])
        assert route is not None
        assert route + hosted["arrival_buffer_days"] <= lead


def test_exactification_conserves_population_and_rank_headcount_and_uses_registered_character_shape() -> None:
    repo = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repo)
    profile = promotion_exam_profiles(repo)[0]
    delegation = next(row for row in profile["hosted_exam"]["foreign_delegations"] if row["service_village"] == "ame")
    population_before = repo.read_json("state/population/registry.json")
    career_before = repo.read_json("state/reg/shinobi-career-pipeline.json")
    pool_before = copy.deepcopy(population_before["pools"][delegation["service_pool_ref"]])
    ranks_before = copy.deepcopy(career_before["villages"]["ame"]["rank_counts"])
    writes: dict[str, dict] = {}
    created = exactify_home_village_genin(
        planner, delegation=delegation, cycle_id=CYCLE + ".test", at=CampaignTime.parse("SE-0061-07-22T07:29:58"), record_writes=writes, count=1, calibration=profile["hosted_exam"]["generated_candidate_calibration"]
    )
    assert len(created) == 1
    ref, person = created[0]
    pool_after = writes["state/population/registry.json"]["pools"][delegation["service_pool_ref"]]
    assert pool_after["count"] == pool_before["count"]
    assert pool_after["representation"]["anonymous_count"] == pool_before["representation"]["anonymous_count"] - 1
    assert pool_after["representation"]["rostered_count"] == pool_before["representation"]["rostered_count"] + 1
    assert writes["state/reg/shinobi-career-pipeline.json"]["villages"]["ame"]["rank_counts"] == ranks_before
    assert person["official_rank_or_status"] == "genin"
    assert person["career_state"]["promotion_eligible"] is True
    assert person["current_location_id"] == delegation["home_place_ref"]
    assert "PKG_AME_ACADEMY_CORE" in person["repertoire"]["packages"]
    template = repo.read_json("runtime/contracts/templates/shinobi_character.template.json")
    RegisteredTemplateValidator._validate_document(person, template, label=ref)


def test_current_exam_history_has_no_destructive_reset_or_discard_event() -> None:
    repo = RepositoryStore(ROOT)
    events = list(repo.read_json("state/reg/world-events.json")["events"])
    for path in (ROOT / "state/history/events").glob("*.json"):
        events.extend(json.loads(path.read_text())["events"])
    for event in events:
        blob = json.dumps(event, sort_keys=True).lower()
        if CYCLE.lower() not in blob:
            continue
        kind = str(event.get("kind", "")).lower()
        reducer = str(event.get("execution", {}).get("reducer_ref", "")).lower()
        assert "discard" not in kind and "reset" not in kind and "regenerate" not in kind
        assert "discard" not in reducer and "reset" not in reducer and "regenerate" not in reducer

