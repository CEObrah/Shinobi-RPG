"""Environment-built production application for the single Jianghu campaign.

Production play composes travel/public-place context, reversible combat parley,
current-revision transition recovery, bounded standing-combat policy, combat
simulation hardening, combat pressure integrity, recovered-readiness integrity,
causal reaction-timing integrity, combat liveness integrity, and resolved-route-
contact reconciliation. Historical repair implementations remain available for
forensic tests, but one-time campaign repair anchors are not installed into normal
production composition after their repair is complete.
"""
from __future__ import annotations

from typing import Any, Mapping


def create_app_from_env():
    from shinobi_runtime.api import app as app_module
    from shinobi_runtime.api.combat_hardening import (
        CombatHardenedCampaignOperations,
        install_combat_simulation_hardening,
    )
    from shinobi_runtime.api.combat_pressure_integrity import install_combat_pressure_integrity
    from shinobi_runtime.api.combat_readiness_integrity import install_combat_readiness_integrity
    from shinobi_runtime.api.combat_reaction_timing_integrity import (
        install_combat_reaction_timing_integrity,
    )
    from shinobi_runtime.api.combat_liveness_integrity import install_combat_liveness_integrity
    from shinobi_runtime.martial_world.route_contact_reconciliation import (
        normalize_resolved_route_contact_context,
    )

    class RouteReconciledCampaignOperations(CombatHardenedCampaignOperations):
        """Production reads that retire stale post-combat route decisions safely."""

        def play_context(self) -> Mapping[str, Any]:
            base = super().play_context()
            return normalize_resolved_route_contact_context(
                base, self.repository.read_json,
            )

    # Order matters. Readiness normalizes only the defender view at the physical
    # defense seam after pressure semantics are installed. Reaction timing then
    # adapts the final defense decision for the exact resolver's legacy timing
    # consumption. Liveness wraps the final hardened span/disengage surfaces
    # that will actually commit.
    install_combat_simulation_hardening()
    install_combat_pressure_integrity()
    install_combat_readiness_integrity()
    install_combat_reaction_timing_integrity()
    install_combat_liveness_integrity()

    app_module.CampaignOperations = RouteReconciledCampaignOperations
    return app_module.create_app_from_env()


__all__ = ["create_app_from_env"]
