"""Production ASGI bootstrap for the configured Shinobi campaign.

Campaign-specific extensions are installed here rather than in
``shinobi_runtime.api.__init__`` so importing API contracts stays side-effect
free and cannot create planner import cycles.
"""

from __future__ import annotations


def _install_campaign_extensions() -> None:
    from shinobi_runtime.commands.legacy_scheduler_compat import install_legacy_scheduler_compat
    from shinobi_runtime.commands.academy_career_sync import install_academy_career_sync
    from shinobi_runtime.commands.shinobi_career_progression import install_shinobi_career_progression
    from shinobi_runtime.commands.promotion_exam_scheduler import install_promotion_exam_scheduler
    from shinobi_runtime.commands.promotion_exam_cycle import install_promotion_exam_projection
    from shinobi_runtime.commands.world_front_progression import install_world_front_progression
    from shinobi_runtime.commands.downtime_until_event import install_downtime_until_event
    from shinobi_runtime.commands.downtime_vitality import install_downtime_vitality
    from shinobi_runtime.api.preview_validation import install_preview_validation

    install_legacy_scheduler_compat()
    install_academy_career_sync()
    install_shinobi_career_progression()
    install_promotion_exam_scheduler()
    install_promotion_exam_projection()
    install_world_front_progression()
    install_downtime_until_event()
    install_downtime_vitality()
    install_preview_validation()


def create_app_from_env():
    # Patch concrete campaign implementations before loading api.app.  Generic
    # base classes remain reusable for isolated unit tests.
    from shinobi_runtime.api import ooc as ooc_module
    from shinobi_runtime.api import route_discovery as route_discovery_module
    from shinobi_runtime.api.campaign_manufacturing_discovery import RouteAwareCampaignOperations
    from shinobi_runtime.api.campaign_ooc import RepositoryOocAudit
    from shinobi_runtime.commands import campaign_planner as planner_module
    from shinobi_runtime.commands.campaign_mission_assignment import CampaignCommandPlanner

    _install_campaign_extensions()
    ooc_module.RepositoryOocAudit = RepositoryOocAudit
    route_discovery_module.RouteAwareCampaignOperations = RouteAwareCampaignOperations
    planner_module.CampaignCommandPlanner = CampaignCommandPlanner

    from shinobi_runtime.api.app import create_app_from_env as factory

    return factory()


__all__ = ["create_app_from_env"]
