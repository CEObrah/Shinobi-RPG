from __future__ import annotations

from shinobi_runtime.commands.academy_pipeline_transfer_ids import _SHARED_POPULATION
from shinobi_runtime.commands.campaign_environment import CampaignCommandPlanner
from shinobi_runtime.commands.production_population_owner_bridge import (
    install_production_population_owner_bridge,
)


def test_production_bridge_wraps_resolved_civil_economy_method() -> None:
    install_production_population_owner_bridge()
    assert getattr(
        CampaignCommandPlanner._settle_governed_civil_economies,
        "_production_population_owner_bridge",
        False,
    ) is True


def test_production_bridge_records_exact_population_object(monkeypatch) -> None:
    observed = {}

    def base(self, governance, population, holders, finance, *args, **kwargs):
        observed["population"] = population
        observed["shared"] = _SHARED_POPULATION.get()
        return []

    monkeypatch.setattr(
        CampaignCommandPlanner,
        "_settle_governed_civil_economies",
        base,
    )
    import shinobi_runtime.commands.production_population_owner_bridge as bridge
    monkeypatch.setattr(bridge, "_INSTALLED", False)
    bridge.install_production_population_owner_bridge()

    population = {"schema": "population-registry", "pools": {}}
    token = _SHARED_POPULATION.set(None)
    try:
        planner = object.__new__(CampaignCommandPlanner)
        result = planner._settle_governed_civil_economies(
            {}, population, {}, {}
        )
    finally:
        _SHARED_POPULATION.reset(token)

    assert result == []
    assert observed["population"] is population
    assert observed["shared"] is population
