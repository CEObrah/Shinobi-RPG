"""Player-safe manufacturing discovery layered over institution growth."""
from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.campaign_growth_discovery import RouteAwareCampaignOperations as _Base
from shinobi_runtime.api.operations import OperationError

_MECHANICS_PATH = "game/data/mechanics/institution-projects.json"


class RouteAwareCampaignOperations(_Base):
    """Expose executable workshop recipe IDs without implying authority or output."""

    def _manufacturing_catalog(self) -> tuple[list[Mapping[str, Any]], int]:
        try:
            mechanics = self.repository.read_json(_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise OperationError(503, "growth_discovery_invalid") from exc
        recipes = mechanics.get("manufacturing_recipes") if isinstance(mechanics, Mapping) else None
        schedule = mechanics.get("manufacturing_schedule") if isinstance(mechanics, Mapping) else None
        weekly_hours = schedule.get("standing_weekly_active_hours") if isinstance(schedule, Mapping) else None
        if (
            not isinstance(recipes, Mapping)
            or len(recipes) > 64
            or isinstance(weekly_hours, bool)
            or not isinstance(weekly_hours, int)
            or not 0 < weekly_hours <= 48
        ):
            raise OperationError(503, "growth_discovery_invalid")
        rows: list[Mapping[str, Any]] = []
        for recipe_ref, recipe in sorted(recipes.items()):
            if not isinstance(recipe_ref, str) or not isinstance(recipe, Mapping):
                raise OperationError(503, "growth_discovery_invalid")
            rows.append({
                "recipe_ref": recipe_ref,
                "output_item_ref": recipe.get("output_item_ref"),
                "output_quantity_per_batch": recipe.get("output_quantity_per_batch"),
                "required_module_kind": recipe.get("required_module_kind"),
                "production_line_field": recipe.get("production_line_field"),
                "active_hours_per_batch": recipe.get("active_hours_per_batch"),
                "procurement_cost_ryo_per_batch": recipe.get("procurement_cost_ryo_per_batch"),
                "authority_scope_ref": recipe.get("authority_scope_ref"),
            })
        return rows, weekly_hours

    def _growth_discovery(self, player_id: str) -> Mapping[str, Any]:
        result = dict(super()._growth_discovery(player_id))
        recipes, weekly_hours = self._manufacturing_catalog()
        houses = result.get("player_house_growth")
        if not isinstance(houses, list):
            raise OperationError(503, "growth_discovery_invalid")
        enriched = []
        for row in houses:
            if not isinstance(row, Mapping):
                raise OperationError(503, "growth_discovery_invalid")
            updated = dict(row)
            updated["manufacturing_command_type"] = "institution_manufacturing_resolution"
            updated["manufacturing_recipes"] = [dict(recipe) for recipe in recipes]
            updated["standing_manufacturing"] = {
                "schedule_action": "schedule",
                "cancel_action": "cancel",
                "standing_weekly_active_hours": weekly_hours,
                "rule": "A schedule produces only from real workshop lines, elapsed labor, conserved procurement funds, and tracked stock.",
            }
            enriched.append(updated)
        result["player_house_growth"] = enriched
        return result

    def inspect_game_object(self, object_ref: str) -> Mapping[str, Any]:
        result = dict(super().inspect_game_object(object_ref))
        if not object_ref.startswith("place."):
            return result
        payload = result.get("object")
        if not isinstance(payload, Mapping):
            return result
        recipes, weekly_hours = self._manufacturing_catalog()
        modules = payload.get("mechanical_modules")
        production = modules.get("production") if isinstance(modules, Mapping) else None
        updated = dict(payload)
        updated["manufacturing_interface"] = {
            "command_type": "institution_manufacturing_resolution",
            "place_ref": object_ref,
            "production_module_present": isinstance(production, Mapping),
            "production_lines": dict(production) if isinstance(production, Mapping) else None,
            "candidate_recipes": [dict(recipe) for recipe in recipes],
            "standing_weekly_active_hours": weekly_hours,
            "rule": "Discovery lists legal recipe IDs only; saved authority, workshop lines, stock ownership and procurement funds still govern execution.",
        }
        result["object"] = updated
        return result


__all__ = ["RouteAwareCampaignOperations"]
