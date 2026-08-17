from types import SimpleNamespace

from shinobi_runtime.api import player_command_mission_projection as module


class Repo:
    def __init__(self):
        self.records = {
            "state/mission/mission.delegated.json": {"id": "mission.delegated"},
            "state/mission/mission.unrelated.json": {"id": "mission.unrelated"},
        }

    def read_json(self, path):
        return self.records[path]


class Ops:
    def __init__(self):
        self.repository = Repo()
        self.teams = {
            "team.blackhound": {
                "schema": "exact-team",
                "status": "active",
                "leader_ref": "pc_wei_tang",
                "member_refs": ["pc_wei_tang", "char.a", "char.b"],
            },
            "team.other": {
                "schema": "exact-team",
                "status": "active",
                "leader_ref": "char.other",
                "member_refs": ["char.other", "char.c"],
            },
        }
        self.index = {
            "char.a": ("mission.delegated",),
            "char.b": ("mission.delegated",),
            "char.c": ("mission.unrelated",),
            "pc_wei_tang": (),
        }

    def _owner_record(self, ref):
        return f"state/team/{ref}.json", self.teams[ref]

    def _mission_context_index(self):
        return self.index


class FakeMissionOwner:
    @staticmethod
    def from_record(record):
        mission_id = record["id"]
        if mission_id == "mission.delegated":
            return SimpleNamespace(
                mission=SimpleNamespace(state="active", participant_refs=("char.a", "char.b")),
                operation_ref="team.blackhound",
                briefing=object(),
                to_record=lambda: {"mission_id": mission_id, "state": "active"},
            )
        return SimpleNamespace(
            mission=SimpleNamespace(state="active", participant_refs=("char.c",)),
            operation_ref="team.other",
            briefing=object(),
            to_record=lambda: {"mission_id": mission_id, "state": "active"},
        )


def test_player_led_team_delegated_mission_remains_readable(monkeypatch):
    ops = Ops()
    monkeypatch.setattr(module, "team_refs_for_member", lambda repository, player_id: ("team.blackhound",))
    monkeypatch.setattr(module, "participant_current_refs", lambda index, ref: index.get(ref, ()))
    monkeypatch.setattr(module, "MissionOwner", FakeMissionOwner)

    mission_ids, briefing_ids, truncated = module._player_command_mission_context(
        ops, "pc_wei_tang"
    )

    assert mission_ids == ("mission.delegated",)
    assert briefing_ids == ("mission.delegated",)
    assert truncated is False
    assert module._mission_is_player_command_readable(
        ops, "pc_wei_tang", "mission.delegated"
    ) is True
    assert module._mission_is_player_command_readable(
        ops, "pc_wei_tang", "mission.unrelated"
    ) is False


def test_unrelated_npc_mission_is_not_exposed(monkeypatch):
    ops = Ops()
    monkeypatch.setattr(module, "team_refs_for_member", lambda repository, player_id: ("team.other",))
    monkeypatch.setattr(module, "participant_current_refs", lambda index, ref: index.get(ref, ()))
    monkeypatch.setattr(module, "MissionOwner", FakeMissionOwner)

    mission_ids, briefing_ids, truncated = module._player_command_mission_context(
        ops, "pc_wei_tang"
    )

    assert mission_ids == ()
    assert briefing_ids == ()
    assert truncated is False
