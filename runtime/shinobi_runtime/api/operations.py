"""Shared, bounded campaign operations for HTTP and MCP transports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Mapping, Optional, Sequence, Tuple, Type

from shinobi_runtime.api.contracts import (
    CommandPlan,
    CommandPlanner,
    CommandPreview,
    CommandRejectedError,
    OocAuditProvider,
    OocAuditResult,
    PersonSheetResolver,
    PlannerUnavailableError,
)
from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.commands.mission_owner import MissionOwner
from shinobi_runtime.commands.paths import (
    JINCHURIKI_REGISTRY_PATH,
    PUPPET_REGISTRY_PATH,
    SUMMON_REGISTRY_PATH,
)
from shinobi_runtime.narration import select_narration_modules
from shinobi_runtime.store import (
    CommittedContentRootCache,
    RegisteredSchemaValidator,
    RegisteredTemplateValidator,
    RepositoryStore,
)
from shinobi_runtime.tx import TransactionCoordinator
from shinobi_runtime.tx.canonical import thaw_json
from shinobi_runtime.tx.errors import (
    DirtyRepositoryError,
    IdempotencyConflictError,
    LockUnavailableError,
    RecoveryError,
    StaleRevisionError,
    TransactionError,
)
from shinobi_runtime.tx.locking import SingleWriterLock


@dataclass(frozen=True)
class OperationError(RuntimeError):
    """Stable transport-neutral failure returned by a campaign operation."""

    status_code: int
    code: str

    def __post_init__(self) -> None:
        if self.status_code < 400 or not self.code:
            raise ValueError("operation errors require an HTTP error status and code")
        RuntimeError.__init__(self, self.code)


class PlanStateChangedError(RuntimeError):
    pass


_NARRATIVE_LIST_FIELDS: Tuple[Tuple[str, int], ...] = (
    ("active_questions", 6),
    ("approaching_consequences", 8),
    ("available_reports", 6),
    ("known_clues", 12),
    ("pending_information_paths", 6),
    ("promises_and_threats", 6),
    ("recent_reveals", 8),
    ("suspected_clues", 8),
    ("unresolved_hooks", 8),
)
_NARRATIVE_TEXT_FIELDS = (
    "current_scene_type",
    "current_tension",
    "last_major_choice",
    "last_scene_summary",
)
_PLAYER_FIELDS = (
    "name",
    "official_rank_or_status",
    "current_location_id",
    "current_assignment_or_office",
    "condition",
    "resources",
    "roles",
    "goals",
    "goal_state",
    "career_state",
    "player_choice_protection",
)
_PLAYER_VISIBLE_PERSON_CORE_FIELDS = (
    "person_id",
    "display_name",
    "aliases",
    "life_status",
    "affiliation",
    "rank_or_status",
    "roles",
    "identity_cues",
)
_MAX_PLAYER_HOUSES = 32
_MAX_PLAYER_HOUSE_MEMBERS = 512
_MAX_CONTEXT_PERSON_IDS = 96
_MAX_MISSION_OWNER_FILES = 256
_MAX_CONTEXT_MISSION_IDS = 16
_MAX_FORMATION_REGISTRIES = 64
_MAX_INSPECT_COMPONENTS = 32
_MAX_INSPECT_INVENTORY_ITEMS = 256


def _bounded_sequence(
    value: object,
    *,
    field: str,
    limit: int,
    item_type: Type[Any],
) -> Tuple[list[Any], bool]:
    if not isinstance(value, list):
        raise OperationError(503, "campaign_scene_invalid")
    if any(not isinstance(item, item_type) for item in value):
        raise OperationError(503, "campaign_scene_invalid")
    return list(value[:limit]), len(value) > limit


class CampaignOperations:
    """One authoritative service shared by legacy HTTP and ChatGPT MCP tools.

    Every read runs under the same writer lock and proves the checkout stayed
    pristine.  The full state hash is cached by clean Git HEAD, so cold shards
    are not reopened for each request.  No method accepts repository paths.
    """

    def __init__(
        self,
        *,
        repository: RepositoryStore,
        coordinator: TransactionCoordinator,
        command_planner: CommandPlanner,
        sheet_resolver: PersonSheetResolver,
        audit_provider: OocAuditProvider,
        allowed_actor_ids: FrozenSet[str],
        lock_timeout_seconds: float,
    ) -> None:
        if repository.root != coordinator.repository.root:
            raise ValueError("operations repository and coordinator differ")
        if not callable(getattr(command_planner, "preview", None)) or not callable(
            getattr(command_planner, "plan", None)
        ):
            raise TypeError("command_planner must implement preview and plan")
        if not callable(sheet_resolver) or not callable(audit_provider):
            raise TypeError("sheet and audit resolvers must be callable")
        self.repository = repository
        self.coordinator = coordinator
        self.command_planner = command_planner
        self.sheet_resolver = sheet_resolver
        self.audit_provider = audit_provider
        self.allowed_actor_ids = frozenset(allowed_actor_ids)
        self.lock_timeout_seconds = lock_timeout_seconds
        self.state_roots = CommittedContentRootCache(
            repository.root,
            include_roots=("state",),
            tracked_only=True,
        )
        self.schema_validator = RegisteredSchemaValidator.optional(repository)
        self.template_validator = RegisteredTemplateValidator.optional(repository)

    def _locked(self) -> SingleWriterLock:
        return SingleWriterLock(
            self.coordinator.lock_path,
            timeout=self.lock_timeout_seconds,
        )

    def _read_fingerprint(self) -> Tuple[str, str]:
        head = self.coordinator.git.head()
        return head, self.state_roots.read(head).root_sha256

    def _require_read_only(self, before: Tuple[str, str], code: str) -> None:
        self.coordinator.git.assert_pristine()
        if self._read_fingerprint() != before:
            raise OperationError(503, code)

    def _require_command_base(self, command: CommandEnvelope) -> None:
        # The public API is the player gameplay boundary. Autonomous commands
        # are runtime-internal only and must never be supplied by a client,
        # even when the client is authenticated as the campaign player.
        if command.mode != "gameplay":
            raise OperationError(403, "public_command_mode_not_allowed")
        try:
            actual_campaign = self.repository.campaign_id(
                self.coordinator.meta_path
            )
        except (FileNotFoundError, ValueError) as exc:
            raise OperationError(503, "campaign_meta_unavailable") from exc
        if actual_campaign != command.campaign_id:
            raise OperationError(409, "campaign_mismatch")
        self.repository.require_revision(
            command.expected_revision,
            self.coordinator.meta_path,
        )

    def _player_house_member_ids(self, player_id: str) -> Tuple[str, ...]:
        """Return bounded members of houses that authoritatively include the player."""

        if self.repository.read_optional_bytes("state/index/owners.json") is None:
            return ()
        index = self.repository.read_json("state/index/owners.json")
        prefixes = index.get("prefix_index") if isinstance(index, Mapping) else None
        shard_path = prefixes.get("house") if isinstance(prefixes, Mapping) else None
        if not isinstance(shard_path, str):
            raise OperationError(503, "person_access_policy_invalid")
        shard = self.repository.read_json(shard_path)
        owners = shard.get("owners") if isinstance(shard, Mapping) else None
        if not isinstance(owners, Mapping) or len(owners) > _MAX_PLAYER_HOUSES:
            raise OperationError(503, "person_access_policy_invalid")

        members: set[str] = set()
        for house_id, house_path in sorted(owners.items()):
            if not isinstance(house_id, str) or not isinstance(house_path, str):
                raise OperationError(503, "person_access_policy_invalid")
            house = self.repository.read_json(house_path)
            if not isinstance(house, Mapping) or house.get("schema") != "house":
                raise OperationError(503, "person_access_policy_invalid")
            house_members = house.get("member_ids")
            if not isinstance(house_members, list) or any(
                not isinstance(item, str)
                or not item
                or len(item) > 128
                for item in house_members
            ):
                raise OperationError(503, "person_access_policy_invalid")
            if len(house_members) > _MAX_PLAYER_HOUSE_MEMBERS:
                raise OperationError(503, "person_access_policy_invalid")
            if player_id in house_members:
                members.update(house_members)
        if len(members) > _MAX_PLAYER_HOUSE_MEMBERS:
            raise OperationError(503, "person_access_policy_invalid")
        return tuple(sorted(members))

    def _player_exact_team_member_ids(self, player_id: str) -> Tuple[str, ...]:
        try:
            registry = self.repository.read_json("state/team/registry.json")
        except (FileNotFoundError, ValueError):
            return ()
        team_ids = registry.get("active_teams") if isinstance(registry, Mapping) else None
        if not isinstance(team_ids, list) or len(team_ids) > 128:
            raise OperationError(503, "person_access_policy_invalid")
        members: set[str] = set()
        # Resolve team owners through the derived owner index so this remains a
        # generic exact-team rule rather than a hardcoded list of team paths.
        try:
            index = self.repository.read_json("state/index/owners.json")
            prefixes = index.get("prefix_index") if isinstance(index, Mapping) else None
            shard_path = prefixes.get("team") if isinstance(prefixes, Mapping) else None
            if not isinstance(shard_path, str):
                return ()
            shard = self.repository.read_json(shard_path)
            owners = shard.get("owners") if isinstance(shard, Mapping) else None
            if not isinstance(owners, Mapping):
                raise OperationError(503, "person_access_policy_invalid")
            for team_id in team_ids:
                path = owners.get(team_id)
                if not isinstance(path, str):
                    continue
                team = self.repository.read_json(path)
                if not isinstance(team, Mapping) or team.get("schema") != "exact-team":
                    continue
                team_members = team.get("member_refs")
                if not isinstance(team_members, list) or any(not isinstance(x, str) for x in team_members):
                    raise OperationError(503, "person_access_policy_invalid")
                if player_id in team_members:
                    members.update(team_members)
        except OperationError:
            raise
        except (FileNotFoundError, ValueError) as exc:
            raise OperationError(503, "person_access_policy_invalid") from exc
        return tuple(sorted(members))

    def _player_exact_team_assignment_refs(self, player_id: str) -> Tuple[str, ...]:
        """Return bounded formation assignments for exact teams containing player."""

        try:
            registry = self.repository.read_json("state/team/registry.json")
        except (FileNotFoundError, ValueError):
            return ()
        team_ids = registry.get("active_teams") if isinstance(registry, Mapping) else None
        if not isinstance(team_ids, list) or len(team_ids) > 128:
            raise OperationError(503, "object_access_policy_invalid")
        assignments: set[str] = set()
        try:
            for team_id in team_ids:
                if not isinstance(team_id, str):
                    raise OperationError(503, "object_access_policy_invalid")
                _path, team = self._owner_record(team_id)
                members = team.get("member_refs") if isinstance(team, Mapping) else None
                if (
                    team.get("schema") != "exact-team"
                    or not isinstance(members, list)
                    or any(not isinstance(value, str) for value in members)
                ):
                    raise OperationError(503, "object_access_policy_invalid")
                assignment_ref = team.get("current_assignment_ref")
                if player_id in members and isinstance(assignment_ref, str):
                    assignments.add(assignment_ref)
        except OperationError:
            raise
        except (FileNotFoundError, ValueError) as exc:
            raise OperationError(503, "object_access_policy_invalid") from exc
        if len(assignments) > 128:
            raise OperationError(503, "object_access_policy_invalid")
        return tuple(sorted(assignments))

    def _permitted_person_lookup_ids(
        self,
        *,
        player_id: str,
    ) -> Tuple[str, ...]:
        permitted = {player_id}
        permitted.update(self._player_house_member_ids(player_id))
        permitted.update(self._player_exact_team_member_ids(player_id))
        if len(permitted) > _MAX_PLAYER_HOUSE_MEMBERS + 129:
            raise OperationError(503, "person_access_policy_invalid")
        return tuple(sorted(permitted))

    def _owner_record(self, owner_id: str) -> Tuple[str, Mapping[str, Any]]:
        """Resolve one exact owner ID through the bounded derived owner index."""

        if not isinstance(owner_id, str) or not owner_id:
            raise OperationError(404, "object_not_player_visible")
        index = self.repository.read_json("state/index/owners.json")
        prefixes = index.get("prefix_index") if isinstance(index, Mapping) else None
        prefix = owner_id.split(".", 1)[0].split("_", 1)[0]
        shard_path = prefixes.get(prefix) if isinstance(prefixes, Mapping) else None
        if not isinstance(shard_path, str):
            raise OperationError(404, "object_not_player_visible")
        shard = self.repository.read_json(shard_path)
        owners = shard.get("owners") if isinstance(shard, Mapping) else None
        path = owners.get(owner_id) if isinstance(owners, Mapping) else None
        if not isinstance(path, str):
            raise OperationError(404, "object_not_player_visible")
        record = self.repository.read_json(path)
        if not isinstance(record, Mapping):
            raise OperationError(503, "object_owner_invalid")
        return path, record

    def _player_leads_owner(self, *, player_id: str, owner_ref: str) -> bool:
        if owner_ref == player_id:
            return True
        try:
            _path, owner = self._owner_record(owner_ref)
        except OperationError:
            return False
        if not isinstance(owner, Mapping):
            return False
        if player_id in {owner.get("leader_id"), owner.get("leader_ref")}:
            return True
        leadership = owner.get("leadership")
        if isinstance(leadership, Mapping) and player_id in {
            value for value in leadership.values() if isinstance(value, str)
        }:
            return True
        leadership_ids = owner.get("leadership_ids")
        return isinstance(leadership_ids, list) and player_id in {
            value for value in leadership_ids if isinstance(value, str)
        }

    def _player_can_read_force(
        self,
        *,
        player_id: str,
        force_ref: str,
        force: Mapping[str, Any],
    ) -> bool:
        owner_ref = force.get("owner_ref")
        if isinstance(owner_ref, str) and self._player_leads_owner(player_id=player_id, owner_ref=owner_ref):
            return True
        try:
            assignments = self.repository.read_json("state/org/assignments.json")
        except (FileNotFoundError, ValueError):
            assignments = {"records": []}
        records = assignments.get("records") if isinstance(assignments, Mapping) else None
        if not isinstance(records, list) or len(records) > 512:
            raise OperationError(503, "force_access_policy_invalid")
        return any(
            isinstance(record, Mapping)
            and record.get("status", "active") == "active"
            and record.get("source_owner") == force_ref
            and record.get("receiving_commander") == player_id
            for record in records
        )

    def _formation_record(self, formation_ref: str) -> Tuple[str, Mapping[str, Any]]:
        index = self.repository.read_json("state/formation/index.json")
        registries = index.get("registries") if isinstance(index, Mapping) else None
        if not isinstance(registries, Mapping) or len(registries) > _MAX_FORMATION_REGISTRIES:
            raise OperationError(503, "formation_index_invalid")
        for _force_ref, path in sorted(registries.items()):
            if not isinstance(path, str):
                raise OperationError(503, "formation_index_invalid")
            registry = self.repository.read_json(path)
            rows = registry.get("formations") if isinstance(registry, Mapping) else None
            if not isinstance(rows, list) or len(rows) > 512:
                raise OperationError(503, "formation_registry_invalid")
            for row in rows:
                if isinstance(row, Mapping) and row.get("id") == formation_ref:
                    return path, row
        raise OperationError(404, "object_not_player_visible")

    def _player_mission_ids(self, player_id: str) -> Tuple[Tuple[str, ...], bool]:
        """Return only persisted missions that name the player as a participant."""

        mission_directory = self.repository.resolve("state/mission")
        if not mission_directory.is_dir():
            return (), False
        paths = sorted(mission_directory.glob("mission.*.json"))
        if len(paths) > _MAX_MISSION_OWNER_FILES:
            raise OperationError(503, "mission_context_out_of_bounds")
        mission_ids: list[str] = []
        try:
            for path in paths:
                relative_path = path.relative_to(self.repository.root).as_posix()
                owner = MissionOwner.from_record(
                    self.repository.read_json(relative_path)
                )
                if player_id in owner.mission.participant_refs:
                    mission_ids.append(owner.mission_id)
        except (TypeError, ValueError) as exc:
            raise OperationError(503, "mission_context_invalid") from exc
        return (
            tuple(mission_ids[:_MAX_CONTEXT_MISSION_IDS]),
            len(mission_ids) > _MAX_CONTEXT_MISSION_IDS,
        )

    @staticmethod
    def _player_visible_person_sheet(
        resolved: Mapping[str, Any],
        *,
        person_id: str,
    ) -> Mapping[str, Any]:
        core = resolved.get("core")
        if not isinstance(core, Mapping) or core.get("person_id") != person_id:
            raise OperationError(503, "person_resolver_invalid")
        projected = {
            field: core[field]
            for field in _PLAYER_VISIBLE_PERSON_CORE_FIELDS
            if field in core
        }
        return {
            "view": "player_visible_identity",
            "core": projected,
        }

    def campaign_snapshot(self) -> Mapping[str, Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                meta = self.repository.read_json(self.coordinator.meta_path)
                if not isinstance(meta, dict):
                    raise OperationError(503, "campaign_meta_unavailable")
                campaign_id = meta.get("campaign_id")
                revision = meta.get("revision")
                world_time = meta.get("time")
                if (
                    not isinstance(campaign_id, str)
                    or isinstance(revision, bool)
                    or not isinstance(revision, int)
                    or not isinstance(world_time, str)
                ):
                    raise OperationError(503, "campaign_meta_invalid")
                head = self.coordinator.git.head()
                state_root = self.state_roots.read(head)
                return {
                    "campaign_id": campaign_id,
                    "revision": revision,
                    "world_time": world_time,
                    "state_root": state_root.root_sha256,
                }
        except OperationError:
            raise
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc

    def play_context(self) -> Mapping[str, Any]:
        """Return a compact player-visible handoff, never a route closure."""

        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                meta = self.repository.read_json(self.coordinator.meta_path)
                scene = self.repository.read_json("state/scene.json")
                player = self.repository.read_json("state/player.json")
                context = self._project_play_context(meta, scene, player, before[1])
                self._require_read_only(before, "play_context_mutated_campaign")
        except OperationError:
            raise
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc
        except (FileNotFoundError, ValueError) as exc:
            raise OperationError(503, "play_context_unavailable") from exc
        try:
            validate_bounded_json(context, label="play context", allow_float=True)
        except ValueError as exc:
            raise OperationError(503, "play_context_out_of_bounds") from exc
        return context

    def _project_play_context(
        self,
        meta: object,
        scene: object,
        player: object,
        state_root: str,
    ) -> Mapping[str, Any]:
        if not isinstance(meta, Mapping) or not isinstance(scene, Mapping):
            raise OperationError(503, "play_context_invalid")
        if not isinstance(player, Mapping):
            raise OperationError(503, "play_context_invalid")
        campaign_id = meta.get("campaign_id")
        revision = meta.get("revision")
        world_time = meta.get("time")
        player_id = meta.get("player_id")
        if (
            not isinstance(campaign_id, str)
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or not isinstance(world_time, str)
            or not isinstance(player_id, str)
            or scene.get("schema") != "scene"
            or scene.get("world_time") != world_time
            or player.get("owner_id") != player_id
        ):
            raise OperationError(503, "play_context_invalid")

        narrative = scene.get("narrative", {})
        if not isinstance(narrative, Mapping):
            raise OperationError(503, "campaign_scene_invalid")
        projected_narrative: dict[str, Any] = {}
        truncated_fields: list[str] = []
        for field in _NARRATIVE_TEXT_FIELDS:
            value = narrative.get(field)
            if value is not None:
                if not isinstance(value, str) or len(value) > 4096:
                    raise OperationError(503, "campaign_scene_invalid")
                projected_narrative[field] = value
        for field, limit in _NARRATIVE_LIST_FIELDS:
            values, truncated = _bounded_sequence(
                narrative.get(field, []),
                field=field,
                limit=limit,
                item_type=str,
            )
            projected_narrative[field] = values
            if truncated:
                truncated_fields.append("narrative." + field)

        _, loaded_refs_truncated = _bounded_sequence(
            scene.get("loaded_owner_ids", []),
            field="loaded_owner_ids",
            limit=64,
            item_type=str,
        )
        if loaded_refs_truncated:
            raise OperationError(503, "campaign_scene_invalid")
        boundaries, boundaries_truncated = _bounded_sequence(
            scene.get("known_clock_boundaries", []),
            field="known_clock_boundaries",
            limit=12,
            item_type=dict,
        )
        pressures, pressures_truncated = _bounded_sequence(
            scene.get("observable_pressures", []),
            field="observable_pressures",
            limit=12,
            item_type=str,
        )
        if boundaries_truncated:
            truncated_fields.append("scene.known_clock_boundaries")
        if pressures_truncated:
            truncated_fields.append("scene.observable_pressures")

        permitted_person_ids = self._permitted_person_lookup_ids(
            player_id=player_id,
        )
        suggested_person_ids = permitted_person_ids[:_MAX_CONTEXT_PERSON_IDS]

        scene_type = narrative.get("current_scene_type")
        pressure_inputs = list(pressures)
        current_tension = narrative.get("current_tension")
        if isinstance(current_tension, str) and current_tension:
            pressure_inputs.append(current_tension)
        try:
            narration_router = self.repository.read_json(
                "runtime/contracts/narration-router.json"
            )
            selection = select_narration_modules(
                narration_router,
                scene_type=scene_type,
                pressures=pressure_inputs,
            )
            module_pairs = [(selection.primary_id, selection.primary_path)]
            if selection.secondary_id is not None:
                module_pairs.append(
                    (selection.secondary_id, selection.secondary_path)
                )
            guidance = []
            for module_id, path in module_pairs:
                if not isinstance(path, str):
                    raise ValueError("narration module path is invalid")
                raw = self.repository.read_bytes(path)
                if len(raw) > 16 * 1024:
                    raise ValueError("narration module exceeds its context budget")
                text = raw.decode("utf-8")
                if "\x00" in text:
                    raise ValueError("narration module contains null bytes")
                guidance.append({"module_id": module_id, "guidance": text})
            narration_projection = {
                "primary_module_id": selection.primary_id,
                "secondary_module_id": selection.secondary_id,
                "scene_type_matched": selection.scene_type_matched,
                "matched_pressure_ids": list(selection.matched_pressures),
                "modules": guidance,
            }
        except (FileNotFoundError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise OperationError(503, "narration_routing_invalid") from exc

        player_view = {field: player.get(field) for field in _PLAYER_FIELDS}
        mission_ids, missions_truncated = self._player_mission_ids(player_id)
        if missions_truncated:
            truncated_fields.append("commands.active_mission_owner_ids")
        supported = sorted(
            getattr(self.command_planner, "COMMAND_TYPES", ())
        )
        command_descriptions = {
            name: spec.public_descriptor()
            for name, spec in sorted(COMMAND_SPECS.items())
        }
        pending_combat_zoom_ref = None
        try:
            zoom_registry = self.repository.read_json("state/reg/combat-zoom.json")
            pending_zoom = zoom_registry.get("pending_by_actor") if isinstance(zoom_registry, Mapping) else None
            if isinstance(pending_zoom, Mapping):
                candidate = pending_zoom.get(player_id)
                if isinstance(candidate, str) and candidate:
                    pending_combat_zoom_ref = candidate
        except (FileNotFoundError, ValueError):
            pending_combat_zoom_ref = None
        command_descriptions["advance_time"]["availability"] = (
            "blocked_by_pending_combat_zoom"
            if pending_combat_zoom_ref is not None
            else ("available" if scene.get("time_passage_allowed") is True else "blocked_by_scene_decision")
        )
        for mission_command in ("mission_transition", "mission_objective_update", "mission_derive_and_settle"):
            command_descriptions[mission_command]["availability"] = (
                "available" if mission_ids else "no_mission_owner"
            )
        if mission_ids:
            command_descriptions["mission_objective_update"]["availability"] = (
                "requires_persisted_terminal_world_event_evidence"
            )

        command_surface = {
            "supported_command_types": supported,
            "command_types": {
                name: command_descriptions[name]
                for name in supported
                if name in command_descriptions
            },
            "active_mission_owner_ids": list(mission_ids),
            "known_unsupported_intents": [
                "nonsemantic_arbitrary_patch",
                "caller_asserted_random_outcome",
                "caller_asserted_population_creation",
            ],
            "limits": {
                "one_semantic_command_per_write": True,
                "preview_before_execute": True,
                "execute_requires_exact_preview_envelope": True,
                "unsupported_intent_fails_closed": True,
            },
        }
        return {
            "campaign": {
                "campaign_id": campaign_id,
                "revision": revision,
                "world_time": world_time,
                "state_root": state_root,
                "player_id": player_id,
            },
            "scene": {
                "scene_id": scene.get("scene_id"),
                "world_time": scene.get("world_time"),
                "location_id": scene.get("location_id"),
                "active_combat": scene.get("active_combat"),
                "time_passage_allowed": scene.get("time_passage_allowed"),
                "freeform_actions_allowed": scene.get("freeform_actions_allowed"),
                "scene_summary": scene.get("scene_summary"),
                "decision_required": scene.get("decision_required"),
                "pending_combat_zoom_ref": pending_combat_zoom_ref,
                "known_clock_boundaries": boundaries,
                "observable_pressures": pressures,
                "causal_refs": [],
                "narrative": projected_narrative,
            },
            "player": player_view,
            "person_reads": {
                "suggested_owner_ids": list(suggested_person_ids),
                "total_permitted_ids": len(permitted_person_ids),
                "suggested_ids_truncated": (
                    len(suggested_person_ids) < len(permitted_person_ids)
                ),
                "access_basis": "player_self_known_house_and_exact_team_membership",
                "exact_known_player_house_member_ids_are_permitted": True,
                "exact_current_team_member_ids_are_permitted": True,
                "player_view": "full_logical_sheet",
                "nonplayer_view": "player_visible_identity_only",
            },
            "object_reads": {
                "supported_ref_prefixes": ["team.", "force.", "formation.", "mission.", "place.", "conflict.", "combat.", "custody.", "family.", "reputation:", "project.", "contract.", "commitment.", "item_", "rel.", "inventory:", "price:", "service:", "economy:"],
                "access_policy": "runtime_authorized_player_visible_only",
                "use": "inspect_game_object for one bounded team, force, formation, mission, place, conflict, combat operation, custody, family, reputation, project, contract, commitment, asset, relationship, player inventory, public price, service price, or authorized finance object",
            },
            "commands": command_surface,
            "narration": narration_projection,
            "context_policy": {
                "projection": "player_visible_bounded_handoff",
                "loaded_owner_ids_are_internal_not_player_visibility": True,
                "causal_refs_require_explicit_player_visibility_authority": True,
                "use_typed_reads_for_details": True,
                "truncated_fields": sorted(truncated_fields),
            },
        }

    def _player_special_combat_state(self, player_id: str) -> Mapping[str, Any]:
        """Project only special-combat state actually owned by the player."""
        result: dict[str, Any] = {}
        try:
            jinchuriki = self.repository.read_json(JINCHURIKI_REGISTRY_PATH)
            puppets = self.repository.read_json(PUPPET_REGISTRY_PATH)
            summons = self.repository.read_json(SUMMON_REGISTRY_PATH)
        except (FileNotFoundError, ValueError, TypeError) as exc:
            raise OperationError(503, "special_combat_registry_invalid") from exc

        records = jinchuriki.get("records") if isinstance(jinchuriki, Mapping) else None
        if not isinstance(records, list):
            raise OperationError(503, "special_combat_registry_invalid")
        host = next(
            (dict(row) for row in records if isinstance(row, Mapping) and row.get("host_id") == player_id),
            None,
        )
        if host is not None:
            result["jinchuriki"] = host

        rows = puppets.get("puppets") if isinstance(puppets, Mapping) else None
        if not isinstance(rows, list):
            raise OperationError(503, "special_combat_registry_invalid")
        owned_puppets = [
            dict(row) for row in rows
            if isinstance(row, Mapping) and row.get("owner_id") == player_id
        ]
        if len(owned_puppets) > 128:
            raise OperationError(503, "special_combat_state_out_of_bounds")
        if owned_puppets:
            result["puppets"] = owned_puppets

        profiles = summons.get("profiles") if isinstance(summons, Mapping) else None
        if not isinstance(profiles, Mapping):
            raise OperationError(503, "special_combat_registry_invalid")
        owned_summons = {
            ref: dict(profile) for ref, profile in profiles.items()
            if isinstance(ref, str)
            and isinstance(profile, Mapping)
            and profile.get("contract_owner") == player_id
        }
        if len(owned_summons) > 64:
            raise OperationError(503, "special_combat_state_out_of_bounds")
        if owned_summons:
            result["summons"] = owned_summons
        return result

    def person_sheet(self, person_id: str) -> Mapping[str, Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                meta = self.repository.read_json(self.coordinator.meta_path)
                scene = self.repository.read_json("state/scene.json")
                player_id = meta.get("player_id") if isinstance(meta, Mapping) else None
                if not isinstance(player_id, str) or not isinstance(scene, Mapping):
                    raise OperationError(503, "person_access_policy_invalid")
                _, loaded_refs_truncated = _bounded_sequence(
                    scene.get("loaded_owner_ids", []),
                    field="loaded_owner_ids",
                    limit=64,
                    item_type=str,
                )
                if loaded_refs_truncated:
                    raise OperationError(503, "person_access_policy_invalid")
                if person_id not in self._permitted_person_lookup_ids(
                    player_id=player_id,
                ):
                    # Do not disclose whether a guessed hidden owner exists.
                    raise OperationError(404, "person_not_player_visible")
                resolved = self.sheet_resolver(person_id)
                self._require_read_only(before, "person_resolver_mutated_campaign")
        except OperationError:
            raise
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise OperationError(503, "person_resolver_invalid") from exc
        if resolved is None:
            raise OperationError(404, "person_unresolved")
        if not isinstance(resolved, Mapping):
            raise OperationError(503, "person_resolver_invalid")
        if person_id == player_id:
            sheet = dict(resolved)
            special_state = self._player_special_combat_state(player_id)
            if special_state:
                sheet["special_combat_state"] = special_state
            sheet["view"] = "player_full_logical_sheet"
            view = "player_full_logical_sheet"
        else:
            sheet = dict(
                self._player_visible_person_sheet(resolved, person_id=person_id)
            )
            view = "player_visible_identity"
        try:
            validate_bounded_json(sheet, label="person sheet", allow_float=True)
        except ValueError as exc:
            raise OperationError(503, "person_sheet_out_of_bounds") from exc
        return {"person_id": person_id, "view": view, "sheet": sheet}

    def inspect_game_object(self, object_ref: str) -> Mapping[str, Any]:
        """Return one bounded, player-authorized organization/mission projection.

        This deliberately does not accept repository paths.  The caller names a
        semantic object ID; the runtime proves both identity and visibility.
        """

        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                meta = self.repository.read_json(self.coordinator.meta_path)
                player_id = meta.get("player_id") if isinstance(meta, Mapping) else None
                if not isinstance(player_id, str):
                    raise OperationError(503, "object_access_policy_invalid")

                if object_ref.startswith("team."):
                    _path, team = self._owner_record(object_ref)
                    members = team.get("member_refs") if isinstance(team, Mapping) else None
                    if (
                        team.get("schema") != "exact-team"
                        or not isinstance(members, list)
                        or any(not isinstance(value, str) for value in members)
                        or player_id not in members
                    ):
                        raise OperationError(404, "object_not_player_visible")
                    result = {
                        key: team.get(key)
                        for key in (
                            "schema", "id", "name", "status", "team_type",
                            "leader_ref", "deputy_ref", "member_refs",
                            "assignment_authority_ref", "doctrine_ref",
                            "current_assignment_ref", "location_ref",
                        )
                        if key in team
                    }
                    view = "exact_team"

                elif object_ref.startswith("force."):
                    _path, force = self._owner_record(object_ref)
                    if force.get("schema") != "force" or not self._player_can_read_force(
                        player_id=player_id,
                        force_ref=object_ref,
                        force=force,
                    ):
                        raise OperationError(404, "object_not_player_visible")
                    pools = force.get("troop_pools")
                    if not isinstance(pools, list) or len(pools) > 128:
                        raise OperationError(503, "force_owner_invalid")
                    result = {
                        "schema": "force",
                        "id": force.get("id"),
                        "name": force.get("name"),
                        "total": force.get("total"),
                        "availability": force.get("availability"),
                        "owner_ref": force.get("owner_ref"),
                        "formation_registry_ref": force.get("formation_registry_ref"),
                        "mobilization_anchor_ref": force.get("mobilization_anchor_ref"),
                        "troop_pools": [
                            {
                                key: pool.get(key)
                                for key in (
                                    "id", "count", "quality", "role", "troop_type",
                                    "readiness_class", "doctrine", "training", "loadout",
                                )
                                if key in pool
                            }
                            for pool in pools
                            if isinstance(pool, Mapping)
                        ],
                    }
                    view = "force_summary"

                elif object_ref.startswith("formation."):
                    _path, formation = self._formation_record(object_ref)
                    force_ref = formation.get("force_ref")
                    if not isinstance(force_ref, str):
                        raise OperationError(503, "formation_registry_invalid")
                    _force_path, force = self._owner_record(force_ref)
                    team_assignment_visible = object_ref in self._player_exact_team_assignment_refs(
                        player_id
                    )
                    if force.get("schema") != "force" or not (
                        team_assignment_visible
                        or self._player_can_read_force(
                            player_id=player_id,
                            force_ref=force_ref,
                            force=force,
                        )
                    ):
                        raise OperationError(404, "object_not_player_visible")
                    components = formation.get("components", [])
                    if not isinstance(components, list) or len(components) > _MAX_INSPECT_COMPONENTS:
                        raise OperationError(503, "formation_projection_out_of_bounds")
                    result = {
                        key: formation.get(key)
                        for key in (
                            "id", "name", "force_ref", "role", "lifecycle_origin",
                            "personnel_total", "authorized_personnel", "doctrine_ref",
                            "training_ref", "cohesion", "morale", "readiness",
                            "location_ref", "activity_summary", "tendencies",
                        )
                        if key in formation
                    }
                    result["components"] = [
                        {
                            key: component.get(key)
                            for key in (
                                "id", "troop_type", "role", "count",
                                "rank_distribution", "condition",
                                "tendency_profile_ref",
                            )
                            if key in component
                        }
                        for component in components
                        if isinstance(component, Mapping)
                    ]
                    view = "formation_summary"

                elif object_ref.startswith("mission."):
                    mission_ids, truncated = self._player_mission_ids(player_id)
                    if truncated or object_ref not in mission_ids:
                        raise OperationError(404, "object_not_player_visible")
                    path = f"state/mission/{object_ref}.json"
                    owner = MissionOwner.from_record(self.repository.read_json(path))
                    result = owner.to_record()
                    view = "mission_owner"

                elif object_ref.startswith("place."):
                    world = self.repository.read_json("state/world/routes-and-settlements.json")
                    places = world.get("payload", {}).get("places", []) if isinstance(world, Mapping) else []
                    if not isinstance(places, list) or len(places) > 4096:
                        raise OperationError(503, "place_registry_invalid")
                    matches = [row for row in places if isinstance(row, Mapping) and row.get("id") == object_ref]
                    if len(matches) != 1:
                        raise OperationError(404, "object_not_player_visible")
                    place = matches[0]
                    classification = place.get("knowledge_classification", "public")
                    current_location = None
                    try:
                        _player_path, player = self._owner_record(player_id)
                        current_location = player.get("current_location_id") or player.get("location_ref")
                    except OperationError:
                        pass
                    locally_known = current_location in {object_ref, place.get("route_anchor_ref")}
                    if classification != "public" and not locally_known:
                        raise OperationError(404, "object_not_player_visible")
                    result = {
                        key: place.get(key)
                        for key in (
                            "id", "name", "country_id", "kind", "status", "timeline_status",
                            "route_anchor_ref", "authority_ref", "knowledge_classification", "mechanical_modules",
                        )
                        if key in place
                    }
                    view = "place_summary"

                elif object_ref.startswith("family."):
                    index = self.repository.read_json("state/family/index.json")
                    path = None
                    for bucket_name in ("courtships", "proposals", "unions", "households", "parentage", "successions", "events", "kinships", "parenthoods"):
                        bucket = index.get(bucket_name) if isinstance(index, Mapping) else None
                        if isinstance(bucket, Mapping) and isinstance(bucket.get(object_ref), str):
                            path = bucket[object_ref]
                            break
                    if not isinstance(path, str):
                        raise OperationError(404, "object_not_player_visible")
                    record = self.repository.read_json(path)
                    if not isinstance(record, Mapping):
                        raise OperationError(503, "family_record_invalid")
                    linked = set()
                    for key in ("subject_refs", "participant_refs", "member_refs", "dependent_refs", "parent_refs", "guardian_refs", "candidate_order"):
                        values = record.get(key)
                        if isinstance(values, list):
                            linked.update(x for x in values if isinstance(x, str))
                    for key in ("proposer_id", "target_id", "child_id", "subject_owner_ref"):
                        value = record.get(key)
                        if isinstance(value, str):
                            linked.add(value)
                    for key in ("parent_links", "guardian_links"):
                        values = record.get(key)
                        if isinstance(values, list):
                            for row in values:
                                if isinstance(row, Mapping):
                                    linked.update(
                                        value for value in row.values()
                                        if isinstance(value, str) and (value == player_id or value.startswith(("person.", "pc_", "char.", "canon_")))
                                    )
                    if player_id not in linked:
                        raise OperationError(404, "object_not_player_visible")
                    result = dict(record)
                    view = "family_record"

                elif object_ref.startswith("reputation:"):
                    subject_ref = object_ref.split(":", 1)[1]
                    if not subject_ref:
                        raise OperationError(404, "object_not_player_visible")
                    index = self.repository.read_json("state/reputation/index.json")
                    subjects = index.get("subjects") if isinstance(index, Mapping) else None
                    subject_path = subjects.get(subject_ref) if isinstance(subjects, Mapping) else None
                    if not isinstance(subject_path, str):
                        raise OperationError(404, "object_not_player_visible")
                    subject = self.repository.read_json(subject_path)
                    profiles = subject.get("audience_profiles") if isinstance(subject, Mapping) else None
                    profile_path = profiles.get(player_id) if isinstance(profiles, Mapping) else None
                    if not isinstance(profile_path, str):
                        raise OperationError(404, "object_not_player_visible")
                    profile = self.repository.read_json(profile_path)
                    result = {
                        "subject_id": subject_ref,
                        "subject_type": subject.get("subject_type"),
                        "audience_id": player_id,
                        "as_of": profile.get("as_of"),
                        "standing": profile.get("standing", {}),
                        "dimensions": profile.get("dimensions", {}),
                        "evidence_count": profile.get("evidence_count", 0),
                        "memory_class": profile.get("memory_class"),
                    }
                    view = "reputation_summary"

                elif object_ref.startswith("contract."):
                    registry = self.repository.read_json("state/reg/missions-contracts-projects.json")
                    contracts = registry.get("contracts") if isinstance(registry, Mapping) else None
                    matches = [row for row in contracts or [] if isinstance(row, Mapping) and row.get("id") == object_ref]
                    if len(matches) != 1:
                        raise OperationError(404, "object_not_player_visible")
                    contract = matches[0]
                    counterparties = contract.get("counterparty_refs")
                    visible_refs = {contract.get("issuer_ref")}
                    if isinstance(counterparties, list):
                        visible_refs.update(value for value in counterparties if isinstance(value, str))
                    buyer_ref = contract.get("buyer_ref")
                    seller_ref = contract.get("seller_ref")
                    if isinstance(buyer_ref, str):
                        visible_refs.add(buyer_ref)
                    if isinstance(seller_ref, str):
                        visible_refs.add(seller_ref)
                    if player_id not in visible_refs:
                        raise OperationError(404, "object_not_player_visible")
                    result = dict(contract)
                    view = "contract_summary"

                elif object_ref.startswith("commitment."):
                    registry = self.repository.read_json("state/reg/commitments.json")
                    records = registry.get("records") if isinstance(registry, Mapping) else None
                    matches = [row for row in records or [] if isinstance(row, Mapping) and row.get("id") == object_ref]
                    if len(matches) != 1:
                        raise OperationError(404, "object_not_player_visible")
                    record = matches[0]
                    visible_refs = {record.get("subject_ref"), record.get("target_ref")}
                    if record.get("visibility") != "public" and player_id not in visible_refs:
                        raise OperationError(404, "object_not_player_visible")
                    result = dict(record)
                    view = "commitment_summary"

                elif object_ref.startswith("conflict."):
                    registry = self.repository.read_json("state/conflict/registry.json")
                    records = registry.get("records") if isinstance(registry, Mapping) else None
                    record = records.get(object_ref) if isinstance(records, Mapping) else None
                    if not isinstance(record, Mapping):
                        raise OperationError(404, "object_not_player_visible")
                    fronts = record.get("fronts")
                    if not isinstance(fronts, Mapping) or len(fronts) > 64:
                        raise OperationError(503, "conflict_projection_invalid")
                    raw_objectives = record.get("objectives", {})
                    visible_objectives = {
                        side_ref: list(values)
                        for side_ref, values in raw_objectives.items()
                        if isinstance(side_ref, str)
                        and isinstance(values, list)
                        and self._player_leads_owner(player_id=player_id, owner_ref=side_ref)
                    } if isinstance(raw_objectives, Mapping) else {}
                    result = {
                        "id": record.get("id"),
                        "name": record.get("name"),
                        "status": record.get("status"),
                        "side_refs": record.get("side_refs", []),
                        "player_authorized_objectives": visible_objectives,
                        "started_at": record.get("started_at"),
                        "ended_at": record.get("ended_at"),
                        "ceasefire_consents": record.get("ceasefire_consents", []),
                        "end_consents": record.get("end_consents", []),
                        "fronts": {
                            ref: {
                                key: front.get(key)
                                for key in (
                                    "id", "name", "status", "place_refs", "route_refs",
                                    "formation_refs", "control_ref", "fortification_milli",
                                    "route_state", "occupations",
                                )
                                if key in front
                            }
                            for ref, front in fronts.items()
                            if isinstance(ref, str) and isinstance(front, Mapping)
                        },
                    }
                    view = "conflict_summary"

                elif object_ref.startswith("custody."):
                    registry = self.repository.read_json("state/reg/custody.json")
                    records = registry.get("records") if isinstance(registry, Mapping) else None
                    record = records.get(object_ref) if isinstance(records, Mapping) else None
                    if not isinstance(record, Mapping):
                        raise OperationError(404, "object_not_player_visible")
                    visible_refs = {record.get("subject_ref"), record.get("custodian_ref")}
                    if record.get("visibility") != "public" and player_id not in visible_refs:
                        raise OperationError(404, "object_not_player_visible")
                    result = dict(record)
                    view = "custody_summary"

                elif object_ref.startswith("combat."):
                    component = re.sub(r"[^a-z0-9._-]", "_", object_ref)
                    path = f"state/operation/{component}.json"
                    try:
                        operation = self.repository.read_json(path)
                    except (FileNotFoundError, ValueError) as exc:
                        raise OperationError(404, "object_not_player_visible") from exc
                    participants = operation.get("participants") if isinstance(operation, Mapping) else None
                    if not isinstance(participants, list) or len(participants) > 24:
                        raise OperationError(503, "combat_operation_invalid")
                    visible = False
                    for row in participants:
                        if not isinstance(row, Mapping):
                            continue
                        if row.get("command_authority_ref") == player_id:
                            visible = True
                            break
                        named = row.get("named_actor_refs")
                        if isinstance(named, list) and player_id in named:
                            visible = True
                            break
                        force_ref = row.get("force_ref")
                        if isinstance(force_ref, str):
                            try:
                                _path, force = self._owner_record(force_ref)
                                if self._player_can_read_force(player_id=player_id, force_ref=force_ref, force=force):
                                    visible = True
                                    break
                            except OperationError:
                                pass
                    if not visible:
                        raise OperationError(404, "object_not_player_visible")
                    projected_participants = []
                    for row in participants:
                        if not isinstance(row, Mapping):
                            continue
                        force_ref = row.get("force_ref")
                        own_detail = False
                        if isinstance(force_ref, str):
                            try:
                                _path, force = self._owner_record(force_ref)
                                own_detail = self._player_can_read_force(player_id=player_id, force_ref=force_ref, force=force)
                            except OperationError:
                                own_detail = False
                        common = {
                            key: row.get(key)
                            for key in (
                                "participant_ref", "side_ref", "formation_ref", "committed_count",
                                "aggregate_resolved_count", "personnel", "formation_personnel_after",
                                "named_actor_refs",
                            )
                            if key in row
                        }
                        if own_detail:
                            common.update({
                                key: row.get(key)
                                for key in (
                                    "force_ref", "supply_state", "fortification_milli", "readiness",
                                    "morale", "cohesion", "doctrine_ref", "training_ref",
                                    "command_authority_ref",
                                )
                                if key in row
                            })
                        projected_participants.append(common)
                    outcome = operation.get("outcome") if isinstance(operation, Mapping) else None
                    safe_outcome = {
                        key: outcome.get(key)
                        for key in ("status", "victorious_side_refs", "resolution_mode", "wake_triggers")
                        if isinstance(outcome, Mapping) and key in outcome
                    }
                    pending = outcome.get("pending_named_actor_refs") if isinstance(outcome, Mapping) else None
                    if isinstance(pending, list) and player_id in pending:
                        safe_outcome["player_pending_named_zoom"] = True
                    result = {
                        key: operation.get(key)
                        for key in ("operation_id", "opened_at", "location_id", "scale", "status")
                        if key in operation
                    }
                    result["participants"] = projected_participants
                    result["outcome"] = safe_outcome
                    view = "combat_operation_summary"

                elif object_ref.startswith("project."):
                    registry = self.repository.read_json("state/reg/missions-contracts-projects.json")
                    projects = registry.get("projects") if isinstance(registry, Mapping) else None
                    matches = [row for row in projects or [] if isinstance(row, Mapping) and row.get("id") == object_ref]
                    if len(matches) != 1:
                        raise OperationError(404, "object_not_player_visible")
                    project = matches[0]
                    visible = project.get("authority_ref") == player_id
                    institution_ref = project.get("institution_ref")
                    if not visible and isinstance(institution_ref, str):
                        try:
                            _path, institution = self._owner_record(institution_ref)
                            visible = player_id in {institution.get("leader_id"), institution.get("leader_ref")}
                        except OperationError:
                            visible = False
                    if not visible:
                        raise OperationError(404, "object_not_player_visible")
                    result = dict(project)
                    view = "project_summary"

                elif object_ref.startswith("price:"):
                    item_ref = object_ref.split(":", 1)[1]
                    economy = self.repository.read_json("game/data/mechanics/economy.json")
                    prices = economy.get("item_prices") if isinstance(economy, Mapping) else None
                    row = prices.get(item_ref) if isinstance(prices, Mapping) else None
                    if not isinstance(row, Mapping):
                        raise OperationError(404, "object_not_player_visible")
                    result = {"item_ref": item_ref, **dict(row)}
                    view = "public_item_price"

                elif object_ref.startswith("service:"):
                    service_ref = object_ref.split(":", 1)[1]
                    if not service_ref.startswith("service."):
                        service_ref = "service." + service_ref
                    economy = self.repository.read_json("game/data/mechanics/economy.json")
                    prices = economy.get("service_prices") if isinstance(economy, Mapping) else None
                    row = prices.get(service_ref) if isinstance(prices, Mapping) else None
                    if not isinstance(row, Mapping):
                        raise OperationError(404, "object_not_player_visible")
                    result = {"service_ref": service_ref, **dict(row)}
                    view = "public_service_price"

                elif object_ref.startswith("economy:"):
                    holder_ref = object_ref.split(":", 1)[1]
                    allowed = holder_ref == player_id
                    if holder_ref == "house.tang":
                        try:
                            house = self.repository.read_json("state/house/tang.json")
                        except (FileNotFoundError, ValueError):
                            house = None
                        leadership = house.get("leadership") if isinstance(house, Mapping) else None
                        warrant = house.get("field_command_warrant") if isinstance(house, Mapping) else None
                        allowed = bool(
                            isinstance(leadership, Mapping)
                            and player_id in leadership.values()
                        ) or bool(isinstance(warrant, Mapping) and warrant.get("holder") == player_id)
                    if not allowed:
                        raise OperationError(404, "object_not_player_visible")
                    inventory = self.repository.read_json("state/inventory/registry.json")
                    holders = inventory.get("holders") if isinstance(inventory, Mapping) else None
                    holding = holders.get(holder_ref) if isinstance(holders, Mapping) else None
                    if not isinstance(holding, Mapping):
                        raise OperationError(404, "object_not_player_visible")
                    world = self.repository.read_json("state/world/economies-and-mission-markets.json")
                    finance = world.get("payload", {}).get("economies_and_mission_markets", {}).get("finance", {}) if isinstance(world, Mapping) else {}
                    accounts = finance.get("accounts") if isinstance(finance, Mapping) else None
                    account = accounts.get(holder_ref, {}) if isinstance(accounts, Mapping) else {}
                    capital_assets = finance.get("capital_assets") if isinstance(finance, Mapping) else None
                    visible_assets = {
                        ref: dict(row) for ref, row in (capital_assets.items() if isinstance(capital_assets, Mapping) else [])
                        if isinstance(row, Mapping) and row.get("owner_ref") == holder_ref
                    }
                    result = {
                        "holder_ref": holder_ref,
                        "currency_ryo": holding.get("currency.ryo", 0),
                        "account": dict(account) if isinstance(account, Mapping) else {},
                        "capital_assets": visible_assets,
                    }
                    view = "authorized_finance_summary"

                elif object_ref.startswith("inventory:"):
                    holder_ref = object_ref.split(":", 1)[1]
                    if holder_ref != player_id:
                        raise OperationError(404, "object_not_player_visible")
                    inventory = self.repository.read_json("state/inventory/registry.json")
                    holders = inventory.get("holders") if isinstance(inventory, Mapping) else None
                    holding = holders.get(holder_ref) if isinstance(holders, Mapping) else None
                    if holding is None:
                        holding = {}
                    if not isinstance(holding, Mapping) or len(holding) > _MAX_INSPECT_INVENTORY_ITEMS:
                        raise OperationError(503, "inventory_projection_invalid")
                    quantities = {}
                    for item_ref, quantity in sorted(holding.items(), key=lambda row: str(row[0])):
                        if not isinstance(item_ref, str) or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
                            raise OperationError(503, "inventory_projection_invalid")
                        if quantity:
                            quantities[item_ref] = quantity
                    result = {
                        "holder_ref": holder_ref,
                        "currency_ryo": quantities.get("currency.ryo", 0),
                        "quantities": quantities,
                    }
                    view = "inventory_summary"

                elif object_ref.startswith("item_"):
                    registry = self.repository.read_json("state/reg/named-items.json")
                    items = registry.get("named_items") if isinstance(registry, Mapping) else None
                    matches = [row for row in items or [] if isinstance(row, Mapping) and row.get("id") == object_ref]
                    if len(matches) != 1:
                        raise OperationError(404, "object_not_player_visible")
                    item = matches[0]
                    permitted = set(self._permitted_person_lookup_ids(player_id=player_id))
                    if item.get("physical_holder_id") not in permitted:
                        raise OperationError(404, "object_not_player_visible")
                    result = {key: item.get(key) for key in item if key not in {"secret_notes"}}
                    view = "asset_summary"

                elif object_ref.startswith("rel."):
                    index = self.repository.read_json("state/reg/relationship-edge-index.json")
                    edge_index = index.get("edge_index") if isinstance(index, Mapping) else None
                    path = edge_index.get(object_ref) if isinstance(edge_index, Mapping) else None
                    if not isinstance(path, str):
                        raise OperationError(404, "object_not_player_visible")
                    shard = self.repository.read_json(path)
                    edges = shard.get("relationship_edges") if isinstance(shard, Mapping) else None
                    edge = edges.get(object_ref) if isinstance(edges, Mapping) else None
                    if not isinstance(edge, Mapping) or player_id not in {edge.get("source_id"), edge.get("target_id")}:
                        raise OperationError(404, "object_not_player_visible")
                    result = dict(edge)
                    view = "relationship_summary"

                else:
                    raise OperationError(404, "object_not_player_visible")

                self._require_read_only(before, "object_inspection_mutated_campaign")
        except OperationError:
            raise
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise OperationError(503, "object_inspection_invalid") from exc

        response = {"object_ref": object_ref, "view": view, "object": result}
        try:
            validate_bounded_json(response, label="game object projection", allow_float=True)
        except ValueError as exc:
            raise OperationError(503, "object_projection_out_of_bounds") from exc
        return response

    def preview_command(self, command: CommandEnvelope) -> Mapping[str, Any]:
        if command.actor_id not in self.allowed_actor_ids:
            raise OperationError(403, "actor_not_allowed")
        try:
            with self._locked():
                self._require_command_base(command)
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                preview = self.command_planner.preview(command)
                self._require_read_only(before, "preview_mutated_campaign")
        except OperationError:
            raise
        except StaleRevisionError as exc:
            raise OperationError(409, "stale_revision") from exc
        except PlannerUnavailableError as exc:
            raise OperationError(503, "planner_unavailable") from exc
        except CommandRejectedError as exc:
            raise OperationError(422, exc.code) from exc
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc
        if not isinstance(preview, CommandPreview):
            raise OperationError(503, "planner_preview_invalid")
        if preview.target_revision != command.expected_revision + 1:
            raise OperationError(503, "planner_preview_invalid")
        return {
            "status": preview.status,
            "code": preview.code,
            "target_revision": preview.target_revision,
            "affected_refs": list(preview.affected_refs),
        }

    @staticmethod
    def _receipt_response(status: str, receipt: Any) -> Mapping[str, Any]:
        return {
            "status": status,
            "request_id": receipt.request_id,
            "transaction_id": receipt.transaction_id,
            "campaign_id": receipt.campaign_id,
            "committed_revision": receipt.committed_revision,
            "committed_at": receipt.committed_at,
            "result": thaw_json(receipt.result),
        }

    def execute_command(self, command: CommandEnvelope) -> Mapping[str, Any]:
        if command.actor_id not in self.allowed_actor_ids:
            raise OperationError(403, "actor_not_allowed")
        try:
            existing = self.coordinator.lookup_receipt(command)
            if existing is not None:
                return self._receipt_response("duplicate", existing)
            with self._locked():
                self.coordinator.git.assert_pristine()
                self._require_command_base(command)
                before = self._read_fingerprint()
                plan = self.command_planner.plan(command)
                if not isinstance(plan, CommandPlan):
                    raise OperationError(503, "planner_plan_invalid")
                self._require_read_only(before, "planner_mutated_campaign")
                planned_head, planned_state_root = before

            def guarded_validator(overlay: Any, manifest: Any) -> None:
                current_head = self.coordinator.git.head()
                if current_head != planned_head:
                    raise PlanStateChangedError()
                current_state_root = self.state_roots.read(current_head).root_sha256
                if current_state_root != planned_state_root:
                    raise PlanStateChangedError()
                if self.schema_validator is not None:
                    self.schema_validator.validate_overlay(
                        overlay,
                        manifest.paths,
                    )
                if self.template_validator is not None:
                    self.template_validator.validate_overlay(
                        overlay,
                        manifest.paths,
                    )
                plan.validator(overlay, manifest)

            execution = self.coordinator.execute(
                command,
                transaction_id=plan.transaction_id,
                created_at=plan.created_at,
                writes=plan.writes,
                result=thaw_json(plan.result),
                validator=guarded_validator,
            )
        except OperationError:
            raise
        except PlannerUnavailableError as exc:
            raise OperationError(503, "planner_unavailable") from exc
        except CommandRejectedError as exc:
            raise OperationError(422, exc.code) from exc
        except StaleRevisionError as exc:
            existing = self.coordinator.lookup_receipt(command)
            if existing is not None:
                return self._receipt_response("duplicate", existing)
            raise OperationError(409, "stale_revision") from exc
        except IdempotencyConflictError as exc:
            raise OperationError(409, "idempotency_conflict") from exc
        except PlanStateChangedError as exc:
            raise OperationError(409, "planned_state_changed") from exc
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except (DirtyRepositoryError, RecoveryError) as exc:
            raise OperationError(503, "campaign_unavailable") from exc
        except TransactionError as exc:
            raise OperationError(409, "transaction_rejected") from exc
        except (TypeError, ValueError) as exc:
            raise OperationError(409, "transaction_rejected") from exc
        return self._receipt_response(execution.status, execution.receipt)

    def lookup_command_receipt(
        self,
        command: CommandEnvelope,
    ) -> Optional[Mapping[str, Any]]:
        """Recover an exact immutable receipt without authorizing a new write."""

        if command.actor_id not in self.allowed_actor_ids:
            raise OperationError(403, "actor_not_allowed")
        try:
            existing = self.coordinator.lookup_receipt(command)
        except IdempotencyConflictError as exc:
            raise OperationError(409, "idempotency_conflict") from exc
        except (TypeError, ValueError) as exc:
            raise OperationError(503, "receipt_store_invalid") from exc
        if existing is None:
            return None
        return self._receipt_response("duplicate", existing)

    def ooc_audit(
        self,
        focus: Optional[str],
        observations: Sequence[str],
    ) -> Mapping[str, Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                report = self.audit_provider(focus, tuple(observations))
                self._require_read_only(before, "ooc_audit_mutated_campaign")
        except OperationError:
            raise
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc
        if not isinstance(report, OocAuditResult):
            raise OperationError(503, "ooc_audit_result_invalid")
        if report.write_plan is not None:
            raise OperationError(409, "ooc_write_plan_rejected")
        return {
            "diagnostics": list(report.diagnostics),
            "suggestions": list(report.suggestions),
        }


__all__ = ["CampaignOperations", "OperationError", "PlanStateChangedError"]
