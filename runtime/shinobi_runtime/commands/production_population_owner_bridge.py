"""Bind the monthly population-owner bridge to the resolved production planner.

The generic TimeCommandsMixin hook is intentionally kept in the Academy
compatibility module, but the production planner composes several mixins and may
resolve a later override.  Install this adapter after the campaign extension
stack so the exact concrete method used by Railway records the already-loaded
population object in the same context-local transaction slot consumed by later
Academy work.

No campaign state is created or merged here. The adapter only exposes object
identity already selected by the base time reducer.
"""
from __future__ import annotations

from functools import wraps
from typing import Any

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.academy_pipeline_transfer_ids import _SHARED_POPULATION

_INSTALLED = False


def install_production_population_owner_bridge() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from shinobi_runtime.commands.campaign_environment import CampaignCommandPlanner

    original = CampaignCommandPlanner._settle_governed_civil_economies
    if getattr(original, "_production_population_owner_bridge", False):
        _INSTALLED = True
        return

    @wraps(original)
    def settle_civil(
        self: Any,
        governance: Any,
        population: Any,
        holders: Any,
        finance: Any,
        *args: Any,
        **kwargs: Any,
    ):
        if not isinstance(population, dict):
            raise CommandRejectedError("population_registry_invalid")
        _SHARED_POPULATION.set(population)
        return original(
            self,
            governance,
            population,
            holders,
            finance,
            *args,
            **kwargs,
        )

    settle_civil._production_population_owner_bridge = True  # type: ignore[attr-defined]
    CampaignCommandPlanner._settle_governed_civil_economies = settle_civil
    _INSTALLED = True


__all__ = ["install_production_population_owner_bridge"]
