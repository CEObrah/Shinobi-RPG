from types import SimpleNamespace

from shinobi_runtime.commands.jianghu_travel_team import JianghuTravelTeamCommandsMixin
from shinobi_runtime.sim.events import CampaignTime


class _Repository:
    def __init__(self):
        self.records = {
            "game/data/martial-world/local-sites.json": {
                "sites": {
                    "site.start": {"parent_place_ref": "place.start"},
                    "site.local": {"parent_place_ref": "place.start"},
                    "site.end": {"parent_place_ref": "place.end"},
                }
            },
            "state/martial-world/deployments.json": {
                "deployments": {
                    "retinue.wei": {
                        "operation_kind": "standing_retinue",
                        "status": "active",
                        "leader_ref": "pc_wei_tang",
                        "member_refs": ["team.medic", "team.scout", "team.guard"],
                    }
                }
            },
            "state/martial-world/equipment-ledger.json": {},
            "game/data/martial-world/equipment.json": {},
            "game/data/martial-world/geography.json": {"places": {}},
            "state/scene.json": {
                "location_id": "site.start",
                "present_person_ids": ["pc_wei_tang"],
                "visible_person_ids": ["pc_wei_tang"],
            },
        }

    def read_json(self, path):
        return self.records[str(path)]


class _Harness(JianghuTravelTeamCommandsMixin):
    scene_path = "state/scene.json"

    def __init__(self):
        self.repository = _Repository()
        self.captured_party = None
        self.people = {
            ref: {
                "person_id": ref,
                "faction_ref": "house_tang",
                "location_ref": "site.start",
                "personal_cash": 100,
                "attributes": {"strength": 60, "endurance": 60},
                "health": {"status": "ready", "consciousness": 100, "injuries": []},
            }
            for ref in ("pc_wei_tang", "team.medic", "team.scout", "team.guard")
        }
        self.roster = {
            "schema": "jianghu-person-lite-roster-1.0",
            "faction_ref": "house_tang",
            "people": [dict(self.people[ref]) for ref in self.people],
        }

    def _person(self, ref):
        refs = list(self.people)
        return "state/martial-world/people/house_tang.json", self.roster, refs.index(ref), dict(self.people[ref])

    def _require_person_available_for_activity(self, _ref):
        return None

    def _person_available_for_activity(self, _ref):
        return True

    def _timed_person_activity_plan(self, command, meta, current_time, **kwargs):
        self.captured_party = list(kwargs["person_refs"])
        extra = dict(kwargs.get("staged_records", {}))
        extra["state/martial-world/people/house_tang.json"] = {
            "schema": "jianghu-person-lite-roster-1.0",
            "faction_ref": "house_tang",
            "people": [dict(self.people[ref]) for ref in self.people],
        }
        return SimpleNamespace(result={"world_time": "SE-0061-01-01T01:00:00"}), extra, current_time

    def _time_after_record(self, _plan, _path, fallback):
        return dict(fallback)

    def _combine_time_plan(self, command, time_plan, *, extra_records, code, result, scene_override=None):
        return {
            "code": code,
            "result": result,
            "extra_records": extra_records,
            "scene": scene_override,
        }


def _patch_common(monkeypatch):
    import shinobi_runtime.commands.jianghu_travel_team as travel_team

    monkeypatch.setattr(
        travel_team,
        "functional_capacity_factors",
        lambda _injuries: {"walking_milli": 1000, "mounted_stability_milli": 1000},
    )
    monkeypatch.setattr(travel_team, "effective_person_loadout", lambda _ledger, _ref: {"items": {}})
    monkeypatch.setattr(travel_team, "carried_mass_kg", lambda _items, _catalog: 0)
    monkeypatch.setattr(
        travel_team,
        "encumbrance_effects",
        lambda **_kwargs: {"movement_factor_milli": 1000},
    )
    return travel_team


def _assert_full_party(result, harness, destination):
    expected = ["pc_wei_tang", "team.medic", "team.scout", "team.guard"]
    assert harness.captured_party == expected
    assert result["result"]["travel_party_refs"] == expected
    assert result["result"]["travel_party_count"] == 4
    assert result["scene"]["present_person_ids"] == expected
    assert result["scene"]["visible_person_ids"] == expected
    final_people = result["extra_records"]["state/martial-world/people/house_tang.json"]["people"]
    assert {row["location_ref"] for row in final_people} == {destination}


def test_strategic_travel_moves_wei_and_three_permanent_team_members(monkeypatch):
    travel_team = _patch_common(monkeypatch)
    import shinobi_runtime.martial_world.live_state as live_state

    monkeypatch.setattr(
        travel_team,
        "travel_plan",
        lambda **_kwargs: {
            "travel_hours": 1.0,
            "distance_km": 10.0,
            "toll_cash": 0,
            "segments": [],
            "nodes": [],
        },
    )
    monkeypatch.setattr(live_state, "set_roster_person", lambda roster, _ordinal, _person: roster)

    harness = _Harness()
    command = SimpleNamespace(
        actor_id="pc_wei_tang",
        request_id="travel-team-test",
        command_type="jianghu_strategic_travel_resolution",
        payload={"destination_site_ref": "site.end", "mode": "foot"},
    )
    result = harness._jianghu_strategic_travel_resolution(
        command,
        {"world_seed": "test"},
        CampaignTime.parse("SE-0061-01-01T00:00:00"),
    )
    _assert_full_party(result, harness, "site.end")


def test_local_travel_keeps_active_permanent_team_with_wei(monkeypatch):
    travel_team = _patch_common(monkeypatch)
    monkeypatch.setattr(
        travel_team,
        "local_travel_quote",
        lambda **_kwargs: {
            "walking_minutes": 10,
            "distance_m": 700,
            "from_site_ref": "site.start",
            "to_site_ref": "site.local",
        },
    )

    harness = _Harness()
    command = SimpleNamespace(
        actor_id="pc_wei_tang",
        request_id="local-travel-team-test",
        command_type="jianghu_local_travel_resolution",
        payload={"destination_site_ref": "site.local"},
    )
    result = harness._jianghu_local_travel_resolution(
        command,
        {"world_seed": "test"},
        CampaignTime.parse("SE-0061-01-01T00:00:00"),
    )
    _assert_full_party(result, harness, "site.local")
