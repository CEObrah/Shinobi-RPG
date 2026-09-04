"""Production ASGI bootstrap for the single Jianghu campaign.

Production play composes travel/public-place context, reversible combat parley,
current-revision transition recovery, bounded standing-combat policy, and
resolved-route-contact reconciliation. Historical repair implementations remain
available for forensic tests, but one-time campaign repair anchors are not
installed into normal production composition after their repair is complete.
"""
from __future__ import annotations

from typing import Any, Mapping


def create_app_from_env():
    from shinobi_runtime.api import app as app_module
    from shinobi_runtime.api.transition_operations import TransitionAwareCampaignOperations
    from shinobi_runtime.commands.combat_span_safety import install_production_combat_span_safety
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

    # Campaign-specific standing combat intent is composed before the service
    # starts accepting commands. The exact reducer remains deterministic; the
    # production wrapper only bounds one transaction's simulated-time footprint
    # and preserves the explicit rapid-lethal target-selection semantics.
    install_production_combat_span_safety()

    # Current-transition response bounding remains intrinsic to the transition
    # operations base. The final production wrapper additionally prevents a
    # route contact whose exact combat is already resolved from resurfacing as a
    # fresh player decision. That normalization is read-only and identity-safe;
    # the route owner itself is reconciled only by a later transactional write.
    app_module.CampaignOperations = RouteReconciledCampaignOperations
    return app_module.create_app_from_env()


__all__ = ["create_app_from_env"]
