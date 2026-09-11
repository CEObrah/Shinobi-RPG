import copy
import json
from datetime import datetime

from shinobi_runtime.commands.jianghu_time import JianghuTimeCommandsMixin
from shinobi_runtime.martial_world.rest_practice import (
    evening_practice_hours_milli,
    journey_hour_budget,
    practice_domain,
)
from shinobi_runtime.store import RepositoryStore
from fixture_support import combat_state_without_people, route_state_without_people


ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
_ROSTER = "state/martial-world/people/house_tang.json"
_ROUTE = "state/martial-world/route-operations.json"
_COMBATS = "state/martial-world/combats.json"


class _OverlayRepository:
    def __init__(self, base, records):
        self.base = base
        self.records = records

    def read_json(self, path):
        key = str(path)
        if key in self.records:
            return copy.deepcopy(self.records[key])
        return self.base.read_json(path)


class _TimeProbe(JianghuTimeCommandsMixin):
    def __init__(self, repository):
        self.repository = repository


def _wei_at_inn_with_sword_focus():
    base = RepositoryStore(ROOT)
    roster = copy.deepcopy(base.read_json(_ROSTER))
    wei = next(row for row in roster["people"] if row.get("person_id") == "pc_wei_tang")
    wei["location_ref"] = "site.luoyang.inn"
    state = copy.deepcopy(wei.get("training_state", {})) if isinstance(wei.get("training_state"), dict) else {}
    state["focus"] = "sword"
    state.pop("institutional_paused", None)
    wei["training_state"] = state
    # This fixture tests the rest-practice clock, not the live player's current
    # high-end sword plateau. Keep the synthetic skill in a trainable band.
    wei.setdefault("martial_skills", {})["sword"] = 60
    wei["fatigue_milli"] = 0
    wei["health"] = {"status": "ready", "consciousness": 100}
    return base, roster


def test_journey_elapsed_clock_is_not_all_active_route_time():
    day = journey_hour_budget(24_000)
    assert day == {
        "elapsed_hours_milli": 24_000,
        "active_route_hours_milli": 8_000,
        "rest_practice_hours_milli": 1_000,
        "other_rest_hours_milli": 15_000,
    }
    short = journey_hour_budget(6_000)
    assert short["active_route_hours_milli"] == 6_000
    assert short["rest_practice_hours_milli"] == 0


def test_evening_window_is_exact_and_chunk_additive():
    start = datetime.fromisoformat("0061-08-14T18:00:00")
    middle = datetime.fromisoformat("0061-08-14T20:00:00")
    end = datetime.fromisoformat("0061-08-14T22:00:00")
    whole = evening_practice_hours_milli(start, end)
    assert whole == 2_000
    assert evening_practice_hours_milli(start, middle) + evening_practice_hours_milli(middle, end) == whole


def test_explicit_personal_focus_overrides_retinue_role_but_role_fills_idle_policy():
    person = {
        "training_state": {"focus": "sword"},
        "martial_skills": {"sword": 70, "unarmed": 90},
    }
    assert practice_domain(person, retinue_role="field_medic") == "sword"
    person["training_state"] = {}
    assert practice_domain(person, retinue_role="field_medic") == "professional:medicine"
    assert practice_domain(person, retinue_role="scout") == "stealth_scouting"
    assert practice_domain(person, retinue_role="field_deputy") == "command"
    assert practice_domain(person, retinue_role="protective_guard") == "unarmed"


def test_remote_player_is_paused_before_world_training_can_settle():
    base, roster = _wei_at_inn_with_sword_focus()
    route_state = route_state_without_people(base.read_json(_ROUTE), "pc_wei_tang")
    combat_state = combat_state_without_people(base.read_json(_COMBATS), "pc_wei_tang")
    probe = _TimeProbe(_OverlayRepository(base, {_ROSTER: roster, _ROUTE: route_state, _COMBATS: combat_state}))
    records = probe._remote_training_pause_records("pc_wei_tang")
    assert _ROSTER in records
    wei = next(row for row in records[_ROSTER]["people"] if row.get("person_id") == "pc_wei_tang")
    assert wei["training_state"]["institutional_paused"] is True


def test_safe_inn_wait_uses_only_evening_hours_for_existing_focus():
    base, roster = _wei_at_inn_with_sword_focus()
    route_state = route_state_without_people(base.read_json(_ROUTE), "pc_wei_tang")
    combat_state = combat_state_without_people(base.read_json(_COMBATS), "pc_wei_tang")
    probe = _TimeProbe(_OverlayRepository(base, {_ROSTER: roster, _ROUTE: route_state, _COMBATS: combat_state}))
    paused = probe._remote_training_pause_records("pc_wei_tang")
    writes = {
        path: json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        for path, row in paused.items()
    }
    summary = probe._safe_lodging_practice(
        writes,
        scene={"location_id": "site.luoyang.inn"},
        start=datetime.fromisoformat("0061-08-14T18:00:00"),
        end=datetime.fromisoformat("0061-08-14T22:00:00"),
        player_id="pc_wei_tang",
    )
    assert summary["practice_hours_milli"] == 2_000
    row = summary["people"]["pc_wei_tang"]
    assert row["domain"] == "sword"
    assert row["gain_milli"] > 0
    after = json.loads(writes[_ROSTER].decode("utf-8"))
    wei = next(person for person in after["people"] if person.get("person_id") == "pc_wei_tang")
    assert "institutional_paused" not in wei.get("training_state", {})
    assert wei["training_state"]["evidence_milli"]["sword"] > 0


def test_safe_lodging_practice_skips_person_in_exact_combat_at_same_site():
    base, roster = _wei_at_inn_with_sword_focus()
    route_state = route_state_without_people(base.read_json(_ROUTE), "pc_wei_tang")
    combat_state = combat_state_without_people(base.read_json(_COMBATS), "pc_wei_tang")
    combat_state.setdefault("combats", {})["combat.test.inn"] = {
        "status": "active",
        "zone_ref": "site.luoyang.inn",
        "sides": {"side_a": ["pc_wei_tang"], "side_b": ["opponent.test"]},
        "combatants": {"pc_wei_tang": {}, "opponent.test": {}},
    }
    probe = _TimeProbe(_OverlayRepository(base, {_ROSTER: roster, _ROUTE: route_state, _COMBATS: combat_state}))
    writes = {_ROSTER: json.dumps(roster, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")}

    summary = probe._safe_lodging_practice(
        writes,
        scene={"location_id": "site.luoyang.inn"},
        start=datetime.fromisoformat("0061-08-14T18:00:00"),
        end=datetime.fromisoformat("0061-08-14T22:00:00"),
        player_id="pc_wei_tang",
    )

    assert summary == {}


def test_safe_lodging_practice_does_not_double_credit_route_lodging_rest():
    base, roster = _wei_at_inn_with_sword_focus()
    route_state = route_state_without_people(base.read_json(_ROUTE), "pc_wei_tang")
    route_state.setdefault("movements", {})["movement.test.lodging_rest"] = {
        "movement_kind": "personal_travel",
        "route_ref": "route.luoyang.huashan",
        "origin_place_ref": "luoyang",
        "destination_place_ref": "huashan",
        "participant_refs": ["pc_wei_tang"],
        "started_at": "0061-08-14T12:00:00",
        "status": "lodging_rest",
        "rest_place_ref": "site.luoyang.inn",
    }
    combat_state = combat_state_without_people(base.read_json(_COMBATS), "pc_wei_tang")
    probe = _TimeProbe(_OverlayRepository(base, {_ROSTER: roster, _ROUTE: route_state, _COMBATS: combat_state}))
    writes = {_ROSTER: json.dumps(roster, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")}

    summary = probe._safe_lodging_practice(
        writes,
        scene={"location_id": "site.luoyang.inn"},
        start=datetime.fromisoformat("0061-08-14T18:00:00"),
        end=datetime.fromisoformat("0061-08-14T22:00:00"),
        player_id="pc_wei_tang",
    )

    assert summary == {}
