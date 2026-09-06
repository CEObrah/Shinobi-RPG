#!/usr/bin/env python3
"""Run focused Jianghu regressions for changed repository owners.

The fast development gate intentionally excludes long-horizon soak tests. Those
remain in the deliberate release/simulation tooling. Every invocation still
runs the small core contract/invariant slice, then adds subsystem tests based on
changed paths.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CORE = {
    "tests/current/test_contract_discovery.py",
    "tests/current/test_preview_validation.py",
    "tests/current/test_runtime_invariants.py",
    "tests/current/test_main_branch_bootstrap.py",
    "tests/current/test_world_invariants.py",
    "tests/current/test_release_surface.py",
    "tests/current/test_combat_defense_timing_integrity.py",
}
COMBAT = {
    "tests/current/test_autonomous_lethality.py",
    "tests/current/test_combat_command_wrapper.py",
    "tests/current/test_combat_geometry.py",
    "tests/current/test_health_targeting.py",
    "tests/current/test_mounted_combat.py",
    "tests/current/test_combat_friendly_line_safety.py",
    "tests/current/test_play_failure_matrix.py",
    "tests/current/test_play_regression_hardening.py",
    "tests/current/test_exact_combat_withdrawal.py",
    "tests/current/test_combat_contact_pursuit_repair.py",
    "tests/current/test_combat_simulation_hardening.py",
    "tests/current/test_combat_frontage_targeting.py",
    "tests/current/test_combat_frontage_live_semantics.py",
    "tests/current/test_combat_pressure_integrity.py",
    "tests/current/test_combat_readiness_integrity.py",
    "tests/current/test_combat_liveness_integrity.py",
    "tests/current/test_combat_reaction_window_causality.py",
    "tests/current/test_combat_reaction_timing_integrity.py",
    "tests/current/test_combat_defense_timing_integrity.py",
    "tests/current/test_player_retinue_doctrine.py",
    "tests/current/test_combat_rally_and_approach_budget.py",
    "tests/current/test_live_active_combat_preview_contract.py",
    "tests/current/test_live_combat_ally_support.py",
}
COMMAND_INTEGRATION = {
    "tests/current/test_combat_command_wrapper.py",
    "tests/current/test_qi_flow_invariants.py",
}
TRAINING = {
    "tests/current/test_training_epochs.py",
    "tests/current/test_rest_practice.py",
    "tests/current/test_field_development.py",
}
WORLD = {
    "tests/current/test_living_world_closure.py",
    "tests/current/test_scheduler_economy.py",
    "tests/current/test_social_civic_estate.py",
    "tests/current/test_membership_duties.py",
    "tests/current/test_family_simulation.py",
}
# Static route/geography edits need the systems that actually consume route
# topology, movement, escort travel, and route-facing world state. Do not drag
# in unrelated player command tests, stale historical fixture replays, or
# far-future whole-world attendance simulations whose exact result depends on
# the currently committed campaign. The global world semantic invariant job
# remains an independent CI gate.
ROUTE_WORLD = {
    "tests/current/test_public_escort_muster.py",
    "tests/current/test_route_intelligence.py",
    "tests/current/test_outlaw_geography.py",
    "tests/current/test_travel_team.py",
    "tests/current/test_scheduler_economy.py",
    "tests/current/test_semantic_wait_and_time_flow.py",
}
TOURNAMENT = {
    "tests/current/test_player_tournament_economy.py",
}
RETINUE = {
    "tests/current/test_retinue_persistence.py",
    "tests/current/test_retinue_selection.py",
    "tests/current/test_membership_duties.py",
    "tests/current/test_play_failure_matrix.py",
    "tests/current/test_play_regression_hardening.py",
}
INSTITUTIONAL = {
    "tests/current/test_institutional_gameplay_layer.py",
}
SCENE = {
    "tests/current/test_scene_session_contract.py",
    "tests/current/test_public_escort_muster.py",
}
RELEASE = {
    "tests/current/test_release_semantics.py",
    "tests/current/test_jianghu_mandate_regressions.py",
}
SCENE_FLOW = {
    "tests/current/test_scene_session_contract.py",
    "tests/current/test_physical_presence_authority.py",
}
TIME_FLOW = {
    "tests/current/test_semantic_wait_and_time_flow.py",
}
DEPLOYMENT_TESTS = {
    "tests/current/test_deployment_write_safety.py",
    "tests/current/test_release_surface.py",
}
DIRECTOR_CONTEXT_TESTS = {"tests/current/test_open_world_gm_architecture.py"}

ROUTE_GRAPH_PATHS = {
    "runtime/shinobi_runtime/martial_world/travel.py",
    "runtime/shinobi_runtime/martial_world/geography.py",
    "runtime/shinobi_runtime/martial_world/frontier_support.py",
}
STATIC_GEOGRAPHY_PATHS = {
    "game/data/martial-world/geography.json",
    "game/data/martial-world/geography-extensions.json",
}


def normalize(raw: str) -> str:
    path = Path(raw)
    try:
        path = path.resolve().relative_to(ROOT.resolve())
    except (OSError, ValueError):
        pass
    return path.as_posix().lstrip("./")


def select(paths: list[str]) -> list[str]:
    selected = set(CORE)
    for raw in paths:
        path = normalize(raw)
        low = path.lower()

        if path.startswith("tests/current/") and path.endswith(".py"):
            selected.add(path)

        if path in {"runtime/shinobi_runtime/bootstrap.py", "railway.toml"}:
            selected.update(DEPLOYMENT_TESTS)
        if path == "runtime/shinobi_runtime/api/gm_scene_context.py":
            selected.update(DIRECTOR_CONTEXT_TESTS)

        if path.startswith("runtime/shinobi_runtime/api/") or path.startswith("runtime/shinobi_runtime/commands/") or path.startswith("runtime/shinobi_runtime/tx/") or path.startswith("runtime/shinobi_runtime/store/"):
            selected.update(RELEASE)

        if path == "runtime/shinobi_runtime/commands/jianghu_extended.py":
            selected.update(COMMAND_INTEGRATION)

        if (
            path.startswith("runtime/shinobi_runtime/combat/")
            or path.startswith("runtime/shinobi_runtime/api/combat_")
            or path.startswith("runtime/shinobi_runtime/commands/combat_")
            or any(token in low for token in ("/exact_combat.py", "/combat.py", "/combat_simulation.py", "/health.py", "/targeting.py", "/mounts.py", "/medicine.py", "/poison.py"))
            or path == "game/data/martial-world/medicine.json"
        ):
            selected.update(COMBAT)

        if path == "game/data/martial-world/equipment-loadouts.json":
            selected.update(RETINUE)

        if any(token in low for token in ("training", "rest_practice", "field_development", "/qi.py", "life_course")):
            selected.update(TRAINING)

        if "tournament" in low:
            selected.update(TOURNAMENT)

        if any(token in low for token in ("retinue", "membership", "duties")):
            selected.update(RETINUE)

        if any(token in low for token in ("institutional", "allied_support", "faction_relations", "warfare", "custody")):
            selected.update(INSTITUTIONAL)

        if (
            "physical_presence" in low
            or "scene_sessions" in low
            or "jianghu_scene.py" in low
            or path == "runtime/shinobi_runtime/commands/jianghu_time.py"
            or path == "runtime/shinobi_runtime/api/operations.py"
            or "jianghu-scene-" in low
            or "interaction-attempt-ledger" in low
        ):
            selected.update(SCENE)

        if (
            path.startswith("runtime/shinobi_runtime/martial_world/")
            and any(token in low for token in (
                "scheduler", "time_", "econom", "production", "faction", "family", "social", "civic",
                "autonom", "strategic", "event", "government", "outlaw", "recruit", "manpower",
                "infrastructure", "institution", "relationship", "property", "services", "weather",
                "world_", "route_", "local_travel", "travel.py", "upkeep", "compensation", "commitment",
            ))
        ):
            if path in ROUTE_GRAPH_PATHS:
                selected.update(ROUTE_WORLD)
            else:
                selected.update(WORLD)

        if path.startswith("runtime/shinobi_runtime/sim/"):
            selected.update(WORLD)
        if (
            path in {
                "runtime/shinobi_runtime/martial_world/physical_presence.py",
                "runtime/shinobi_runtime/martial_world/scene_sessions.py",
                "runtime/shinobi_runtime/commands/jianghu_scene.py",
                "runtime/shinobi_runtime/api/operations.py",
            }
            or "scene-session" in low
            or "scene-history" in low
            or "interaction-attempt-ledger" in low
        ):
            selected.update(SCENE_FLOW)
            selected.add("tests/current/test_combat_command_wrapper.py")

        if path in {
            "runtime/shinobi_runtime/martial_world/warfare.py",
            "runtime/shinobi_runtime/martial_world/project_frontier.py",
            "runtime/shinobi_runtime/martial_world/tournament_frontier.py",
            "runtime/shinobi_runtime/martial_world/allied_support.py",
            "runtime/shinobi_runtime/martial_world/institutional_evolution_frontier.py",
        }:
            selected.update(SCENE_FLOW)

        if path in {
            "runtime/shinobi_runtime/commands/jianghu_time.py",
            "runtime/shinobi_runtime/commands/jianghu.py",
            "runtime/shinobi_runtime/martial_world/frontier_bridge.py",
        }:
            selected.update(TIME_FLOW)

        if path.startswith("game/") or path.startswith("state/"):
            selected.update(RELEASE)
            # Current world/state changes deserve the bounded living-world slice;
            # pure schema/reference edits remain covered by quick_check + core.
            if not path.startswith("game/schemas/"):
                if path in STATIC_GEOGRAPHY_PATHS:
                    selected.update(ROUTE_WORLD)
                else:
                    selected.update(WORLD)

        if path.startswith("plugins/shinobi-rpg/skill/") or path in {"pyproject.toml", "requirements.txt"} or path.startswith("runtime/contracts/"):
            selected.update(RELEASE)
            if path.startswith("plugins/shinobi-rpg/skill/"):
                selected.update({
                    "tests/current/test_gm_skill_interaction_style.py",
                    "tests/current/test_gm_skill_combat_presence.py",
                    "tests/current/test_cross_game_audit_contract.py",
                })
        if path == "state/meta.json":
            selected.add("tests/current/test_campaign_rebaseline.py")

    return sorted(test for test in selected if (ROOT / test).is_file())


def main(argv: list[str]) -> int:
    tests = select(argv)
    print("CHANGED TESTS: " + " ".join(tests), flush=True)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT / "runtime")
    # Running pytest in-process lets this release gate return its assertion
    # status before third-party interpreter-shutdown hooks can stall an
    # otherwise complete test run. The process is disposable verification
    # tooling, so exit immediately after pytest has produced its final code.
    os.environ.update(env)
    import pytest
    rc = int(pytest.main(["-q", "-p", "no:cacheprovider", *tests]))
    print(f"CHANGED TESTS RC={rc}", flush=True)
    os._exit(rc)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
