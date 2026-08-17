from shinobi_runtime.commands import downtime_until_event
from shinobi_runtime.commands.campaign_mission_assignment import (
    CampaignCommandPlanner as FinalCampaignCommandPlanner,
)


def test_downtime_installer_refreshes_final_production_planner(monkeypatch):
    """The final campaign subclass must not rely on another installer for refresh."""

    stale_types = frozenset(
        command_type
        for command_type in FinalCampaignCommandPlanner.COMMAND_TYPES
        if command_type != "advance_until_event"
    )
    monkeypatch.setattr(FinalCampaignCommandPlanner, "COMMAND_TYPES", stale_types)
    monkeypatch.setattr(downtime_until_event, "_INSTALLED", False)

    downtime_until_event.install_downtime_until_event()

    assert "advance_until_event" in FinalCampaignCommandPlanner.COMMAND_TYPES
    assert (
        getattr(FinalCampaignCommandPlanner, "_advance_until_event")
        is downtime_until_event._advance_until_event
    )
