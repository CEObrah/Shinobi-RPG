"""Production ASGI bootstrap for the single Jianghu campaign.

Production play composes travel/public-place context, reversible combat parley,
current-revision transition recovery, bounded standing-combat policy, and
resolved-route-contact reconciliation so exact co-travelers, observer-specific
combat knowledge, interrupted committed scene chronology, and long delegated
combat intent remain safe and recoverable from Runtime authority. Historical
one-off repair anchors are intentionally excluded from live composition once
their repaired state is part of the canonical campaign baseline.
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

    # Do not install historical one-off repair anchors here. The canonical
    # packaged baseline already contains their repaired truth, while a fresh
    # private recovery store intentionally lacks the legacy WAL chain required
    # to prove those old incidents. The forensic helper remains importable for
    # explicit disposable-copy investigations and regression tests only.

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
