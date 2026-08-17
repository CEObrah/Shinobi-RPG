"""Production ASGI bootstrap for the configured Shinobi campaign.

Campaign-specific extensions are installed here rather than in
``shinobi_runtime.api.__init__`` so importing API contracts stays side-effect
free and cannot create planner import cycles.
"""

from __future__ import annotations


def _install_campaign_extensions() -> None:
    from shinobi_runtime.commands.semantic_event_integrity import install_semantic_event_integrity
    from shinobi_runtime.commands.mission_boundary_integrity import install_mission_boundary_integrity
    from shinobi_runtime.commands.legacy_scheduler_compat import install_legacy_scheduler_compat
    from shinobi_runtime.commands.academy_pipeline_transfer_ids import install_academy_pipeline_transfer_ids
    from shinobi_runtime.commands.academy_career_sync import install_academy_career_sync
    from shinobi_runtime.commands.shinobi_career_progression import install_shinobi_career_progression
    from shinobi_runtime.commands.promotion_exam_scheduler import (
        install_promotion_exam_projection,
        install_promotion_exam_scheduler,
    )
    from shinobi_runtime.commands.promotion_exam_pacing import install_promotion_exam_pacing
    from shinobi_runtime.commands.promotion_exam_evaluation import install_promotion_exam_evaluation
    from shinobi_runtime.commands.promotion_exam_finals import install_promotion_exam_finals
    from shinobi_runtime.commands.promotion_exam_integrity import install_promotion_exam_integrity
    from shinobi_runtime.commands.promotion_exam_pairing import install_promotion_exam_pairing
    from shinobi_runtime.commands.promotion_exam_service_eligibility import install_promotion_exam_service_eligibility
    from shinobi_runtime.commands.promotion_exam_attendance import install_promotion_exam_attendance
    from shinobi_runtime.commands.promotion_exam_hosted_intervillage import install_promotion_exam_hosted_intervillage
    from shinobi_runtime.commands.shinobi_career_service_authority import install_shinobi_career_service_authority
    from shinobi_runtime.commands.career_history_retention import install_career_history_retention
    from shinobi_runtime.commands.world_front_progression import install_world_front_progression
    from shinobi_runtime.commands.downtime_until_event import install_downtime_until_event
    from shinobi_runtime.commands.downtime_vitality import install_downtime_vitality
    from shinobi_runtime.commands.story_vitality import install_story_vitality
    from shinobi_runtime.commands.scene_resume_projection import install_scene_resume_projection
    from shinobi_runtime.commands.player_mission_continuity import install_player_mission_continuity
    from shinobi_runtime.commands.mission_progression import install_mission_progression
    from shinobi_runtime.commands.player_mission_delegation import install_player_mission_delegation
    from shinobi_runtime.commands.campaign_mission_continuity_repair import install_campaign_mission_continuity_repair
    from shinobi_runtime.commands.campaign_mission_boundary_repair import install_campaign_mission_boundary_repair
    from shinobi_runtime.commands.campaign_family_continuity_repair import install_campaign_family_continuity_repair
    from shinobi_runtime.commands.campaign_player_training_order_repair import install_campaign_player_training_order_repair
    from shinobi_runtime.commands.campaign_player_attribute_correction import install_campaign_player_attribute_correction
    from shinobi_runtime.commands.campaign_named_training_exam_repair import install_campaign_named_training_exam_repair
    from shinobi_runtime.commands.campaign_promotion_exam_eligibility_repair import (
        install_campaign_promotion_exam_eligibility_repair,
    )
    from shinobi_runtime.commands.campaign_promotion_exam_participation_repair import (
        install_campaign_promotion_exam_participation_repair,
    )
    from shinobi_runtime.commands.campaign_promotion_exam_attendance_repair import (
        install_campaign_promotion_exam_attendance_repair,
    )
    from shinobi_runtime.commands.team_checkin_handoffs import install_team_checkin_handoffs
    from shinobi_runtime.commands.institution_review_runtime_guard import install_institution_review_runtime_guard
    from shinobi_runtime.commands.production_population_owner_bridge import install_production_population_owner_bridge
    from shinobi_runtime.commands.team_training_cursor_reconciliation import install_team_training_cursor_reconciliation
    from shinobi_runtime.commands.named_service_development import install_named_service_development
    from shinobi_runtime.commands.global_team_training_load import install_global_team_training_load
    from shinobi_runtime.commands.joint_player_team_training import install_joint_player_team_training
    from shinobi_runtime.commands.autonomous_training_error_guard import install_autonomous_training_error_guard
    from shinobi_runtime.commands.time_planner_error_guard import install_time_planner_error_guard
    from shinobi_runtime.commands.house_recruitment_outreach import install_house_recruitment_outreach
    from shinobi_runtime.commands.external_house_intake_origin import install_external_house_intake_origin
    from shinobi_runtime.api.preview_validation import install_preview_validation
    from shinobi_runtime.api.preview_error_diagnostics import install_preview_error_diagnostics
    from shinobi_runtime.api.player_report_projection import install_player_report_projection
    from shinobi_runtime.api.player_report_lifecycle import install_player_report_lifecycle
    from shinobi_runtime.api.player_team_checkin_projection import install_player_team_checkin_projection
    from shinobi_runtime.api.player_promotion_exam_projection import install_player_promotion_exam_projection
    from shinobi_runtime.api.player_promotion_exam_schedule_projection import (
        install_player_promotion_exam_schedule_projection,
    )
    from shinobi_runtime.api.player_promotion_exam_participation_projection import (
        install_player_promotion_exam_participation_projection,
    )
    from shinobi_runtime.api.player_promotion_exam_results_read import (
        install_player_promotion_exam_results_read,
    )
    from shinobi_runtime.api.mission_assignment_request_projection import install_mission_assignment_request_projection
    from shinobi_runtime.api.player_command_mission_projection import install_player_command_mission_projection
    from shinobi_runtime.api.player_global_team_training_projection import install_player_global_team_training_projection
    from shinobi_runtime.api.player_house_outreach_projection import install_player_house_outreach_projection
    from shinobi_runtime.api.player_house_status_projection import install_player_house_status_projection
    from shinobi_runtime.api.player_family_projection import install_player_family_projection
    from shinobi_runtime.api.player_training_model_projection import install_player_training_model_projection

    install_semantic_event_integrity()
    install_mission_boundary_integrity()
    install_legacy_scheduler_compat()
    install_academy_pipeline_transfer_ids()
    install_academy_career_sync()
    install_shinobi_career_progression()
    install_promotion_exam_scheduler()
    install_promotion_exam_pacing()
    install_promotion_exam_evaluation()
    install_promotion_exam_finals()
    install_promotion_exam_projection()
    install_world_front_progression()
    install_player_report_projection()
    install_player_report_lifecycle()
    install_player_team_checkin_projection()
    install_player_promotion_exam_projection()
    install_player_promotion_exam_schedule_projection()
    install_mission_assignment_request_projection()
    install_player_command_mission_projection()
    install_downtime_until_event()
    install_team_checkin_handoffs()
    install_downtime_vitality()
    install_story_vitality()
    install_scene_resume_projection()
    install_player_mission_continuity()
    install_mission_progression()
    install_player_mission_delegation()
    install_campaign_mission_continuity_repair()
    install_campaign_mission_boundary_repair()
    install_campaign_family_continuity_repair()
    install_campaign_player_training_order_repair()
    install_campaign_player_attribute_correction()
    install_campaign_named_training_exam_repair()
    install_campaign_promotion_exam_eligibility_repair()
    install_campaign_promotion_exam_participation_repair()
    install_campaign_promotion_exam_attendance_repair()
    install_house_recruitment_outreach()
    install_external_house_intake_origin()
    install_joint_player_team_training()
    install_team_training_cursor_reconciliation()
    install_global_team_training_load()
    install_named_service_development()
    install_autonomous_training_error_guard()
    install_promotion_exam_integrity()
    install_promotion_exam_pairing()
    install_promotion_exam_service_eligibility()
    install_promotion_exam_attendance()
    install_promotion_exam_hosted_intervillage()
    install_shinobi_career_service_authority()
    install_career_history_retention()
    install_time_planner_error_guard()
    install_player_global_team_training_projection()
    install_player_house_outreach_projection()
    install_player_house_status_projection()
    install_player_family_projection()
    install_player_promotion_exam_participation_projection()
    install_player_promotion_exam_results_read()
    install_player_training_model_projection()
    install_preview_validation()
    install_preview_error_diagnostics()
    install_institution_review_runtime_guard()
    install_production_population_owner_bridge()


def create_app_from_env():
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