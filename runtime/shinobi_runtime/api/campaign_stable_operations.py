"""Production transaction failure classification and stable context extensions.

The generic API intentionally collapses transaction failures. The persistent
campaign needs one additional bounded signal so a rolled-back gameplay write can
be diagnosed without shell access to the Railway volume. Production reads also
surface player-safe cold site topology after the base visibility check so the GM
can narrate established places without duplicating static world content into
mutable campaign state. Mission handoff routing stays bounded while distinguishing
current mission work from historical participant missions. Scene-vitality routing
projects already-authoritative locality into a bounded cast without making
presentation detail a second source of campaign truth.
"""

from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.campaign_route_discovery import (
    RouteAwareCampaignOperations as _BaseRouteAwareCampaignOperations,
)
from shinobi_runtime.api.contracts import (
    CommandPlan,
    CommandRejectedError,
    PlannerUnavailableError,
)
from shinobi_runtime.api.operations import OperationError, PlanStateChangedError
from shinobi_runtime.api.scene_vitality import (
    apply_scene_vitality_handoff,
    build_scene_cast,
)
from shinobi_runtime.commands.mission_owner import MissionOwner
from shinobi_runtime.tx.canonical import thaw_json
from shinobi_runtime.tx.errors import (
    DirtyRepositoryError,
    IdempotencyConflictError,
    LockUnavailableError,
    RecoveryError,
    StaleRevisionError,
    TransactionError,
)


_TRANSACTION_FAILURE_CODES = {
    "GitStageError": "transaction_git_stage_failed",
    "GitCommitError": "transaction_git_commit_failed",
    "CommitVerificationError": "transaction_commit_verification_failed",
    "ReadbackVerificationError": "transaction_readback_failed",
    "RemoteDurabilityError": "transaction_remote_durability_failed",
    "WalError": "transaction_wal_failed",
    "PersistenceError": "transaction_persistence_failed",
    "ManifestError": "transaction_manifest_failed",
}
_SITE_DEFINITION_PATH = "game/data/content/strategic-site-definitions.json"
_MAX_SITE_LIST_ITEMS = 96
_MAX_SITE_TEXT = 512
_CURRENT_MISSION_STATES = frozenset(("offered", "accepted", "active", "resolving"))
_MAX_MISSION_OWNER_FILES = 256
_MAX_CONTEXT_MISSION_IDS = 16
_MISSION_COMMANDS = (
    "mission_transition",
    "mission_objective_update",
    "mission_derive_and_settle",
)


def transaction_failure_code(exc: TransactionError) -> str:
    """Return a stable low-information code for a transaction exception type."""

    return _TRANSACTION_FAILURE_CODES.get(type(exc).__name__, "transaction_rejected")


def _bounded_site_list(value: object) -> list[Any] | None:
    if not isinstance(value, list) or len(value) > _MAX_SITE_LIST_ITEMS:
        return None
    result: list[Any] = []
    for item in value:
        if isinstance(item, str):
            if not item or len(item) > _MAX_SITE_TEXT:
                return None
            result.append(item)
            continue
        if isinstance(item, list):
            if len(item) > 8 or any(
                not isinstance(part, str) or not part or len(part) > _MAX_SITE_TEXT
                for part in item
            ):
                return None
            result.append(list(item))
            continue
        if isinstance(item, Mapping):
            clean: dict[str, Any] = {}
            if len(item) > 16:
                return None
            for key, raw in item.items():
                if not isinstance(key, str) or len(key) > 128:
                    return None
                if isinstance(raw, str):
                    if len(raw) > _MAX_SITE_TEXT:
                        return None
                    clean[key] = raw
                elif isinstance(raw, (int, float, bool)) or raw is None:
                    clean[key] = raw
                else:
                    return None
            result.append(clean)
            continue
        return None
    return result


def _apply_current_mission_handoff(
    payload: Mapping[str, Any],
    *,
    mission_ids: tuple[str, ...],
    briefing_ids: tuple[str, ...],
    missions_truncated: bool,
) -> dict[str, Any]:
    """Replace historical mission routing with bounded current-mission routing."""

    projected = dict(payload)
    commands = projected.get("commands")
    if not isinstance(commands, Mapping):
        raise OperationError(503, "mission_context_invalid")
    updated_commands = dict(commands)
    updated_commands["active_mission_owner_ids"] = list(mission_ids)

    command_types = updated_commands.get("command_types")
    if isinstance(command_types, Mapping):
        updated_types = {
            key: dict(value) if isinstance(value, Mapping) else value
            for key, value in command_types.items()
        }
        for command_name in _MISSION_COMMANDS:
            descriptor = updated_types.get(command_name)
            if not isinstance(descriptor, dict):
                continue
            descriptor["availability"] = (
                "available" if mission_ids else "no_mission_owner"
            )
        objective_descriptor = updated_types.get("mission_objective_update")
        if mission_ids and isinstance(objective_descriptor, dict):
            objective_descriptor["availability"] = (
                "requires_persisted_terminal_world_event_evidence"
            )
        updated_commands["command_types"] = updated_types
    projected["commands"] = updated_commands

    projected["mission_reads"] = {
        "operational_brief_owner_ids": list(briefing_ids),
        "use": (
            "Inspect the exact mission owner before presenting a mission briefing, "
            "acceptance or activation, departure or travel, objective resolution, "
            "or reporting when operational details materially matter."
        ),
    }

    context_policy = projected.get("context_policy")
    if isinstance(context_policy, Mapping):
        updated_policy = dict(context_policy)
        raw_truncated = updated_policy.get("truncated_fields", [])
        truncated_fields = [
            value
            for value in raw_truncated
            if isinstance(value, str)
            and value != "commands.active_mission_owner_ids"
        ]
        if missions_truncated:
            truncated_fields.append("commands.active_mission_owner_ids")
        updated_policy["truncated_fields"] = sorted(set(truncated_fields))
        projected["context_policy"] = updated_policy
    return projected


class RouteAwareCampaignOperations(_BaseRouteAwareCampaignOperations):
    """Production operations with diagnostics and player-safe place topology."""

    def _current_player_mission_context(
        self,
        player_id: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
        """Return current participant missions plus briefing read hints.

        Historical terminal missions remain authorized through the inherited
        exact-object read path. This selector is only for current play handoff.
        """

        mission_directory = self.repository.resolve("state/mission")
        if not mission_directory.is_dir():
            return (), (), False
        paths = sorted(mission_directory.glob("mission.*.json"))
        if len(paths) > _MAX_MISSION_OWNER_FILES:
            raise OperationError(503, "mission_context_out_of_bounds")

        current: list[MissionOwner] = []
        try:
            for path in paths:
                relative_path = path.relative_to(self.repository.root).as_posix()
                owner = MissionOwner.from_record(
                    self.repository.read_json(relative_path)
                )
                if (
                    player_id in owner.mission.participant_refs
                    and owner.mission.state in _CURRENT_MISSION_STATES
                ):
                    current.append(owner)
        except (TypeError, ValueError) as exc:
            raise OperationError(503, "mission_context_invalid") from exc

        selected = current[:_MAX_CONTEXT_MISSION_IDS]
        mission_ids = tuple(owner.mission_id for owner in selected)
        briefing_ids = tuple(
            owner.mission_id for owner in selected if owner.briefing is not None
        )
        return mission_ids, briefing_ids, len(current) > _MAX_CONTEXT_MISSION_IDS

    def _player_scene_cast(
        self,
        *,
        player_id: str,
        scene: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Project local known people without inferring immediate presence."""

        permitted_ids = self._permitted_person_lookup_ids(player_id=player_id)
        person_records: dict[str, Mapping[str, Any]] = {}
        for person_id in permitted_ids:
            if person_id == player_id:
                continue
            try:
                _path, record = self._owner_record(person_id)
            except (FileNotFoundError, ValueError, OperationError):
                continue
            if record.get("schema") in ("shinobi_character", "person"):
                person_records[person_id] = record

        team_records: dict[str, Mapping[str, Any]] = {}
        object_reads = payload.get("object_reads")
        team_refs = (
            object_reads.get("suggested_exact_team_refs")
            if isinstance(object_reads, Mapping)
            else None
        )
        if isinstance(team_refs, list):
            for team_ref in team_refs:
                if not isinstance(team_ref, str) or not team_ref.startswith("team."):
                    continue
                try:
                    _path, team = self._owner_record(team_ref)
                except (FileNotFoundError, ValueError, OperationError):
                    continue
                if team.get("schema") == "exact-team":
                    team_records[team_ref] = team

        return build_scene_cast(
            scene=scene,
            player_id=player_id,
            permitted_person_ids=permitted_ids,
            person_records=person_records,
            team_records=team_records,
        )

    def _project_play_context(
        self,
        meta: object,
        scene: object,
        player: object,
        state_root: str,
    ) -> Mapping[str, Any]:
        payload = super()._project_play_context(meta, scene, player, state_root)
        campaign = payload.get("campaign") if isinstance(payload, Mapping) else None
        player_id = campaign.get("player_id") if isinstance(campaign, Mapping) else None
        if not isinstance(player_id, str):
            raise OperationError(503, "mission_context_invalid")
        mission_ids, briefing_ids, missions_truncated = (
            self._current_player_mission_context(player_id)
        )
        projected = _apply_current_mission_handoff(
            payload,
            mission_ids=mission_ids,
            briefing_ids=briefing_ids,
            missions_truncated=missions_truncated,
        )
        if not isinstance(scene, Mapping):
            raise OperationError(503, "scene_vitality_invalid")
        scene_cast = self._player_scene_cast(
            player_id=player_id,
            scene=scene,
            payload=projected,
        )
        return apply_scene_vitality_handoff(
            projected,
            scene_cast=scene_cast,
        )

    def _authored_site_context(self, object_ref: str) -> Mapping[str, Any] | None:
        try:
            catalog = self.repository.read_json(_SITE_DEFINITION_PATH)
        except (FileNotFoundError, ValueError):
            return None
        records = catalog.get("records") if isinstance(catalog, Mapping) else None
        record = records.get(object_ref) if isinstance(records, Mapping) else None
        if not isinstance(record, Mapping):
            return None

        # Hidden cold definitions are not made player-visible merely because the
        # broader place owner can be inspected. Information-path discovery must
        # remain authoritative for secret topology.
        visibility = record.get("visibility")
        if isinstance(visibility, str) and visibility not in ("public", "player_known"):
            return None

        context: dict[str, Any] = {}
        name = record.get("name")
        if isinstance(name, str) and name and len(name) <= 256:
            context["name"] = name
        for key in ("facilities", "infrastructure", "zones", "connections", "site_notes"):
            bounded = _bounded_site_list(record.get(key))
            if bounded is not None:
                context[key] = bounded
        scale = record.get("physical_scale")
        if isinstance(scale, Mapping) and len(scale) <= 16:
            clean_scale: dict[str, Any] = {}
            for key, value in scale.items():
                if (
                    isinstance(key, str)
                    and len(key) <= 128
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                ):
                    clean_scale[key] = value
            if clean_scale:
                context["physical_scale"] = clean_scale
        cells = record.get("cells")
        if isinstance(cells, Mapping) and len(cells) <= 16:
            clean_cells = {
                str(key): value
                for key, value in cells.items()
                if isinstance(key, str)
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            }
            if clean_cells:
                context["cells"] = clean_cells
        rooms = _bounded_site_list(record.get("rooms"))
        if rooms is not None:
            context["rooms"] = rooms
        systems = _bounded_site_list(record.get("systems"))
        if systems is not None:
            context["systems"] = systems
        if not context:
            return None
        context["scope"] = (
            "Stable authored topology and scene affordances only. Current access, occupancy, staffing, stock, "
            "damage, alert, custody, training capacity, and medical capability remain owned by live state."
        )
        return context

    def inspect_game_object(self, object_ref: str) -> Mapping[str, Any]:
        projection = dict(super().inspect_game_object(object_ref))
        if not object_ref.startswith("place."):
            return projection
        authored = self._authored_site_context(object_ref)
        if authored is None:
            return projection
        object_payload = projection.get("object")
        if not isinstance(object_payload, Mapping):
            raise OperationError(503, "place_projection_invalid")
        updated = dict(projection)
        updated_object = dict(object_payload)
        updated_object["authored_site_context"] = dict(authored)
        updated["object"] = updated_object
        return updated

    def execute_command(self, command) -> dict[str, Any]:
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
                    self.schema_validator.validate_overlay(overlay, manifest.paths)
                if self.template_validator is not None:
                    self.template_validator.validate_overlay(overlay, manifest.paths)
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
            raise OperationError(409, transaction_failure_code(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise OperationError(409, "transaction_validation_failed") from exc
        return self._receipt_response(execution.status, execution.receipt)


__all__ = [
    "RouteAwareCampaignOperations",
    "_apply_current_mission_handoff",
    "transaction_failure_code",
]
