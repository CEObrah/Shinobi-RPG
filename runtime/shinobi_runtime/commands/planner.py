"""Repository-backed deterministic gameplay command planner.

The public command surface is deliberately semantic and closed.  A caller can
name a supported action and its bounded domain inputs, but can never name a
repository path or provide replacement bytes.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    Dict,
    Iterable,
    Mapping,
    Optional,
    Tuple,
)

from shinobi_runtime.api.contracts import (
    CommandPlan,
    CommandPreview,
    CommandRejectedError,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.domains.strategic import StrategicCommandsMixin
from shinobi_runtime.commands.domains.battlefield import OperationalBattlefieldCommandsMixin
from shinobi_runtime.commands.domains.missions import MissionCommandsMixin
from shinobi_runtime.commands.domains.teams import TeamCommandsMixin
from shinobi_runtime.commands.domains.training import TrainingCommandsMixin
from shinobi_runtime.commands.domains.population import PopulationTravelCommandsMixin
from shinobi_runtime.commands.domains.social import SocialCommandsMixin
from shinobi_runtime.commands.domains.commitments import CommitmentForceCommandsMixin
from shinobi_runtime.commands.domains.internal_events import InternalEventCommandsMixin
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.commands.domains.family import FamilyCommandsMixin
from shinobi_runtime.commands.domains.economy import EconomyCommandsMixin
from shinobi_runtime.commands.domains.medical import MedicalCommandsMixin
from shinobi_runtime.commands.domains.combat import CombatCommandsMixin
from shinobi_runtime.commands.domains.special import SpecialCombatCommandsMixin
from shinobi_runtime.commands.domains.civil_state import CivilStateCommandsMixin
from shinobi_runtime.commands.domains.operational_world import OperationalWorldCommandsMixin
from shinobi_runtime.commands.formation_index import (
    FORMATION_INDEX_PATH,
    reconcile_formation_writes,
    validate_formation_index,
)
from shinobi_runtime.commands.mission_context_index import (
    MISSION_CONTEXT_INDEX_PATH,
    reconcile_mission_writes,
    validate_mission_context_index,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry
from shinobi_runtime.sim.scheduler_store import SchedulerStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.domain import (
    DomainAuthorityResolver,
    LocationGraph,
)
from shinobi_runtime.store.repository import RepositoryStore
from shinobi_runtime.tx.manifest import TransactionManifest


_SUBMITTED_AT = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_WORLD_EVENT_ID = re.compile(r"^event\.[a-z0-9][a-z0-9._-]*$")
from shinobi_runtime.commands.paths import (
    WORLD_EVENT_REGISTRY_PATH as _WORLD_EVENT_REGISTRY_PATH,
    ROUTES_PATH as _ROUTES_PATH,
)
_CLARIFICATION_CODES = frozenset(
    (
        "scene_boundary_requires_player_decision",
        "time_boundary_requires_domain_settlement",
        "time_boundary_requires_life_course_settlement",
        "time_boundary_requires_mission_settlement",
        "time_boundary_requires_faction_review",
        "time_boundary_requires_combat_zoom",
        "mission_outcome_unresolved",
    )
)


@dataclass(frozen=True)
class _ExpandedCommand:
    """Internal variant-expanded view that preserves the submitted digest."""

    original: CommandEnvelope
    payload: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return self.original.digest

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)



class _ValidatorOverlayView:
    """Delegate overlay reads while preserving a reducer's original write set.

    Derived routing projections may be appended after a domain reducer plans
    its authoritative writes.  Existing reducer validators still see exactly
    the write set they planned, while the outer validator verifies the derived
    projection separately.
    """

    def __init__(self, overlay: StagedOverlay, changed_paths: Tuple[str, ...]) -> None:
        self._overlay = overlay
        self.changed_paths = changed_paths

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


class RepositoryCommandPlanner(TimeCommandsMixin, AutonomyCommandsMixin, InternalEventCommandsMixin, MissionCommandsMixin, TeamCommandsMixin, TrainingCommandsMixin, PopulationTravelCommandsMixin, SocialCommandsMixin, CommitmentForceCommandsMixin, FamilyCommandsMixin, EconomyCommandsMixin, MedicalCommandsMixin, CivilStateCommandsMixin, OperationalWorldCommandsMixin, SpecialCombatCommandsMixin, CombatCommandsMixin, OperationalBattlefieldCommandsMixin, StrategicCommandsMixin):
    """Plan the closed set of gameplay commands against current owner bytes."""

    COMMAND_TYPES = frozenset(COMMAND_SPECS)
    MAX_ADVANCE_SECONDS = 25 * 366 * 24 * 60 * 60
    MAX_SCENE_LOADED_OWNER_IDS = 64
    MAX_MISSION_EVIDENCE_EVENTS = 4096

    def __init__(
        self,
        repository: RepositoryStore,
        *,
        meta_path: str = "state/meta.json",
        scene_path: str = "state/scene.json",
        pressures_path: str = "state/canon/pressures.json",
        scheduler_path: str = "state/time/causal-scheduler.json",
    ) -> None:
        self.repository = repository
        self.meta_path = meta_path
        self.scene_path = scene_path
        self.pressures_path = pressures_path
        self.scheduler_path = scheduler_path

    def _base(self, command: CommandEnvelope) -> Tuple[Dict[str, Any], CampaignTime]:
        if command.mode not in ("gameplay", "autonomous"):
            raise CommandRejectedError("gameplay_mode_required")
        if command.command_type not in self.COMMAND_TYPES:
            raise CommandRejectedError("unsupported_command_type")
        if not _SUBMITTED_AT.fullmatch(command.submitted_at):
            raise CommandRejectedError("submitted_at_must_be_utc_rfc3339")
        try:
            parsed_submitted = datetime.fromisoformat(
                command.submitted_at.removesuffix("Z") + "+00:00"
            )
        except ValueError as exc:
            raise CommandRejectedError("submitted_at_must_be_utc_rfc3339") from exc
        if parsed_submitted.tzinfo != timezone.utc:
            raise CommandRejectedError("submitted_at_must_be_utc_rfc3339")

        try:
            actual_campaign = self.repository.campaign_id(self.meta_path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("campaign_meta_invalid") from exc
        if actual_campaign != command.campaign_id:
            raise CommandRejectedError("campaign_mismatch")
        self.repository.require_revision(command.expected_revision, self.meta_path)
        meta = self.repository.read_json(self.meta_path)
        if not isinstance(meta, dict) or meta.get("schema") != "meta":
            raise CommandRejectedError("campaign_meta_invalid")
        if command.mode == "gameplay" and meta.get("player_id") != command.actor_id:
            raise CommandRejectedError("actor_not_campaign_player")
        if command.mode == "autonomous":
            try:
                self._resolve_covered_owner(command.actor_id, cache=_OwnerResolutionCache())
            except CommandRejectedError as exc:
                raise CommandRejectedError("autonomous_actor_unresolved") from exc
        try:
            current_time = CampaignTime.parse(meta.get("time"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("campaign_time_invalid") from exc
        return meta, current_time

    @staticmethod
    def _meta_after(
        meta: Mapping[str, Any],
        command: CommandEnvelope,
        *,
        world_time: CampaignTime,
    ) -> Dict[str, Any]:
        updated = copy.deepcopy(dict(meta))
        updated["time"] = str(world_time)
        updated["revision"] = command.expected_revision + 1
        return updated

    def _prune_noop_writes(self, writes: Mapping[str, bytes]) -> Dict[str, bytes]:
        """Remove byte-identical after-images before manifest construction.

        Reducers may lawfully resolve an action without changing every loaded
        authority owner (for example, a short training interval may leave an
        integer skill unchanged, or a bounded combat exchange may inflict no
        participant effect).  Such owners remain part of causal reads but must
        not become fake writes because FileMutation correctly rejects no-ops.
        """
        return {
            path: content
            for path, content in writes.items()
            if self.repository.read_optional_bytes(path) != content
        }

    @staticmethod
    def _assert_meta(
        overlay: StagedOverlay,
        manifest: TransactionManifest,
        *,
        meta_path: str,
        command: CommandEnvelope,
        world_time: CampaignTime,
    ) -> None:
        meta = overlay.read_json(meta_path)
        if (
            not isinstance(meta, dict)
            or meta.get("schema") != "meta"
            or meta.get("campaign_id") != command.campaign_id
            or meta.get("revision") != command.expected_revision + 1
            or meta.get("time") != str(world_time)
            or manifest.base_revision != command.expected_revision
            or manifest.target_revision != command.expected_revision + 1
        ):
            raise ValueError("planned meta does not preserve campaign transaction law")

    def _load_scheduler(
        self,
        *,
        current_time: CampaignTime,
        scene: Mapping[str, Any],
        target_time: Optional[CampaignTime] = None,
    ) -> CausalSchedulerRegistry:
        """Load the production temporal authority through deterministic shards.

        Normal exact scheduler mutations still request a complete in-memory view.
        Time advancement supplies ``target_time`` and loads only event-day shards
        that can become due through that horizon plus the exact host shards those
        events reference. The scheduler root remains the sole world-time frontier.
        """
        try:
            store = SchedulerStore(self.repository, self.scheduler_path)
            scheduler = store.load(
                target=target_time,
                full=target_time is None,
            )
        except FileNotFoundError as exc:
            raise CommandRejectedError("temporal_authority_missing") from exc
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("temporal_authority_invalid") from exc
        if scheduler.world_time != current_time:
            raise CommandRejectedError("temporal_authority_invalid")
        return scheduler

    def _scheduler_write_images(self, scheduler: CausalSchedulerRegistry) -> Dict[str, bytes]:
        try:
            return SchedulerStore(self.repository, self.scheduler_path).write_images(scheduler)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("temporal_authority_invalid") from exc

    def _scheduler_from_reader(self, reader: Any, *, full: bool = True) -> CausalSchedulerRegistry:
        try:
            return SchedulerStore(reader, self.scheduler_path).load(full=full)
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise ValueError("staged temporal authority invalid") from exc


    def _domain_authority(self, *, cache: Optional[_OwnerResolutionCache] = None) -> DomainAuthorityResolver:
        owner_cache = cache or _OwnerResolutionCache()

        def load_owner(ref: str) -> Mapping[str, Any]:
            _path, _digest, view = self._resolve_covered_owner_view(ref, cache=owner_cache)
            return view

        try:
            assignments = self.repository.read_json("state/org/assignments.json")
        except (FileNotFoundError, ValueError):
            assignments = {"records": []}
        return DomainAuthorityResolver(load_owner=load_owner, assignments=assignments)


    @staticmethod
    def _world_event_writes(registry: Mapping[str, Any]) -> Dict[str, bytes]:
        record = copy.deepcopy(dict(registry))
        pending = record.pop("__pending_archive_writes__", {})
        if not isinstance(pending, Mapping):
            raise CommandRejectedError("world_event_archive_invalid")
        writes = {_WORLD_EVENT_REGISTRY_PATH: _json_bytes(record)}
        for path, archive in sorted(pending.items()):
            if not isinstance(path, str) or not isinstance(archive, Mapping):
                raise CommandRejectedError("world_event_archive_invalid")
            writes[path] = _json_bytes(dict(archive))
        return writes

    def _world_event_by_id(
        self, event_id: str, *, registry: Optional[Mapping[str, Any]] = None
    ) -> Optional[Mapping[str, Any]]:
        current = registry if registry is not None else self._world_events()
        events = current.get("events") if isinstance(current, Mapping) else None
        if isinstance(events, list):
            for event in events:
                if isinstance(event, Mapping) and event.get("id") == event_id:
                    return event
        refs = current.get("archive_refs") if isinstance(current, Mapping) else None
        if not isinstance(refs, list):
            raise CommandRejectedError("world_event_registry_invalid")
        for path in reversed(refs):
            if not isinstance(path, str):
                raise CommandRejectedError("world_event_registry_invalid")
            try:
                archive = self.repository.read_json(path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("world_event_archive_invalid") from exc
            archived_events = archive.get("events") if isinstance(archive, Mapping) else None
            if not isinstance(archived_events, list):
                raise CommandRejectedError("world_event_archive_invalid")
            for event in archived_events:
                if isinstance(event, Mapping) and event.get("id") == event_id:
                    return event
        return None

    def _world_event_record_and_digest(
        self, event_id: str, *, registry: Optional[Mapping[str, Any]] = None
    ) -> Tuple[Optional[Mapping[str, Any]], Optional[str]]:
        current = registry if registry is not None else self._world_events()
        events = current.get("events") if isinstance(current, Mapping) else None
        if isinstance(events, list):
            for event in events:
                if isinstance(event, Mapping) and event.get("id") == event_id:
                    return event, self.repository.digest(_WORLD_EVENT_REGISTRY_PATH)
        refs = current.get("archive_refs") if isinstance(current, Mapping) else None
        if not isinstance(refs, list):
            raise CommandRejectedError("world_event_registry_invalid")
        for path in reversed(refs):
            if not isinstance(path, str):
                raise CommandRejectedError("world_event_registry_invalid")
            try:
                archive = self.repository.read_json(path)
                digest = self.repository.digest(path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("world_event_archive_invalid") from exc
            archived_events = archive.get("events") if isinstance(archive, Mapping) else None
            if not isinstance(archived_events, list) or digest is None:
                raise CommandRejectedError("world_event_archive_invalid")
            for event in archived_events:
                if isinstance(event, Mapping) and event.get("id") == event_id:
                    return event, digest
        return None, None

    def _world_events(self) -> Dict[str, Any]:
        try:
            record = self.repository.read_json(_WORLD_EVENT_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("world_event_registry_invalid") from exc
        if not isinstance(record, dict) or record.get("schema") != "world-event-registry":
            raise CommandRejectedError("world_event_registry_invalid")
        events = record.get("events")
        if not isinstance(events, list):
            raise CommandRejectedError("world_event_registry_invalid")
        return copy.deepcopy(record)

    def _world_events_after(self, base: Optional[_BuiltPlan] = None) -> Dict[str, Any]:
        """Load the event registry after an optional nested time settlement.

        Time-spanning semantic commands compose an ``advance_time`` plan before
        appending their own event.  If that nested settlement emitted recovery,
        economy, commitment, population, or autonomy events, reloading the
        repository here would discard those staged events.  Always prefer the
        staged registry from the base plan when present.
        """
        if base is None or _WORLD_EVENT_REGISTRY_PATH not in base.writes:
            return self._world_events()
        try:
            record = json.loads(base.writes[_WORLD_EVENT_REGISTRY_PATH].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CommandRejectedError("world_event_registry_invalid") from exc
        if not isinstance(record, dict) or record.get("schema") != "world-event-registry" or not isinstance(record.get("events"), list):
            raise CommandRejectedError("world_event_registry_invalid")
        return copy.deepcopy(record)

    def _formation_registry_path(self, force_ref: str) -> str:
        try:
            index = self.repository.read_json("state/formation/index.json")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("formation_registry_index_invalid") from exc
        registries = index.get("registries") if isinstance(index, Mapping) else None
        path = registries.get(force_ref) if isinstance(registries, Mapping) else None
        if not isinstance(path, str) or not path:
            raise CommandRejectedError("formation_registry_unresolved")
        return path

    def _formation_by_id(
        self, formation_ref: str
    ) -> Tuple[str, str, Mapping[str, Any]]:
        """Resolve one explicit formation directly through its route index."""

        try:
            index = self.repository.read_json(FORMATION_INDEX_PATH)
            validate_formation_index(index)
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise CommandRejectedError("formation_registry_index_invalid") from exc
        route = index["formation_routes"].get(formation_ref)
        if not isinstance(route, Mapping):
            raise CommandRejectedError("formation_unresolved")
        force_ref = route.get("force_ref")
        path = route.get("registry_path")
        if not isinstance(force_ref, str) or not isinstance(path, str):
            raise CommandRejectedError("formation_registry_index_invalid")
        try:
            registry = self.repository.read_json(path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("formation_registry_invalid") from exc
        formations = registry.get("formations") if isinstance(registry, Mapping) else None
        if registry.get("force_ref") != force_ref or not isinstance(formations, list):
            raise CommandRejectedError("formation_registry_invalid")
        row = next(
            (item for item in formations if isinstance(item, Mapping) and item.get("id") == formation_ref),
            None,
        )
        if not isinstance(row, Mapping) or row.get("force_ref") != force_ref:
            raise CommandRejectedError("formation_registry_index_invalid")
        return path, force_ref, row


    @staticmethod
    def _semantic_event_id(command: CommandEnvelope, kind: str) -> str:
        clean = re.sub(r"[^a-z0-9._-]+", "_", kind.lower()).strip("._-")
        return f"event.{clean}.{command.digest[:20]}"

    def _append_semantic_event(
        self,
        registry: Dict[str, Any],
        *,
        command: CommandEnvelope,
        kind: str,
        at: CampaignTime,
        host_refs: Iterable[str] = (),
        actor_refs: Iterable[str] = (),
        place_refs: Iterable[str] = (),
        causal_refs: Iterable[str] = (),
        affected_owner_refs: Iterable[str] = (),
        material_consequence_refs: Iterable[str] = (),
        classification: str = "restricted",
        audience_refs: Iterable[str] = (),
        knowledge_refs: Iterable[str] = (),
        route_refs: Iterable[str] = (),
        source_refs: Iterable[str] = (),
        reducer_ref: Optional[str] = None,
    ) -> str:
        event_id = self._semantic_event_id(command, kind)
        events = registry.get("events")
        if not isinstance(events, list):
            raise CommandRejectedError("world_event_registry_invalid")
        if any(isinstance(item, Mapping) and item.get("id") == event_id for item in events):
            raise CommandRejectedError("semantic_event_id_conflict")

        def refs(values: Iterable[str]) -> list[str]:
            normalized = sorted({value for value in values if isinstance(value, str) and value})
            return normalized

        actors = refs(actor_refs)
        sources = refs(source_refs) or actors or [command.actor_id]
        event = {
            "id": event_id,
            "kind": kind,
            "status": "resolved",
            "timing": {
                "scheduled_for": None,
                "occurred_at": str(at),
                "started_at": str(at),
                "ended_at": str(at),
            },
            "host_refs": refs(host_refs),
            "actor_refs": actors,
            "place_refs": refs(place_refs),
            "causal_refs": refs(causal_refs),
            "affected_owner_refs": refs(affected_owner_refs),
            "material_consequence_refs": refs(material_consequence_refs),
            "visibility": {
                "classification": classification,
                "witness_refs": actors,
                "audience_refs": refs(audience_refs),
                "knowledge_refs": refs(knowledge_refs),
                "route_refs": refs(route_refs),
            },
            "provenance": {
                "source_kind": "semantic_command",
                "source_refs": sources,
                "archetype_ref": None,
                "recorded_at": str(at),
            },
            "execution": {
                "reducer_ref": reducer_ref or f"shinobi_runtime.commands.{kind}",
                "transaction_ref": "tx.gameplay." + command.digest,
                "receipt_refs": ["receipt.gameplay." + command.digest],
            },
            "supersedes_ref": None,
            "superseded_by_ref": None,
        }
        events.append(event)
        self._roll_world_events(registry, at=at)
        return event_id

    def _resolve_actor_for_write(
        self,
        actor_ref: object,
    ) -> Tuple[str, Dict[str, Any]]:
        actor_id = _stable_id(actor_ref, "actor_ref_invalid")
        try:
            path, _, view = self._resolve_covered_owner_view(
                actor_id, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("actor_owner_unresolved") from exc
        record = self.repository.read_json(path)
        if not isinstance(record, dict):
            raise CommandRejectedError("actor_owner_invalid")
        if view is not record and record.get("schema") == "person-core-registry":
            # A rostered logical person stays indexed through the persistent core.
            # Exactification adds a deeper component rather than replacing identity.
            component_refs = view.get("component_refs") if isinstance(view, Mapping) else None
            exact_path = component_refs.get("profile.exact") if isinstance(component_refs, Mapping) else None
            if not isinstance(exact_path, str):
                raise CommandRejectedError("actor_requires_exact_materialization")
            try:
                exact_record = self.repository.read_json(exact_path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("actor_exact_component_invalid") from exc
            if (
                not isinstance(exact_record, dict)
                or exact_record.get("schema") != "shinobi_character"
                or exact_record.get("owner_id") != actor_id
            ):
                raise CommandRejectedError("actor_exact_component_invalid")
            return exact_path, copy.deepcopy(exact_record)
        if view is not record and record.get("schema") != "shinobi_character":
            raise CommandRejectedError("actor_requires_exact_materialization")
        if record.get("owner_id") != actor_id or record.get("schema") != "shinobi_character":
            raise CommandRejectedError("actor_owner_invalid")
        return path, copy.deepcopy(record)

    def preview(self, command: CommandEnvelope) -> CommandPreview:
        try:
            built = self._build(command)
        except CommandRejectedError as exc:
            if exc.code in _CLARIFICATION_CODES:
                return CommandPreview(
                    status="needs_clarification",
                    code=exc.code,
                    target_revision=command.expected_revision + 1,
                    affected_refs=(self.meta_path, self.scene_path),
                )
            raise
        return CommandPreview(
            status="ready",
            code=built.code,
            target_revision=command.expected_revision + 1,
            affected_refs=built.affected_refs,
        )

    def plan(self, command: CommandEnvelope) -> CommandPlan:
        built = self._build(command)
        return CommandPlan(
            transaction_id=("tx.autonomous." if command.mode == "autonomous" else "tx.gameplay.") + command.digest,
            created_at=command.submitted_at,
            writes=built.writes,
            result=built.result,
            validator=built.validator,
        )

    def _build(self, command: CommandEnvelope) -> _BuiltPlan:
        meta, current_time = self._base(command)
        spec = COMMAND_SPECS[command.command_type]
        if spec.variants:
            expanded = spec.expand_variant_payload(command.payload)
            if expanded is None:
                raise CommandRejectedError(command.command_type + "_payload_fields_invalid")
            command = _ExpandedCommand(command, expanded)
        handler = getattr(self, "_" + command.command_type, None)
        if not callable(handler):
            # COMMAND_SPECS is the single public command registry. A missing
            # same-name reducer is an implementation error, not a second
            # dispatch authority.
            raise RuntimeError(f"missing command reducer for {command.command_type}")
        built = handler(command, meta, current_time)
        return self._with_routing_projections(built)

    def _with_routing_projections(self, built: _BuiltPlan) -> _BuiltPlan:
        """Append non-authoritative routing projections after domain planning.

        Exact mission and formation owners remain authoritative. Central
        reconciliation prevents every producer from reimplementing index
        maintenance while preserving each reducer validator's original write
        set through a read-delegating overlay view.
        """

        try:
            mission_index = reconcile_mission_writes(self.repository, built.writes)
            formation_index = reconcile_formation_writes(self.repository, built.writes)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("derived_routing_index_invalid") from exc
        if mission_index is None and formation_index is None:
            return built

        writes = dict(built.writes)
        if mission_index is not None:
            writes[MISSION_CONTEXT_INDEX_PATH] = _json_bytes(mission_index)
        if formation_index is not None:
            writes[FORMATION_INDEX_PATH] = _json_bytes(formation_index)
        writes = self._prune_noop_writes(writes)
        derived_paths = tuple(
            path for path in (MISSION_CONTEXT_INDEX_PATH, FORMATION_INDEX_PATH)
            if path in writes and path not in built.writes
        )
        if not derived_paths:
            return built

        original_paths = tuple(sorted(built.writes))
        expected_paths = tuple(sorted(writes))
        original_validator = built.validator

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            original_validator(_ValidatorOverlayView(overlay, original_paths), manifest)
            if MISSION_CONTEXT_INDEX_PATH in derived_paths:
                staged_index = overlay.read_json(MISSION_CONTEXT_INDEX_PATH)
                validate_mission_context_index(staged_index)
                if staged_index != mission_index:
                    raise ValueError("mission context projection changed after planning")
            if FORMATION_INDEX_PATH in derived_paths:
                staged_index = overlay.read_json(FORMATION_INDEX_PATH)
                validate_formation_index(staged_index)
                if staged_index != formation_index:
                    raise ValueError("formation route projection changed after planning")

        return _BuiltPlan(
            code=built.code,
            affected_refs=expected_paths,
            writes=writes,
            result=built.result,
            validator=validate,
        )

    def _location_graph(self) -> LocationGraph:
        """Return the current canonical location graph for shared domain helpers."""
        try:
            record = self.repository.read_json(_ROUTES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("world_location_registry_invalid") from exc
        try:
            return LocationGraph(record)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("world_location_registry_invalid") from exc

    def _scene_base(self, current_time: CampaignTime) -> Dict[str, Any]:
        try:
            scene = self.repository.read_json(self.scene_path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("campaign_scene_invalid") from exc
        if (
            not isinstance(scene, dict)
            or scene.get("schema") != "scene"
            or scene.get("world_time") != str(current_time)
        ):
            raise CommandRejectedError("campaign_scene_invalid")
        return scene



    def _time_spanning_base(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        *,
        target_time: CampaignTime,
    ) -> _BuiltPlan:
        inner = CommandEnvelope(
            campaign_id=command.campaign_id,
            request_id=command.request_id + ".time",
            actor_id=command.actor_id,
            command_type="advance_time",
            expected_revision=command.expected_revision,
            submitted_at=command.submitted_at,
            payload={"target_time": str(target_time)},
            mode=command.mode,
        )
        return self._advance_time(inner, meta, current_time)

    def _item_market_price(self, item_ref: str, unit_price_ryo: int) -> Mapping[str, Any]:
        mechanics = self._economy_mechanics()
        prices = mechanics.get("item_prices")
        price_rules = mechanics.get("price_rules")
        row = prices.get(item_ref) if isinstance(prices, Mapping) else None
        if not isinstance(row, Mapping) or not isinstance(price_rules, Mapping):
            raise CommandRejectedError("purchase_contract_item_unpriced")
        base = row.get("base_price_ryo")
        access = row.get("market_access")
        if isinstance(base, bool) or not isinstance(base, int) or base < 0 or not isinstance(access, str):
            raise CommandRejectedError("economy_mechanics_invalid")
        if access in ("institutional_only", "not_for_sale"):
            raise CommandRejectedError("purchase_contract_market_access_denied")
        if access == "open":
            lo_milli, hi_milli = price_rules.get("open_market_min_milli"), price_rules.get("open_market_max_milli")
        elif access == "controlled":
            lo_milli, hi_milli = price_rules.get("controlled_min_milli"), price_rules.get("controlled_max_milli")
        else:
            raise CommandRejectedError("economy_mechanics_invalid")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (lo_milli, hi_milli)):
            raise CommandRejectedError("economy_mechanics_invalid")
        minimum = (base * lo_milli + 999) // 1000
        maximum = (base * hi_milli) // 1000
        if unit_price_ryo < minimum or unit_price_ryo > maximum:
            raise CommandRejectedError("purchase_contract_price_out_of_band")
        return row

    def _service_market_price(self, service_ref: str, unit_price_ryo: Optional[int]) -> Tuple[int, Mapping[str, Any]]:
        mechanics = self._economy_mechanics()
        prices = mechanics.get("service_prices")
        rules = mechanics.get("price_rules")
        row = prices.get(service_ref) if isinstance(prices, Mapping) else None
        if not isinstance(row, Mapping) or not isinstance(rules, Mapping):
            raise CommandRejectedError("service_unpriced")
        base = row.get("base_price_ryo")
        if isinstance(base, bool) or not isinstance(base, int) or base <= 0:
            raise CommandRejectedError("economy_mechanics_invalid")
        price = base if unit_price_ryo is None else unit_price_ryo
        if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
            raise CommandRejectedError("service_price_invalid")
        lo_milli, hi_milli = rules.get("open_market_min_milli"), rules.get("open_market_max_milli")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (lo_milli, hi_milli)):
            raise CommandRejectedError("economy_mechanics_invalid")
        minimum = (base * lo_milli + 999) // 1000
        maximum = (base * hi_milli) // 1000
        if price < minimum or price > maximum:
            raise CommandRejectedError("service_price_out_of_band")
        return price, row

    def _resolve_covered_owner_view(
        self,
        owner_id: str,
        *,
        cache: _OwnerResolutionCache,
    ) -> Tuple[str, str, Mapping[str, Any]]:
        """Resolve one coverage identity through the registered owner index.

        A continuous no-op is still a semantic review: the runtime must prove
        that each declared coverage identity resolves to a readable authority.
        Merely counting strings in a coverage record is not sufficient.
        """

        prefix_index = cache.prefix_index
        if prefix_index is None:
            try:
                index = self.repository.read_json("state/index/owners.json")
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("owner_index_invalid") from exc
            prefix_index = (
                index.get("prefix_index") if isinstance(index, Mapping) else None
            )
            if not isinstance(prefix_index, Mapping):
                raise CommandRejectedError("owner_index_invalid")
            cache.prefix_index = prefix_index
        prefix = re.split(r"[._]", owner_id, maxsplit=1)[0]
        shard_path = prefix_index.get(prefix)
        if not isinstance(shard_path, str):
            raise CommandRejectedError("continuous_coverage_owner_unresolved")
        shard = cache.shards.get(shard_path)
        if shard is None:
            try:
                loaded_shard = self.repository.read_json(shard_path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("owner_index_invalid") from exc
            if (
                not isinstance(loaded_shard, Mapping)
                or loaded_shard.get("prefix") != prefix
            ):
                raise CommandRejectedError("owner_index_invalid")
            shard = loaded_shard
            cache.shards[shard_path] = shard
        owners = shard.get("owners") if isinstance(shard, Mapping) else None
        path = owners.get(owner_id) if isinstance(owners, Mapping) else None
        if not isinstance(path, str):
            raise CommandRejectedError("continuous_coverage_owner_unresolved")
        cached_record = cache.records.get(path)
        if cached_record is None:
            try:
                record = self.repository.read_json(path)
                digest = self.repository.digest(path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError(
                    "continuous_coverage_owner_unresolved"
                ) from exc
            if not isinstance(record, Mapping) or digest is None:
                raise CommandRejectedError("continuous_coverage_owner_unresolved")
            cached_record = (record, digest)
            cache.records[path] = cached_record
        record, digest = cached_record
        identities = (record.get("owner_id"), record.get("id"))
        if owner_id in identities:
            owner_view = record
        else:
            # Bounded causal hosts may own several rostered people or world
            # institutions.  The derived owner index points each stable child
            # ID to the bundle, while this resolver returns only that child
            # view.  The bundle remains the sole writable authority.
            people = record.get("people")
            person = people.get(owner_id) if isinstance(people, Mapping) else None
            if isinstance(person, Mapping) and person.get("id") == owner_id:
                owner_view = person
            else:
                faction = record.get("faction")
                if isinstance(faction, Mapping) and faction.get("id") == owner_id:
                    return path, digest, faction
                payload = record.get("payload")
                institutions = payload.get("institutions") if isinstance(payload, Mapping) else None
                institution = next(
                    (
                        item for item in institutions
                        if isinstance(item, Mapping) and item.get("id") == owner_id
                    ),
                    None,
                ) if isinstance(institutions, list) else None
                if not isinstance(institution, Mapping):
                    raise CommandRejectedError("continuous_coverage_owner_unresolved")
                owner_view = institution
        return path, digest, owner_view

    def _resolve_covered_owner(
        self,
        owner_id: str,
        *,
        cache: Optional[_OwnerResolutionCache] = None,
    ) -> Tuple[str, str]:
        active_cache = cache or _OwnerResolutionCache()
        path, digest, _ = self._resolve_covered_owner_view(
            owner_id,
            cache=active_cache,
        )
        return path, digest

__all__ = ["RepositoryCommandPlanner"]
