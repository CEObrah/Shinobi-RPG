"""Jianghu-native public semantic commands.

These reducers write current Jianghu authorities directly and preserve one mechanical authority.
"""
from __future__ import annotations

import copy, hashlib, json
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

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
from shinobi_runtime.martial_world.tournaments import register as tournament_register, advance_individual_competition, placement_payouts as tournament_placement_payouts, event_profile as tournament_event_profile
from shinobi_runtime.martial_world.rankings import add_public_points
from shinobi_runtime.martial_world.field_command import build_deployment_structure, validate_deployment_structure
from shinobi_runtime.martial_world.commitments import derived_commitment_state, reserve_resources, release_resources
from shinobi_runtime.martial_world.infrastructure import (
    start_building_upgrade, advance_building_upgrade,
    start_building_expansion, advance_building_expansion, estate_land_summary,
    start_estate_boundary_expansion, advance_estate_boundary_expansion,
    start_enterprise_upgrade, advance_enterprise_upgrade,
    start_enterprise_scale_expansion, advance_enterprise_scale_expansion, enterprise_scale_basis,
    workshop_capacity, infirmary_capacity, transport_yard_capacity, residential_capacity, compact_project_state,
)
from shinobi_runtime.martial_world.recruitment import deterministic_candidate, screening_report
from shinobi_runtime.martial_world.people import apply_age_development, deterministic_body_mass_kg, deterministic_name, deterministic_sex
from shinobi_runtime.martial_world.faction_state import (
    compact_faction_state, faction_admission_policy, faction_path as canonical_faction_path, hydrate_faction_state,
    inventory_path as canonical_inventory_path, read_faction, roster_path as canonical_roster_path,
)
from shinobi_runtime.martial_world.inventory_state import compact_inventory_state
from shinobi_runtime.martial_world.civilian_state import compact_civilian_state
from shinobi_runtime.martial_world.equipment_state import compact_equipment_ledger
from shinobi_runtime.martial_world.social_state import compact_social_state
from shinobi_runtime.martial_world.training import advance_faction_training_epoch, apply_institutional_training, institutional_training_pause_refs, settle_and_reset_faction_training_cycle, training_epoch_elapsed_days
from shinobi_runtime.martial_world.person_state import compact_roster_state, hydrate_roster_state, reconcile_faction_population
from shinobi_runtime.martial_world.scheduler import sync_route_activity, upsert_one_off_event
from shinobi_runtime.martial_world.manpower import combat_ready_count, is_living_and_conscious
from shinobi_runtime.martial_world.health import functional_capacity_factors
from shinobi_runtime.martial_world.physical_presence import effective_person_presence, physical_unavailable_person_refs, same_effective_location
from shinobi_runtime.martial_world.scene_sessions import close_active_session_writes
from shinobi_runtime.martial_world.regional_economy import region_for_place
from shinobi_runtime.martial_world.medicine import stabilize_wounds

_CONTRACTS = "state/martial-world/contracts/index.json"
_TOURNAMENTS = "state/martial-world/tournaments.json"
_REPUTATION = "state/martial-world/reputation.json"
_EQUIPMENT_LEDGER = "state/martial-world/equipment-ledger.json"
_COMBATS = "state/martial-world/combats.json"
_DEPLOYMENTS = "state/martial-world/deployments.json"
_PROJECTS = "state/martial-world/projects.json"
_CIVILIANS = "state/martial-world/civilian-populations.json"
_CUSTODY = "state/martial-world/custody.json"
_ROUTE_OPS = "state/martial-world/route-operations.json"
_SCHEDULE = "state/martial-world/scheduler.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_TRAVEL_DATA = "game/data/martial-world/travel.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_WORLD_SEED_DEFAULT = "jianghu"
_TRAINING_FOCI = frozenset({"sword","spear","bow","unarmed","hidden_weapons","stealth_scouting","command","qi","qi_control","standing_faction_curriculum"})


class _RecordOverlayRepository:
    """Read-only repository view over staged current facts.

    Full-horizon time settlement can cross many deterministic frontiers in one
    command. The underlying committed repository is immutable for that whole
    planning pass, so unchanged bytes/JSON may be safely cached and shared
    across successive overlay views. Staged after-images always shadow the
    committed cache. Every JSON read still returns a deep copy so reducers
    cannot mutate cached authority by reference.
    """

    def __init__(
        self,
        repository: Any,
        staged_records: Mapping[str, Mapping[str, Any]],
        *,
        base_bytes_cache: dict[str, bytes | None] | None = None,
        base_json_cache: dict[str, Any] | None = None,
    ) -> None:
        self._repository = repository
        self._base_bytes_cache = base_bytes_cache if base_bytes_cache is not None else {}
        self._base_json_cache = base_json_cache if base_json_cache is not None else {}
        self._image_json = {str(path): copy.deepcopy(dict(record)) for path, record in staged_records.items()}
        self._images = {
            path: _json_bytes(_canonical_write_record(path, record))
            for path, record in self._image_json.items()
        }

    def read_optional_bytes(self, path: object) -> bytes | None:
        key = str(path)
        if key in self._images:
            return self._images[key]
        if key not in self._base_bytes_cache:
            self._base_bytes_cache[key] = self._repository.read_optional_bytes(path)
        return self._base_bytes_cache[key]

    def read_bytes(self, path: object) -> bytes:
        raw = self.read_optional_bytes(path)
        if raw is None:
            raise FileNotFoundError(str(path))
        return raw

    def read_json(self, path: object) -> Any:
        key = str(path)
        if key in self._image_json:
            return copy.deepcopy(self._image_json[key])
        if key not in self._base_json_cache:
            raw = self.read_optional_bytes(path)
            if raw is None:
                raise FileNotFoundError(key)
            self._base_json_cache[key] = json.loads(raw.decode("utf-8"))
        return copy.deepcopy(self._base_json_cache[key])

    def set_record(self, path: object, record: Mapping[str, Any], *, raw: bytes | None = None) -> None:
        """Replace one staged after-image without rebuilding the whole view."""
        key = str(path)
        value = copy.deepcopy(dict(record))
        self._image_json[key] = value
        self._images[key] = raw if raw is not None else _json_bytes(_canonical_write_record(key, value))


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



class JianghuCommandsMixin:
    def _require_jianghu(self, meta: Mapping[str, Any]) -> None:
        if meta.get("game") != "jianghu":
            raise CommandRejectedError("jianghu_campaign_required")

    def _person(self, ref: str) -> tuple[str, dict[str, Any], int, dict[str, Any]]:
        try:
            return roster_person(self.repository, ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_person_unresolved") from exc

    def _effective_person_presence(self, ref: str, person: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if person is None:
            _path, _roster, _ordinal, person = self._person(ref)
        return effective_person_presence(self.repository.read_json, str(ref), person=person)

    def _effective_person_location(self, ref: str, person: Mapping[str, Any] | None = None) -> str:
        presence = self._effective_person_presence(ref, person)
        return str(presence.get("location_ref") or "")

    def _physically_unavailable_person_refs(self) -> set[str]:
        return physical_unavailable_person_refs(self.repository.read_json)

    def _same_effective_location(self, left_ref: str, right_ref: str) -> bool:
        try:
            _lp, _lr, _lo, left = self._person(str(left_ref))
            _rp, _rr, _ro, right = self._person(str(right_ref))
            return same_effective_location(
                self.repository.read_json, str(left_ref), str(right_ref),
                left_person=left, right_person=right,
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, CommandRejectedError):
            return False

    def _custody_person_refs(self) -> set[str]:
        custody = self.repository.read_json(_CUSTODY)
        records = custody.get("records", []) if isinstance(custody, Mapping) else []
        if not isinstance(records, list):
            return set()
        return {
            str(row.get("person_ref")) for row in records
            if isinstance(row, Mapping)
            and isinstance(row.get("person_ref"), str)
            and row.get("status") not in {"released", "escaped", "rescued", "executed"}
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
        commitments = derived_commitment_state(self.repository.read_json)
        index = commitments.get("person_index", {}) if isinstance(commitments, Mapping) else {}
        refs = {str(x) for x in index} if isinstance(index, Mapping) else set()
        return refs | self._physically_unavailable_person_refs() | self._custody_person_refs() | self._active_combat_person_refs()

    def _scene_transition_records(self, *, at: str, reason: str) -> dict[str, Mapping[str, Any]]:
        """Close reversible scene continuity before a hard physical transition.

        These records are presentation/history authorities only. They never move
        a person, consume time, or establish the transition itself.
        """
        try:
            rows = close_active_session_writes(self.repository.read_json, at=str(at), reason=reason)
        except ValueError as exc:
            raise CommandRejectedError("jianghu_scene_transition_invalid") from exc
        return {str(path): copy.deepcopy(dict(row)) for path, row in rows.items()}

    def _active_commitment_for_person(self, ref: str) -> Mapping[str, Any] | None:
        commitments = derived_commitment_state(self.repository.read_json)
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
        if str(ref) in self._physically_unavailable_person_refs():
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
            if not is_living_and_conscious(person):
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

    def _resumable_after_commitment_release(
        self,
        refs: Sequence[str],
        commitments_after: Mapping[str, Any],
        *,
        read_json: Callable[[str], Any] | None = None,
    ) -> list[str]:
        index = commitments_after.get("person_index", {}) if isinstance(commitments_after, Mapping) else {}
        still_committed = {str(x) for x in index} if isinstance(index, Mapping) else set()
        blocked = still_committed | physical_unavailable_person_refs(read_json or self.repository.read_json)
        return [str(ref) for ref in refs if isinstance(ref, str) and str(ref) not in blocked]

    def _activity_after_read(self, time_plan: _BuiltPlan, path: str) -> Any:
        raw = time_plan.writes.get(path)
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        return self.repository.read_json(path)

    def _derived_activity_after_plan(self, time_plan: _BuiltPlan) -> dict[str, Any]:
        return derived_commitment_state(lambda path: self._activity_after_read(time_plan, path))

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
        current_commitments = derived_commitment_state(self.repository.read_json)
        current_index = current_commitments.get("person_index", {}) if isinstance(current_commitments, Mapping) else {}
        current_busy = set(str(ref) for ref in current_index if isinstance(ref, str)) if isinstance(current_index, Mapping) else set()
        current_busy.update(self._physically_unavailable_person_refs())
        paused_now = institutional_training_pause_refs(
            faction, [p for p in roster.get("people", []) if isinstance(p, Mapping)], unavailable_refs=sorted(current_busy),
        )
        faction, roster, _summary = settle_and_reset_faction_training_cycle(
            faction, roster, at_iso=at_iso, paused_refs=paused_now,
        )
        people = roster.get("people", [])
        if not isinstance(people, list):
            raise CommandRejectedError("jianghu_roster_invalid")
        indices = {str(row.get("person_id")): i for i, row in enumerate(people) if isinstance(row, Mapping) and isinstance(row.get("person_id"), str)}
        for ref in refs:
            idx = indices.get(str(ref))
            if idx is None:
                raise CommandRejectedError("jianghu_commitment_member_unresolved")
            person = copy.deepcopy(dict(people[idx]))
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
        current_commitments = derived_commitment_state(self.repository.read_json)
        current_index = current_commitments.get("person_index", {}) if isinstance(current_commitments, Mapping) else {}
        current_busy = set(str(ref) for ref in current_index) if isinstance(current_index, Mapping) else set()
        current_busy.update(self._physically_unavailable_person_refs())
        current_busy.update(str(ref) for ref in refs if isinstance(ref, str))
        paused_through_release = institutional_training_pause_refs(
            faction, [p for p in roster.get("people", []) if isinstance(p, Mapping)], unavailable_refs=sorted(current_busy),
        )
        faction, roster, _summary = settle_and_reset_faction_training_cycle(
            faction, roster, at_iso=at_iso, paused_refs=paused_through_release,
        )
        people = roster.get("people", [])
        if not isinstance(people, list):
            raise CommandRejectedError("jianghu_roster_invalid")
        indices = {str(row.get("person_id")): i for i, row in enumerate(people) if isinstance(row, Mapping) and isinstance(row.get("person_id"), str)}
        for ref in refs:
            idx = indices.get(str(ref))
            if idx is None:
                raise CommandRejectedError("jianghu_commitment_member_unresolved")
            person = copy.deepcopy(dict(people[idx]))
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
        allow_hard_interrupt: bool = False,
        stop_on_soft_interrupt: bool = False,
        handoff_matcher: Callable[[Mapping[str, Any]], bool] | None = None,
        include_unmatched_handoffs: bool = True,
        persist_staged_records: bool = False,
        max_frontiers: int | None = None,
    ) -> _BuiltPlan:
        """Advance an occupied activity through all quiet causal frontiers.

        The primitive time reducer settles one frontier at a time. Timed semantic
        commands and broad player waits instead walk those deterministic frontiers
        internally so their activity remains visible until completion or a lawful
        interruption. This helper follows non-player continuations in an in-memory
        overlay and emits one final atomic after-image. Callers decide whether soft
        handoffs stop the span, whether unmatched notices are returned, and whether
        a hard boundary may commit the partial span instead of rejecting it.
        """
        if seconds < 0:
            raise CommandRejectedError("jianghu_duration_invalid")
        if seconds == 0:
            raise CommandRejectedError("jianghu_zero_duration_internal")
        if max_frontiers is not None and (not isinstance(max_frontiers, int) or isinstance(max_frontiers, bool) or max_frontiers <= 0):
            raise CommandRejectedError("jianghu_internal_frontier_budget_invalid")

        target = current_time.add_seconds(seconds)
        staged = {str(path): copy.deepcopy(dict(record)) for path, record in staged_records.items()}
        shared_base_bytes: dict[str, bytes | None] = {}
        shared_base_json: dict[str, Any] = {}
        base_view = _RecordOverlayRepository(
            self.repository, staged,
            base_bytes_cache=shared_base_bytes, base_json_cache=shared_base_json,
        )
        images: dict[str, Mapping[str, Any]] = dict(staged)
        view = _RecordOverlayRepository(
            self.repository, images,
            base_bytes_cache=shared_base_bytes, base_json_cache=shared_base_json,
        )
        planner = _StagedTimePlanner(self, view)
        touched: set[str] = set()
        soft_handoffs: list[dict[str, Any]] = []
        seen_handoff_keys: set[tuple[str, str]] = set()
        working_meta = copy.deepcopy(dict(meta))
        working_time = current_time
        stopped_for_handoff = False
        stop_boundary_kind = ""
        quiet_chunk_boundary = False
        seen_frontier_states: set[tuple[str, str]] = set()
        step = 0

        while True:
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
            plan = planner._advance_time_single_frontier(internal, working_meta, working_time)
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
                if hard and not allow_hard_interrupt:
                    raise CommandRejectedError("jianghu_action_interrupted_before_completion")
                matched_rows = [
                    row for row in handoffs
                    if handoff_matcher is None or bool(handoff_matcher(row))
                ]
                matched = bool(matched_rows) or (handoff_matcher is None and not handoffs)
                if hard or (stop_on_soft_interrupt and matched):
                    stopped_for_handoff = True
                    stop_boundary_kind = boundary_kind
                report_rows = handoffs if include_unmatched_handoffs else matched_rows
                if hard and not report_rows:
                    report_rows = handoffs
                for row in report_rows:
                    key = (str(row.get("event_id") or ""), str(row.get("kind") or ""))
                    if key in seen_handoff_keys:
                        continue
                    seen_handoff_keys.add(key)
                    if len(soft_handoffs) < 32:
                        soft_handoffs.append(row)

            previous_time = working_time
            previous_scheduler = copy.deepcopy(images.get("state/martial-world/scheduler.json"))
            if previous_scheduler is None:
                try:
                    previous_scheduler = copy.deepcopy(view.read_json("state/martial-world/scheduler.json"))
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    previous_scheduler = None
            for path, raw in plan.writes.items():
                try:
                    record = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CommandRejectedError("jianghu_internal_time_after_image_invalid") from exc
                if not isinstance(record, Mapping):
                    raise CommandRejectedError("jianghu_internal_time_after_image_invalid")
                key = str(path)
                images[key] = copy.deepcopy(dict(record))
                view.set_record(key, record, raw=raw)
                touched.add(key)

            try:
                working_time = CampaignTime.parse(plan.result.get("world_time"))
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("jianghu_internal_time_result_invalid") from exc
            meta_image = images.get(self.meta_path)
            if isinstance(meta_image, Mapping):
                working_meta = copy.deepcopy(dict(meta_image))

            step += 1
            if stopped_for_handoff:
                break
            if working_time == target and not plan.result.get("continuation_required"):
                break
            current_dt = datetime(working_time.year, working_time.month, working_time.day, working_time.hour, working_time.minute, working_time.second)
            previous_dt = datetime(previous_time.year, previous_time.month, previous_time.day, previous_time.hour, previous_time.minute, previous_time.second)
            if current_dt <= previous_dt:
                # Several bounded scheduler owners may lawfully settle at the
                # exact same timestamp (for example annual faction-life work is
                # chunked across a few owners).  Same clock time is therefore
                # progress when the authoritative scheduler cursor changed.
                # Reject only a true no-op so finite activities cannot spin.
                current_scheduler = images.get("state/martial-world/scheduler.json")
                scheduler_progress = (
                    isinstance(previous_scheduler, Mapping)
                    and isinstance(current_scheduler, Mapping)
                    and dict(previous_scheduler) != dict(current_scheduler)
                )
                if not scheduler_progress:
                    raise CommandRejectedError("jianghu_internal_time_did_not_advance")
            scheduler_image = images.get("state/martial-world/scheduler.json")
            scheduler_key = json.dumps(scheduler_image, sort_keys=True, separators=(",", ":"), ensure_ascii=False) if isinstance(scheduler_image, Mapping) else ""
            cycle_key = (str(working_time), scheduler_key)
            if cycle_key in seen_frontier_states:
                raise CommandRejectedError("jianghu_internal_time_frontier_cycle")
            seen_frontier_states.add(cycle_key)
            if max_frontiers is not None and step >= max_frontiers:
                quiet_chunk_boundary = True
                break

        writes: dict[str, bytes] = {}
        for path in sorted(touched):
            record = images.get(path)
            if not isinstance(record, Mapping):
                continue
            raw = _json_bytes(_canonical_write_record(path, record))
            if base_view.read_optional_bytes(path) != raw:
                writes[path] = raw

        if persist_staged_records:
            for path, initial in staged.items():
                record = images.get(path, initial)
                if not isinstance(record, Mapping):
                    continue
                raw = _json_bytes(_canonical_write_record(path, record))
                if self.repository.read_optional_bytes(path) != raw:
                    writes[path] = raw

        expected = tuple(sorted(writes))
        settled = working_time
        settled_dt = datetime(settled.year, settled.month, settled.day, settled.hour, settled.minute, settled.second)

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("jianghu staged time write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=settled)
            schedule = overlay.read_json("state/martial-world/scheduler.json")
            if schedule.get("settled_through") != settled_dt.isoformat():
                raise ValueError("jianghu staged scheduler frontier mismatch")

        result = {
            "command_type": "advance_time",
            "world_time": str(settled),
            "requested_time": str(target),
            "interrupted": bool(stopped_for_handoff),
            "continuation_required": bool(quiet_chunk_boundary),
            "internal_frontiers_settled": step,
        }
        if quiet_chunk_boundary:
            result["continuation_reason"] = "quiet_frontier_chunk"
            result["continuation_target"] = str(target)
        if stopped_for_handoff:
            result["player_boundary_kind"] = stop_boundary_kind or "soft_player_facing"
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
        allow_hard_interrupt: bool = False,
        staged_activity_authority: bool = False,
    ) -> tuple[_BuiltPlan, dict[str, Mapping[str, Any]], CampaignTime]:
        refs = [str(x) for x in person_refs if isinstance(x, str)]
        if not refs or len(refs) != len(set(refs)):
            raise CommandRejectedError("jianghu_activity_people_invalid")
        unavailable = self._unavailable_person_refs()
        if any(ref in unavailable for ref in refs):
            raise CommandRejectedError("jianghu_person_unavailable")
        unusable_allowed = {str(x) for x in allow_unusable_refs if isinstance(x, str)}
        resolved: dict[str, tuple[str, dict[str, Any], int, dict[str, Any]]] = {}
        faction_groups: dict[str, list[str]] = {}
        resource_rows: list[tuple[str, str, str]] = []
        for ref in refs:
            row = self._person(ref)
            resolved[ref] = row
            person = row[3]
            if ref not in unusable_allowed and not is_living_and_conscious(person):
                raise CommandRejectedError("jianghu_person_unavailable")
            faction_ref = str(person.get("faction_ref") or "")
            if faction_ref:
                faction_groups.setdefault(faction_ref, []).append(ref)
            resource_owner = faction_ref or str(person.get("affiliation_ref") or ref)
            resource_rows.append(("person", ref, resource_owner))

        existing_staged = {str(k): copy.deepcopy(dict(v)) for k,v in dict(staged_records or {}).items()}
        # Finite commands are atomic to campaign state, but their participants
        # must still be unavailable to autonomous work crossed while the staged
        # clock advances.  This overlay-only owner is read by the universal
        # derived availability index and is never persisted as a receipt/history.
        transient_path = "__runtime__/jianghu-finite-activity.json"
        if not staged_activity_authority:
            commitments = derived_commitment_state(self.repository.read_json)
            try:
                reserve_resources(
                    commitments, resources=resource_rows, actor_ref=command.actor_id,
                    owner_ref=str(owner_ref), activity_ref=str(activity_ref),
                    activity_kind=str(activity_kind), started_at=str(current_time).removeprefix("SE-"),
                    location_ref=location_ref,
                )
            except ValueError as exc:
                raise CommandRejectedError("jianghu_person_unavailable") from exc
            existing_staged[transient_path] = {
                "schema": "jianghu-transient-finite-activity",
                "activities": {str(activity_ref): {
                    "activity_kind": str(activity_kind), "actor_ref": command.actor_id,
                    "owner_ref": str(owner_ref), "person_refs": list(refs),
                    "started_at": str(current_time).removeprefix("SE-"),
                    "location_ref": str(location_ref or ""),
                }},
            }

        # Pause institutional training for every represented faction. Civic and
        # independent exact people have no faction curriculum to pause, but the
        # transient availability owner above still conserves their time.
        paused_by_faction: dict[str, tuple[str, str, list[str]]] = {}
        for faction_ref, group_refs in sorted(faction_groups.items()):
            first_path = resolved[group_refs[0]][0]
            fpath = _faction_path(faction_ref)
            staged_faction = existing_staged.get(fpath)
            staged_roster = existing_staged.get(first_path)
            pfpath, pfaction, prpath, proster = self._pause_institutional_training_now(
                group_refs, current_time,
                faction_override=staged_faction if isinstance(staged_faction, Mapping) else None,
                roster_override=staged_roster if isinstance(staged_roster, Mapping) else None,
            )
            existing_staged[pfpath] = pfaction
            existing_staged[prpath] = proster
            paused_by_faction[faction_ref] = (pfpath, prpath, list(group_refs))

        plan = self._time_plan_exact_staged(
            command, meta, current_time, seconds=seconds, staged_records=existing_staged,
            allow_hard_interrupt=allow_hard_interrupt,
        )
        try:
            target = CampaignTime.parse(plan.result.get("world_time"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_internal_time_result_invalid") from exc
        commitments_after = self._derived_activity_after_plan(plan)

        final_records: dict[str, Mapping[str, Any]] = {}
        for path, record in existing_staged.items():
            if path == transient_path:
                continue
            final_records[path] = self._time_after_record(plan, path, record)

        resumable = set(self._resumable_after_commitment_release(
            refs,
            commitments_after,
            read_json=lambda path: self._activity_after_read(plan, path),
        ))
        for faction_ref, (fpath, rpath, group_refs) in paused_by_faction.items():
            resume_refs = [ref for ref in group_refs if ref in resumable]
            faction_after = final_records.get(fpath)
            roster_after = final_records.get(rpath)
            if resume_refs:
                rfpath, rfaction, rrpath, rroster = self._resume_institutional_training_now(
                    resume_refs, target,
                    faction_override=faction_after if isinstance(faction_after, Mapping) else None,
                    roster_override=roster_after if isinstance(roster_after, Mapping) else None,
                )
                if rfpath != fpath or rrpath != rpath:
                    raise CommandRejectedError("jianghu_activity_roster_invariant")
                final_records[fpath] = rfaction
                final_records[rpath] = rroster
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
        if self._effective_person_location(command.actor_id, person) != site_ref:
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
        duration = max(60, int(quote.get("duration_minutes", 0)) * 60)
        person = copy.deepcopy(dict(person))
        person["personal_cash"] = cash - price
        if service_ref == "packed_rations_day":
            person["travel_ration_days"] = max(0, int(person.get("travel_ration_days", 0))) + 1
        staged_roster = set_roster_person(copy.deepcopy(roster), idx, person)

        # Public providers are aggregate regional economic activity. Payment is
        # conserved into the regional market; a material service additionally
        # changes the buyer's current physical state rather than being a label.
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
            command, meta, current_time, person_refs=[command.actor_id], seconds=duration,
            activity_ref=f"service:{command.request_id}", activity_kind="local_service",
            owner_ref=str(person.get("faction_ref") or command.actor_id), location_ref=site_ref,
            staged_records={path: staged_roster, market_path: market},
        )
        final_roster = copy.deepcopy(final_records[path])
        rows = final_roster.get("people", []) if isinstance(final_roster, Mapping) else []
        final_idx = next((i for i,row in enumerate(rows) if isinstance(row,Mapping) and row.get("person_id")==command.actor_id), None)
        if final_idx is None:
            raise CommandRejectedError("jianghu_person_unresolved")
        final_person = copy.deepcopy(dict(rows[final_idx]))
        effect = str(quote.get("simulation_effect") or "")
        if service_ref == "basic_dressings" or effect == "stabilize_current_wounds":
            final_person["health"] = stabilize_wounds(
                final_person.get("health", {}), treatment_score_value=45,
                advanced_procedure_enabled=False, medical_supply_available=True,
            )
            rows[final_idx] = final_person
            final_roster["people"] = rows
            final_records[path] = final_roster
        cash_after = int(final_person.get("personal_cash", 0))
        return self._combine_time_plan(
            command,time_plan,extra_records=final_records,code="jianghu_service_purchase_ready",
            result={"command_type":command.command_type,**quote,"cash_after":cash_after,
                    "travel_ration_days_after":max(0,int(final_person.get("travel_ration_days",0)))},
        )

    def _jianghu_tournament_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        self._require_jianghu(meta)
        if set(command.payload)!={"action","tournament_ref"} or command.payload.get("action") not in {"register","spectate","advance"}: raise CommandRejectedError("jianghu_tournament_resolution_payload_fields_invalid")
        action=str(command.payload.get("action"))
        tref=_stable_text(command.payload.get("tournament_ref"),"jianghu_tournament_invalid")
        state=copy.deepcopy(self.repository.read_json(_TOURNAMENTS)); tourn=state.get("tournaments",{}).get(tref)
        if not isinstance(tourn,Mapping): raise CommandRejectedError("jianghu_tournament_unresolved")
        if action=="spectate":
            _ppath,_proster,_pidx,actor=self._person(command.actor_id)
            self._require_person_available_for_activity(command.actor_id)
            venue=str(tourn.get("venue_site_ref") or "")
            if not venue or self._effective_person_location(command.actor_id, actor)!=venue:
                raise CommandRejectedError("jianghu_tournament_spectate_requires_venue")
            if str(tourn.get("status") or "") not in {"registration_open","bracket_ready","in_progress","awaiting_player_match"}:
                raise CommandRejectedError("jianghu_tournament_not_open_to_spectators")
            kind=str(tourn.get("tournament_kind") or tourn.get("event_id") or "regional_martial_tournament")
            try: profile=tournament_event_profile(kind)
            except (KeyError,ValueError): raise CommandRejectedError("jianghu_tournament_profile_invalid")
            ticket=max(0,int(profile.get("public_spectator_ticket_cash_per_day",0)))
            cash=max(0,int(actor.get("personal_cash",0)))
            if cash<ticket: raise CommandRejectedError("jianghu_personal_cash_insufficient")
            actor=copy.deepcopy(dict(actor)); actor["personal_cash"]=cash-ticket
            staged_roster=set_roster_person(copy.deepcopy(_proster),_pidx,actor)
            state_after=copy.deepcopy(state); live=state_after.setdefault("tournaments",{}).get(tref)
            if not isinstance(live,dict): raise CommandRejectedError("jianghu_tournament_unresolved")
            live["prize_escrow_cash"]=max(0,int(live.get("prize_escrow_cash",0)))+ticket
            live["spectator_admission_cash"]=max(0,int(live.get("spectator_admission_cash",0)))+ticket
            time_plan,extra,_target=self._timed_person_activity_plan(
                command,meta,current_time,person_refs=[command.actor_id],seconds=4*3600,
                activity_ref=f"tournament-spectate:{command.request_id}",activity_kind="tournament_spectating",owner_ref=tref,location_ref=venue,
                staged_records={_ppath:staged_roster,_TOURNAMENTS:state_after},
            )
            return self._combine_time_plan(command,time_plan,extra_records=extra,code="jianghu_tournament_spectated",result={"command_type":command.command_type,"action":action,"tournament_ref":tref,"ticket_cash":ticket,"prize_escrow_cash_after":int(extra[_TOURNAMENTS]["tournaments"][tref].get("prize_escrow_cash",0))})
        if action=="advance":
            registrations=tourn.get("registrations",[]) if isinstance(tourn.get("registrations"),list) else []
            owner_map={str(r.get("entrant_ref")):str(r.get("faction_ref")) for r in registrations if isinstance(r,Mapping) and isinstance(r.get("entrant_ref"),str) and isinstance(r.get("faction_ref"),str)}
            if command.actor_id not in owner_map: raise CommandRejectedError("jianghu_tournament_not_registered")
            commitments=derived_commitment_state(self.repository.read_json); person_index=commitments.get("person_index",{}) if isinstance(commitments,Mapping) else {}
            if not isinstance(person_index,Mapping): raise CommandRejectedError("jianghu_commitments_invalid")
            custody_refs=self._custody_person_refs()
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
                site=sites.get(self._effective_person_location(ref, person)) if isinstance(person,Mapping) and isinstance(sites,Mapping) else None
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
        site=sites.get(self._effective_person_location(command.actor_id, person)) if isinstance(sites,Mapping) else None
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
            commitments=derived_commitment_state(self.repository.read_json)
            try:commitments=release_resources(commitments,activity_ref=dep_ref)
            except ValueError as exc:raise CommandRejectedError("jianghu_deployment_commitment_missing") from exc
            writes_records={_DEPLOYMENTS:state,}
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
            if ref in unavailable or p.get("faction_ref")!=faction_ref or not is_living_and_conscious(p): raise CommandRejectedError("jianghu_deployment_member_unavailable")
            records[ref]=p; locations.add(self._effective_person_location(ref, p))
        if len(locations)!=1: raise CommandRejectedError("jianghu_deployment_members_not_colocated")
        offices=set(actor.get("standing_offices",[])) if isinstance(actor.get("standing_offices"),list) else set()
        if not ({"leader","deputy_leader","field_commander","deputy_field_commander"}&{str(x).split(':',1)[0] for x in offices}): raise CommandRejectedError("jianghu_deployment_not_authorized")
        try: structure=build_deployment_structure(member_refs=list(refs),records=records,preferred_commander_ref=command.actor_id)
        except ValueError as exc: raise CommandRejectedError("jianghu_deployment_structure_invalid") from exc
        validate_deployment_structure(structure)
        commitments=derived_commitment_state(self.repository.read_json)
        try:commitments=reserve_resources(commitments,resources=[("person",ref,faction_ref) for ref in refs],actor_ref=command.actor_id,owner_ref=faction_ref,activity_ref=dep_ref,activity_kind="deployment",started_at=str(current_time).removeprefix("SE-"),location_ref=str(next(iter(locations))))
        except ValueError as exc:raise CommandRejectedError("jianghu_deployment_member_already_committed") from exc
        deployments[dep_ref]={"faction_ref":faction_ref,"created_at":str(current_time).removeprefix("SE-"),"location_ref":next(iter(locations)),"objective":str(command.payload.get("objective")),"structure":structure,"status":"active"}
        pause_fpath, paused_faction, pause_path, paused_roster = self._pause_institutional_training_now(list(refs), current_time)
        return self._simple_plan(command,meta,current_time,writes_records={_DEPLOYMENTS:state,pause_fpath:paused_faction,pause_path:paused_roster},code="jianghu_deployment_form_ready",result={"command_type":command.command_type,"deployment_ref":dep_ref,"kind":structure["kind"],"headcount":structure["deployment_headcount"],"commander_ref":structure["commander_ref"],"deputy_ref":structure["deputy_ref"]})

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
            # Project cash is payment into the surrounding ordinary economy, not
            # a sink. Resolve the destination before any staged deduction so a
            # malformed headquarters/market fails closed.
            try:
                project_region=region_for_place(str(faction.get("headquarters") or ""))
                project_market_path=f"state/martial-world/markets/{project_region}.json"
                project_market=copy.deepcopy(self.repository.read_json(project_market_path))
            except (FileNotFoundError,KeyError,TypeError,ValueError) as exc:
                raise CommandRejectedError("jianghu_project_region_unresolved") from exc
            treasury_before_start=max(0,int(faction.get("treasury_cash",0)))
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
            project_cash_spent=treasury_before_start-max(0,int(faction.get("treasury_cash",0)))
            if project_cash_spent<0:
                raise CommandRejectedError("jianghu_project_cash_conservation_failure")
            if project_cash_spent:
                project_market["cash_pool"]=max(0,int(project_market.get("cash_pool",0)))+project_cash_spent
            roster_path=_roster_path(faction_ref); roster=self.repository.read_json(roster_path); people=roster.get("people",[]) if isinstance(roster,Mapping) else []
            commitments=derived_commitment_state(self.repository.read_json); unavailable=self._unavailable_person_refs()
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
            project_site=str(faction.get("local_site_ref") or faction.get("headquarters") or "")
            if not project_site: raise CommandRejectedError("jianghu_project_site_unresolved")
            started_at=str(current_time).removeprefix("SE-")
            out["planned_skilled_worker_count"]=len(out.get("skilled_worker_refs",[]))
            out["planned_management_worker_count"]=len(out.get("management_worker_refs",[]))
            out["planned_general_worker_count"]=len(out.get("general_worker_refs",[]))
            out.update({"project_ref":project_ref,"faction_ref":faction_ref,"site_ref":project_site,"started_at":started_at,"last_progress_at":started_at})
            if out.get("project_type") in {"building_upgrade","building_expansion","estate_boundary_expansion"}:
                days_needed=max(min_days,(int(out.get("skilled_labor_hours_remaining",0))+6*max(1,len(out.get("skilled_worker_refs",[])))-1)//(6*max(1,len(out.get("skilled_worker_refs",[])))),(int(out.get("general_labor_hours_remaining",0))+8*max(1,len(out.get("general_worker_refs",[])))-1)//(8*max(1,len(out.get("general_worker_refs",[])))))
            else:
                days_needed=max(min_days,(int(out.get("management_labor_hours_remaining",0))+4*max(1,len(out.get("management_worker_refs",[])))-1)//(4*max(1,len(out.get("management_worker_refs",[])))),(int(out.get("general_setup_labor_hours_remaining",0))+4*max(1,len(out.get("general_worker_refs",[])))-1)//(4*max(1,len(out.get("general_worker_refs",[])))))
            try:commitments=reserve_resources(commitments,resources=[("person",ref,faction_ref) for ref in worker_refs],actor_ref=command.actor_id,owner_ref=faction_ref,activity_ref=project_ref,activity_kind="construction" if out.get("project_type") in {"building_upgrade","building_expansion","estate_boundary_expansion"} else "enterprise_setup",started_at=started_at,location_ref=project_site)
            except ValueError as exc: raise CommandRejectedError("jianghu_project_labor_unavailable") from exc
            registry[project_ref]=compact_project_state(out,project_ref=project_ref)
            schedule=copy.deepcopy(self.repository.read_json(_SCHEDULE))
            due=datetime(current_time.year,current_time.month,current_time.day,current_time.hour,current_time.minute,current_time.second)+timedelta(days=max(1,int(days_needed)))
            try:
                schedule=upsert_one_off_event(schedule,{"event_id":f"autonomous_project_due:{project_ref}","kind":"autonomous_project_due","due_at":due.isoformat(),"owner_ref":project_ref,"requires_player_decision":False})
            except ValueError as exc:
                raise CommandRejectedError("jianghu_project_schedule_conflict") from exc
            pause_fpath, paused_faction, pause_path, paused_roster = self._pause_institutional_training_now(worker_refs, current_time, faction_override=faction)
            faction = paused_faction
            return self._simple_plan(command,meta,current_time,writes_records={_PROJECTS:projects,_SCHEDULE:schedule,fpath:faction,ipath:inv,pause_path:paused_roster,project_market_path:project_market},code="jianghu_project_start_ready",result={"command_type":command.command_type,"project_ref":project_ref,"project_type":out["project_type"],"target_level":target if target else None,"additional_footprint_m2":int(out.get("additional_footprint_m2",0)),"additional_land_m2":int(out.get("additional_land_m2",0)),"worker_refs":worker_refs,"regional_cash_paid":project_cash_spent,"scheduled_due_at":due.isoformat()})
        row=registry.get(project_ref)
        if not isinstance(row,Mapping): raise CommandRejectedError("jianghu_project_unresolved")
        faction_ref=str(row.get("faction_ref") or "")
        if faction_ref!=actor_faction: raise CommandRejectedError("jianghu_infrastructure_not_authorized")
        try: target=CampaignTime.parse(command.payload.get("target_time"))
        except (TypeError,ValueError) as exc: raise CommandRejectedError("jianghu_project_target_time_invalid") from exc
        seconds=int((datetime(target.year,target.month,target.day,target.hour,target.minute,target.second)-datetime(current_time.year,current_time.month,current_time.day,current_time.hour,current_time.minute,current_time.second)).total_seconds())
        if seconds<=0: raise CommandRejectedError("jianghu_project_target_time_invalid")
        # Project labor is owned exclusively by the scheduler frontier. This
        # command advances world time to the requested target and then reports
        # that authoritative after-image; it never applies a second labor chunk.
        time_plan=self._time_plan_exact(command,meta,current_time,seconds=seconds)
        projects_after=copy.deepcopy(self._time_after_record(time_plan,_PROJECTS,projects))
        registry_after=projects_after.get("projects",{}) if isinstance(projects_after,Mapping) else {}
        row_after=registry_after.get(project_ref) if isinstance(registry_after,Mapping) else None
        completed=not isinstance(row_after,Mapping)
        result_row=row if completed else row_after
        active_level=int(result_row.get("target_level",0) if completed and result_row.get("target_level") is not None else result_row.get("active_level",0)) if isinstance(result_row,Mapping) else 0
        return self._combine_time_plan(command,time_plan,extra_records={},code="jianghu_project_advance_ready",result={"command_type":command.command_type,"project_ref":project_ref,"completed":completed,"active_level":active_level,"additional_footprint_m2":int(result_row.get("additional_footprint_m2",0)) if isinstance(result_row,Mapping) else 0,"additional_land_m2":int(result_row.get("additional_land_m2",0)) if isinstance(result_row,Mapping) else 0,"additional_scale":int(result_row.get("additional_scale",0)) if isinstance(result_row,Mapping) else 0})

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
        actor_location=self._effective_person_location(command.actor_id, actor); actor_site=sites.get(actor_location) if isinstance(sites,Mapping) else None
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
            owner_ref=faction_ref, location_ref=self._effective_person_location(command.actor_id, actor),
        )

        fpath=_faction_path(faction_ref); roster_path=_roster_path(faction_ref)
        faction=hydrate_faction_state(copy.deepcopy(dict(final_records.get(fpath) or self._time_after_record(time_plan,fpath,read_faction(self.repository,faction_ref)[1]))))
        roster_raw=copy.deepcopy(dict(final_records.get(roster_path) or self._time_after_record(time_plan,roster_path,self.repository.read_json(roster_path))))
        roster=hydrate_roster_state(roster_raw,faction=faction)
        faction=reconcile_faction_population(faction,roster)
        # Recruitment changes student count and can introduce future instructors.
        # Materialize all elapsed institutional training under the pre-recruit
        # environment before adding any new identities, so the intake cannot
        # retroactively alter prior instruction coverage or gains.
        people=roster.get("people")
        if not isinstance(people,list): raise CommandRejectedError("jianghu_roster_invalid")
        boundary_commitments=self._derived_activity_after_plan(time_plan)
        boundary_index=boundary_commitments.get("person_index",{}) if isinstance(boundary_commitments,Mapping) else {}
        boundary_busy={str(ref) for ref in boundary_index if isinstance(ref,str)} if isinstance(boundary_index,Mapping) else set()
        boundary_busy.update(physical_unavailable_person_refs(lambda path: self._activity_after_read(time_plan,path)))
        # Recruitment itself consumed the responsible officer's week.  The
        # transient finite-activity owner is intentionally absent from the
        # final durable write set, so retain that consumed time explicitly at
        # this pre-intake training boundary.
        boundary_busy.add(command.actor_id)
        paused_boundary=institutional_training_pause_refs(
            faction,[p for p in people if isinstance(p,Mapping)],unavailable_refs=sorted(boundary_busy),
        )
        faction,roster,_training_boundary=settle_and_reset_faction_training_cycle(
            faction,roster,at_iso=str(target_time).removeprefix("SE-"),paused_refs=paused_boundary,
        )
        people=roster.get("people")
        if not isinstance(people,list): raise CommandRejectedError("jianghu_roster_invalid")
        actor_after=next((p for p in people if isinstance(p,Mapping) and p.get("person_id")==command.actor_id),None)
        if not isinstance(actor_after,Mapping): raise CommandRejectedError("jianghu_person_unresolved")

        policy=faction.get("recruitment_policy",{}) if isinstance(faction.get("recruitment_policy"),Mapping) else {}
        admission=faction_admission_policy(faction_ref,faction)
        minimum_entry_age=max(0,int(admission.get("minimum_entry_age",8)))
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

        cursor=max(0,int(pool.get("identity_ordinal_cursor",0)))
        evaluator=max(int(actor_after.get("professional_skills",{}).get("administration",0)),int(actor_after.get("attributes",{}).get("perception",0)))
        accepted=[]; examined=0; world_seed=str(meta.get("world_seed") or _WORLD_SEED_DEFAULT)
        limit=min(available,max(requested,requested*12))
        for offset in range(limit):
            cand=deterministic_candidate(world_seed=world_seed,origin_population_id=place_ref,ordinal=cursor+offset)
            examined+=1
            report=screening_report(cand,evaluator_skill=evaluator)
            if int(cand.get("age",0))<minimum_entry_age: continue
            if int(cand["aptitudes"]["martial"])<int(policy.get("minimum_martial_aptitude",0)) or int(cand["aptitudes"]["qi"])<int(policy.get("minimum_qi_aptitude",0)): continue
            if not report.get("eligible",True): continue
            accepted.append(cand)
            if len(accepted)>=requested: break
        pool["identity_ordinal_cursor"]=cursor+examined
        if not accepted: raise CommandRejectedError("jianghu_recruitment_no_eligible_candidates")
        if len(accepted)>available: raise CommandRejectedError("jianghu_civilian_pool_insufficient")
        pool["current_population"]=int(pool["current_population"])-len(accepted)

        existing_names={str(p.get("name")) for p in people if isinstance(p,Mapping) and isinstance(p.get("name"),str)}
        materialized_ids=[]
        for cand in accepted:
            pid=f"mw.recruit.{hashlib.sha256((world_seed+'|'+place_ref+'|'+str(cand['origin_ordinal'])).encode()).hexdigest()[:24]}"
            if any(isinstance(row,Mapping) and row.get("person_id")==pid for row in people): raise CommandRejectedError("jianghu_recruitment_identity_conflict")
            age=max(0,int(cand["age"])); sex=deterministic_sex(stable=pid,faction_id=faction_ref,admission_policy=admission)
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

        final_records.update({_CIVILIANS:civilians,fpath:faction,roster_path:roster})
        return self._combine_time_plan(command,time_plan,extra_records=final_records,code="jianghu_recruitment_ready",result={"command_type":command.command_type,"faction_ref":faction_ref,"place_ref":place_ref,"examined":examined,"accepted_count":len(accepted),"accepted_person_refs":materialized_ids})
