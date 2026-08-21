"""Jianghu-native public semantic commands.

These reducers write current Jianghu authorities directly and preserve one mechanical authority.
"""
from __future__ import annotations

import copy, hashlib, json
from datetime import datetime
from typing import Any, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu_time import JianghuTimeCommandsMixin
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest
from shinobi_runtime.martial_world.live_state import (
    age_at_year, roster_person, set_roster_person,
)
from shinobi_runtime.martial_world.services import service_quote
from shinobi_runtime.martial_world.local_travel import local_travel_quote
from shinobi_runtime.martial_world.contracts import transition as contract_transition
from shinobi_runtime.martial_world.tournaments import register as tournament_register, advance_individual_competition, placement_payouts as tournament_placement_payouts
from shinobi_runtime.martial_world.rankings import add_public_points
from shinobi_runtime.martial_world.field_command import build_deployment_structure, validate_deployment_structure
from shinobi_runtime.martial_world.commitments import reserve_resources, release_resources
from shinobi_runtime.martial_world.infrastructure import (
    start_building_upgrade, advance_building_upgrade,
    start_building_expansion, advance_building_expansion, estate_land_summary,
    start_estate_boundary_expansion, advance_estate_boundary_expansion,
    start_enterprise_upgrade, advance_enterprise_upgrade,
    start_enterprise_scale_expansion, advance_enterprise_scale_expansion, enterprise_scale_basis,
    workshop_capacity, infirmary_capacity, transport_yard_capacity, residential_capacity,
)
from shinobi_runtime.martial_world.recruitment import deterministic_candidate, screening_report
from shinobi_runtime.martial_world.people import apply_age_development, deterministic_body_mass_kg, deterministic_name, deterministic_sex
from shinobi_runtime.martial_world.faction_state import (
    compact_faction_state, faction_path as canonical_faction_path, hydrate_faction_state,
    inventory_path as canonical_inventory_path, read_faction, roster_path as canonical_roster_path,
)
from shinobi_runtime.martial_world.inventory_state import compact_inventory_state
from shinobi_runtime.martial_world.civilian_state import compact_civilian_state
from shinobi_runtime.martial_world.equipment_state import compact_equipment_ledger
from shinobi_runtime.martial_world.social_state import compact_social_state
from shinobi_runtime.martial_world.training import advance_faction_training_epoch, apply_institutional_training
from shinobi_runtime.martial_world.person_state import compact_roster_state, hydrate_roster_state, reconcile_faction_population
from shinobi_runtime.martial_world.scheduler import sync_route_activity
from shinobi_runtime.martial_world.manpower import combat_ready_count
from shinobi_runtime.martial_world.health import functional_capacity_factors

_CONTRACTS = "state/martial-world/contracts/index.json"
_TOURNAMENTS = "state/martial-world/tournaments.json"
_REPUTATION = "state/martial-world/reputation.json"
_EQUIPMENT_LEDGER = "state/martial-world/equipment-ledger.json"
_COMBATS = "state/martial-world/combats.json"
_DEPLOYMENTS = "state/martial-world/deployments.json"
_PROJECTS = "state/martial-world/projects.json"
_CIVILIANS = "state/martial-world/civilian-populations.json"
_COMMITMENTS = "state/martial-world/commitments.json"
_CUSTODY = "state/martial-world/custody.json"
_ROUTE_OPS = "state/martial-world/route-operations.json"
_SCHEDULE = "state/martial-world/scheduler.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_TRAVEL_DATA = "game/data/martial-world/travel.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_WORLD_SEED_DEFAULT = "jianghu"
_TRAINING_FOCI = frozenset({"sword","spear","bow","unarmed","hidden_weapons","stealth_scouting","command","qi","qi_control","standing_faction_curriculum"})


class _RecordOverlayRepository:
    """Read-only repository view with a small set of staged current facts.

    Timed semantic commands use this to make the scheduler see the activity
    that begins *before* campaign time advances, without persisting an
    intermediate transaction or creating an append-only activity history.
    """

    def __init__(self, repository: Any, staged_records: Mapping[str, Mapping[str, Any]]) -> None:
        self._repository = repository
        self._images = {
            str(path): _json_bytes(_canonical_write_record(str(path), record))
            for path, record in staged_records.items()
        }

    def read_optional_bytes(self, path: object) -> bytes | None:
        key = str(path)
        if key in self._images:
            return self._images[key]
        return self._repository.read_optional_bytes(path)

    def read_bytes(self, path: object) -> bytes:
        raw = self.read_optional_bytes(path)
        if raw is None:
            raise FileNotFoundError(str(path))
        return raw

    def read_json(self, path: object) -> Any:
        return json.loads(self.read_bytes(path).decode("utf-8"))


class _StagedTimePlanner(JianghuTimeCommandsMixin):
    """Minimal time planner bound to a staged read view."""

    def __init__(self, owner: Any, repository: _RecordOverlayRepository) -> None:
        self._owner = owner
        self.repository = repository
        self.meta_path = owner.meta_path
        self.scene_path = owner.scene_path

    def _meta_after(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._owner._meta_after(*args, **kwargs)

    def _prune_noop_writes(self, writes: Mapping[str, bytes]) -> dict[str, bytes]:
        return {p: b for p, b in writes.items() if self.repository.read_optional_bytes(p) != b}

    def _assert_meta(self, *args: Any, **kwargs: Any) -> None:
        self._owner._assert_meta(*args, **kwargs)


class _OriginalWriteView:
    """Let a reused time-plan validator see its exact original after-images."""
    def __init__(self, overlay: StagedOverlay, original_writes: Mapping[str, bytes]) -> None:
        self._overlay = overlay
        self._writes = dict(original_writes)
        self.changed_paths = tuple(sorted(original_writes))
    def read_json(self, path: str) -> Any:
        if path in self._writes:
            return json.loads(self._writes[path].decode("utf-8"))
        return self._overlay.read_json(path)
    def read_bytes(self, path: str) -> bytes:
        if path in self._writes:
            return self._writes[path]
        return self._overlay.read_bytes(path)
    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def _stable_text(value: object, code: str, *, max_len: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > max_len or any(c in value for c in "\r\n\x00"):
        raise CommandRejectedError(code)
    return value


def _campaign_year(time: CampaignTime) -> int:
    return int(time.year)


def _faction_path(faction_ref: str) -> str:
    return canonical_faction_path(faction_ref)


def _inventory_path(faction_ref: str) -> str:
    return canonical_inventory_path(faction_ref)


def _roster_path(faction_ref: str) -> str:
    return canonical_roster_path(faction_ref)


def _canonical_write_record(path: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
    if path.startswith("state/martial-world/factions/"):
        return compact_faction_state(record)
    if path.startswith("state/martial-world/inventories/"):
        return compact_inventory_state(record)
    if path == _CIVILIANS:
        return compact_civilian_state(record)
    if path == "state/martial-world/equipment-ledger.json":
        return compact_equipment_ledger(record)
    if path == "state/martial-world/social.json":
        return compact_social_state(record)
    return record


def _person_is_usable(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {})
    return isinstance(health, Mapping) and health.get("status") not in {"dead", "incapacitated"} and int(health.get("consciousness", 100)) > 0


class JianghuCommandsMixin:
    def _require_jianghu(self, meta: Mapping[str, Any]) -> None:
        if meta.get("game") != "jianghu":
            raise CommandRejectedError("jianghu_campaign_required")

    def _person(self, ref: str) -> tuple[str, dict[str, Any], int, dict[str, Any]]:
        try:
            return roster_person(self.repository, ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_person_unresolved") from exc

    def _custody_person_refs(self) -> set[str]:
        custody = self.repository.read_json(_CUSTODY)
        records = custody.get("records", []) if isinstance(custody, Mapping) else []
        if not isinstance(records, list):
            return set()
        return {
            str(row.get("person_ref")) for row in records
            if isinstance(row, Mapping)
            and isinstance(row.get("person_ref"), str)
            and row.get("status") not in {"released", "escaped", "executed"}
        }

    def _active_combat_person_refs(self) -> set[str]:
        try:
            state = self.repository.read_json(_COMBATS)
        except FileNotFoundError:
            return set()
        combats = state.get("combats", {}) if isinstance(state, Mapping) else {}
        if not isinstance(combats, Mapping):
            return set()
        refs: set[str] = set()
        for combat in combats.values():
            if not isinstance(combat, Mapping) or combat.get("status") != "active":
                continue
            sides = combat.get("sides", {})
            if not isinstance(sides, Mapping):
                continue
            for side in ("side_a", "side_b"):
                members = sides.get(side, [])
                if isinstance(members, list):
                    refs.update(str(x) for x in members if isinstance(x, str))
        return refs

    def _unavailable_person_refs(self) -> set[str]:
        commitments = self.repository.read_json(_COMMITMENTS)
        index = commitments.get("person_index", {}) if isinstance(commitments, Mapping) else {}
        refs = {str(x) for x in index} if isinstance(index, Mapping) else set()
        return refs | self._custody_person_refs() | self._active_combat_person_refs()

    def _active_commitment_for_person(self, ref: str) -> Mapping[str, Any] | None:
        commitments = self.repository.read_json(_COMMITMENTS)
        if not isinstance(commitments, Mapping):
            return None
        index = commitments.get("person_index", {})
        rows = commitments.get("commitments", {})
        if not isinstance(index, Mapping) or not isinstance(rows, Mapping):
            return None
        cid = index.get(str(ref))
        row = rows.get(cid) if isinstance(cid, str) else None
        if not isinstance(row, Mapping) or row.get("status", "active") != "active":
            return None
        return row

    def _person_is_in_custody(self, ref: str) -> bool:
        return str(ref) in self._custody_person_refs()

    def _person_available_for_activity(
        self,
        ref: str,
        *,
        allow_commitment_kinds: Sequence[str] = (),
        require_usable: bool = True,
    ) -> bool:
        if self._person_is_in_custody(ref) or str(ref) in self._active_combat_person_refs():
            return False
        commitment = self._active_commitment_for_person(ref)
        if isinstance(commitment, Mapping):
            kind = str(commitment.get("activity_kind") or commitment.get("kind") or "")
            if kind not in {str(x) for x in allow_commitment_kinds}:
                return False
        if require_usable:
            try:
                _path, _roster, _ordinal, person = self._person(ref)
            except CommandRejectedError:
                return False
            if not _person_is_usable(person):
                return False
        return True

    def _require_person_available_for_activity(
        self,
        ref: str,
        code: str = "jianghu_person_unavailable",
        *,
        allow_commitment_kinds: Sequence[str] = (),
        require_usable: bool = True,
    ) -> None:
        if not self._person_available_for_activity(
            ref,
            allow_commitment_kinds=allow_commitment_kinds,
            require_usable=require_usable,
        ):
            raise CommandRejectedError(code)

    def _resumable_after_commitment_release(self, refs: Sequence[str], commitments_after: Mapping[str, Any]) -> list[str]:
        index = commitments_after.get("person_index", {}) if isinstance(commitments_after, Mapping) else {}
        still_committed = {str(x) for x in index} if isinstance(index, Mapping) else set()
        blocked = still_committed | self._custody_person_refs() | self._active_combat_person_refs()
        return [str(ref) for ref in refs if isinstance(ref, str) and str(ref) not in blocked]

    @staticmethod
    def _time_after_record(time_plan: _BuiltPlan, path: str, fallback: Mapping[str, Any]) -> dict[str, Any]:
        raw = time_plan.writes.get(path)
        if raw is None:
            return copy.deepcopy(dict(fallback))
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, Mapping):
            raise CommandRejectedError("jianghu_timed_after_image_invalid")
        return copy.deepcopy(dict(value))

    def _pause_institutional_training_now(
        self,
        refs: Sequence[str],
        current_time: CampaignTime,
        *,
        faction_override: Mapping[str, Any] | None = None,
        roster_override: Mapping[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
        if not refs:
            raise CommandRejectedError("jianghu_commitment_members_invalid")
        first_path, _base, _ordinal, first_person = self._person(str(refs[0]))
        faction_ref = str(first_person.get("faction_ref") or "")
        if not faction_ref:
            raise CommandRejectedError("jianghu_commitment_members_invalid")
        fpath, stored_faction = read_faction(self.repository, faction_ref)
        faction = copy.deepcopy(dict(faction_override)) if isinstance(faction_override, Mapping) else stored_faction
        roster_raw = copy.deepcopy(dict(roster_override)) if isinstance(roster_override, Mapping) else self.repository.read_json(first_path)
        roster = hydrate_roster_state(roster_raw, faction=faction)
        at_iso = str(current_time).removeprefix("SE-")
        faction, _summary = advance_faction_training_epoch(
            faction, roster, at_iso=at_iso,
            refresh_environment=False,
        )
        people = roster.get("people", [])
        if not isinstance(people, list):
            raise CommandRejectedError("jianghu_roster_invalid")
        indices = {str(row.get("person_id")): i for i, row in enumerate(people) if isinstance(row, Mapping) and isinstance(row.get("person_id"), str)}
        for ref in refs:
            idx = indices.get(str(ref))
            if idx is None:
                raise CommandRejectedError("jianghu_commitment_member_unresolved")
            person = apply_institutional_training(people[idx], faction=faction, roster_people=people)
            state = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
            state["institutional_paused"] = True
            person["training_state"] = state
            people[idx] = person
        return fpath, faction, first_path, compact_roster_state(roster, faction=faction)

    def _resume_institutional_training_now(
        self, refs: Sequence[str], current_time: CampaignTime, *, faction_override: Mapping[str, Any] | None = None, roster_override: Mapping[str, Any] | None = None
    ) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
        if not refs:
            raise CommandRejectedError("jianghu_commitment_members_invalid")
        first_path, _base, _ordinal, first_person = self._person(str(refs[0]))
        faction_ref = str(first_person.get("faction_ref") or "")
        fpath, stored_faction = read_faction(self.repository, faction_ref)
        faction = copy.deepcopy(dict(faction_override)) if isinstance(faction_override, Mapping) else stored_faction
        roster_source = roster_override if isinstance(roster_override, Mapping) else self.repository.read_json(first_path)
        roster = hydrate_roster_state(roster_source, faction=faction)
        at_iso = str(current_time).removeprefix("SE-")
        faction, _summary = advance_faction_training_epoch(
            faction, roster, at_iso=at_iso,
            refresh_environment=False,
        )
        people = roster.get("people", [])
        if not isinstance(people, list):
            raise CommandRejectedError("jianghu_roster_invalid")
        indices = {str(row.get("person_id")): i for i, row in enumerate(people) if isinstance(row, Mapping) and isinstance(row.get("person_id"), str)}
        for ref in refs:
            idx = indices.get(str(ref))
            if idx is None:
                raise CommandRejectedError("jianghu_commitment_member_unresolved")
            person = apply_institutional_training(people[idx], faction=faction, roster_people=people)
            state = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
            state.pop("institutional_paused", None)
            person["training_state"] = state
            people[idx] = person
        return fpath, faction, first_path, compact_roster_state(roster, faction=faction)

    @staticmethod
    def _resume_institutional_training_in_roster(roster: Mapping[str, Any], refs: Sequence[str], *, epoch_days: int) -> dict[str, Any]:
        out = copy.deepcopy(dict(roster))
        people = out.get("people")
        if not isinstance(people, list):
            raise CommandRejectedError("jianghu_roster_invalid")
        indices = {str(row.get("person_id")): i for i, row in enumerate(people) if isinstance(row, Mapping) and isinstance(row.get("person_id"), str)}
        for ref in refs:
            idx = indices.get(str(ref))
            if idx is None:
                raise CommandRejectedError("jianghu_commitment_member_unresolved")
            row = copy.deepcopy(dict(people[idx]))
            state = copy.deepcopy(dict(row.get("training_state", {}))) if isinstance(row.get("training_state"), Mapping) else {}
            state["institutional_days_applied"] = max(0, int(epoch_days))
            state.pop("institutional_paused", None)
            if state:
                row["training_state"] = state
            else:
                row.pop("training_state", None)
            people[idx] = row
        return out

    def _simple_plan(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        *,
        writes_records: Mapping[str, Mapping[str, Any]],
        code: str,
        result: Mapping[str, Any],
        scene: Mapping[str, Any] | None = None,
    ) -> _BuiltPlan:
        writes: dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time))
        }
        for path, record in writes_records.items():
            writes[str(path)] = _json_bytes(_canonical_write_record(str(path), record))
        if scene is not None:
            writes[self.scene_path] = _json_bytes(scene)
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))
        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("jianghu command write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
        return _BuiltPlan(code=code, affected_refs=expected, writes=writes, result=dict(result), validator=validate)

    def _time_plan_exact(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        *,
        seconds: int,
    ) -> _BuiltPlan:
        return self._time_plan_exact_staged(
            command, meta, current_time, seconds=seconds, staged_records={}
        )

    def _time_plan_exact_staged(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        *,
        seconds: int,
        staged_records: Mapping[str, Mapping[str, Any]],
    ) -> _BuiltPlan:
        """Advance an occupied activity through all quiet causal frontiers.

        ``advance_time`` intentionally settles only the earliest due frontier so
        ordinary player continuation can stop at meaningful handoffs. A timed
        semantic command is different: its activity starts now and must remain
        visible to every scheduler frontier until its exact completion time.
        This helper therefore follows non-player continuations in an in-memory
        overlay and emits one final atomic after-image. If any frontier produces
        a player-facing handoff, the entire timed action is rejected before any
        partial state is committed.
        """
        if seconds < 0:
            raise CommandRejectedError("jianghu_duration_invalid")
        if seconds == 0:
            raise CommandRejectedError("jianghu_zero_duration_internal")

        target = current_time.add_seconds(seconds)
        staged = {str(path): copy.deepcopy(dict(record)) for path, record in staged_records.items()}
        base_view = _RecordOverlayRepository(self.repository, staged)
        images: dict[str, Mapping[str, Any]] = dict(staged)
        touched: set[str] = set()
        soft_handoffs: list[dict[str, Any]] = []
        seen_handoff_keys: set[tuple[str, str]] = set()
        working_meta = copy.deepcopy(dict(meta))
        working_time = current_time

        for step in range(2048):
            internal = CommandEnvelope(
                campaign_id=command.campaign_id,
                request_id=f"{command.request_id}.jianghu-time-staged.{step}",
                actor_id=command.actor_id,
                command_type="advance_time",
                expected_revision=command.expected_revision,
                submitted_at=command.submitted_at,
                payload={"target_time": str(target)},
                mode=command.mode,
            )
            view = _RecordOverlayRepository(self.repository, images)
            planner = _StagedTimePlanner(self, view)
            plan = planner._advance_time(internal, working_meta, working_time)
            if plan.result.get("interrupted"):
                boundary_kind = str(plan.result.get("player_boundary_kind") or "soft_player_facing")
                handoffs = [
                    dict(row) for row in plan.result.get("player_handoffs", [])
                    if isinstance(row, Mapping)
                ]
                hard = boundary_kind == "hard_decision" or any(
                    bool(row.get("requires_player_decision"))
                    or str(row.get("handoff", {}).get("class", "")) == "hard_decision"
                    for row in handoffs
                )
                if hard:
                    raise CommandRejectedError("jianghu_action_interrupted_before_completion")
                for row in handoffs:
                    key = (str(row.get("event_id") or ""), str(row.get("kind") or ""))
                    if key in seen_handoff_keys:
                        continue
                    seen_handoff_keys.add(key)
                    if len(soft_handoffs) < 32:
                        soft_handoffs.append(row)

            previous_time = working_time
            for path, raw in plan.writes.items():
                try:
                    record = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CommandRejectedError("jianghu_internal_time_after_image_invalid") from exc
                if not isinstance(record, Mapping):
                    raise CommandRejectedError("jianghu_internal_time_after_image_invalid")
                images[str(path)] = copy.deepcopy(dict(record))
                touched.add(str(path))

            try:
                working_time = CampaignTime.parse(plan.result.get("world_time"))
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("jianghu_internal_time_result_invalid") from exc
            meta_image = images.get(self.meta_path)
            if isinstance(meta_image, Mapping):
                working_meta = copy.deepcopy(dict(meta_image))

            if working_time == target and not plan.result.get("continuation_required"):
                break
            if datetime(working_time.year, working_time.month, working_time.day, working_time.hour, working_time.minute, working_time.second) <= datetime(previous_time.year, previous_time.month, previous_time.day, previous_time.hour, previous_time.minute, previous_time.second):
                raise CommandRejectedError("jianghu_internal_time_did_not_advance")
        else:
            raise CommandRejectedError("jianghu_internal_time_frontier_limit")

        writes: dict[str, bytes] = {}
        for path in sorted(touched):
            record = images.get(path)
            if not isinstance(record, Mapping):
                continue
            raw = _json_bytes(_canonical_write_record(path, record))
            if base_view.read_optional_bytes(path) != raw:
                writes[path] = raw

        expected = tuple(sorted(writes))
        target_dt = datetime(target.year, target.month, target.day, target.hour, target.minute, target.second)

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("jianghu staged time write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=target)
            schedule = overlay.read_json("state/martial-world/scheduler.json")
            if schedule.get("settled_through") != target_dt.isoformat():
                raise ValueError("jianghu staged scheduler frontier mismatch")

        result = {
            "command_type": "advance_time",
            "world_time": str(target),
            "requested_time": str(target),
            "interrupted": False,
            "continuation_required": False,
        }
        if soft_handoffs:
            result["player_handoffs"] = soft_handoffs
        return _BuiltPlan(
            code="jianghu_internal_time_settled",
            affected_refs=expected,
            writes=writes,
            result=result,
            validator=validate,
        )

    def _timed_person_activity_plan(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        *,
        person_refs: Sequence[str],
        seconds: int,
        activity_ref: str,
        activity_kind: str,
        owner_ref: str,
        location_ref: str | None = None,
        staged_records: Mapping[str, Mapping[str, Any]] | None = None,
        allow_unusable_refs: Sequence[str] = (),
    ) -> tuple[_BuiltPlan, dict[str, Mapping[str, Any]], CampaignTime]:
        refs = [str(x) for x in person_refs if isinstance(x, str)]
        if not refs or len(refs) != len(set(refs)):
            raise CommandRejectedError("jianghu_activity_people_invalid")
        unavailable = self._unavailable_person_refs()
        if any(ref in unavailable for ref in refs):
            raise CommandRejectedError("jianghu_person_unavailable")
        first_path, _first_roster, _first_ordinal, first_person = self._person(refs[0])
        faction_ref = str(first_person.get("faction_ref") or "")
        if not faction_ref:
            raise CommandRejectedError("jianghu_activity_people_invalid")
        unusable_allowed = {str(x) for x in allow_unusable_refs if isinstance(x, str)}
        for ref in refs:
            _p, _r, _o, person = self._person(ref)
            if str(person.get("faction_ref") or "") != faction_ref:
                raise CommandRejectedError("jianghu_person_unavailable")
            if ref not in unusable_allowed and not _person_is_usable(person):
                raise CommandRejectedError("jianghu_person_unavailable")
        commitments = copy.deepcopy(self.repository.read_json(_COMMITMENTS))
        try:
            commitments = reserve_resources(
                commitments,
                resources=[("person", ref, faction_ref) for ref in refs],
                actor_ref=command.actor_id,
                owner_ref=str(owner_ref),
                activity_ref=str(activity_ref),
                activity_kind=str(activity_kind),
                started_at=str(current_time).removeprefix("SE-"),
                location_ref=location_ref,
            )
        except ValueError as exc:
            raise CommandRejectedError("jianghu_person_unavailable") from exc
        existing_staged = dict(staged_records or {})
        fpath = _faction_path(faction_ref)
        staged_faction = existing_staged.get(fpath)
        staged_roster = existing_staged.get(first_path)
        fpath, paused_faction, rpath, paused_roster = self._pause_institutional_training_now(
            refs,
            current_time,
            faction_override=staged_faction if isinstance(staged_faction, Mapping) else None,
            roster_override=staged_roster if isinstance(staged_roster, Mapping) else None,
        )
        staged: dict[str, Mapping[str, Any]] = {
            _COMMITMENTS: commitments,
            fpath: paused_faction,
            rpath: paused_roster,
        }
        for path, record in existing_staged.items():
            if str(path) in {fpath, rpath}:
                continue
            staged[str(path)] = record
        plan = self._time_plan_exact_staged(
            command,
            meta,
            current_time,
            seconds=seconds,
            staged_records=staged,
        )
        target = current_time.add_seconds(seconds)
        commitments_after = self._time_after_record(plan, _COMMITMENTS, commitments)
        try:
            commitments_after = release_resources(commitments_after, activity_ref=str(activity_ref))
        except ValueError as exc:
            raise CommandRejectedError("jianghu_activity_commitment_missing") from exc
        faction_after = self._time_after_record(plan, fpath, paused_faction)
        roster_after = self._time_after_record(plan, rpath, paused_roster)
        resume_fpath, resumed_faction, resume_rpath, resumed_roster = self._resume_institutional_training_now(
            refs,
            target,
            faction_override=faction_after,
            roster_override=roster_after,
        )
        if resume_fpath != fpath or resume_rpath != rpath or first_path != rpath:
            raise CommandRejectedError("jianghu_activity_roster_invariant")
        final_records: dict[str, Mapping[str, Any]] = {
            _COMMITMENTS: commitments_after,
            fpath: resumed_faction,
            rpath: resumed_roster,
        }
        for path, record in existing_staged.items():
            key = str(path)
            if key in {fpath, rpath, _COMMITMENTS}:
                continue
            final_records[key] = self._time_after_record(plan, key, record)
        return plan, final_records, target

    def _combine_time_plan(
        self,
        command: CommandEnvelope,
        time_plan: _BuiltPlan,
        *,
        extra_records: Mapping[str, Mapping[str, Any]],
        code: str,
        result: Mapping[str, Any],
        scene_override: Mapping[str, Any] | None = None,
    ) -> _BuiltPlan:
        original_writes = dict(time_plan.writes)
        writes = dict(original_writes)
        for path, record in extra_records.items():
            writes[str(path)] = _json_bytes(_canonical_write_record(str(path), record))
        if scene_override is not None:
            writes[self.scene_path] = _json_bytes(scene_override)
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))
        original_validator = time_plan.validator
        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("jianghu timed command write set changed after planning")
            original_validator(_OriginalWriteView(overlay, original_writes), manifest)
        final_result = {**dict(result), "world_time": time_plan.result.get("world_time")}
        notices = time_plan.result.get("player_handoffs")
        if isinstance(notices, list) and notices:
            final_result["player_handoffs"] = copy.deepcopy(notices[:32])
        return _BuiltPlan(code=code, affected_refs=expected, writes=writes, result=final_result, validator=validate)

    def _jianghu_training_focus_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        self._require_jianghu(meta)
        if set(command.payload) != {"subject_ref", "focus"}:
            raise CommandRejectedError("jianghu_training_focus_resolution_payload_fields_invalid")
        subject = _stable_text(command.payload.get("subject_ref"), "jianghu_subject_invalid")
        focus = _stable_text(command.payload.get("focus"), "jianghu_training_focus_invalid")
        if focus not in _TRAINING_FOCI:
            raise CommandRejectedError("jianghu_training_focus_invalid")
        if subject != command.actor_id:
            raise CommandRejectedError("jianghu_training_focus_not_authorized")
        path, roster, idx, person = self._person(subject)
        state = copy.deepcopy(person.get("training_state", {})) if isinstance(person.get("training_state"), Mapping) else {}
        state.setdefault("residual_milli", {}); state.setdefault("evidence_milli", {})
        if focus == "standing_faction_curriculum": state.pop("focus", None)
        else: state["focus"] = focus
        person["training_state"] = state
        roster = set_roster_person(roster, idx, person)
        writes_records = {path: roster}
        return self._simple_plan(command, meta, current_time, writes_records=writes_records, code="jianghu_training_focus_ready", result={"command_type":command.command_type,"subject_ref":subject,"focus":focus})

    def _jianghu_service_purchase_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        self._require_jianghu(meta)
        if set(command.payload) != {"site_ref", "service_ref"}:
            raise CommandRejectedError("jianghu_service_purchase_resolution_payload_fields_invalid")
        site_ref = _stable_text(command.payload.get("site_ref"), "jianghu_site_invalid")
        service_ref = _stable_text(command.payload.get("service_ref"), "jianghu_service_invalid")
        path, roster, idx, person = self._person(command.actor_id)
        self._require_person_available_for_activity(command.actor_id)
        if person.get("location_ref") != site_ref:
            raise CommandRejectedError("jianghu_service_requires_presence")
        try:
            quote = service_quote(site_ref=site_ref, service_ref=service_ref, buyer_age=age_at_year(person, _campaign_year(current_time)))
        except PermissionError as exc:
            raise CommandRejectedError("jianghu_service_age_restricted") from exc
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_service_unavailable") from exc
        price = int(quote["price_cash"])
        cash = max(0, int(person.get("personal_cash", 0)))
        if cash < price:
            raise CommandRejectedError("jianghu_personal_cash_insufficient")
        duration = int(quote.get("duration_minutes", 0)) * 60
        person["personal_cash"] = cash - price
        staged_roster = set_roster_person(copy.deepcopy(roster), idx, person)
        # Public local services are aggregate regional economic activity. The
        # buyer's cash must therefore enter the authoritative regional market
        # pool rather than disappear from the simulation. Stage the transfer at
        # service start so any market frontier crossed during the service sees
        # the already-paid cash.
        sites = self.repository.read_json(_LOCAL_SITES).get("sites", {})
        site = sites.get(site_ref) if isinstance(sites, Mapping) else None
        place_ref = str(site.get("parent_place_ref") or "") if isinstance(site, Mapping) else ""
        places = self.repository.read_json(_GEOGRAPHY).get("places", {})
        place = places.get(place_ref) if isinstance(places, Mapping) else None
        region = str(place.get("climate_profile") or "") if isinstance(place, Mapping) else ""
        if not region:
            raise CommandRejectedError("jianghu_service_region_unresolved")
        market_path = f"state/martial-world/markets/{region}.json"
        market = copy.deepcopy(self.repository.read_json(market_path))
        market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + price
        time_plan, final_records, _target = self._timed_person_activity_plan(
            command,
            meta,
            current_time,
            person_refs=[command.actor_id],
            seconds=max(1, duration),
            activity_ref=f"service:{command.request_id}",
            activity_kind="local_service",
            owner_ref=str(person.get("faction_ref") or command.actor_id),
            location_ref=site_ref,
            staged_records={path: staged_roster, market_path: market},
        )
        final_roster = final_records[path]
        final_person = next((p for p in final_roster.get("people", []) if isinstance(p, Mapping) and p.get("person_id") == command.actor_id), None)
        cash_after = int(final_person.get("personal_cash", 0)) if isinstance(final_person, Mapping) else cash - price
        return self._combine_time_plan(command,time_plan,extra_records=final_records,code="jianghu_service_purchase_ready",result={"command_type":command.command_type,**quote,"cash_after":cash_after})

    def _jianghu_local_travel_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        self._require_jianghu(meta)
        if set(command.payload) != {"destination_site_ref"}:
            raise CommandRejectedError("jianghu_local_travel_resolution_payload_fields_invalid")
        destination = _stable_text(command.payload.get("destination_site_ref"), "jianghu_destination_invalid")
        path, roster, idx, person = self._person(command.actor_id)
        self._require_person_available_for_activity(command.actor_id)
        start = person.get("location_ref")
        if not isinstance(start, str): raise CommandRejectedError("jianghu_person_location_invalid")
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
        walking = max(0, min(1000, int(functional_capacity_factors([row for row in wounds if isinstance(row, Mapping)]).get("walking_milli", 1000))))
        if walking <= 0:
            raise CommandRejectedError("jianghu_walking_function_unavailable")
        try:
            quote = local_travel_quote(start_site_ref=start, end_site_ref=destination, walking_speed_kph=4.8*max(50,walking)/1000.0)
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc: raise CommandRejectedError("jianghu_local_route_invalid") from exc
        quote = {**quote, "walking_capacity_milli": walking}
        time_plan, extra, _target = self._timed_person_activity_plan(
            command,
            meta,
            current_time,
            person_refs=[command.actor_id],
            seconds=int(quote["walking_minutes"]) * 60,
            activity_ref=f"local-travel:{command.request_id}",
            activity_kind="travel",
            owner_ref=str(person.get("faction_ref") or command.actor_id),
            location_ref=start,
        )
        final_roster = copy.deepcopy(dict(extra[path]))
        final_people = final_roster.get("people", [])
        if not isinstance(final_people, list):
            raise CommandRejectedError("jianghu_roster_invalid")
        for i, row in enumerate(final_people):
            if isinstance(row, Mapping) and row.get("person_id") == command.actor_id:
                updated = copy.deepcopy(dict(row)); updated["location_ref"] = destination; final_people[i] = updated; break
        else:
            raise CommandRejectedError("jianghu_person_unresolved")
        extra[path] = final_roster
        original_scene = self._time_after_record(time_plan, self.scene_path, self.repository.read_json(self.scene_path))
        original_scene["location_id"] = destination
        original_scene["present_person_ids"] = [command.actor_id]
        original_scene["visible_person_ids"] = [command.actor_id]
        return self._combine_time_plan(command,time_plan,extra_records=extra,scene_override=original_scene,code="jianghu_local_travel_ready",result={"command_type":command.command_type,"from_site_ref":start,"destination_site_ref":destination,**quote})

    def _jianghu_contract_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        self._require_jianghu(meta)
        allowed={"accept":{"action","contract_ref","participant_refs"},"start":{"action","contract_ref"}}
        action=command.payload.get("action")
        if action not in allowed or set(command.payload)!=allowed[action]: raise CommandRejectedError("jianghu_contract_resolution_payload_fields_invalid")
        contract_ref=_stable_text(command.payload.get("contract_ref"),"jianghu_contract_invalid")
        index=copy.deepcopy(self.repository.read_json(_CONTRACTS)); active=index.get("active")
        contract=active.get(contract_ref) if isinstance(active,dict) else None
        if not isinstance(contract,Mapping): raise CommandRejectedError("jianghu_contract_unresolved")
        _,_,_,actor=self._person(command.actor_id); faction_ref=str(actor.get("faction_ref") or "")
        if action=="accept":
            try:
                if datetime.fromisoformat(str(contract.get("expires_at", ""))) <= datetime(current_time.year,current_time.month,current_time.day,current_time.hour,current_time.minute,current_time.second):
                    raise CommandRejectedError("jianghu_contract_offer_expired")
            except ValueError as exc:
                raise CommandRejectedError("jianghu_contract_expiry_invalid") from exc
            refs=command.payload.get("participant_refs")
            if not isinstance(refs,(list,tuple)) or not refs or any(not isinstance(x,str) for x in refs) or len(set(refs))!=len(refs) or command.actor_id not in refs:
                raise CommandRejectedError("jianghu_contract_participants_invalid")
            unavailable = self._unavailable_person_refs()
            for ref in refs:
                _,_,_,p=self._person(ref)
                if ref in unavailable or p.get("faction_ref")!=faction_ref or not _person_is_usable(p): raise CommandRejectedError("jianghu_contract_participant_unavailable")
            try: updated=contract_transition(contract,at=str(current_time).removeprefix("SE-"),to_status="accepted",actor_ref=command.actor_id,participants=list(refs))
            except ValueError as exc: raise CommandRejectedError("jianghu_contract_transition_invalid") from exc
            updated["beneficiary_ref"]=faction_ref
            active[contract_ref]=updated
            records={_CONTRACTS:index}
        else:
            if contract.get("beneficiary_ref")!=faction_ref or command.actor_id not in contract.get("participants",[]): raise CommandRejectedError("jianghu_contract_not_authorized")
            try:
                if datetime.fromisoformat(str(contract.get("expires_at", ""))) <= datetime(current_time.year,current_time.month,current_time.day,current_time.hour,current_time.minute,current_time.second):
                    raise CommandRejectedError("jianghu_contract_offer_expired")
            except ValueError as exc:
                raise CommandRejectedError("jianghu_contract_expiry_invalid") from exc
            objective=contract.get("objective",{}) if isinstance(contract.get("objective"),Mapping) else {}
            if objective.get("kind")!="escort_shipment": raise CommandRejectedError("jianghu_contract_objective_unsupported")
            route_ref=str(objective.get("route_ref") or ""); source_region=str(objective.get("source_region") or ""); destination_region=str(objective.get("destination_region") or "")
            item_ref=str(objective.get("item_ref") or ""); quantity=max(0,int(objective.get("quantity",0))); minimum=max(1,int(objective.get("minimum_escort_count",1)))
            participants=[str(x) for x in contract.get("participants",[]) if isinstance(x,str)]
            if len(participants)<minimum: raise CommandRejectedError("jianghu_contract_escort_count_insufficient")
            geography=self.repository.read_json(_GEOGRAPHY); routes=geography.get("routes",[]) if isinstance(geography,Mapping) else []
            route=next((r for r in routes if isinstance(r,Mapping) and r.get("id")==route_ref),None) if isinstance(routes,list) else None
            places=geography.get("places",{}) if isinstance(geography,Mapping) else {}
            if not isinstance(route,Mapping) or not isinstance(places,Mapping): raise CommandRejectedError("jianghu_contract_route_unresolved")
            ends=[str(route.get("from") or ""),str(route.get("to") or "")]; origin=next((x for x in ends if isinstance(places.get(x),Mapping) and places[x].get("climate_profile")==source_region),None)
            destination=next((x for x in ends if x!=origin and isinstance(places.get(x),Mapping) and places[x].get("climate_profile")==destination_region),None)
            if not origin or not destination: raise CommandRejectedError("jianghu_contract_route_direction_unresolved")
            sites=self.repository.read_json(_LOCAL_SITES).get("sites",{})
            if not isinstance(sites,Mapping): raise CommandRejectedError("jianghu_local_sites_invalid")
            unavailable = self._unavailable_person_refs()
            for ref in participants:
                if ref in unavailable: raise CommandRejectedError("jianghu_contract_participant_unavailable")
                _,_,_,p=self._person(ref); site=sites.get(str(p.get("location_ref")))
                if not isinstance(site,Mapping) or site.get("parent_place_ref")!=origin: raise CommandRejectedError("jianghu_contract_participant_not_at_route_origin")
            market_path=f"state/martial-world/markets/{source_region}.json"; market=copy.deepcopy(self.repository.read_json(market_path)); stock=market.get("stock")
            if not isinstance(stock,dict) or int(stock.get(item_ref,0))<quantity: raise CommandRejectedError("jianghu_contract_cargo_unavailable")
            stock[item_ref]=int(stock.get(item_ref,0))-quantity
            if stock[item_ref]==0: stock.pop(item_ref,None)
            travel=self.repository.read_json(_TRAVEL_DATA); speed=max(1,float(travel.get("mode_speed_km_per_day",{}).get("convoy",24)))
            terrain=int(travel.get("terrain_time_milli",{}).get(str(route.get("terrain","plain")),1000)); road=int(travel.get("road_time_milli",{}).get(str(route.get("road_quality","maintained")),1000))
            required_hours=max(1,int((float(route.get("distance_km",0))*24.0/speed*terrain*road/1_000_000.0)+float(route.get("fixed_delay_hours",0))+0.999999))
            commitments=copy.deepcopy(self.repository.read_json(_COMMITMENTS))
            try: commitments=reserve_resources(commitments,resources=[("person",ref,faction_ref) for ref in participants],actor_ref=command.actor_id,owner_ref=faction_ref,activity_ref=contract_ref,activity_kind="contract_escort",started_at=str(current_time).removeprefix("SE-"),location_ref=origin)
            except ValueError as exc: raise CommandRejectedError("jianghu_contract_participant_committed") from exc
            pause_fpath,paused_faction,pause_path,paused_roster=self._pause_institutional_training_now(participants,current_time)
            route_ops=copy.deepcopy(self.repository.read_json(_ROUTE_OPS)); movements=route_ops.setdefault("movements",{})
            if contract_ref in movements: raise CommandRejectedError("jianghu_contract_movement_conflict")
            movements[contract_ref]={"movement_ref":contract_ref,"contract_ref":contract_ref,"route_ref":route_ref,"origin_place_ref":origin,"destination_place_ref":destination,"source_region":source_region,"destination_region":destination_region,"item_ref":item_ref,"quantity":quantity,"cargo_value_cash":max(0,int(objective.get("cargo_value_cash",0))),"beneficiary_ref":faction_ref,"participant_refs":participants,"started_at":str(current_time).removeprefix("SE-"),"elapsed_hours":0,"required_hours":required_hours,"known_escort_count":len(participants),"status":"active"}
            try: updated=contract_transition(contract,at=str(current_time).removeprefix("SE-"),to_status="in_progress",actor_ref=command.actor_id)
            except ValueError as exc: raise CommandRejectedError("jianghu_contract_transition_invalid") from exc
            objective_after=copy.deepcopy(dict(objective)); objective_after["cargo_committed"]=True; updated["objective"]=objective_after
            active[contract_ref]=updated
            schedule=copy.deepcopy(self.repository.read_json(_SCHEDULE))
            active_route_ids=[str(row.get("route_ref")) for row in movements.values() if isinstance(row,Mapping) and row.get("status","active")=="active" and isinstance(row.get("route_ref"),str)]
            try:schedule=sync_route_activity(schedule,active_route_ids=active_route_ids,now=datetime(current_time.year,current_time.month,current_time.day,current_time.hour,current_time.minute,current_time.second))
            except ValueError as exc:raise CommandRejectedError("jianghu_scheduler_invalid") from exc
            records={_CONTRACTS:index,_ROUTE_OPS:route_ops,_COMMITMENTS:commitments,_SCHEDULE:schedule,market_path:market,pause_fpath:paused_faction,pause_path:paused_roster}
        return self._simple_plan(command,meta,current_time,writes_records=records,code="jianghu_contract_ready",result={"command_type":command.command_type,"action":action,"contract_ref":contract_ref,"status":updated["status"]})

    def _jianghu_tournament_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        self._require_jianghu(meta)
        if set(command.payload)!={"action","tournament_ref"} or command.payload.get("action") not in {"register","advance"}: raise CommandRejectedError("jianghu_tournament_resolution_payload_fields_invalid")
        action=str(command.payload.get("action"))
        tref=_stable_text(command.payload.get("tournament_ref"),"jianghu_tournament_invalid")
        state=copy.deepcopy(self.repository.read_json(_TOURNAMENTS)); tourn=state.get("tournaments",{}).get(tref)
        if not isinstance(tourn,Mapping): raise CommandRejectedError("jianghu_tournament_unresolved")
        if action=="advance":
            registrations=tourn.get("registrations",[]) if isinstance(tourn.get("registrations"),list) else []
            owner_map={str(r.get("entrant_ref")):str(r.get("faction_ref")) for r in registrations if isinstance(r,Mapping) and isinstance(r.get("entrant_ref"),str) and isinstance(r.get("faction_ref"),str)}
            if command.actor_id not in owner_map: raise CommandRejectedError("jianghu_tournament_not_registered")
            commitments=copy.deepcopy(self.repository.read_json(_COMMITMENTS)); person_index=commitments.get("person_index",{}) if isinstance(commitments,Mapping) else {}
            if not isinstance(person_index,Mapping): raise CommandRejectedError("jianghu_commitments_invalid")
            custody=self.repository.read_json(_CUSTODY); custody_rows=custody.get("records",[]) if isinstance(custody,Mapping) else []
            custody_refs={str(row.get("person_ref")) for row in custody_rows if isinstance(row,Mapping) and isinstance(row.get("person_ref"),str) and row.get("status") not in {"released","escaped","executed"}} if isinstance(custody_rows,list) else set()
            active_combat_refs=self._active_combat_person_refs()
            prior_pending=str(tourn.get("pending_combat_ref") or "")
            prior_pair=[str(x) for x in tourn.get("active_pair",[]) if isinstance(x,str)] if prior_pending and isinstance(tourn.get("active_pair"),list) else []
            prior_commitment=f"commitment:{prior_pending}" if prior_pending else ""
            sites=self.repository.read_json(_LOCAL_SITES).get("sites",{}); host_place=str(tourn.get("host_place_ref") or "")
            rosters:dict[str,tuple[str,dict[str,Any],dict[str,Any]]]={}; people:dict[str,Mapping[str,Any]]={}; doctrines:dict[str,Mapping[str,Any]]={}
            for fid in sorted(set(owner_map.values())):
                fpath,faction=read_faction(self.repository,fid); rpath=canonical_roster_path(fid); roster=hydrate_roster_state(self.repository.read_json(rpath),faction=faction)
                rosters[fid]=(rpath,roster,faction); doctrines[fid]=faction.get("doctrine",{}) if isinstance(faction.get("doctrine"),Mapping) else {}
            for ref,fid in owner_map.items():
                roster=rosters[fid][1]
                person=next((p for p in roster.get("people",[]) if isinstance(p,Mapping) and p.get("person_id")==ref),None)
                commitment_ref=person_index.get(ref)
                available=commitment_ref in {None, prior_commitment} and ref not in custody_refs and ref not in active_combat_refs
                site=sites.get(str(person.get("location_ref"))) if isinstance(person,Mapping) and isinstance(sites,Mapping) else None
                at_host=(not host_place) or (isinstance(site,Mapping) and site.get("parent_place_ref")==host_place)
                if isinstance(person,Mapping) and available and at_host: people[ref]=copy.deepcopy(dict(person))
            if command.actor_id not in people:
                raise CommandRejectedError("jianghu_tournament_entrant_unavailable")
            equipment=copy.deepcopy(self.repository.read_json(_EQUIPMENT_LEDGER)); combats=copy.deepcopy(self.repository.read_json(_COMBATS)); at_iso=str(current_time).removeprefix("SE-")
            try:
                advanced=advance_individual_competition(
                    tourn,people=people,equipment_ledger=equipment,doctrines=doctrines,combats_state=combats,
                    zone_ref=str(tourn.get("venue_site_ref") or tourn.get("host_place_ref") or "tournament_venue"),at_iso=at_iso,player_ref=command.actor_id,
                )
            except ValueError as exc: raise CommandRejectedError("jianghu_tournament_advance_invalid") from exc
            records:dict[str,Any]={_COMBATS:advanced["combats_state_after"],_EQUIPMENT_LEDGER:compact_equipment_ledger(advanced["equipment_ledger_after"])}
            next_combat_ref=str(advanced.get("combat_ref") or "")
            next_pair: list[str] = []
            if prior_pending and (not advanced.get("waiting_for_player") or next_combat_ref!=prior_pending):
                commitments=release_resources(commitments,activity_ref=prior_pending)
            if advanced.get("waiting_for_player") and next_combat_ref and next_combat_ref!=prior_pending:
                live=advanced.get("tournament_after",{}) if isinstance(advanced.get("tournament_after"),Mapping) else {}
                pair=[str(x) for x in live.get("active_pair",[]) if isinstance(x,str)]
                if len(pair)!=2: raise CommandRejectedError("jianghu_tournament_match_pair_invalid")
                next_pair = list(pair)
                try:
                    commitments=reserve_resources(
                        commitments,resources=[("person",ref,owner_map.get(ref,"")) for ref in pair],
                        actor_ref=command.actor_id,owner_ref=tref,activity_ref=next_combat_ref,
                        activity_kind="tournament_match",started_at=at_iso,
                        location_ref=str(tourn.get("venue_site_ref") or tourn.get("host_place_ref") or ""),
                    )
                except ValueError as exc:
                    raise CommandRejectedError("jianghu_tournament_match_participant_committed") from exc
            records[_COMMITMENTS]=commitments
            rep=copy.deepcopy(self.repository.read_json(_REPUTATION))
            for ref,points in advanced.get("winner_points",{}).items(): rep=add_public_points(rep,str(ref),tournament_points=int(points))
            # Merge exact match after-images into their authoritative faction rosters.
            for fid,(rpath,roster,faction) in rosters.items():
                replacements={ref:p for ref,p in advanced["people_after"].items() if owner_map.get(str(ref))==fid and isinstance(p,Mapping)}
                rows=roster.get("people",[]) if isinstance(roster.get("people"),list) else []
                roster["people"]=[copy.deepcopy(dict(replacements[str(row.get("person_id"))])) if isinstance(row,Mapping) and str(row.get("person_id")) in replacements else row for row in rows]
                faction=reconcile_faction_population(faction,roster)
                records[canonical_faction_path(fid)]=faction
                records[rpath]=compact_roster_state(roster,faction=faction)

            # Tournament match commitments obey the same training-time law as
            # deployments, escorts and construction. A fighter leaving the
            # prior match resumes only if no new match, custody, combat or other
            # commitment still owns that person's time; the next pair is paused
            # exactly at this continuation frontier.
            resumable_prior = [
                ref for ref in self._resumable_after_commitment_release(prior_pair, commitments)
                if ref not in set(next_pair)
            ]
            for fid in sorted(set(owner_map.get(ref, "") for ref in resumable_prior if owner_map.get(ref))):
                refs = [ref for ref in resumable_prior if owner_map.get(ref) == fid]
                fpath = canonical_faction_path(fid); rpath = canonical_roster_path(fid)
                rf, rr = records.get(fpath, rosters[fid][2]), records.get(rpath, compact_roster_state(rosters[fid][1], faction=rosters[fid][2]))
                _fp, faction_after, _rp, roster_after = self._resume_institutional_training_now(
                    refs, current_time, faction_override=rf, roster_override=rr
                )
                records[fpath] = faction_after; records[rpath] = roster_after
            for fid in sorted(set(owner_map.get(ref, "") for ref in next_pair if owner_map.get(ref))):
                refs = [ref for ref in next_pair if owner_map.get(ref) == fid]
                fpath = canonical_faction_path(fid); rpath = canonical_roster_path(fid)
                rf, rr = records.get(fpath, rosters[fid][2]), records.get(rpath, compact_roster_state(rosters[fid][1], faction=rosters[fid][2]))
                _fp, faction_after, _rp, roster_after = self._pause_institutional_training_now(
                    refs, current_time, faction_override=rf, roster_override=rr
                )
                records[fpath] = faction_after; records[rpath] = roster_after

            if advanced["waiting_for_player"]:
                state["tournaments"][tref]=advanced["tournament_after"]; records[_TOURNAMENTS]=state; records[_REPUTATION]=rep
                return self._simple_plan(command,meta,current_time,writes_records=records,code="jianghu_tournament_match_ready",result={"command_type":command.command_type,"tournament_ref":tref,"status":"awaiting_player_match","combat_ref":advanced.get("combat_ref")})
            live=advanced["tournament_after"]
            champion=advanced.get("champion_ref"); prize=max(0,int(live.get("prize_escrow_cash",0)))
            payout_rows=tournament_placement_payouts(live)
            if prize>0 and sum(max(0,int(row.get("cash",0))) for row in payout_rows)!=prize:
                raise CommandRejectedError("jianghu_tournament_prize_settlement_invalid")
            faction_share=max(0,min(1000,int(live.get("placement_faction_share_permille",700))))
            personal_share=max(0,min(1000,int(live.get("placement_personal_share_permille",300))))
            if faction_share+personal_share!=1000:
                raise CommandRejectedError("jianghu_tournament_prize_settlement_invalid")
            placement_points={"first":4,"second":3,"third":3,"fourth":2}
            placement_awards=[]
            for award in payout_rows:
                place=str(award.get("place") or ""); ref=str(award.get("entrant_ref") or ""); gross=max(0,int(award.get("cash",0)))
                if not place or not ref or gross<=0:
                    continue
                fid=owner_map.get(ref)
                faction_cash=0; personal_cash=gross
                if isinstance(fid,str) and fid in rosters:
                    rpath,base_roster,base_faction=rosters[fid]
                    fpath=canonical_faction_path(fid)
                    faction=copy.deepcopy(dict(records.get(fpath,base_faction)))
                    faction_cash=gross*faction_share//1000; personal_cash=gross-faction_cash
                    faction["treasury_cash"]=max(0,int(faction.get("treasury_cash",0)))+faction_cash
                    records[fpath]=faction
                    roster=hydrate_roster_state(records.get(rpath,compact_roster_state(base_roster,faction=base_faction)),faction=faction)
                    rows=roster.get("people",[]) if isinstance(roster.get("people"),list) else []
                    for i,row in enumerate(rows):
                        if isinstance(row,Mapping) and row.get("person_id")==ref:
                            person=copy.deepcopy(dict(row)); person["personal_cash"]=max(0,int(person.get("personal_cash",0)))+personal_cash; rows[i]=person; break
                    records[rpath]=compact_roster_state(roster,faction=faction)
                rep=add_public_points(rep,ref,tournament_points=max(1,int(placement_points.get(place,1))))
                placement_awards.append({"place":place,"entrant_ref":ref,"faction_ref":fid if isinstance(fid,str) else None,"gross_prize_cash":gross,"faction_prize_cash":faction_cash,"personal_prize_cash":personal_cash})
            state["tournaments"].pop(tref,None); records[_TOURNAMENTS]=state; records[_REPUTATION]=rep
            return self._simple_plan(command,meta,current_time,writes_records=records,code="jianghu_tournament_completed",result={"command_type":command.command_type,"tournament_ref":tref,"status":"completed","champion_ref":champion,"placements":dict(live.get("placements",{})) if isinstance(live.get("placements"),Mapping) else {},"placement_awards":placement_awards,"prize_cash":prize})

        path,roster,idx,person=self._person(command.actor_id)
        if command.actor_id in self._unavailable_person_refs():
            raise CommandRejectedError("jianghu_tournament_entrant_unavailable")
        health=person.get("health",{}); alive=isinstance(health,Mapping) and health.get("status")!="dead"; eligible=alive and int(health.get("consciousness",100))>0
        # Qualification is public-evidence only; no private stat seed is used.
        rep=self.repository.read_json("state/martial-world/reputation.json")
        audience=rep.get("audiences",{}).get(command.actor_id,{}) if isinstance(rep,Mapping) else {}
        qualifying=max(0,int(audience.get("public_score",0))) if isinstance(audience,Mapping) else 0
        # Registration is physical. The runtime never teleports the player into
        # a bracket on competition day; reach the advertised host first.
        host_place=str(tourn.get("host_place_ref") or "")
        sites=self.repository.read_json(_LOCAL_SITES).get("sites",{})
        site=sites.get(str(person.get("location_ref"))) if isinstance(sites,Mapping) else None
        if host_place and (not isinstance(site,Mapping) or site.get("parent_place_ref")!=host_place):
            raise CommandRejectedError("jianghu_tournament_entrant_not_at_host")
        faction_ref=str(person.get("faction_ref") or "")
        if not faction_ref:
            raise CommandRejectedError("jianghu_tournament_faction_sponsor_required")
        fpath,faction=read_faction(self.repository,faction_ref)
        try: outcome=tournament_register(tourn,entrant_ref=command.actor_id,qualifying_score=qualifying,payer_cash=int(faction.get("treasury_cash",0)),alive=alive,medically_eligible=eligible)
        except ValueError as exc: raise CommandRejectedError("jianghu_tournament_registration_invalid") from exc
        updated=copy.deepcopy(dict(outcome["tournament_after"]))
        if isinstance(updated.get("registrations"),list) and updated["registrations"]:
            updated["registrations"][-1]["faction_ref"]=faction_ref
        state["tournaments"][tref]=updated
        faction["treasury_cash"]=int(outcome["payer_cash_after"])
        records={_TOURNAMENTS:state,fpath:faction}
        return self._simple_plan(command,meta,current_time,writes_records=records,code="jianghu_tournament_registration_ready",result={"command_type":command.command_type,"tournament_ref":tref,"entry_fee_cash":int(tourn.get("entry_fee_cash",0) or 0),"sponsor_faction_ref":faction_ref,"sponsor_cash_after":faction["treasury_cash"]})

    def _jianghu_deployment_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        self._require_jianghu(meta)
        action=command.payload.get("action")
        if action=="form": expected={"action","deployment_ref","member_refs","objective"}
        elif action=="release": expected={"action","deployment_ref"}
        else: raise CommandRejectedError("jianghu_deployment_action_invalid")
        if set(command.payload)!=expected: raise CommandRejectedError("jianghu_deployment_resolution_payload_fields_invalid")
        dep_ref=_stable_text(command.payload.get("deployment_ref"),"jianghu_deployment_invalid")
        state=copy.deepcopy(self.repository.read_json(_DEPLOYMENTS)); deployments=state.setdefault("deployments",{})
        _,_,_,actor=self._person(command.actor_id); faction_ref=str(actor.get("faction_ref") or "")
        if action=="release":
            row=deployments.get(dep_ref)
            if not isinstance(row,Mapping) or row.get("faction_ref")!=faction_ref: raise CommandRejectedError("jianghu_deployment_unresolved")
            structure = row.get("structure", {}) if isinstance(row.get("structure"), Mapping) else {}
            office_keys={str(x).split(':',1)[0] for x in actor.get("standing_offices",[]) if isinstance(x,str)}
            command_refs={str(structure.get("commander_ref") or ""), str(structure.get("deputy_ref") or "")}
            if command.actor_id not in command_refs and not ({"leader","deputy_leader","field_commander","deputy_field_commander"}&office_keys):
                raise CommandRejectedError("jianghu_deployment_not_authorized")
            member_refs = row.get("structure", {}).get("member_refs", []) if isinstance(row.get("structure"), Mapping) else []
            if not isinstance(member_refs, list): member_refs = []
            deployments.pop(dep_ref,None)
            commitments=copy.deepcopy(self.repository.read_json(_COMMITMENTS))
            try:commitments=release_resources(commitments,activity_ref=dep_ref)
            except ValueError as exc:raise CommandRejectedError("jianghu_deployment_commitment_missing") from exc
            writes_records={_DEPLOYMENTS:state,_COMMITMENTS:commitments}
            resumable=self._resumable_after_commitment_release(member_refs,commitments)
            if resumable:
                resume_fpath,resume_faction,rpath,resumed=self._resume_institutional_training_now(resumable,current_time)
                writes_records.update({resume_fpath:resume_faction,rpath:resumed})
            return self._simple_plan(command,meta,current_time,writes_records=writes_records,code="jianghu_deployment_release_ready",result={"command_type":command.command_type,"deployment_ref":dep_ref,"status":"released"})
        refs=command.payload.get("member_refs")
        if not isinstance(refs,(list,tuple)) or not refs or any(not isinstance(x,str) for x in refs) or len(set(refs))!=len(refs) or command.actor_id not in refs: raise CommandRejectedError("jianghu_deployment_members_invalid")
        records={}; locations=set(); unavailable=self._unavailable_person_refs()
        for ref in refs:
            _,_,_,p=self._person(ref)
            if ref in unavailable or p.get("faction_ref")!=faction_ref or not _person_is_usable(p): raise CommandRejectedError("jianghu_deployment_member_unavailable")
            records[ref]=p; locations.add(p.get("location_ref"))
        if len(locations)!=1: raise CommandRejectedError("jianghu_deployment_members_not_colocated")
        offices=set(actor.get("standing_offices",[])) if isinstance(actor.get("standing_offices"),list) else set()
        if not ({"leader","deputy_leader","field_commander","deputy_field_commander"}&{str(x).split(':',1)[0] for x in offices}): raise CommandRejectedError("jianghu_deployment_not_authorized")
        try: structure=build_deployment_structure(member_refs=list(refs),records=records,preferred_commander_ref=command.actor_id)
        except ValueError as exc: raise CommandRejectedError("jianghu_deployment_structure_invalid") from exc
        validate_deployment_structure(structure)
        commitments=copy.deepcopy(self.repository.read_json(_COMMITMENTS))
        try:commitments=reserve_resources(commitments,resources=[("person",ref,faction_ref) for ref in refs],actor_ref=command.actor_id,owner_ref=faction_ref,activity_ref=dep_ref,activity_kind="deployment",started_at=str(current_time).removeprefix("SE-"),location_ref=str(next(iter(locations))))
        except ValueError as exc:raise CommandRejectedError("jianghu_deployment_member_already_committed") from exc
        deployments[dep_ref]={"deployment_ref":dep_ref,"faction_ref":faction_ref,"created_at":str(current_time).removeprefix("SE-"),"location_ref":next(iter(locations)),"objective":str(command.payload.get("objective")),"structure":structure,"status":"active","commitment_ref":f"commitment:{dep_ref}"}
        pause_fpath, paused_faction, pause_path, paused_roster = self._pause_institutional_training_now(list(refs), current_time)
        return self._simple_plan(command,meta,current_time,writes_records={_DEPLOYMENTS:state,_COMMITMENTS:commitments,pause_fpath:paused_faction,pause_path:paused_roster},code="jianghu_deployment_form_ready",result={"command_type":command.command_type,"deployment_ref":dep_ref,"kind":structure["kind"],"headcount":structure["deployment_headcount"],"commander_ref":structure["commander_ref"],"deputy_ref":structure["deputy_ref"]})

    def _jianghu_infrastructure_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        self._require_jianghu(meta)
        action=command.payload.get("action")
        if action=="start_building": expected={"action","faction_ref","project_ref","building_type","target_level"}
        elif action=="expand_building": expected={"action","faction_ref","project_ref","building_type","additional_footprint_m2"}
        elif action=="expand_estate": expected={"action","faction_ref","project_ref","additional_land_m2"}
        elif action=="start_enterprise": expected={"action","faction_ref","project_ref","enterprise_type","target_level"}
        elif action=="expand_enterprise": expected={"action","faction_ref","project_ref","enterprise_type","additional_scale"}
        elif action=="advance": expected={"action","project_ref","target_time"}
        else: raise CommandRejectedError("jianghu_infrastructure_action_invalid")
        if set(command.payload)!=expected: raise CommandRejectedError("jianghu_infrastructure_resolution_payload_fields_invalid")
        project_ref=_stable_text(command.payload.get("project_ref"),"jianghu_project_invalid")
        projects=copy.deepcopy(self.repository.read_json(_PROJECTS)); registry=projects.setdefault("projects",{})
        _,_,_,actor=self._person(command.actor_id); actor_faction=str(actor.get("faction_ref") or "")
        if action in {"start_building","expand_building","expand_estate","start_enterprise","expand_enterprise"}:
            self._require_person_available_for_activity(command.actor_id)
            faction_ref=_stable_text(command.payload.get("faction_ref"),"jianghu_faction_invalid")
            if faction_ref!=actor_faction: raise CommandRejectedError("jianghu_infrastructure_not_authorized")
            office_keys={str(x).split(':',1)[0] for x in actor.get("standing_offices",[]) if isinstance(x,str)}
            if not ({"leader","deputy_leader","chief_steward","treasurer"}&office_keys): raise CommandRejectedError("jianghu_infrastructure_not_authorized")
            if project_ref in registry: raise CommandRejectedError("jianghu_project_conflict")
            fpath,faction=read_faction(self.repository,faction_ref); ipath=_inventory_path(faction_ref); inv=copy.deepcopy(self.repository.read_json(ipath))
            target=int(command.payload.get("target_level",0) or 0); skill=max(int(actor.get("professional_skills",{}).get("crafting",0)),int(actor.get("professional_skills",{}).get("administration",0))) if isinstance(actor.get("professional_skills"),Mapping) else 0
            try:
                if action=="start_building":
                    typ=str(command.payload.get("building_type")); cur=int(faction.get("buildings",{}).get(typ,0)); out=start_building_upgrade(treasury_cash=int(faction.get("treasury_cash",0)),material_stock=inv.get("raw_materials",{}),building_type=typ,current_level=cur,target_level=target,crafting_or_administration=skill)
                    faction["treasury_cash"]=out["treasury_cash_after_start"]; inv["raw_materials"]=out["material_stock_after_start"]
                elif action=="expand_building":
                    typ=str(command.payload.get("building_type")); added=int(command.payload.get("additional_footprint_m2",0) or 0)
                    reserved=sum(int(r.get("additional_footprint_m2",0) or 0) for r in registry.values() if isinstance(r,Mapping) and r.get("faction_ref")==faction_ref and r.get("project_type")=="building_expansion" and not r.get("completed"))
                    land=estate_land_summary(faction.get("infrastructure",{}))
                    if added>max(0,int(land.get("remaining_land_m2",0))-reserved): raise ValueError("insufficient unreserved estate land")
                    out=start_building_expansion(treasury_cash=int(faction.get("treasury_cash",0)),material_stock=inv.get("raw_materials",{}),buildings=faction.get("buildings",{}),infrastructure=faction.get("infrastructure",{}),building_type=typ,additional_footprint_m2=added,crafting_or_administration=skill)
                    faction["treasury_cash"]=out["treasury_cash_after_start"]; inv["raw_materials"]=out["material_stock_after_start"]
                elif action=="expand_estate":
                    # One estate boundary may expand at a time. Two concurrent
                    # geometry owners could otherwise buy overlapping adjacent
                    # parcels against the same old perimeter.
                    if any(isinstance(r,Mapping) and r.get("faction_ref")==faction_ref and r.get("project_type")=="estate_boundary_expansion" and not r.get("completed") for r in registry.values()):
                        raise ValueError("estate boundary expansion already active")
                    added_land=int(command.payload.get("additional_land_m2",0) or 0)
                    geography=self.repository.read_json(_GEOGRAPHY); places=geography.get("places",{}) if isinstance(geography,Mapping) else {}
                    headquarters=str(faction.get("headquarters") or ""); place=places.get(headquarters) if isinstance(places,Mapping) else None
                    if not isinstance(place,Mapping): raise ValueError("faction headquarters place missing")
                    out=start_estate_boundary_expansion(
                        treasury_cash=int(faction.get("treasury_cash",0)),material_stock=inv.get("raw_materials",{}),
                        infrastructure=faction.get("infrastructure",{}),walls_level=int(faction.get("buildings",{}).get("walls_gate",0)),
                        additional_land_m2=added_land,settlement_kind=str(place.get("kind") or "city"),
                    )
                    faction["treasury_cash"]=out["treasury_cash_after_start"]; inv["raw_materials"]=out["material_stock_after_start"]
                elif action=="start_enterprise":
                    typ=str(command.payload.get("enterprise_type")); cur=int(faction.get("enterprises",{}).get(typ,0)); scale=faction.get("enterprise_scale",{}).get(typ,{}) if isinstance(faction.get("enterprise_scale"),Mapping) else {}; operating_scale=max([0]+[int(v) for v in scale.values() if isinstance(v,int) and not isinstance(v,bool)])
                    out=start_enterprise_upgrade(treasury_cash=int(faction.get("treasury_cash",0)),enterprise_type=typ,current_level=cur,target_level=target,operating_scale=operating_scale,supporting_building_levels=faction.get("buildings",{})); faction["treasury_cash"]=out["treasury_cash_after_start"]
                else:
                    typ=str(command.payload.get("enterprise_type")); cur=int(faction.get("enterprises",{}).get(typ,0)); added=int(command.payload.get("additional_scale",0) or 0); basis=enterprise_scale_basis(typ)
                    current_scale=faction.get("enterprise_scale",{}).get(typ,{}) if isinstance(faction.get("enterprise_scale"),Mapping) else {}; current_value=int(current_scale.get(basis,0)) if isinstance(current_scale,Mapping) else 0; target_value=current_value+added
                    # Scale cannot outrun the actual physical/asset basis it claims to organize.
                    if typ=="agriculture_landholding":
                        holdings=faction.get("holdings",{}) if isinstance(faction.get("holdings"),Mapping) else {}; owned=max(0,int(holdings.get("rural_land_mu",0)))
                        if target_value>owned: raise ValueError("agriculture scale exceeds owned rural land")
                    elif typ=="crafting_workshop":
                        if target_value>workshop_capacity(faction.get("buildings",{}),faction.get("infrastructure",{})).get("craft_workstations",0): raise ValueError("crafting enterprise exceeds physical workstations")
                    elif typ=="medicine_apothecary":
                        if target_value>infirmary_capacity(faction.get("buildings",{}),faction.get("infrastructure",{})).get("apothecary_workstations",0): raise ValueError("medicine enterprise exceeds physical workstations")
                    elif typ == "escort_service":
                        transport=transport_yard_capacity(faction.get("buildings",{}),faction.get("infrastructure",{}))
                        rpath=canonical_roster_path(faction_ref)
                        logical_roster=hydrate_roster_state(self.repository.read_json(rpath),faction=faction)
                        roster_people=logical_roster.get("people",[]) if isinstance(logical_roster,Mapping) else []
                        ready=combat_ready_count(
                            [p for p in roster_people if isinstance(p,Mapping)],
                            year=current_time.year, unavailable_refs=self._unavailable_person_refs(),
                            minimum_age=14, minimum_combat_skill=20,
                        )
                        people_cap=max(0,ready)//4
                        transport_cap=max(0,int(transport.get("mount_or_pack_slots",0)))//4+max(0,int(transport.get("wagon_slots",0)))//2
                        if target_value>max(1,min(people_cap,transport_cap)): raise ValueError("contract enterprise exceeds people/transport capacity")
                    out=start_enterprise_scale_expansion(treasury_cash=int(faction.get("treasury_cash",0)),enterprise_type=typ,current_level=cur,additional_scale=added); faction["treasury_cash"]=out["treasury_cash_after_start"]
            except (KeyError,TypeError,ValueError) as exc: raise CommandRejectedError("jianghu_project_requirements_not_met") from exc
            roster_path=_roster_path(faction_ref); roster=self.repository.read_json(roster_path); people=roster.get("people",[]) if isinstance(roster,Mapping) else []
            commitments=copy.deepcopy(self.repository.read_json(_COMMITMENTS)); unavailable=self._unavailable_person_refs()
            available=[p for p in people if isinstance(p,Mapping) and isinstance(p.get("person_id"),str) and p.get("person_id") not in unavailable and p.get("health",{}).get("status") not in {"dead","incapacitated"}]
            min_days=max(1,int(out.get("minimum_calendar_days",1)))
            if out.get("project_type") in {"building_upgrade","building_expansion","estate_boundary_expansion"}:
                skilled_need=max(1,(int(out.get("skilled_labor_hours_remaining",0))+6*min_days-1)//(6*min_days))
                general_need=max(1,(int(out.get("general_labor_hours_remaining",0))+8*min_days-1)//(8*min_days))
                skilled=sorted(available,key=lambda p:(-max(int(p.get("professional_skills",{}).get("crafting",0)),int(p.get("professional_skills",{}).get("administration",0))),str(p.get("person_id"))))[:skilled_need]
                skilled_ids={str(p["person_id"]) for p in skilled}
                general=sorted((p for p in available if str(p["person_id"]) not in skilled_ids),key=lambda p:str(p.get("person_id")))[:general_need]
                if not skilled or not general: raise CommandRejectedError("jianghu_project_labor_unavailable")
                out["skilled_worker_refs"]=[str(p["person_id"]) for p in skilled]; out["general_worker_refs"]=[str(p["person_id"]) for p in general]
            else:
                management_need=max(1,(int(out.get("management_labor_hours_remaining",0))+4*min_days-1)//(4*min_days))
                general_need=max(1,(int(out.get("general_setup_labor_hours_remaining",0))+4*min_days-1)//(4*min_days))
                managers=sorted(available,key=lambda p:(-max(int(p.get("professional_skills",{}).get("administration",0)),int(p.get("professional_skills",{}).get("commerce",0))),str(p.get("person_id"))))[:management_need]
                manager_ids={str(p["person_id"]) for p in managers}
                general=sorted((p for p in available if str(p["person_id"]) not in manager_ids),key=lambda p:str(p.get("person_id")))[:general_need]
                if not managers or not general: raise CommandRejectedError("jianghu_project_labor_unavailable")
                out["management_worker_refs"]=[str(p["person_id"]) for p in managers]; out["general_worker_refs"]=[str(p["person_id"]) for p in general]
            worker_refs=list(dict.fromkeys(out.get("skilled_worker_refs",[])+out.get("management_worker_refs",[])+out.get("general_worker_refs",[])))
            try:commitments=reserve_resources(commitments,resources=[("person",ref,faction_ref) for ref in worker_refs],actor_ref=command.actor_id,owner_ref=faction_ref,activity_ref=project_ref,activity_kind="construction" if out.get("project_type") in {"building_upgrade","building_expansion","estate_boundary_expansion"} else "enterprise_setup",started_at=str(current_time).removeprefix("SE-"),location_ref=str(faction.get("local_site_ref") or faction.get("headquarters") or ""))
            except ValueError as exc: raise CommandRejectedError("jianghu_project_labor_unavailable") from exc
            out.update({"project_ref":project_ref,"faction_ref":faction_ref})
            registry[project_ref]=out
            pause_fpath, paused_faction, pause_path, paused_roster = self._pause_institutional_training_now(worker_refs, current_time, faction_override=faction)
            faction = paused_faction
            return self._simple_plan(command,meta,current_time,writes_records={_PROJECTS:projects,fpath:faction,ipath:inv,_COMMITMENTS:commitments,pause_path:paused_roster},code="jianghu_project_start_ready",result={"command_type":command.command_type,"project_ref":project_ref,"project_type":out["project_type"],"target_level":target if target else None,"additional_footprint_m2":int(out.get("additional_footprint_m2",0)),"additional_land_m2":int(out.get("additional_land_m2",0)),"worker_refs":worker_refs})
        row=registry.get(project_ref)
        if not isinstance(row,Mapping): raise CommandRejectedError("jianghu_project_unresolved")
        faction_ref=str(row.get("faction_ref") or "")
        if faction_ref!=actor_faction: raise CommandRejectedError("jianghu_infrastructure_not_authorized")
        try: target=CampaignTime.parse(command.payload.get("target_time"))
        except (TypeError,ValueError) as exc: raise CommandRejectedError("jianghu_project_target_time_invalid") from exc
        seconds=int((datetime(target.year,target.month,target.day,target.hour,target.minute,target.second)-datetime(current_time.year,current_time.month,current_time.day,current_time.hour,current_time.minute,current_time.second)).total_seconds())
        if seconds<=0: raise CommandRejectedError("jianghu_project_target_time_invalid")
        time_plan=self._time_plan_exact(command,meta,current_time,seconds=seconds)
        days=max(0,seconds//86400); rpath,current_faction=read_faction(self.repository,faction_ref)
        # Long project advances may cross monthly/annual frontiers. Rebase every
        # mutable owner we touch onto the scheduler's after-image before
        # applying project labor/completion, otherwise a project command can
        # resurrect a commitment or discard another causal update.
        projects_after=copy.deepcopy(self._time_after_record(time_plan,_PROJECTS,projects))
        registry_after=projects_after.setdefault("projects",{})
        row_after=registry_after.get(project_ref)
        if not isinstance(row_after,Mapping): raise CommandRejectedError("jianghu_project_unresolved_after_time")
        row=copy.deepcopy(dict(row_after))
        roster_path=_roster_path(faction_ref)
        faction_after_raw=self._time_after_record(time_plan,rpath,current_faction)
        faction=hydrate_faction_state(faction_after_raw)
        roster_fallback=self.repository.read_json(roster_path)
        roster_after_raw=self._time_after_record(time_plan,roster_path,roster_fallback)
        roster_after=hydrate_roster_state(roster_after_raw,faction=faction)
        target_iso=str(target).removeprefix("SE-")
        # Settle all training through the exact completion frontier under the
        # pre-completion environment before any facility level changes.
        faction,_training_summary=advance_faction_training_epoch(
            faction,roster_after,at_iso=target_iso,
            refresh_environment=False,
        )
        commitments_before=self.repository.read_json(_COMMITMENTS)
        commitments=copy.deepcopy(self._time_after_record(time_plan,_COMMITMENTS,commitments_before))
        active=commitments.get("commitments",{}).get(f"commitment:{project_ref}") if isinstance(commitments.get("commitments"),Mapping) else None
        if not isinstance(active,Mapping) or active.get("status","active")!="active": raise CommandRejectedError("jianghu_project_labor_commitment_missing")
        general_workers=len(row.get("general_worker_refs",[])); skilled_workers=len(row.get("skilled_worker_refs",[])); management_workers=len(row.get("management_worker_refs",[]))
        if general_workers<=0 or (row.get("project_type") in {"building_upgrade","building_expansion","estate_boundary_expansion"} and skilled_workers<=0) or (row.get("project_type") in {"enterprise_upgrade","enterprise_scale_expansion"} and management_workers<=0): raise CommandRejectedError("jianghu_project_labor_unavailable")
        try:
            if row.get("project_type")=="building_upgrade": updated=advance_building_upgrade(row,elapsed_calendar_days=days,general_labor_hours=general_workers*8*days,skilled_labor_hours=skilled_workers*6*days)
            elif row.get("project_type")=="building_expansion": updated=advance_building_expansion(row,elapsed_calendar_days=days,general_labor_hours=general_workers*8*days,skilled_labor_hours=skilled_workers*6*days)
            elif row.get("project_type")=="estate_boundary_expansion": updated=advance_estate_boundary_expansion(row,elapsed_calendar_days=days,general_labor_hours=general_workers*8*days,skilled_labor_hours=skilled_workers*6*days)
            elif row.get("project_type")=="enterprise_upgrade": updated=advance_enterprise_upgrade(row,elapsed_calendar_days=days,management_labor_hours=management_workers*4*days,general_setup_labor_hours=general_workers*4*days)
            elif row.get("project_type")=="enterprise_scale_expansion": updated=advance_enterprise_scale_expansion(row,elapsed_calendar_days=days,management_labor_hours=management_workers*4*days,general_setup_labor_hours=general_workers*4*days)
            else: raise ValueError('unknown project')
        except ValueError as exc: raise CommandRejectedError("jianghu_project_progress_invalid") from exc
        registry_after[project_ref]=updated
        if updated.get("completed"):
            if updated["project_type"]=="building_upgrade":
                faction.setdefault("buildings",{})[updated["building_type"]]=int(updated["target_level"])
            elif updated["project_type"]=="building_expansion":
                facilities=faction.setdefault("infrastructure",{}).setdefault("facilities",{})
                facility=facilities.setdefault(updated["building_type"],{})
                facility["footprint_m2"]=max(0,int(facility.get("footprint_m2",0)))+int(updated["additional_footprint_m2"])
            elif updated["project_type"]=="estate_boundary_expansion":
                infrastructure=faction.setdefault("infrastructure",{}); facilities=infrastructure.setdefault("facilities",{})
                walls=facilities.setdefault("walls_gate",{}); old_perimeter=max(1,int(walls.get("defended_perimeter_m",updated.get("old_perimeter_m",1))))
                old_footprint=max(0,int(walls.get("footprint_m2",0))); new_perimeter=max(old_perimeter,int(updated["new_perimeter_m"]))
                # Preserve the existing wall's physical width while extending the
                # same outer boundary. Integer ceil avoids silently losing area.
                new_footprint=(old_footprint*new_perimeter+old_perimeter-1)//old_perimeter if old_footprint>0 else new_perimeter*2
                walls["defended_perimeter_m"]=new_perimeter; walls["footprint_m2"]=new_footprint
                walls["wall_height_m"]=max(1,int(updated.get("wall_height_m",walls.get("wall_height_m",4))))
                infrastructure["estate_area_m2"]=max(int(infrastructure.get("estate_area_m2",0)),int(updated["new_estate_area_m2"]))
                holdings=faction.setdefault("holdings",{}); holdings["urban_estate_area_m2"]=int(infrastructure["estate_area_m2"])
            elif updated["project_type"]=="enterprise_upgrade":
                faction.setdefault("enterprises",{})[updated["enterprise_type"]]=int(updated["target_level"])
            else:
                scales=faction.setdefault("enterprise_scale",{}); erow=scales.setdefault(updated["enterprise_type"],{}); basis=str(updated["scale_basis"]); erow[basis]=max(0,int(erow.get(basis,0)))+int(updated["additional_scale"])
                if updated["enterprise_type"]=="agriculture_landholding":
                    holdings=faction.setdefault("holdings",{}); holdings["cultivated_land_mu"]=min(max(0,int(holdings.get("rural_land_mu",0))),max(0,int(holdings.get("cultivated_land_mu",0)))+int(updated["additional_scale"]))
            # Rotate the immutable environment at the same exact frontier, so
            # the new facility never rewrites training that happened earlier.
            faction,_rotation=advance_faction_training_epoch(faction,roster_after,at_iso=target_iso)
            commitments=release_resources(commitments,activity_ref=project_ref)
            worker_refs=list(dict.fromkeys(row.get("skilled_worker_refs",[])+row.get("management_worker_refs",[])+row.get("general_worker_refs",[])))
            resumable=self._resumable_after_commitment_release(worker_refs,commitments)
            if resumable:
                epoch_days=int((faction.get("training_epoch") or {}).get("elapsed_training_days",0)) if isinstance(faction.get("training_epoch"),Mapping) else 0
                roster_after_raw=self._resume_institutional_training_in_roster(roster_after_raw,resumable,epoch_days=epoch_days)
            # Completed projects cease to be active owners. The resulting
            # building/enterprise level is the permanent current fact.
            registry_after.pop(project_ref,None)
        return self._combine_time_plan(command,time_plan,extra_records={_PROJECTS:projects_after,rpath:faction,roster_path:roster_after_raw,_COMMITMENTS:commitments},code="jianghu_project_advance_ready",result={"command_type":command.command_type,"project_ref":project_ref,"completed":bool(updated.get("completed")),"active_level":int(updated.get("active_level",0)),"additional_footprint_m2":int(updated.get("additional_footprint_m2",0)),"additional_land_m2":int(updated.get("additional_land_m2",0)),"additional_scale":int(updated.get("additional_scale",0))})

    def _jianghu_recruitment_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        self._require_jianghu(meta)
        if set(command.payload)!={"faction_ref","place_ref","requested_count"}: raise CommandRejectedError("jianghu_recruitment_resolution_payload_fields_invalid")
        faction_ref=_stable_text(command.payload.get("faction_ref"),"jianghu_faction_invalid"); place_ref=_stable_text(command.payload.get("place_ref"),"jianghu_place_invalid"); requested=int(command.payload.get("requested_count",0))
        if requested<=0: raise CommandRejectedError("jianghu_recruitment_count_invalid")
        _path,_,_,actor=self._person(command.actor_id)
        if actor.get("faction_ref")!=faction_ref: raise CommandRejectedError("jianghu_recruitment_not_authorized")
        office_keys={str(x).split(':',1)[0] for x in actor.get("standing_offices",[]) if isinstance(x,str)}
        if not ({"leader","deputy_leader","chief_steward","field_commander"}&office_keys): raise CommandRejectedError("jianghu_recruitment_not_authorized")
        self._require_person_available_for_activity(command.actor_id)
        sites=self.repository.read_json(_LOCAL_SITES).get("sites",{})
        actor_site=sites.get(str(actor.get("location_ref"))) if isinstance(sites,Mapping) else None
        if not isinstance(actor_site,Mapping) or str(actor_site.get("parent_place_ref") or "")!=place_ref:
            raise CommandRejectedError("jianghu_recruitment_requires_presence_in_place")

        # Recruitment occupies the responsible officer for a real week. The
        # scheduler sees that reservation throughout the interval, and the
        # final intake is rebased onto whatever lawful world changes occurred
        # during the week instead of overwriting pre-time snapshots.
        time_plan, final_records, target_time = self._timed_person_activity_plan(
            command, meta, current_time,
            person_refs=[command.actor_id], seconds=7*86400,
            activity_ref=f"recruitment:{command.request_id}", activity_kind="recruitment",
            owner_ref=faction_ref, location_ref=str(actor.get("location_ref") or ""),
        )

        fpath=_faction_path(faction_ref); roster_path=_roster_path(faction_ref)
        faction=copy.deepcopy(dict(final_records.get(fpath) or self._time_after_record(time_plan,fpath,read_faction(self.repository,faction_ref)[1])))
        roster=copy.deepcopy(dict(final_records.get(roster_path) or self._time_after_record(time_plan,roster_path,self.repository.read_json(roster_path))))
        people=roster.get("people")
        if not isinstance(people,list): raise CommandRejectedError("jianghu_roster_invalid")
        actor_after=next((p for p in people if isinstance(p,Mapping) and p.get("person_id")==command.actor_id),None)
        if not isinstance(actor_after,Mapping): raise CommandRejectedError("jianghu_person_unresolved")

        policy=faction.get("recruitment_policy",{}) if isinstance(faction.get("recruitment_policy"),Mapping) else {}
        maximum=max(0,int(policy.get("maximum_intake_per_season",0)))
        season_id=f"{target_time.year:04d}-Q{((target_time.month-1)//3)+1}"
        season=faction.get("recruitment_season",{}) if isinstance(faction.get("recruitment_season"),Mapping) else {}
        intake_used=int(season.get("intake_used",0)) if season.get("season_id")==season_id else 0
        if requested>max(0,maximum-intake_used): raise CommandRejectedError("jianghu_recruitment_exceeds_seasonal_capacity")

        # A faction with an explicit residential compound cannot silently add
        # permanent residents beyond that physical capacity. Factions without
        # such a facility are not forced into an invented housing model.
        resident_cap=residential_capacity(faction.get("buildings",{}),faction.get("infrastructure",{}))
        if resident_cap>0 and int(faction.get("population",0))+requested>resident_cap:
            raise CommandRejectedError("jianghu_residential_capacity_exceeded")

        civilians=self._time_after_record(time_plan,_CIVILIANS,self.repository.read_json(_CIVILIANS))
        pool=civilians.get("places",{}).get(place_ref) if isinstance(civilians.get("places"),Mapping) else None
        if not isinstance(pool,dict): raise CommandRejectedError("jianghu_civilian_pool_unresolved")
        available=int(pool.get("current_population",0))-int(pool.get("reserved_for_recruitment",0))
        if available<=0: raise CommandRejectedError("jianghu_civilian_pool_empty")

        cursor=max(0,int(pool.get("recruitment_ordinal_cursor",0)))
        evaluator=max(int(actor_after.get("professional_skills",{}).get("administration",0)),int(actor_after.get("attributes",{}).get("perception",0)))
        accepted=[]; examined=0; world_seed=str(meta.get("world_seed") or _WORLD_SEED_DEFAULT)
        limit=min(available,max(requested,requested*12))
        for offset in range(limit):
            cand=deterministic_candidate(world_seed=world_seed,origin_population_id=place_ref,ordinal=cursor+offset)
            examined+=1
            report=screening_report(cand,evaluator_skill=evaluator)
            if int(cand["aptitudes"]["martial"])<int(policy.get("minimum_martial_aptitude",0)) or int(cand["aptitudes"]["qi"])<int(policy.get("minimum_qi_aptitude",0)): continue
            if not report.get("eligible",True): continue
            accepted.append(cand)
            if len(accepted)>=requested: break
        pool["recruitment_ordinal_cursor"]=cursor+examined
        if not accepted: raise CommandRejectedError("jianghu_recruitment_no_eligible_candidates")
        if len(accepted)>available: raise CommandRejectedError("jianghu_civilian_pool_insufficient")
        pool["current_population"]=int(pool["current_population"])-len(accepted)

        existing_names={str(p.get("name")) for p in people if isinstance(p,Mapping) and isinstance(p.get("name"),str)}
        materialized_ids=[]
        for cand in accepted:
            pid=f"mw.recruit.{hashlib.sha256((world_seed+'|'+place_ref+'|'+str(cand['origin_ordinal'])).encode()).hexdigest()[:24]}"
            if any(isinstance(row,Mapping) and row.get("person_id")==pid for row in people): raise CommandRejectedError("jianghu_recruitment_identity_conflict")
            age=max(0,int(cand["age"])); sex=deterministic_sex(stable=pid,faction_id=faction_ref)
            name=None
            for attempt in range(64):
                proposal=deterministic_name(stable=f"{pid}:{attempt}",sex=sex)
                if proposal not in existing_names: name=proposal; break
            if name is None: raise CommandRejectedError("jianghu_recruitment_name_space_exhausted")
            existing_names.add(name)
            professional={"medicine":0,"administration":0,"commerce":0,"crafting":0,"instruction":0}
            developed=apply_age_development(age=age,attributes=cand["attributes"],martial_skills=cand["martial_skills"],professional_skills=professional,qi=0,qi_control=0)
            peak=max(developed["martial_skills"].values(),default=0); grade="junior" if peak>=25 else "probationary"
            person={"person_id":pid,"name":name,"birth_year":target_time.year-age,"sex":sex,"body_mass_kg":deterministic_body_mass_kg(stable=pid,sex=sex,age=age),"appearance":int(cand["appearance"]),"aptitudes":copy.deepcopy(cand["aptitudes"]),"attributes":developed["attributes"],"martial_skills":developed["martial_skills"],"professional_skills":developed["professional_skills"],"qi":developed["qi"],"qi_control":developed["qi_control"],"membership_grade":grade,"personal_cash":0}
            people.append(person); materialized_ids.append(pid)

        faction["population"]=int(faction.get("population",0))+len(accepted)
        faction["recruitment_season"]={"season_id":season_id,"intake_used":intake_used+len(accepted)}
        roster=compact_roster_state(roster,faction=faction)

        route_records={}
        rows=roster.get("people",[]) if isinstance(roster,Mapping) else []
        for ordinal,person in enumerate(rows if isinstance(rows,list) else []):
            pid=str(person.get("person_id") or "") if isinstance(person,Mapping) else ""
            if pid not in materialized_ids: continue
            bucket=hashlib.sha256(pid.encode()).hexdigest()[:2]; rpath=f"state/martial-world/person-routes/{bucket}.json"
            shard=route_records.get(rpath)
            if shard is None:
                shard=self._time_after_record(time_plan,rpath,self.repository.read_json(rpath)); route_records[rpath]=shard
            shard.setdefault("people",{})[pid]=[faction_ref,ordinal]
        index_path="state/martial-world/person-routes.json"
        pindex=self._time_after_record(time_plan,index_path,self.repository.read_json(index_path)); pindex["person_count"]=int(pindex.get("person_count",0))+len(accepted)

        final_records.update({_CIVILIANS:civilians,fpath:faction,roster_path:roster,index_path:pindex,**route_records})
        return self._combine_time_plan(command,time_plan,extra_records=final_records,code="jianghu_recruitment_ready",result={"command_type":command.command_type,"faction_ref":faction_ref,"place_ref":place_ref,"examined":examined,"accepted_count":len(accepted),"accepted_person_refs":materialized_ids})
