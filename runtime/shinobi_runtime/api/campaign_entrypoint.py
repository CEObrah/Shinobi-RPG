"""Production ASGI bootstrap for the configured Shinobi campaign.

Campaign-specific extensions are installed here rather than in
``shinobi_runtime.api.__init__`` so importing API contracts stays side-effect
free and cannot create planner import cycles.
"""

from __future__ import annotations


def _install_campaign_extensions() -> None:
    from shinobi_runtime.commands.legacy_scheduler_compat import install_legacy_scheduler_compat
    from shinobi_runtime.commands.academy_pipeline_transfer_ids import install_academy_pipeline_transfer_ids
    from shinobi_runtime.commands.academy_career_sync import install_academy_career_sync
    from shinobi_runtime.commands.shinobi_career_progression import install_shinobi_career_progression
    from shinobi_runtime.commands.promotion_exam_scheduler import (
        install_promotion_exam_projection,
        install_promotion_exam_scheduler,
    )
    from shinobi_runtime.commands.world_front_progression import install_world_front_progression
    from shinobi_runtime.commands.downtime_until_event import install_downtime_until_event
    from shinobi_runtime.commands.downtime_vitality import install_downtime_vitality
    from shinobi_runtime.commands.team_checkin_handoffs import install_team_checkin_handoffs
    from shinobi_runtime.commands.institution_review_runtime_guard import install_institution_review_runtime_guard
    from shinobi_runtime.commands.production_population_owner_bridge import install_production_population_owner_bridge
    from shinobi_runtime.commands.global_team_training_load import install_global_team_training_load
    from shinobi_runtime.commands.joint_player_team_training import install_joint_player_team_training
    from shinobi_runtime.commands.house_recruitment_outreach import install_house_recruitment_outreach
    from shinobi_runtime.commands.external_house_intake_origin import install_external_house_intake_origin
    from shinobi_runtime.api.preview_validation import install_preview_validation
    from shinobi_runtime.api.player_report_projection import install_player_report_projection
    from shinobi_runtime.api.player_report_lifecycle import install_player_report_lifecycle
    from shinobi_runtime.api.player_team_checkin_projection import install_player_team_checkin_projection
    from shinobi_runtime.api.player_promotion_exam_projection import install_player_promotion_exam_projection
    from shinobi_runtime.api.mission_assignment_request_projection import install_mission_assignment_request_projection
    from shinobi_runtime.api.player_global_team_training_projection import install_player_global_team_training_projection
    from shinobi_runtime.api.player_house_outreach_projection import install_player_house_outreach_projection

    install_legacy_scheduler_compat()
    install_academy_pipeline_transfer_ids()
    install_academy_career_sync()
    install_shinobi_career_progression()
    install_promotion_exam_scheduler()
    install_promotion_exam_projection()
    install_world_front_progression()
    install_player_report_projection()
    install_player_report_lifecycle()
    install_player_team_checkin_projection()
    install_player_promotion_exam_projection()
    install_mission_assignment_request_projection()
    install_downtime_until_event()
    install_team_checkin_handoffs()
    install_downtime_vitality()
    install_house_recruitment_outreach()
    install_external_house_intake_origin()
    # Joint participation is installed first; the global-load installer then
    # scopes the final autonomous-training surface so staged sessions created by
    # either base team training or the joint block are visible to one another.
    install_joint_player_team_training()
    install_global_team_training_load()
    install_player_global_team_training_projection()
    install_player_house_outreach_projection()
    install_preview_validation()
    # Install final guards after every campaign wrapper has resolved its concrete
    # production method surface.
    install_institution_review_runtime_guard()
    install_production_population_owner_bridge()


def create_app_from_env():
    # Patch concrete campaign implementations before loading api.app. Generic
    # base classes remain reusable for isolated unit tests.
    from shinobi_runtime.api import ooc as ooc_module
    from shinobi_runtime.api import route_discovery as route_discovery_module
    from shinobi_runtime.api.campaign_environment import RouteAwareCampaignOperations
    from shinobi_runtime.api.campaign_ooc import RepositoryOocAudit
    from shinobi_runtime.commands import campaign_planner as planner_module
    from shinobi_runtime.commands.campaign_environment import CampaignCommandPlanner

    _install_campaign_extensions()
    ooc_module.RepositoryOocAudit = RepositoryOocAudit
    route_discovery_module.RouteAwareCampaignOperations = RouteAwareCampaignOperations
    planner_module.CampaignCommandPlanner = CampaignCommandPlanner

    from shinobi_runtime.api.app import create_app_from_env as factory

    return factory()


__all__ = ["create_app_from_env"]
