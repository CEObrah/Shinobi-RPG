"""Closed forward repair for the current Black Lance full-fresh replay.

The combat engine was corrected after the packaged campaign had already advanced
three bad combat revisions beyond the certified revision-97 handoff.  The old
transaction/WAL chain is no longer recoverable, so generic historical rollback
correctly fails closed.  This module exposes exactly one non-configurable repair
identity that recognizes the exact bad revision-100 state tree plus the existing
release contract, removes only consequences mechanically attributable to that
invalid combat span, rebuilds the exact-combat owner with the current engine,
and commits the result as a new forward revision.

Callers cannot choose combat refs, people, paths, quantities, timestamps, or
restore revisions.  Any state drift disables the anchor.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.martial_world.equipment_state import (
    compact_equipment_ledger,
    hydrate_equipment_ledger,
)
from shinobi_runtime.martial_world.exact_combat import initialize_combat
from shinobi_runtime.release_baseline import load_release_contract, state_tree_sha256
from shinobi_runtime.tx.errors import StaleRevisionError

BLACK_LANCE_COMBAT_RESET_ANCHOR = "jianghu.black_lance.fullfresh_r100_repair.v1"
_CAMPAIGN_ID = "jianghu-wei-main"
_PLAYER_ID = "pc_wei_tang"
_META_PATH = "state/meta.json"
_SCHEDULER_PATH = "state/martial-world/scheduler.json"
_COMBATS_PATH = "state/martial-world/combats.json"
_EQUIPMENT_PATH = "state/martial-world/equipment-ledger.json"
_SOCIAL_PATH = "state/martial-world/social.json"
_ROUTE_PATH = "state/martial-world/route-operations.json"
_HOUSE_ROSTER = "state/martial-world/people/house_tang.json"
_BLACK_LANCE_ROSTER = "state/martial-world/people/black_lance_company.json"
_COMBAT_REF = "combat:contact:escort_muster:0e52cfa45f5bbea72ba0:0061-09-27:black_lance_company"
_MOVEMENT_REF = "escort_muster:0e52cfa45f5bbea72ba0"
_CONTACT_REF = "contact:escort_muster:0e52cfa45f5bbea72ba0:0061-09-27:black_lance_company"
_BAD_REVISION = 100
_BAD_TIME = "SE-0061-09-27T21:22:34"
_BAD_STATE_SHA256 = "5d340ff0bb3f22d80c6b28fa96596e4bd346d29ecfe57b02df71795fb8c0954e"
_BASELINE_ID = "black-lance-fullfresh-r97-20260908"
_BASELINE_REVISION = 97
_HANDOFF_TIME = "SE-0061-09-27T21:21:45"
_PLAYER_NEEDLES = 19
_PLAYER_JIAN_CONDITION = 1000

_SIDE_A = (
    "pc_wei_tang",
    "mw.person.house_tang.1032",
    "mw.person.house_tang.1050",
    "mw.person.house_tang.1030",
    "mw.person.house_tang.1006",
    "mw.person.house_tang.1027",
    "mw.person.house_tang.1037",
    "mw.person.house_tang.1025",
    "mw.person.house_tang.1045",
    "mw.person.house_tang.1001",
    "mw.person.house_tang.1017",
    "mw.person.house_tang.1020",
)
_SIDE_B = (
    "mw.person.black_lance_company.0041",
    "mw.person.black_lance_company.0035",
    "mw.person.black_lance_company.0004",
    "mw.person.black_lance_company.0048",
    "mw.person.black_lance_company.0037",
    "mw.person.black_lance_company.0066",
    "mw.person.black_lance_company.0085",
    "mw.person.black_lance_company.0092",
    "mw.person.black_lance_company.0012",
    "mw.person.black_lance_company.0026",
    "mw.person.black_lance_company.0029",
    "mw.person.black_lance_company.0077",
    "mw.person.black_lance_company.0021",
    "mw.person.black_lance_company.0031",
    "mw.person.black_lance_company.0060",
    "mw.person.black_lance_company.0079",
    "mw.person.black_lance_company.0044",
    "mw.person.black_lance_company.0050",
)
_PARTICIPANTS = frozenset(_SIDE_A + _SIDE_B)


@dataclass(frozen=True)
class BlackLanceResetBuild:
    writes: Mapping[str, Optional[bytes]]
    result: Mapping[str, Any]
    affected_refs: tuple[str, ...]


@dataclass(frozen=True)
class _BlackLanceResetRequest:
    historical_anchor: str


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _release_contract_matches(repository: Any) -> bool:
    try:
        contract = load_release_contract(repository.root)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return (
        contract.get("release_baseline_id") == _BASELINE_ID
        and contract.get("campaign_id") == _CAMPAIGN_ID
        and contract.get("player_id") == _PLAYER_ID
        and contract.get("revision") == _BASELINE_REVISION
        and contract.get("world_time") == _HANDOFF_TIME
        and contract.get("active_combat_ref") == _COMBAT_REF
        and contract.get("active_combat_elapsed_ms") == 0
        and sorted(contract.get("active_combat_side_sizes", [])) == [12, 18]
        and contract.get("active_combatant_count") == 30
        and contract.get("player_qi") == 150
        and contract.get("player_needles") == _PLAYER_NEEDLES
        and contract.get("player_jian_condition_milli") == _PLAYER_JIAN_CONDITION
    )


def _require_exact_bad_state(repository: Any, expected_revision: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if expected_revision != _BAD_REVISION:
        raise OperationError(409, "black_lance_reset_revision_mismatch")
    if not _release_contract_matches(repository):
        raise OperationError(409, "black_lance_reset_release_contract_mismatch")
    if state_tree_sha256(repository.root) != _BAD_STATE_SHA256:
        raise OperationError(409, "black_lance_reset_state_mismatch")

    meta = repository.read_json(_META_PATH)
    if not isinstance(meta, Mapping) or meta.get("campaign_id") != _CAMPAIGN_ID or meta.get("revision") != _BAD_REVISION or meta.get("time") != _BAD_TIME:
        raise OperationError(409, "black_lance_reset_state_mismatch")

    combats = repository.read_json(_COMBATS_PATH)
    rows = combats.get("combats") if isinstance(combats, Mapping) else None
    combat = rows.get(_COMBAT_REF) if isinstance(rows, Mapping) else None
    if not isinstance(combat, Mapping):
        raise OperationError(409, "black_lance_reset_combat_mismatch")
    sides = combat.get("sides") if isinstance(combat.get("sides"), Mapping) else {}
    if (
        combat.get("status") != "active"
        or int(combat.get("elapsed_ms", -1)) <= 0
        or tuple(sides.get("side_a", ())) != _SIDE_A
        or tuple(sides.get("side_b", ())) != _SIDE_B
    ):
        raise OperationError(409, "black_lance_reset_combat_mismatch")

    route = repository.read_json(_ROUTE_PATH)
    movements = route.get("movements") if isinstance(route, Mapping) else None
    contacts = route.get("contacts") if isinstance(route, Mapping) else None
    movement = movements.get(_MOVEMENT_REF) if isinstance(movements, Mapping) else None
    contact = contacts.get(_CONTACT_REF) if isinstance(contacts, Mapping) else None
    if (
        not isinstance(movement, Mapping)
        or not isinstance(contact, Mapping)
        or movement.get("status") != "contact_pending"
        or movement.get("combat_ref") != _COMBAT_REF
        or contact.get("status") != "active"
        or contact.get("combat_ref") != _COMBAT_REF
        or tuple(contact.get("attacker_refs", ())) != _SIDE_B
        or int(contact.get("field_equipment_materialized_count", -1)) != len(_SIDE_B)
    ):
        raise OperationError(409, "black_lance_reset_route_mismatch")
    return copy.deepcopy(dict(combats)), copy.deepcopy(dict(combat))


def _reset_roster(repository: Any, path: str, participant_refs: frozenset[str]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    owner = copy.deepcopy(repository.read_json(path))
    rows = owner.get("people") if isinstance(owner, Mapping) else None
    if not isinstance(rows, list):
        raise OperationError(409, "black_lance_reset_roster_invalid")
    found: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            continue
        ref = str(raw.get("person_id") or "")
        if ref not in participant_refs:
            continue
        row = copy.deepcopy(dict(raw))
        health = row.get("health")
        if isinstance(health, Mapping):
            allowed_health = {"status", "injuries", "toxicity_milli", "blood_lost_ml", "shock", "consciousness"}
            if set(health) - allowed_health:
                raise OperationError(409, "black_lance_reset_unexpected_health_state")
        elif health is not None:
            raise OperationError(409, "black_lance_reset_unexpected_health_state")
        row.pop("health", None)
        row.pop("fatigue_milli", None)
        row.pop("current_qi", None)
        row.pop("current_qi_milli", None)
        row.pop("poison_burdens", None)
        row.pop("pending_poison_burdens", None)
        rows[index] = row
        found[ref] = row
    if set(found) != set(participant_refs):
        raise OperationError(409, "black_lance_reset_participant_missing")
    return owner, found


def _reset_equipment(repository: Any) -> dict[str, Any]:
    raw = repository.read_json(_EQUIPMENT_PATH)
    if not isinstance(raw, Mapping):
        raise OperationError(409, "black_lance_reset_equipment_invalid")
    ledger = hydrate_equipment_ledger(raw)
    loadouts = ledger.get("person_loadouts")
    if not isinstance(loadouts, dict):
        raise OperationError(409, "black_lance_reset_equipment_invalid")
    for ref in _PARTICIPANTS:
        row = loadouts.get(ref)
        if not isinstance(row, Mapping):
            raise OperationError(409, "black_lance_reset_equipment_invalid")
        clean = copy.deepcopy(dict(row))
        items = clean.get("items")
        conditions = clean.get("condition_milli")
        if not isinstance(items, dict) or not isinstance(conditions, dict):
            raise OperationError(409, "black_lance_reset_equipment_invalid")
        for weapon_ref in ("weapon_jian", "weapon_spear"):
            if int(items.get(weapon_ref, 0) or 0) > 0:
                conditions[weapon_ref] = 1000
        if ref == _PLAYER_ID:
            if int(items.get("weapon_needle", 0) or 0) != 16:
                raise OperationError(409, "black_lance_reset_player_ammo_mismatch")
            items["weapon_needle"] = _PLAYER_NEEDLES
            conditions["weapon_needle"] = 1000
        loadouts[ref] = clean
    return compact_equipment_ledger(ledger)


def _reset_social(repository: Any) -> dict[str, Any]:
    social = copy.deepcopy(repository.read_json(_SOCIAL_PATH))
    relationships = social.get("relationships") if isinstance(social, Mapping) else None
    if not isinstance(relationships, dict):
        raise OperationError(409, "black_lance_reset_social_invalid")
    side_a, side_b = set(_SIDE_A), set(_SIDE_B)
    for key in list(relationships):
        if not isinstance(key, str) or "|" not in key:
            continue
        left, right = key.split("|", 1)
        if (left in side_a and right in side_b) or (left in side_b and right in side_a):
            relationships.pop(key, None)
    familiarity = social.get("martial_familiarity")
    if isinstance(familiarity, dict):
        for key in list(familiarity):
            if not isinstance(key, str) or "|" not in key:
                continue
            left, right = key.split("|", 1)
            if (left in side_a and right in side_b) or (left in side_b and right in side_a):
                familiarity.pop(key, None)
        if not familiarity:
            social.pop("martial_familiarity", None)
    return social


def build_black_lance_combat_reset(*, repository: Any, expected_revision: int) -> BlackLanceResetBuild:
    combats, combat_before = _require_exact_bad_state(repository, expected_revision)

    house_owner, house_people = _reset_roster(repository, _HOUSE_ROSTER, frozenset(_SIDE_A))
    black_owner, black_people = _reset_roster(repository, _BLACK_LANCE_ROSTER, frozenset(_SIDE_B))
    people = {**house_people, **black_people}
    equipment = _reset_equipment(repository)
    social = _reset_social(repository)

    combat_after = initialize_combat(
        combat_ref=_COMBAT_REF,
        side_a_refs=_SIDE_A,
        side_b_refs=_SIDE_B,
        people=people,
        zone_ref=str(combat_before.get("zone_ref") or "route.changan.huashan"),
        started_at=str(combat_before.get("started_at") or "0061-09-27T21:15:00"),
        objective=combat_before.get("objective") if isinstance(combat_before.get("objective"), Mapping) else {},
        awareness_mode=str(combat_before.get("awareness_mode") or "mutual"),
        initial_range_band=2,
        equipment_ledger=equipment,
        environment=combat_before.get("environment") if isinstance(combat_before.get("environment"), Mapping) else {},
    )
    combat_rows = combats.get("combats")
    if not isinstance(combat_rows, dict):
        raise OperationError(409, "black_lance_reset_combat_mismatch")
    combat_rows[_COMBAT_REF] = combat_after

    meta = copy.deepcopy(repository.read_json(_META_PATH))
    scheduler = copy.deepcopy(repository.read_json(_SCHEDULER_PATH))
    if not isinstance(meta, dict) or not isinstance(scheduler, dict):
        raise OperationError(409, "black_lance_reset_clock_invalid")
    if scheduler.get("settled_through") != _BAD_TIME.removeprefix("SE-"):
        raise OperationError(409, "black_lance_reset_clock_invalid")
    meta["revision"] = expected_revision + 1
    meta["time"] = _HANDOFF_TIME
    scheduler["settled_through"] = _HANDOFF_TIME.removeprefix("SE-")

    desired = {
        _META_PATH: meta,
        _SCHEDULER_PATH: scheduler,
        _COMBATS_PATH: combats,
        _EQUIPMENT_PATH: equipment,
        _SOCIAL_PATH: social,
        _HOUSE_ROSTER: house_owner,
        _BLACK_LANCE_ROSTER: black_owner,
    }
    writes: dict[str, Optional[bytes]] = {}
    for path, value in desired.items():
        encoded = _json_bytes(value)
        if repository.read_optional_bytes(path) != encoded:
            writes[path] = encoded
    if _META_PATH not in writes or _COMBATS_PATH not in writes:
        raise OperationError(409, "black_lance_reset_noop")

    result = {
        "repair_kind": "forward_black_lance_fullfresh_combat_reset",
        "historical_anchor": BLACK_LANCE_COMBAT_RESET_ANCHOR,
        "bad_revision": _BAD_REVISION,
        "committed_revision": expected_revision + 1,
        "restored_handoff_time": _HANDOFF_TIME,
        "combat_ref": _COMBAT_REF,
        "combatant_count": len(_PARTICIPANTS),
        "restored_player_needles": _PLAYER_NEEDLES,
        "repaired_path_count": len(writes),
        "preserved_route_and_mission_state": True,
        "provenance_source": "exact_r100_state_digest_plus_certified_r97_release_contract",
    }
    return BlackLanceResetBuild(writes=writes, result=result, affected_refs=tuple(sorted(writes)))


def _validate_command(self: Any, command: CommandEnvelope, *, require_revision: bool) -> _BlackLanceResetRequest:
    from shinobi_runtime.api.repair import REPAIR_COMMAND_TYPE, REPAIR_MODE

    if command.mode != REPAIR_MODE or command.command_type != REPAIR_COMMAND_TYPE:
        raise OperationError(403, "repair_mode_required")
    if command.actor_id not in self.operations.allowed_actor_ids:
        raise OperationError(403, "actor_not_allowed")
    if set(command.payload) != {"historical_anchor"} or command.payload.get("historical_anchor") != BLACK_LANCE_COMBAT_RESET_ANCHOR:
        raise OperationError(422, "repair_payload_invalid")
    try:
        self.repository.require_campaign(command.campaign_id, _META_PATH)
        if require_revision:
            self.repository.require_revision(command.expected_revision, _META_PATH)
    except StaleRevisionError as exc:
        raise OperationError(409, "stale_revision") from exc
    except (TypeError, ValueError) as exc:
        raise OperationError(409, "repair_campaign_mismatch") from exc
    return _BlackLanceResetRequest(BLACK_LANCE_COMBAT_RESET_ANCHOR)


def install_black_lance_combat_reset() -> None:
    """Temporarily compose the one closed reset into the ordinary repair service."""
    from shinobi_runtime.api import repair as repair_module

    service_type = repair_module.CampaignRepairService
    if getattr(service_type, "_black_lance_reset_installed", False):
        return
    original_require_base = service_type._require_base
    original_build = service_type._build

    def require_base(self: Any, command: CommandEnvelope, *, require_revision: bool = True):
        if set(command.payload) == {"historical_anchor"}:
            return _validate_command(self, command, require_revision=require_revision)
        return original_require_base(self, command, require_revision=require_revision)

    def build(self: Any, command: CommandEnvelope):
        if set(command.payload) != {"historical_anchor"}:
            return original_build(self, command)
        request = self._require_base(command)
        if request.historical_anchor != BLACK_LANCE_COMBAT_RESET_ANCHOR:
            raise OperationError(422, "repair_payload_invalid")
        self._require_fresh_deployment()
        repaired = build_black_lance_combat_reset(
            repository=self.repository,
            expected_revision=command.expected_revision,
        )
        return repair_module._RepairPlan(
            transaction_id="tx.repair." + command.digest,
            created_at=command.submitted_at,
            writes=repaired.writes,
            result=repaired.result,
            affected_refs=repaired.affected_refs,
        )

    service_type._require_base = require_base
    service_type._build = build
    service_type._black_lance_reset_installed = True


__all__ = [
    "BLACK_LANCE_COMBAT_RESET_ANCHOR",
    "BlackLanceResetBuild",
    "build_black_lance_combat_reset",
    "install_black_lance_combat_reset",
]
