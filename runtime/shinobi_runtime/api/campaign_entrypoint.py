"""Production ASGI bootstrap with campaign repair/freshness extensions wired in."""

from __future__ import annotations


def create_app_from_env():
    # Patch the concrete imports used by api.app before loading that module.
    # The base classes remain reusable for isolated unit tests and generic runtimes.
    from shinobi_runtime.api import ooc as ooc_module
    from shinobi_runtime.api import route_discovery as route_discovery_module
    from shinobi_runtime.api.campaign_manufacturing_discovery import RouteAwareCampaignOperations
    from shinobi_runtime.api.campaign_ooc import RepositoryOocAudit
    from shinobi_runtime.api.preview_validation import install_preview_validation
    from shinobi_runtime.commands import campaign_planner as planner_module
    from shinobi_runtime.commands.campaign_manufacturing import CampaignCommandPlanner
    from shinobi_runtime.commands.downtime_vitality import install_downtime_vitality
    from shinobi_runtime.commands.promotion_exam_cycle import install_promotion_exam_projection

    install_promotion_exam_projection()
    install_downtime_vitality()
    install_preview_validation()
    ooc_module.RepositoryOocAudit = RepositoryOocAudit
    route_discovery_module.RouteAwareCampaignOperations = RouteAwareCampaignOperations
    planner_module.CampaignCommandPlanner = CampaignCommandPlanner

    from shinobi_runtime.api.app import create_app_from_env as factory

    return factory()


__all__ = ["create_app_from_env"]
