from shinobi_runtime.commands.living_world_training import _development_cursor_skip
from shinobi_runtime.sim.events import CampaignTime


def test_autonomous_training_never_advances_across_unsettled_backlog() -> None:
    interval = CampaignTime.parse("SE-0061-07-24T07:00:00")
    stale = CampaignTime.parse("SE-0061-03-12T07:00:00")
    assert _development_cursor_skip(stale, interval) == "development_backlog_requires_reconciliation"


def test_autonomous_training_rejects_partially_settled_window() -> None:
    interval = CampaignTime.parse("SE-0061-07-24T07:00:00")
    later = CampaignTime.parse("SE-0061-07-25T07:00:00")
    assert _development_cursor_skip(later, interval) == "development_window_already_partially_settled"


def test_autonomous_training_accepts_exact_interval_frontier() -> None:
    interval = CampaignTime.parse("SE-0061-07-24T07:00:00")
    assert _development_cursor_skip(interval, interval) is None
