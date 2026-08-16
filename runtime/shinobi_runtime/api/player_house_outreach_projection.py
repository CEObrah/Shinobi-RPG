"""Player-safe discovery for House Tang external recruitment outreach."""
from __future__ import annotations

from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.operations import OperationError

_RULE = "game/rules/recruitment/sword-manor-outreach.json"
_POPULATION = "state/population/registry.json"
_INSTALLED = False


def install_player_house_outreach_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.api import campaign_environment as module

    operations = module.RouteAwareCampaignOperations
    original = operations._growth_discovery
    if getattr(original, "_house_outreach_projection", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, player_id: str) -> Mapping[str, Any]:
        result = dict(original(self, player_id))
        try:
            rule = self.repository.read_json(_RULE)
            population = self.repository.read_json(_POPULATION)
        except (FileNotFoundError, ValueError) as exc:
            raise OperationError(503, "growth_discovery_invalid") from exc
        owners = rule.get("eligible_source_owner_refs") if isinstance(rule, Mapping) else None
        categories = rule.get("eligible_source_categories") if isinstance(rule, Mapping) else None
        pools = population.get("pools") if isinstance(population, Mapping) else None
        if (
            not isinstance(owners, list)
            or not isinstance(categories, list)
            or not isinstance(pools, Mapping)
        ):
            raise OperationError(503, "growth_discovery_invalid")
        candidates = []
        for pool_id, pool in sorted(pools.items()):
            if not isinstance(pool_id, str) or not isinstance(pool, Mapping):
                continue
            if (
                pool.get("status") == "active"
                and pool.get("owner_ref") in owners
                and pool.get("category") in categories
            ):
                representation = pool.get("representation")
                candidates.append(
                    {
                        "source_pool_id": pool_id,
                        "category": pool.get("category"),
                        "owner_ref": pool.get("owner_ref"),
                        "anonymous_count": representation.get("anonymous_count")
                        if isinstance(representation, Mapping)
                        else None,
                    }
                )
        policies = list(result.get("recruitment_policies") or [])
        policy_ref = rule.get("policy_ref")
        if not any(
            isinstance(row, Mapping) and row.get("policy_ref") == policy_ref
            for row in policies
        ):
            policies.append(
                {
                    "policy_ref": policy_ref,
                    "eligible_source_categories": list(categories),
                    "outreach_source_candidates": candidates[:64],
                    "selection_model": rule.get("selection_mode"),
                    "materialization": "house_identity_required_after_mature_outreach",
                    "destination_owner_ref": "house.tang",
                    "destination_pool_id": None,
                    "decision_authority_ref": "char.zhu",
                    "oath_required": True,
                    "outreach_command_type": "institution_recruitment_outreach_resolution",
                    "intake_command_type": "institution_intake_resolution",
                    "intake_requires_mature_outreach": True,
                    "outreach_modes": list(rule.get("outreach_modes", ())),
                    "destination_service_status": rule.get("destination_service_status"),
                    "source_sovereignty_rule": rule.get("source_sovereignty_rule"),
                }
            )
        result["recruitment_policies"] = policies
        result["recruitment_policy_count"] = max(
            int(result.get("recruitment_policy_count", 0)), len(policies)
        )
        return result

    wrapped._house_outreach_projection = True  # type: ignore[attr-defined]
    operations._growth_discovery = wrapped
    _INSTALLED = True


__all__ = ["install_player_house_outreach_projection"]
