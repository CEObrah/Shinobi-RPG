import copy
import json
from pathlib import Path

from shinobi_runtime.martial_world.route_frontier import _route_interception_objective

ROOT = Path(__file__).resolve().parents[2]


def test_live_outbound_grain_muster_does_not_protect_nonexistent_cargo():
    routes = json.loads((ROOT / "state/martial-world/route-operations.json").read_text())["movements"]
    ref = "escort_muster:0e52cfa45f5bbea72ba0"
    movement = routes[ref]
    assert movement["movement_kind"] == "player_strategic_travel"
    assert movement.get("destination_place_ref") == "huashan"
    assert _route_interception_objective(ref, movement, cargo_value_cash=0)["kind"] == "preserve_route_mission"


def test_actual_carried_quantity_changes_interception_stakes_to_cargo():
    movement = {"movement_kind": "player_strategic_travel", "item_ref": "food_ration_day", "quantity": 12}
    assert _route_interception_objective("movement.test", movement, cargo_value_cash=0)["kind"] == "protect_cargo"


def test_person_protection_does_not_masquerade_as_cargo():
    movement = {"movement_kind": "player_strategic_travel", "protected_person_refs": ["person.a"]}
    objective = _route_interception_objective("movement.people", movement, cargo_value_cash=0)
    assert objective["kind"] == "protect_party"
    assert objective["protected_person_refs"] == ["person.a"]
