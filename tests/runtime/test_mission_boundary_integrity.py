from types import SimpleNamespace

from shinobi_runtime.commands.mission_boundary_integrity import _sync_mission_scheduler
from shinobi_runtime.sim.events import CampaignTime, EventQueue
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry, SchedulerHost, one_shot_event


class Repo:
    def __init__(self, player_id="pc_wei_tang"):
        self.player_id = player_id

    def read_json(self, path):
        assert path == "state/meta.json"
        return {"player_id": self.player_id}


class Planner:
    meta_path = "state/meta.json"

    def __init__(self):
        self.repository = Repo()


def scheduler(now):
    return CausalSchedulerRegistry(
        world_time=now,
        hosts={},
        queue=EventQueue(),
        seeded_at=now,
        bootstrap_source="test",
        metrics={},
    )


def owner(*, participants, now, mission_id="mission.test"):
    return SimpleNamespace(
        mission_id=mission_id,
        mission=SimpleNamespace(state="active", participant_refs=tuple(participants)),
        next_due_at=None,
        deadline_at=now.add_seconds(3600),
    )


def test_player_participant_keeps_player_boundary():
    now = CampaignTime.parse("SE-0061-07-16T07:00:00")
    state = scheduler(now)
    mission = owner(participants=("pc_wei_tang", "char.a"), now=now)

    _sync_mission_scheduler(
        Planner(), state, owner=mission, path="state/mission/mission.test.json", current_time=now
    )

    assert "host.mission.test" in state.hosts
    events = state.queue.snapshot()
    assert len(events) == 1
    assert events[0].kind == "mission.boundary"
    assert events[0].requires_player is True
    assert events[0].payload["mission_id"] == "mission.test"


def test_nonplayer_delegated_mission_removes_stale_player_boundary():
    now = CampaignTime.parse("SE-0061-07-16T07:00:00")
    due = now.add_seconds(3600)
    state = scheduler(now)
    host_id = "host.mission.test"
    state.add_host(
        SchedulerHost(
            state=HostState(
                host_id=host_id,
                kind="mission",
                resolved_through=now,
                safe_through=due.add_seconds(-1),
                handler_ref="causal.scheduler",
                rng_namespace="mission.test",
                next_due=None,
            ),
            authority_kind="mission",
            owner_ref="state/mission/mission.test.json",
            metadata={"mission_id": "mission.test"},
        )
    )
    state.upsert_event(
        one_shot_event(
            kind="mission.boundary",
            identity="mission.test",
            source_host=host_id,
            target_host=host_id,
            due_at=due,
            payload={"mission_id": "mission.test", "owner_ref": "state/mission/mission.test.json"},
            visibility="player_known",
            requires_player=True,
        )
    )
    mission = owner(participants=("char.a", "char.b"), now=now)

    _sync_mission_scheduler(
        Planner(), state, owner=mission, path="state/mission/mission.test.json", current_time=now
    )

    assert host_id not in state.hosts
    assert all(event.target_host != host_id for event in state.queue.snapshot())
    assert state.metrics["pending_event_count"] == 0
