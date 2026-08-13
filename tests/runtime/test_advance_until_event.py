from shinobi_runtime.commands.downtime_until_event import install_downtime_until_event
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS


def test_advance_until_event_is_registered():
    install_downtime_until_event()
    assert "advance_until_event" in COMMAND_SPECS
    assert "advance_until_event" in RepositoryCommandPlanner.COMMAND_TYPES
    assert callable(getattr(RepositoryCommandPlanner,"_advance_until_event",None))
