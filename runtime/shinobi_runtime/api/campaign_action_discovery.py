"""Bounded player-safe discovery for institution growth workflows."""
from __future__ import annotations
from typing import Any, Mapping
from shinobi_runtime.api.campaign_stable_operations import RouteAwareCampaignOperations as _Base
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.membership_routes import house_refs_for_member


class RouteAwareCampaignOperations(_Base):
    """Production reads plus executable-value discovery for growth workflows."""

    def _growth_discovery(self, player_id: str) -> Mapping[str, Any]:
        try:
            house_refs = house_refs_for_member(self.repository, player_id)
            projects_record = self.repository.read_json("game/data/mechanics/institution-projects.json")
            projects = projects_record.get("project_types") if isinstance(projects_record, Mapping) else None
            policy_record = self.repository.read_json("game/rules/recruitment/policies.json")
            policies = policy_record.get("policies") if isinstance(policy_record, Mapping) else None
            population_record = self.repository.read_json("state/population/registry.json")
            pools = population_record.get("pools") if isinstance(population_record, Mapping) else None
            stock_record = self.repository.read_json("game/data/items/stock-owner-paths.json")
            stocks = stock_record.get("stocks") if isinstance(stock_record, Mapping) else None
        except (FileNotFoundError, ValueError) as exc:
            raise OperationError(503, "growth_discovery_invalid") from exc
        if not all(isinstance(value, Mapping) for value in (projects, policies, pools, stocks)):
            raise OperationError(503, "growth_discovery_invalid")
        # Catalog sizes are not validity conditions. Player-facing projections
        # remain bounded below while exact owners stay directly retrievable.

        project_rows = []
        project_items = sorted(projects.items())
        for project_type, row in project_items[:64]:
            if not isinstance(project_type, str) or not isinstance(row, Mapping):
                continue
            project_rows.append({
                "project_type": project_type,
                "module_kind": row.get("module_kind"),
                "currency_cost_ryo": row.get("currency_cost_ryo"),
                "required_work_units": row.get("required_work_units"),
                "work_units_per_active_hour": row.get("work_units_per_active_hour"),
                "resource_costs": dict(row.get("resource_costs", {})) if isinstance(row.get("resource_costs", {}), Mapping) else {},
            })

        house_rows = []
        for house_ref in house_refs:
            try:
                path, house = self._owner_record(house_ref)
            except OperationError:
                continue
            members = house.get("member_ids") if isinstance(house, Mapping) else None
            if not isinstance(members, list) or player_id not in members:
                continue
            leadership = house.get("leadership") if isinstance(house, Mapping) else None
            head = leadership.get("head_of_house") if isinstance(leadership, Mapping) else None
            stock_refs = []
            for stock_ref, stock_path in sorted(stocks.items()):
                if not isinstance(stock_ref, str) or not isinstance(stock_path, str):
                    continue
                try:
                    stock = self.repository.read_json(stock_path)
                except (FileNotFoundError, ValueError):
                    continue
                owner = stock.get("owner_ref", stock.get("owner")) if isinstance(stock, Mapping) else None
                if owner == house_ref:
                    stock_refs.append(stock_ref)
            house_rows.append({
                "institution_ref": house_ref,
                "home_place_ref": house.get("home"),
                "stock_refs": stock_refs,
                "decision_authority_ref": head,
                "player_can_direct_strategic_growth": head == player_id,
                "project_command_type": "institution_project_resolution",
                "project_types": project_rows,
            })

        recruitment_rows = []
        policy_items = sorted(policies.items())
        for policy_ref, policy in policy_items[:64]:
            if not isinstance(policy_ref, str) or not isinstance(policy, Mapping):
                continue
            source_owners = policy.get("eligible_source_owner_refs")
            categories = policy.get("eligible_source_categories")
            if not isinstance(source_owners, list) or not source_owners or not isinstance(categories, list):
                continue
            candidates = []
            for pool_id, pool in sorted(pools.items()):
                if len(candidates) >= 64:
                    break
                if not isinstance(pool_id, str) or not isinstance(pool, Mapping):
                    continue
                if pool.get("status") == "active" and pool.get("owner_ref") in source_owners and pool.get("category") in categories:
                    candidates.append({
                        "source_pool_id": pool_id,
                        "category": pool.get("category"),
                        "owner_ref": pool.get("owner_ref"),
                        "anonymous_count": (pool.get("representation") or {}).get("anonymous_count") if isinstance(pool.get("representation"), Mapping) else None,
                    })
            recruitment_rows.append({
                "policy_ref": policy_ref,
                "eligible_source_categories": list(categories),
                "source_pool_candidates": candidates,
                "selection_model": policy.get("selection_model", policy.get("selection_mode")),
                "materialization": policy.get("materialization", policy.get("materialization_policy")),
                "destination_owner_ref": policy.get("destination_owner_ref"),
                "destination_pool_id": policy.get("destination_pool_id"),
                "decision_authority_ref": policy.get("decision_authority_ref"),
                "oath_required": policy.get("oath_required", False),
            })
        return {
            "player_house_growth": house_rows[:32],
            "player_house_growth_truncated": len(house_rows) > 32,
            "project_type_count": len(project_items),
            "project_types_truncated": len(project_items) > 64,
            "recruitment_policies": recruitment_rows,
            "recruitment_policy_count": len(policy_items),
            "recruitment_policies_truncated": len(policy_items) > 64,
            "authority_rule": "Strategic House growth remains with the saved House authority holder; discovery never implies approval.",
        }

    def _project_play_context(self, meta: object, scene: object, player: object, state_root: str) -> Mapping[str, Any]:
        result = dict(super()._project_play_context(meta, scene, player, state_root))
        campaign = result.get("campaign")
        player_id = campaign.get("player_id") if isinstance(campaign, Mapping) else None
        if not isinstance(player_id, str):
            raise OperationError(503, "growth_discovery_invalid")
        result["growth_interfaces"] = self._growth_discovery(player_id)
        return result

    def inspect_game_object(self, object_ref: str) -> Mapping[str, Any]:
        result = dict(super().inspect_game_object(object_ref))
        if not object_ref.startswith("place."):
            return result
        payload = result.get("object")
        if not isinstance(payload, Mapping) or not isinstance(payload.get("authority_ref"), str):
            return result
        try:
            registry = self.repository.read_json("game/data/mechanics/institution-projects.json")
        except (FileNotFoundError, ValueError):
            return result
        project_types = registry.get("project_types") if isinstance(registry, Mapping) else None
        if not isinstance(project_types, Mapping):
            return result
        updated = dict(payload)
        updated["project_interface"] = {
            "command_type": "institution_project_resolution",
            "institution_ref": payload.get("authority_ref"),
            "place_ref": object_ref,
            "candidate_project_types": sorted(project_types),
            "rule": "Use only a listed project_type; saved strategic authority and conserved startup resources are still required.",
        }
        result["object"] = updated
        return result


__all__ = ["RouteAwareCampaignOperations"]
