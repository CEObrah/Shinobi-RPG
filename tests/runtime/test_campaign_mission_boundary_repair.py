import json
from types import SimpleNamespace

from shinobi_runtime.commands import campaign_mission_boundary_repair as module
from shinobi_runtime.sim.events import CampaignTime, EventQueue
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry, SchedulerHost, one_shot_event


NOW = CampaignTime.parse("SE-0061-07-16T07:00:00")
MISSION = "mission.offer.test"
MISSION_PATH = "state/mission/mission.offer.test.json"
TEAM_PATH = "state/team/team.blackhound.json"
FACTION_PATH = "state/faction/konoha.json"


class FakeMissionOwner:
    def __init__(self):
        self.mission_id = MISSION
        self.mission = SimpleNamespace(
            state="active",
            participant_refs=("char.a", "char.b"),
        )
        self.operation_ref = "team.blackhound"
        self.issuer_ref = "faction.konoha_mission_office"

    @staticmethod
    def from_record(record):
        return FakeMissionOwner()


class Repo:
    def __init__(self):
        self.records = {
            MISSION_PATH: {"id": MISSION},
            TEAM_PATH: {
                "schema": "exact-team",
                "status": "active",
                "leader_ref": "pc_wei_tang",
                "member_refs": ["pc_wei_tang", "char.a", "char.b"],
            },
            FACTION_PATH: {
                "faction": {
                    "plan_state": {
                        "autonomous_mission_refs": [MISSION],
                        "wake_required_mission_refs": [],
                    }
                }
            },
            "state/scene.json": {
                "world_time": str(NOW),
                "location_id": "place.sword_manor",
                "time_passage_allowed": False,
                "decision_required": f"The boundary {MISSION} requires an explicit player response.",
            },
        }

    def read_json(self, path):
        return self.records[path]

    def digest(self, path):
        assert path in self.records
        return "digest:" + path


class Planner:
    meta_path = "state/meta.json"
    scene_path = "state/scene.json"
    scheduler_path = "state/time/causal-scheduler.json"

    def __init__(self):
        self.repository = Repo()
        host_id = "host." + MISSION
        self.scheduler = CausalSchedulerRegistry(
            world_time=NOW,
            hosts={},
            queue=EventQueue(),
            seeded_at=NOW,
            bootstrap_source="test",
            metrics={},
        )
        self.scheduler.add_host(
            SchedulerHost(
                state=HostState(
                    host_id=host_id,
                    kind="mission",
                    resolved_through=NOW.add_seconds(-1),
                    safe_through=NOW,
                    handler_ref="causal.scheduler",
                    rng_namespace=MISSION,
                    next_due=None,
                ),
                authority_kind="mission",
                owner_ref=MISSION_PATH,
                metadata={"mission_id": MISSION},
            )
        )
        self.scheduler.upsert_event(
            one_shot_event(
                kind="mission.boundary",
                identity=MISSION,
                source_host=host_id,
                target_host=host_id,
                due_at=NOW,
                payload={"mission_id": MISSION, "owner_ref": MISSION_PATH},
                visibility="player_known",
                requires_player=True,
            )
        )

    def _resolve_covered_owner_view(self, ref, cache=None):
        assert ref == "faction.konoha_mission_office"
        return FACTION_PATH, self.repository.digest(FACTION_PATH), self.repository.read_json(FACTION_PATH)

    def _exact_team(self, ref):
        assert ref == "team.blackhound"
        return TEAM_PATH, self.repository.read_json(TEAM_PATH)

    def _scene_base(self, current_time):
        return self.repository.read_json(self.scene_path)

    def _load_scheduler(self, **kwargs):
        return self.scheduler

    def _sync_mission_scheduler(self, scheduler, *, owner, path, current_time):
        host_id = "host." + owner.mission_id
        scheduler.queue.replace(event for event in scheduler.queue.snapshot() if event.target_host != host_id)
        scheduler.hosts.pop(host_id, None)

    def _world_events(self):
        return {"events": []}

    def _append_semantic_event(self, registry, **kwargs):
        registry["events"].append({"id": "event.repair", "kind": kwargs["kind"]})
        return "event.repair"

    def _meta_after(self, meta, command, world_time):
        return {**meta, "revision": meta["revision"] + 1, "time": str(world_time)}

    def _scheduler_write_images(self, scheduler):
        return {self.scheduler_path: json.dumps(scheduler.to_record()).encode()}

    def _world_event_writes(self, events):
        return {"state/reg/world-events.json": json.dumps(events).encode()}

    def _prune_noop_writes(self, writes):
        return writes


class Command:
    command_type = "campaign_mission_boundary_repair"
    actor_id = "pc_wei_tang"
    payload = {"mission_id": MISSION}


def test_guarded_repair_only_clears_stale_scene_and_scheduler(monkeypatch):
    monkeypatch.setattr(module, "MissionOwner", FakeMissionOwner)
    planner = Planner()
    meta = {
        "player_id": "pc_wei_tang",
        "revision": 176,
        "time": str(NOW),
    }

    plan = module._repair(planner, Command(), meta, NOW)

    assert plan.result["status"] == "repaired"
    assert MISSION_PATH not in plan.writes
    repaired_scene = json.loads(plan.writes[planner.scene_path])
    assert repaired_scene["decision_required"] is None
    assert repaired_scene["time_passage_allowed"] is True
    assert "host." + MISSION not in planner.scheduler.hosts
    assert not planner.scheduler.queue.snapshot()
