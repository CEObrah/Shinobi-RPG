import copy
import json
from pathlib import Path

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.martial_world.retinue_migration import plan_permanent_team_cohort_v2_migration
from shinobi_runtime.martial_world.retinues import permanent_team_member_eligible
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENTS = "state/martial-world/deployments.json"
RETINUE = "retinue.wei.permanent_travel_team"
OLD_BAD_TEAM = {
    "mw.person.house_tang.5000",
    "mw.person.house_tang.1037",
    "mw.person.house_tang.1001",
}


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _roster_map():
    roster = load("state/martial-world/people/house_tang.json")
    return {row["person_id"]: row for row in roster["people"]}


def _bad_assignment_read():
    deployments = copy.deepcopy(load(DEPLOYMENTS))
    row = deployments["deployments"][RETINUE]
    row["member_refs"] = [
        "mw.person.house_tang.5000",
        "mw.person.house_tang.1037",
        "mw.person.house_tang.1001",
    ]
    row["member_roles"] = {
        "mw.person.house_tang.5000": "field_medic",
        "mw.person.house_tang.1037": "protective_guard",
        "mw.person.house_tang.1001": "scout",
    }
    row["status"] = "active"

    def read(rel: str):
        if rel == DEPLOYMENTS:
            return copy.deepcopy(deployments)
        return load(rel)

    return read, deployments


def _assert_corrected_team(row):
    assert row["leader_ref"] == "pc_wei_tang"
    assert row["chooser_refs"] == ["char.zhu", "char.ling"]
    assert row["status"] == "active"
    assert len(row["member_refs"]) == 3
    assert len(set(row["member_refs"])) == 3
    assert not OLD_BAD_TEAM & set(row["member_refs"])
    assert set(row["member_roles"].values()) == {"field_medic", "protective_guard", "scout"}

    people = _roster_map()
    leader = people["pc_wei_tang"]
    for ref in row["member_refs"]:
        assert ref in people
        assert permanent_team_member_eligible(leader, people[ref], year=61)
        age = 61 - people[ref]["birth_year"]
        assert 16 <= age <= 35


def test_bad_multi_generation_team_is_recomputed_from_same_conserved_roster():
    read, before = _bad_assignment_read()
    current = before["deployments"][RETINUE]
    result = plan_permanent_team_cohort_v2_migration(read)
    assert result["reason"] == "selector_policy_corrected"
    assert set(result["writes"]) == {DEPLOYMENTS}
    after = result["writes"][DEPLOYMENTS]["deployments"][RETINUE]

    assert after["requested_at"] == current["requested_at"]
    assert after["assigned_at"] == current["assigned_at"]
    _assert_corrected_team(after)


def test_permanent_team_migration_is_idempotent_after_corrected_after_image():
    read, _before = _bad_assignment_read()
    first = plan_permanent_team_cohort_v2_migration(read)
    assert first["writes"]

    def read_after(rel: str):
        if rel in first["writes"]:
            return copy.deepcopy(first["writes"][rel])
        return read(rel)

    second = plan_permanent_team_cohort_v2_migration(read_after)
    assert second["writes"] == {}
    assert second["reason"] == "already_current"
    assert second["member_refs"] == first["member_refs"]
    assert second["member_roles"] == first["member_roles"]


def test_current_save_is_either_repairable_now_or_already_repaired():
    result = plan_permanent_team_cohort_v2_migration(load)
    if result["writes"]:
        row = result["writes"][DEPLOYMENTS]["deployments"][RETINUE]
    else:
        assert result["reason"] == "already_current"
        row = load(DEPLOYMENTS)["deployments"][RETINUE]
    _assert_corrected_team(row)


def test_retinue_migration_after_image_passes_registered_deployment_contracts():
    repository = RepositoryStore(ROOT)
    result = plan_permanent_team_cohort_v2_migration(repository.read_json)
    if not result["writes"]:
        assert result["reason"] == "already_current"
        return
    meta = repository.read_json("state/meta.json")
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="test.permanent-team-cohort-v2.maintenance",
        actor_id=meta["player_id"],
        command_type="maintenance_permanent_travel_team_cohort_v2",
        expected_revision=meta["revision"],
        submitted_at="2026-08-22T00:00:00Z",
        mode="maintenance",
        payload={"migration": "permanent_travel_team_cohort_v2"},
    )
    writes = {
        path: (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        for path, value in result["writes"].items()
    }
    manifest = TransactionPlanner(repository).plan(
        command,
        transaction_id="tx.maintenance.test.permanent-team-cohort-v2",
        created_at="2026-08-22T00:00:00Z",
        writes=writes,
    )
    overlay = StagedOverlay(repository, manifest)
    paths = tuple(sorted(writes))
    RegisteredSchemaValidator(repository).validate_overlay(overlay, paths)
    RegisteredTemplateValidator(repository).validate_overlay(overlay, paths)
