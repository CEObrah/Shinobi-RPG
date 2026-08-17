"""Player-safe discovery additions for bounded institution growth."""
from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.campaign_action_discovery import RouteAwareCampaignOperations as _Base
from shinobi_runtime.api.operations import OperationError


class RouteAwareCampaignOperations(_Base):
    """Adds the exact authority-request scopes needed to execute discovered growth actions."""

    def _growth_discovery(self, player_id: str) -> Mapping[str, Any]:
        result = dict(super()._growth_discovery(player_id))
        try:
            registry = self.repository.read_json("game/rules/institutions/growth-requests.json")
        except (FileNotFoundError, ValueError) as exc:
            raise OperationError(503, "growth_discovery_invalid") from exc
        policies = registry.get("policies") if isinstance(registry, Mapping) else None
        if not isinstance(policies, Mapping):
            raise OperationError(503, "growth_discovery_invalid")

        houses = result.get("player_house_growth")
        if not isinstance(houses, list):
            raise OperationError(503, "growth_discovery_invalid")
        enriched_houses = []
        for row in houses:
            if not isinstance(row, Mapping):
                raise OperationError(503, "growth_discovery_invalid")
            updated = dict(row)
            institution_ref = updated.get("institution_ref")
            policy = policies.get(institution_ref) if isinstance(institution_ref, str) else None
            scopes = []
            if isinstance(policy, Mapping):
                scopes.extend(
                    f"project:{value}"
                    for value in policy.get("delegable_project_types", [])
                    if isinstance(value, str) and value
                )
                scopes.extend(
                    f"recruitment:{value}"
                    for value in policy.get("delegable_recruitment_policies", [])
                    if isinstance(value, str) and value
                )
                updated["decision_authority_ref"] = policy.get(
                    "decision_authority_ref", updated.get("decision_authority_ref")
                )
            updated["growth_request_command_type"] = "institution_growth_request_resolution"
            updated["growth_request_scope_refs"] = scopes
            enriched_houses.append(updated)
        result["player_house_growth"] = enriched_houses

        recruitment = result.get("recruitment_policies")
        if not isinstance(recruitment, list):
            raise OperationError(503, "growth_discovery_invalid")
        enriched_recruitment = []
        for row in recruitment:
            if not isinstance(row, Mapping):
                raise OperationError(503, "growth_discovery_invalid")
            updated = dict(row)
            updated["command_type"] = "institution_intake_resolution"
            enriched_recruitment.append(updated)
        result["recruitment_policies"] = enriched_recruitment
        result["authority_rule"] = (
            "Strategic House growth remains with the saved House authority holder; "
            "a registered request may delegate only the listed scope refs."
        )
        return result


__all__ = ["RouteAwareCampaignOperations"]
