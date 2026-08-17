from __future__ import annotations

from pathlib import Path

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.campaign_environment import CampaignCommandPlanner
from shinobi_runtime.combat.models import SideTerrain, TerrainState
from shinobi_runtime.environment import apply_environment_to_terrain, environment_snapshot, route_travel_factor_milli
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = RepositoryStore(ROOT)
META = REPOSITORY.read_json("state/meta.json")
PLAYER = REPOSITORY.read_json("state/player.json")
CAMPAIGN_ID = META["campaign_id"]
ACTOR = META["player_id"]
REVISION = META["revision"]
NOW = META["time"]
LOCATION = PLAYER["current_location_id"]


def _command(kind: str, payload: dict, suffix: str) -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id=CAMPAIGN_ID,
        request_id=f"environment-{suffix}",
        actor_id=ACTOR,
        command_type=kind,
        expected_revision=REVISION,
        submitted_at="2026-08-16T00:00:00Z",
        payload=payload,
        mode="gameplay",
    )


def _validated_plan(planner, envelope: CommandEnvelope):
    plan = planner.plan(envelope)
    manifest = TransactionPlanner(REPOSITORY).plan(
        envelope,
        transaction_id=plan.transaction_id,
        created_at=plan.created_at,
        writes=plan.writes,
    )
    overlay = StagedOverlay(REPOSITORY, manifest)
    plan.validator(overlay, manifest)
    RegisteredSchemaValidator(REPOSITORY).validate_overlay(overlay, manifest.paths)
    RegisteredTemplateValidator(REPOSITORY).validate_overlay(overlay, manifest.paths)
    return plan, overlay


def test_environment_is_deterministic_current_place_authority() -> None:
    first = environment_snapshot(REPOSITORY, world_time=NOW, location_ref=LOCATION)
    second = environment_snapshot(REPOSITORY, world_time=NOW, location_ref=LOCATION)
    assert first == second
    assert first["source"] == "derived_environment_authority"
    assert first["location_ref"] == LOCATION
    assert first["country_ref"] == "land_fire"
    assert first["mechanical_effects"]["travel_time_milli"] >= 1000
    assert 0 <= first["mechanical_effects"]["hazard_milli"] <= 300


def test_environment_evolves_by_campaign_time_without_state_write() -> None:
    before = REPOSITORY.digest("state/meta.json")
    now = environment_snapshot(REPOSITORY, world_time=NOW, location_ref=LOCATION)
    later_time = str(CampaignTime.parse(NOW).add_seconds(12 * 3600))
    later = environment_snapshot(REPOSITORY, world_time=later_time, location_ref=LOCATION)
    assert later["weather_block_ref"] != now["weather_block_ref"]
    assert later["as_of"] != now["as_of"]
    assert REPOSITORY.digest("state/meta.json") == before


def test_environment_modifies_only_registered_terrain_channels() -> None:
    base = TerrainState(
        terrain_ref="terrain:test",
        side_modifiers=(
            SideTerrain(side_ref="side.a", cover_milli=1120, mobility_milli=980, visibility_milli=940, hazard_milli=30),
        ),
    )
    env = environment_snapshot(REPOSITORY, world_time=NOW, location_ref=LOCATION)
    adjusted = apply_environment_to_terrain(base, env)
    row = adjusted.side_modifiers[0]
    assert row.cover_milli == 1120
    assert row.mobility_milli <= 980
    assert row.visibility_milli <= 940
    assert row.hazard_milli >= 30
    assert env["weather_block_ref"] in adjusted.terrain_ref


def test_final_planner_terrain_combines_authored_place_and_environment() -> None:
    planner = CampaignCommandPlanner(REPOSITORY)
    terrain = planner._terrain_state_for_location(
        location_ref=LOCATION,
        side_refs=("side.a", "side.b"),
        mechanics={
            "terrain_profiles_by_place_kind": {
                "household_compound": {"cover_milli": 1075, "mobility_milli": 1000, "visibility_milli": 1000, "hazard_milli": 0},
                "default": {"cover_milli": 1000, "mobility_milli": 1000, "visibility_milli": 1000, "hazard_milli": 0},
            }
        },
    )
    assert len(terrain.side_modifiers) == 2
    assert all(row.cover_milli == 1075 for row in terrain.side_modifiers)
    assert all(row.mobility_milli <= 1000 for row in terrain.side_modifiers)
    assert all(row.visibility_milli <= 1000 for row in terrain.side_modifiers)
    assert "env." in terrain.terrain_ref


def test_final_travel_plan_consumes_environment_without_changing_command_contract() -> None:
    payload = {
        "route_id": "route_konoha_wave",
        "destination_id": "place.waves.town",
        "traveler_refs": [ACTOR],
        "party_context_ref": None,
        "mission_ref": None,
    }
    envelope = _command("travel_resolution", payload, "travel")
    final, overlay = _validated_plan(CampaignCommandPlanner(REPOSITORY), envelope)
    assert final.result["environment_travel_factor_milli"] >= 1000
    assert final.result["travel_seconds"] > 0
    assert final.result["destination_id"] == "place.waves.town"
    assert overlay.read_json("state/player.json")["current_location_id"] == "place.waves.town"


def test_route_factor_is_bounded_and_country_sensitive() -> None:
    factor = route_travel_factor_milli(
        REPOSITORY,
        world_time=NOW,
        origin_ref="place.konoha",
        destination_ref="place.waves.town",
        base_hours=120,
    )
    assert 1000 <= factor <= 1400
    fire = environment_snapshot(REPOSITORY, world_time=NOW, location_ref="place.konoha")
    water = environment_snapshot(REPOSITORY, world_time=NOW, location_ref="place.kiri")
    assert fire["climate_ref"] != water["climate_ref"]
