#!/usr/bin/env python3
"""Route changed repository paths to the maintained pytest regression slices."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BASELINE = {
    "tests/runtime/test_authority.py",
    "tests/runtime/test_schema_validation.py",
    "tests/runtime/test_template_validation.py",
}

ROUTES = (
    (("plugins/", "runtime/contracts/narration"), {
        "tests/runtime/test_narration_contract.py",
        "tests/runtime/test_narration_routing_fallback.py",
        "tests/runtime/test_social_narration_contract.py",
        "tests/runtime/test_gm_turn_completion_contract.py",
    }),
    (("state/person-core/", "state/house/", "game/data/house/", "runtime/shinobi_runtime/people/", "rostered_house_progression.py", "person_exactification"), {
        "tests/runtime/test_people.py",
        "tests/runtime/test_house_lazy_progression.py",
        "tests/runtime/test_sword_manor_intake_profiles.py",
        "tests/runtime/test_sword_manor_intake_onboarding.py",
        "tests/runtime/test_release_representation_bridge.py",
    }),
    (("state/population/", "recruit", "academy", "background_profiles"), {
        "tests/runtime/test_people.py",
        "tests/runtime/test_academy_exact_graduation.py",
        "tests/runtime/test_academy_team_assignment_policy.py",
        "tests/runtime/test_release_recruitment_progression.py",
    }),
    (("state/team/", "team_", "teams.py"), {
        "tests/runtime/test_team_intelligence.py",
        "tests/runtime/test_team_lifecycle_intelligence.py",
        "tests/runtime/test_team_playability_interface.py",
        "tests/runtime/test_team_training_readiness_projection.py",
    }),
    (("promotion_exam", "promotion-exams.json", "career_history_retention"), {
        "tests/runtime/test_promotion_exam_scheduler.py",
        "tests/runtime/test_promotion_exam_evaluation.py",
        "tests/runtime/test_promotion_exam_finals.py",
        "tests/runtime/test_promotion_exam_integrity.py",
        "tests/runtime/test_promotion_exam_pairing.py",
        "tests/runtime/test_promotion_exam_participation_projection.py",
        "tests/runtime/test_promotion_exam_service_eligibility.py",
        "tests/runtime/test_promotion_exam_attendance.py",
        "tests/runtime/test_promotion_exam_participation_repair.py",
        "tests/runtime/test_career_history_retention.py",
    }),
    (("mission",), {
        "tests/runtime/test_missions.py",
        "tests/runtime/test_mission_briefing.py",
        "tests/runtime/test_mission_context_handoff.py",
    }),
    (("combat", "formation", "force/", "conflict"), {
        "tests/runtime/test_combat.py",
        "tests/runtime/test_command_planner.py",
        "tests/runtime/test_release_cohort_combat.py",
        "tests/runtime/test_release_combat_resources.py",
        "tests/runtime/test_release_mass_battle_factors.py",
        "tests/runtime/test_release_recruitment_progression.py",
        "tests/runtime/test_release_representation_bridge.py",
    }),
    (("environment.py", "environment-climates", "runtime/contracts/environment", "campaign_environment.py"), {
        "tests/runtime/test_environment.py",
        "tests/runtime/test_real_campaign_planner.py",
        "tests/runtime/test_combat.py",
        "tests/runtime/test_api_service.py",
        "tests/runtime/test_route_discovery.py",
    }),
    (("development", "training"), {
        "tests/runtime/test_development_breakthrough_policy.py",
        "tests/runtime/test_training_autonomy_policy.py",
        "tests/runtime/test_training_progression_ceiling.py",
        "tests/runtime/test_training_model_discovery.py",
    }),
    (("time", "scheduler", "world_front", "advance_until_event", "institution_review_runtime_guard", "semantic_event"), {
        "tests/runtime/test_advance_until_event.py",
        "tests/runtime/test_world_front_progression.py",
        "tests/runtime/test_long_horizon_stability.py",
        "tests/runtime/test_production_monthly_frontier.py",
        "tests/runtime/test_institution_review_runtime_guard.py",
        "tests/runtime/test_academy_pipeline_transfer_ids.py",
        "tests/runtime/test_semantic_event_integrity.py",
        "tests/runtime/test_world_event_staged_archives.py",
    }),
    (("api/", "mcp", "route_discovery"), {
        "tests/runtime/test_api_service.py",
        "tests/runtime/test_mcp_plugin.py",
        "tests/runtime/test_route_discovery.py",
    }),
    (("bootstrap", "remote.py", "railway.toml", "deployment_freshness"), {
        "tests/runtime/test_bootstrap.py",
        "tests/runtime/test_bootstrap_repository_replacement.py",
        "tests/runtime/test_remote_durability.py",
        "tests/runtime/test_deployment_freshness.py",
    }),
    (("tx/", "transaction", "invalidations"), {
        "tests/runtime/test_transaction_coordinator.py",
        "tests/runtime/test_transaction_foundation.py",
        "tests/runtime/test_runtime_state_guardrails.py",
    }),
)


def select(paths: list[str]) -> list[str]:
    if not paths:
        return ["tests/runtime"]
    normalized = [path.replace("\\", "/").lower() for path in paths]
    selected = set(BASELINE)
    matched = False
    for needles, tests in ROUTES:
        if any(any(needle.lower() in path for needle in needles) for path in normalized):
            selected.update(tests)
            matched = True
    if not matched:
        return ["tests/runtime"]
    return sorted(selected)


def main() -> int:
    targets = select(sys.argv[1:])
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets]
    print("CHANGED TESTS:", " ".join(targets))
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
