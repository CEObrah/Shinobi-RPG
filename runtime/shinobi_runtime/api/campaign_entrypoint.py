"""Environment-built production application for the single Jianghu campaign.

Production play composes the canonical combat engine, travel/public-place context,
reversible combat parley, current-revision transition recovery, and resolved-route
contact reconciliation. Combat laws are statically owned by their domain modules;
startup does not layer historical integrity monkey patches over those owners.
"""
from __future__ import annotations

from typing import Any, Mapping


def create_app_from_env():
    from shinobi_runtime.api import app as app_module
    from shinobi_runtime.api.transition_operations import TransitionAwareCampaignOperations
    from shinobi_runtime.api.combat_production import install_production_combat_runtime
    from shinobi_runtime.martial_world.route_contact_reconciliation import (
        normalize_resolved_route_contact_context,
    )

    class RouteReconciledCampaignOperations(TransitionAwareCampaignOperations):
        """Production reads that retire stale post-combat route decisions safely."""

        def play_context(self) -> Mapping[str, Any]:
            base = super().play_context()
            return normalize_resolved_route_contact_context(
                base, self.repository.read_json,
            )

    # Production combat composition has one static owner handshake. Historical
    # integrity adapters are not installed at runtime.
    install_production_combat_runtime()

    app_module.CampaignOperations = RouteReconciledCampaignOperations
    return app_module.create_app_from_env()


__all__ = ["create_app_from_env"]
