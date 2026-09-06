from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from shinobi_runtime.api.combat_tactical_movement_integrity import (
    _MOVEMENT_CONTEXT,
    install_combat_tactical_movement_integrity,
)
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.combat.models import ActionProfile, CapabilityProfile, PositionState
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _profile(*, budget_mm: int = 2400, approach_ms: int = 600) -> ActionProfile:
    return ActionProfile(
        method_ref="test_jian",
        effect_kind="physical",
        delivery="direct",
        startup_ms=180,
        external_contact=True,
        speed_score=100,
        damage_channels=("cut",),
        effect_parameters={
            "physical_reach_m": 1.15,
            "approach_distance_mm": budget_mm,
            "approach_time_ms": approach_ms,
            "tactical_movement_intent": "lateral",
        },
    )


def _position(x: int, y: int) -> PositionState:
    return PositionState(zone_ref="test", x_mm=x, y_mm=y, facing_mdeg=0)


def _capability() -> CapabilityProfile:
    return CapabilityProfile(
        offense=100,
        defense=100,
        control=100,
        mobility=100,
        perception=100,
        stealth=0,
        capture=50,
        escape=100,
        reaction=100,
    )


def _copy_runtime_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "runtime/contracts", root / "runtime/contracts")

    route_path = root / "state/martial-world/route-operations.json"
    route_state = json.loads(route_path.read_text())
    movements = route_state.get("movements", {})
    if isinstance(movements, dict):
        route_state["movements"] = {
            ref: row
            for ref, row in movements.items()
            if not (
                isinstance(row, dict)
                and "pc_wei_tang"
                in [
                    str(member_ref)
                    for member_ref in row.get("participant_refs", [])
                    if isinstance(member_ref, str)
                ]
            )
        }
    route_path.write_text(json.dumps(route_state))

    combat_path = root / "state/martial-world/combats.json"
    combat_state = json.loads(combat_path.read_text())
    combats = combat_state.get("combats", {})
    if isinstance(combats, dict):
        combat_state["combats"] = {
            ref: row
            for ref, row in combats.items()
            if not (
                isinstance(row, dict)
                and row.get("status") == "active"
                and "pc_wei_tang"
                in {
                    str(member_ref)
                    for members in row.get("sides", {}).values()
                    if isinstance(members, list)
                    for member_ref in members
                    if isinstance(member_ref, str)
                }
            )
        }
    combat_path.write_text(json.dumps(combat_state))

    roster_path = root / "state/martial-world/people/house_tang.json"
    roster_state = json.loads(roster_path.read_text())
    people = roster_state.get("people", [])
    if isinstance(people, list):
        for person in people:
            if not isinstance(person, dict):
                continue
            if person.get("person_id") in {"pc_wei_tang", "char.zhu"}:
                person["location_ref"] = "site.changan.inn"
            if person.get("person_id") == "char.zhu":
                attrs = person.setdefault("attributes", {})
                attrs.update(
                    {
                        "strength": 10,
                        "speed": 10,
                        "dexterity": 10,
                        "endurance": 10,
                        "perception": 10,
                        "intelligence": 10,
                        "willpower": 10,
                    }
                )
                skills = person.setdefault("martial_skills", {})
                for key in ("sword", "spear", "unarmed", "bow", "hidden_weapons"):
                    skills[key] = 1
    roster_path.write_text(json.dumps(roster_state))
    return root


def _apply_plan(repo: RepositoryStore, plan) -> None:
    for path, content in plan.writes.items():
        repo.replace_image(path, content)


def test_installer_extends_existing_exchange_contract_only() -> None:
    before_types = set(COMMAND_SPECS)
    install_combat_tactical_movement_integrity()
    spec = COMMAND_SPECS["jianghu_combat_resolution"]
    exchange = spec.variants["exchange"]

    assert set(COMMAND_SPECS) == before_types
    assert "movement_intent" in exchange.optional_fields
    assert exchange.payload_hints["movement_intent"] == "<lateral|lateral_left|lateral_right>"


def test_lateral_approach_spends_existing_distance_budget_instead_of_teleporting() -> None:
    from shinobi_runtime.martial_world import exact_combat as exact

    install_combat_tactical_movement_integrity()
    positions = {
        "attacker": _position(0, 0).to_record(),
        "defender": _position(4000, 0).to_record(),
    }
    profile = _profile(budget_mm=2400, approach_ms=600)
    token = _MOVEMENT_CONTEXT.set(
        {"actor_ref": "attacker", "movement_intent": "lateral"}
    )
    try:
        moved, trace = exact.close_attacker_into_reach(
            attacker_ref="attacker",
            defender_ref="defender",
            positions=positions,
            attacker_position=_position(0, 0),
            defender_position=_position(4000, 0),
            attacker_capability=_capability(),
            profile=profile,
            body_refs=("attacker", "defender"),
            obstacles=(),
        )
    finally:
        _MOVEMENT_CONTEXT.reset(token)

    assert trace["moved"] is True
    assert trace["movement_intent"] == "lateral"
    assert trace["lateral_distance_mm"] > 0
    assert trace["distance_mm"] <= 2400
    assert trace["approach_time_ms"] == 600
    assert abs(trace["waypoint_y_mm"]) > 0
    assert moved.y_mm != 0


def test_adaptive_lateral_entry_uses_open_side_when_other_side_is_body_blocked() -> None:
    from shinobi_runtime.martial_world import exact_combat as exact

    install_combat_tactical_movement_integrity()
    positions = {
        "attacker": _position(0, 0).to_record(),
        "defender": _position(3500, 0).to_record(),
        "blocker": _position(0, 700).to_record(),
    }
    profile = _profile(budget_mm=2500, approach_ms=700)
    token = _MOVEMENT_CONTEXT.set(
        {"actor_ref": "attacker", "movement_intent": "lateral"}
    )
    try:
        _moved, trace = exact.close_attacker_into_reach(
            attacker_ref="attacker",
            defender_ref="defender",
            positions=positions,
            attacker_position=_position(0, 0),
            defender_position=_position(3500, 0),
            attacker_capability=_capability(),
            profile=profile,
            body_refs=("attacker", "defender", "blocker"),
            obstacles=(),
        )
    finally:
        _MOVEMENT_CONTEXT.reset(token)

    assert trace["lateral_side"] == "right"
    assert trace["waypoint_y_mm"] < 0


def test_tactical_movement_rejects_multi_exchange_scope_before_resolution() -> None:
    from shinobi_runtime.commands import jianghu_extended as extended

    install_combat_tactical_movement_integrity()
    command = SimpleNamespace(
        actor_id="pc_wei_tang",
        payload={
            "action": "exchange",
            "combat_ref": "combat.test",
            "movement_intent": "lateral",
            "exchange_count": 2,
        },
    )
    with pytest.raises(CommandRejectedError):
        extended.JianghuExtendedCommandsMixin._jianghu_combat_core_resolution(
            object(), command, {}, object()
        )


def test_command_preview_accepts_lateral_melee_entry_end_to_end(tmp_path: Path) -> None:
    install_combat_tactical_movement_integrity()
    root = _copy_runtime_repository(tmp_path)
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    combat_ref = "combat.test.tactical-movement"

    start = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="test.tactical-movement.start",
        actor_id=meta["player_id"],
        command_type="jianghu_combat_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-09-06T10:00:00Z",
        payload={
            "action": "start",
            "combat_ref": combat_ref,
            "side_a_refs": [meta["player_id"]],
            "side_b_refs": ["char.zhu"],
            "objective": {"kind": "eliminate", "target_refs": ["char.zhu"]},
            "awareness_mode": "mutual",
            "initial_range_band": 1,
        },
        mode="gameplay",
    )
    _apply_plan(repo, planner.plan(start))

    current = repo.read_json("state/meta.json")
    exchange = CommandEnvelope(
        campaign_id=current["campaign_id"],
        request_id="test.tactical-movement.exchange",
        actor_id=current["player_id"],
        command_type="jianghu_combat_resolution",
        expected_revision=current["revision"],
        submitted_at="2026-09-06T10:00:01Z",
        payload={
            "action": "exchange",
            "combat_ref": combat_ref,
            "action_kind": "cut",
            "weapon_ref": "auto",
            "movement_intent": "lateral",
        },
        mode="gameplay",
    )

    preview = planner.preview(exchange)
    assert preview.status == "ready"
    assert preview.code == "jianghu_combat_exchange_resolved"
    plan = planner.plan(exchange)
    player_events = [
        row for row in plan.result["events"] if row.get("actor_ref") == current["player_id"]
    ]
    assert player_events
    assert plan.result["world_time"] > current["time"]
