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


def test_current_bad_team_is_recomputed_from_same_conserved_roster_under_cohort_policy():
    before = load(DEPLOYMENTS)
    current = before["deployments"][RETINUE]
    assert current["status"] == "active"
    assert set(current["member_refs"]) == OLD_BAD_TEAM

    result = plan_permanent_team_cohort_v2_migration(load)
    assert result["reason"] == "selector_policy_corrected"
    assert set(result["writes"]) == {DEPLOYMENTS}
    after = result["writes"][DEPLOYMENTS]["deployments"][RETINUE]

    assert after["leader_ref"] == current["leader_ref"] == "pc_wei_tang"
    assert after["chooser_refs"] == current["chooser_refs"] == ["char.zhu", "char.ling"]
    assert after["requested_at"] == current["requested_at"]
    assert after["assigned_at"] == current["assigned_at"]
    assert after["status"] == "active"
    assert len(after["member_refs"]) == 3
    assert len(set(after["member_refs"])) == 3
    assert not OLD_BAD_TEAM & set(after["member_refs"])
    assert set(after["member_roles"].values()) == {"field_medic", "protective_guard", "scout"}

    people = _roster_map()
    leader = people["pc_wei_tang"]
    for ref in after["member_refs"]:
        assert ref in people
        assert permanent_team_member_eligible(leader, people[ref], year=61)
        age = 61 - people[ref]["birth_year"]
        assert 16 <= age <= 35


def test_permanent_team_migration_is_idempotent_after_corrected_after_image():
    first = plan_permanent_team_cohort_v2_migration(load)
    assert first["writes"]

    def read_after(rel: str):
        if rel in first["writes"]:
            return copy.deepcopy(first["writes"][rel])
        return load(rel)

    second = plan_permanent_team_cohort_v2_migration(read_after)
    assert second["writes"] == {}
    assert second["reason"] == "already_current"
    assert second["member_refs"] == first["member_refs"]
    assert second["member_roles"] == first["member_roles"]


def test_retinue_migration_after_image_passes_registered_deployment_contracts():
    repository = RepositoryStore(ROOT)
    result = plan_permanent_team_cohort_v2_migration(repository.read_json)
    assert result["writes"]
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
