from __future__ import annotations

import copy
import json
from pathlib import Path

from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _planner() -> RepositoryCommandPlanner:
    return RepositoryCommandPlanner(RepositoryStore(ROOT))


def _iron_formation() -> dict:
    record = json.loads((ROOT / "state/formation/force-iron-samurai.json").read_text())
    return copy.deepcopy(next(row for row in record["formations"] if row["id"] == "formation.iron.samurai.1"))


def _mechanics() -> dict:
    return json.loads((ROOT / "game/data/mechanics/formation-resolution.json").read_text())


def test_commander_quality_changes_control_timing_not_troop_capability_source() -> None:
    planner = _planner()
    mechanics = _mechanics()
    formation = _iron_formation()
    tactical = planner._formation_command_channels(formation, mechanics)
    weaker = copy.deepcopy(formation)
    weaker["command_slot"]["profile_ref"] = "command_profile.shinobi_support_standard"
    support = planner._formation_command_channels(weaker, mechanics)
    assert tactical["control_milli"] > support["control_milli"]
    assert tactical["initiative_milli"] > support["initiative_milli"]
    # Commander projection does not mutate the soldiers' persistent skill state.
    assert weaker["components"] == formation["components"]


def test_doctrine_familiarity_and_suitability_are_distinct_inputs() -> None:
    planner = _planner()
    mechanics = _mechanics()
    formation = _iron_formation()
    low = copy.deepcopy(formation)
    high = copy.deepcopy(formation)
    low["doctrine_familiarity"] = 20
    high["doctrine_familiarity"] = 90
    low_channels = planner._formation_doctrine_channels(
        low, action="hold", location_ref="place.iron.northern.pass", mechanics=mechanics
    )
    high_channels = planner._formation_doctrine_channels(
        high, action="hold", location_ref="place.iron.northern.pass", mechanics=mechanics
    )
    assert high_channels["familiarity_milli"] > low_channels["familiarity_milli"]
    assert high_channels["suitability_milli"] == low_channels["suitability_milli"]
    off_terrain = planner._formation_doctrine_channels(
        high, action="hold", location_ref="place.konoha", mechanics=mechanics
    )
    assert high_channels["suitability_milli"] > off_terrain["suitability_milli"]
    assert high_channels["familiarity_milli"] == off_terrain["familiarity_milli"]


def test_registered_battle_location_produces_non_neutral_terrain() -> None:
    planner = _planner()
    mechanics = _mechanics()
    terrain = planner._terrain_state_for_location(
        location_ref="place.iron.northern.pass", side_refs=("side:red", "side:blue"), mechanics=mechanics
    )
    assert terrain.terrain_ref.endswith(":border_pass")
    assert all(mod.cover_milli > 1000 for mod in terrain.side_modifiers)
    assert all(mod.mobility_milli < 1000 for mod in terrain.side_modifiers)
    default = planner._terrain_state_for_location(
        location_ref="zone:unregistered", side_refs=("side:red", "side:blue"), mechanics=mechanics
    )
    assert all(mod.cover_milli == 1000 for mod in default.side_modifiers)
    assert all(mod.mobility_milli == 1000 for mod in default.side_modifiers)


def test_embedded_elites_are_discovered_from_team_authority_not_caller_list(monkeypatch) -> None:
    planner = _planner()
    monkeypatch.setattr(
        "shinobi_runtime.commands.domains.combat.team_refs_for_assignment",
        lambda _repo, assignment_ref: ("team.alpha", "team.beta") if assignment_ref == "formation.target" else (),
    )
    teams = {
        "team.alpha": {"status": "active", "current_assignment_ref": "formation.target", "embedded_member_refs": ["naruto", "kakashi"]},
        "team.beta": {"status": "active", "current_assignment_ref": "formation.target", "embedded_member_refs": ["kakashi", "sakura"]},
    }
    monkeypatch.setattr(planner, "_exact_team", lambda ref: (f"state/team/{ref}.json", teams[ref]))
    actors, provenance = planner._embedded_exact_members_for_formation("formation.target")
    assert actors == ("kakashi", "naruto", "sakura")
    assert provenance["kakashi"] == ("team.alpha", "team.beta")
    # Exact people are identities, not a scalar bonus or an averaged cohort statistic.
    formation = _iron_formation()
    before = copy.deepcopy(formation["components"])
    planner._embedded_exact_members_for_formation("formation.target")
    assert formation["components"] == before


def test_embedded_elite_resolves_as_conserved_exact_body_inside_aggregate_battle(tmp_path: Path) -> None:
    """An elite body is discovered from team authority, never averaged or duplicated."""
    import shutil

    from shinobi_runtime.commands import CommandEnvelope
    from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator
    from shinobi_runtime.store.overlay import StagedOverlay
    from shinobi_runtime.tx.manifest import TransactionPlanner

    campaign = tmp_path / "campaign"
    shutil.copytree(ROOT, campaign, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    location = "place.konoha.training_ground.3"

    team_path = campaign / "state/team/guy.json"
    team = json.loads(team_path.read_text())
    team["current_assignment_ref"] = "formation.konoha.anbu.1"
    team["embedded_member_refs"] = ["canon_guy"]
    team_path.write_text(json.dumps(team, indent=2) + "\n")

    from shinobi_runtime.membership_routes import stage_team_change
    route_writes: dict[str, dict] = {}
    stage_team_change(
        RepositoryStore(campaign), route_writes, team_ref="team.konoha.guy",
        before_members=tuple(ref for ref in team.get("member_refs", []) if isinstance(ref, str)),
        after_members=tuple(ref for ref in team.get("member_refs", []) if isinstance(ref, str)),
        before_parent=team.get("parent_institution_ref"), after_parent=team.get("parent_institution_ref"),
        after_assignment="formation.konoha.anbu.1",
    )
    for relative, record in route_writes.items():
        path = campaign / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n")

    for relative, formation_ref in (
        ("state/formation/force-konoha-shinobi.json", "formation.konoha.anbu.1"),
        ("state/formation/force-iwa-shinobi.json", "formation.iwa.demolition.1"),
    ):
        path = campaign / relative
        registry = json.loads(path.read_text())
        formation = next(row for row in registry["formations"] if row["id"] == formation_ref)
        formation["location_ref"] = location
        path.write_text(json.dumps(registry, indent=2) + "\n")

    repo = RepositoryStore(campaign)
    meta = repo.read_json("state/meta.json")
    envelope = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="release-elite-overlay-battle",
        actor_id="faction_konoha",
        command_type="combat_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-14T03:00:00Z",
        mode="autonomous",
        payload={
            "combat_id": "combat.release.elite.overlay",
            "scale": "battle",
            "location_ref": location,
            "participants": [
                {
                    "participant_ref": "formation:formation.konoha.anbu.1",
                    "committed_count": 25,
                    "side_ref": "side:konoha",
                    "action": "attack",
                    "target_refs": ["formation:formation.iwa.demolition.1"],
                    "objective_ref": "objective:konoha.eliminate",
                    "lethal": False,
                    "command_authority_ref": "faction_konoha",
                    # Deliberately empty: authoritative team assignment must discover Guy.
                    "named_actor_refs": [],
                },
                {
                    "participant_ref": "formation:formation.iwa.demolition.1",
                    "committed_count": 25,
                    "side_ref": "side:iwa",
                    "action": "attack",
                    "target_refs": ["formation:formation.konoha.anbu.1"],
                    "objective_ref": None,
                    "lethal": False,
                    "command_authority_ref": "faction_iwa",
                    "named_actor_refs": [],
                },
            ],
            "objectives": [
                {
                    "objective_ref": "objective:konoha.eliminate",
                    "side_ref": "side:konoha",
                    "kind": "eliminate",
                    "target_refs": ["formation:formation.iwa.demolition.1"],
                    "zone_ref": None,
                    "deadline_tick": 8,
                }
            ],
        },
    )
    planner = RepositoryCommandPlanner(repo)
    plan = planner.plan(envelope)
    manifest = TransactionPlanner(repo).plan(
        envelope,
        transaction_id=plan.transaction_id,
        created_at=plan.created_at,
        writes=plan.writes,
    )
    overlay = StagedOverlay(repo, manifest)
    plan.validator(overlay, manifest)
    RegisteredSchemaValidator(repo).validate_overlay(overlay, manifest.paths)
    RegisteredTemplateValidator(repo).validate_overlay(overlay, manifest.paths)

    assert plan.result["represented_personnel"] == 50
    assert tuple(plan.result["resolved_elite_actor_refs"]) == ("canon_guy",)
    assert plan.result["pending_custody_refs"] == ()
    effects = {row["participant_ref"]: row for row in plan.result["participant_effects"]}
    assert effects["canon_guy"]["before_personnel"]["total"] == 1
    assert effects["canon_guy"]["after_personnel"]["total"] == 1

    operation = overlay.read_json("state/operation/combat.release.elite.overlay.json")
    rows = {row["participant_ref"]: row for row in operation["participants"]}
    konoha = rows["formation:formation.konoha.anbu.1"]
    assert konoha["committed_count"] == 25
    assert konoha["aggregate_resolved_count"] == 24
    assert konoha["named_actor_refs"] == ["canon_guy"]
    assert konoha["personnel"]["total"] == 24
    assert operation["outcome"]["pending_named_actor_refs"] == []
    assert operation["outcome"]["resolved_elite_actor_refs"] == ["canon_guy"]
    assert operation["outcome"]["elite_overlay_model"] == "exact_discrete_participants_not_averaged_into_formation_kernel"
    elite = operation["outcome"]["elite_overlay_results"][0]
    assert elite["actor_ref"] == "canon_guy"
    assert elite["parent_formation_participant_ref"] == "formation:formation.konoha.anbu.1"
    assert elite["personnel"]["total"] == 1
    assert elite["player_action_delegated"] is True
    assert elite["chakra_cost"] >= 0

    # Exact resource state belongs to Guy, not to the formation average.
    if elite["chakra_cost"]:
        assert "state/char/guy.json" in plan.writes
        before_chakra = repo.read_json("state/char/guy.json")["resources"]["chakra"]["current"]
        after_chakra = overlay.read_json("state/char/guy.json")["resources"]["chakra"]["current"]
        assert after_chakra == before_chakra - elite["chakra_cost"]


def test_formation_kernel_consumes_component_specialization_without_long_range_leakage() -> None:
    planner = _planner()
    formation = _iron_formation()
    force = planner.repository.read_json("state/force/iron-samurai.json")
    close = planner._formation_aggregate_capability(
        formation=formation, force=force, action="attack", range_band=0
    )[0]
    no_sword = copy.deepcopy(formation)
    assault = next(row for row in no_sword["components"] if row.get("role") == "assault")
    assault["capability_state"]["methods"]["sword"] = 0
    degraded_close = planner._formation_aggregate_capability(
        formation=no_sword, force=force, action="attack", range_band=0
    )[0]
    assert close.offense > degraded_close.offense

    long = planner._formation_aggregate_capability(
        formation=formation, force=force, action="attack", range_band=3
    )[0]
    degraded_long = planner._formation_aggregate_capability(
        formation=no_sword, force=force, action="attack", range_band=3
    )[0]
    assert long == degraded_long


def test_split_representation_preserves_people_and_capability_with_integer_rounding_only() -> None:
    planner = _planner()
    original = _iron_formation()
    parent = copy.deepcopy(original)
    child = copy.deepcopy(original)
    planner._resize_formation_strength(parent, 15)
    planner._resize_formation_strength(child, original["personnel_total"] - 15)
    assert parent["personnel_total"] + child["personnel_total"] == original["personnel_total"]
    assert sum(row["count"] for row in parent["components"]) + parent["command_personnel"]["count"] == 15
    assert sum(row["count"] for row in child["components"]) + child["command_personnel"]["count"] == original["personnel_total"] - 15

    # Splitting never rerolls capability. Every surviving component keeps its
    # established capability state; only integer headcount partitions change.
    for piece in (parent, child):
        for original_row, row in zip(original["components"], piece["components"]):
            assert row["capability_state"] == original_row["capability_state"]
