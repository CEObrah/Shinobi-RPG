"""One-time semantic repair for historical Konoha exact-team assignment debt.

An older autonomy layer replaced explicitly bounded policy rosters with broad
capability-ranked service personnel. That corrupted ANBU Ro and Root membership
and indirectly blocked the corrected Academy cohort because Kakashi was consumed
by the bad ANBU roster. This command reconciles only the known still-missionless
teams against current bounded policy and Academy affinity policy, keeps existing
team identities and scheduler hosts, preserves individual development already
earned, records roster replacement history, and never advances campaign time.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping, Optional, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.living_world_academy import _academy_affinity_groups
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.commands.team_composition import (
    build_compact_doctrine,
    capability_profile_from_record,
    derive_member_roles,
    doctrine_seed,
)
from shinobi_runtime.sim.events import CampaignTime

_COMMAND = "academy_team_assignment_reconciliation"
_ACADEMY_FACTION_ID = "faction.konoha_mission_office"
_ACADEMY_FACTION_PATH = "state/reg/factions/faction-konoha-mission-office.json"
_TEAM_REGISTRY_PATH = "state/team/registry.json"
_GENERATED_PREFIX = "team.konoha.generated."
_BOUNDED_FACTIONS = (
    "faction.konoha_anbu",
    "faction.root",
)
_INSTALLED = False


def _rank(record: Mapping[str, Any]) -> str:
    career = record.get("career_state")
    return str(
        record.get("official_rank_or_status")
        or (career.get("current_rank_or_status") if isinstance(career, Mapping) else "")
        or ""
    ).lower()


def _load_owner(planner: Any, person_ref: str) -> tuple[str, Dict[str, Any]]:
    try:
        path, _digest, view = planner._resolve_covered_owner_view(
            person_ref, cache=_OwnerResolutionCache()
        )
    except CommandRejectedError as exc:
        raise CommandRejectedError("academy_team_reconciliation_person_invalid") from exc
    if not isinstance(view, Mapping):
        raise CommandRejectedError("academy_team_reconciliation_person_invalid")
    return path, copy.deepcopy(dict(view))


def _academy_policy(planner: Any) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    try:
        _profile, assignment = planner._autonomy_policy_book().faction_context(_ACADEMY_FACTION_ID)
    except (CommandRejectedError, TypeError, ValueError) as exc:
        raise CommandRejectedError("academy_team_reconciliation_policy_invalid") from exc
    spec = assignment.get("team_creation") if isinstance(assignment, Mapping) else None
    if not isinstance(spec, Mapping) or spec.get("mode") != "academy_dynamic":
        raise CommandRejectedError("academy_team_reconciliation_policy_invalid")
    groups = _academy_affinity_groups(spec)
    if len(groups) != 3:
        raise CommandRejectedError("academy_team_reconciliation_policy_invalid")
    all_students = [ref for group in groups for ref in group["student_refs"]]
    if len(all_students) != 9 or len(set(all_students)) != 9:
        raise CommandRejectedError("academy_team_reconciliation_policy_invalid")
    configured = spec.get("instructor_candidate_refs")
    if not isinstance(configured, list):
        raise CommandRejectedError("academy_team_reconciliation_policy_invalid")
    configured_set = {ref for ref in configured if isinstance(ref, str)}
    for group in groups:
        preferred = group.get("preferred_instructor_refs")
        if (
            not isinstance(preferred, Sequence)
            or isinstance(preferred, (str, bytes, bytearray))
            or not preferred
            or not isinstance(preferred[0], str)
            or preferred[0] not in configured_set
        ):
            raise CommandRejectedError("academy_team_reconciliation_policy_invalid")
    return spec, groups


def _bounded_policy_assignments(planner: Any) -> list[Mapping[str, Any]]:
    book = planner._autonomy_policy_book()
    rows: list[Mapping[str, Any]] = []
    for faction_id in _BOUNDED_FACTIONS:
        try:
            _profile, assignment = book.faction_context(faction_id)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("academy_team_reconciliation_policy_invalid") from exc
        spec = assignment.get("team_creation") if isinstance(assignment, Mapping) else None
        if not isinstance(spec, Mapping):
            raise CommandRejectedError("academy_team_reconciliation_policy_invalid")
        team_id = spec.get("team_id")
        candidates = spec.get("candidate_refs")
        leader = spec.get("leader_ref")
        if (
            not isinstance(team_id, str)
            or not team_id
            or not isinstance(candidates, list)
            or len(candidates) < 2
            or len(candidates) > 16
            or any(not isinstance(ref, str) or not ref for ref in candidates)
            or len(set(candidates)) != len(candidates)
            or not isinstance(leader, str)
            or leader not in candidates
        ):
            raise CommandRejectedError("academy_team_reconciliation_policy_invalid")
        rows.append(
            {
                "faction_id": faction_id,
                "team_id": team_id,
                "leader_ref": leader,
                "member_refs": tuple(candidates),
                "team_type": str(spec.get("team_type") or "temporary_task_force"),
            }
        )
    return rows


def _outside_active_members(planner: Any, repair_refs: set[str]) -> set[str]:
    try:
        registry = planner.repository.read_json(_TEAM_REGISTRY_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("academy_team_reconciliation_registry_invalid") from exc
    active = registry.get("active_teams") if isinstance(registry, Mapping) else None
    if not isinstance(active, list):
        raise CommandRejectedError("academy_team_reconciliation_registry_invalid")
    occupied: set[str] = set()
    for team_ref in active:
        if not isinstance(team_ref, str) or team_ref in repair_refs:
            continue
        try:
            _path, team = planner._living_team_view(team_ref, record_writes={})
        except CommandRejectedError:
            continue
        if not isinstance(team, Mapping) or team.get("status") != "active":
            continue
        occupied.update(ref for ref in team.get("member_refs", []) if isinstance(ref, str))
    return occupied


def _missionless_team(planner: Any, team_ref: str) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
    try:
        path, view = planner._living_team_view(team_ref, record_writes={})
    except CommandRejectedError as exc:
        raise CommandRejectedError("academy_team_reconciliation_team_invalid") from exc
    if not isinstance(view, Mapping) or view.get("status") != "active":
        raise CommandRejectedError("academy_team_reconciliation_team_invalid")
    team = copy.deepcopy(dict(view))
    history_path = planner._team_history_path(team_ref)
    try:
        history = copy.deepcopy(planner.repository.read_json(history_path))
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("academy_team_reconciliation_history_invalid") from exc
    if (
        not isinstance(history, dict)
        or history.get("team_id") != team_ref
        or history.get("missions_total") != 0
        or history.get("missions_succeeded") != 0
        or history.get("missions_failed") != 0
    ):
        raise CommandRejectedError("academy_team_reconciliation_team_has_mission_history")
    if team.get("current_assignment_ref") is not None:
        raise CommandRejectedError("academy_team_reconciliation_team_has_active_assignment")
    return path, team, history


def _current_academy_teams(
    planner: Any,
    *,
    desired_students: set[str],
) -> list[tuple[CampaignTime, str, str, Dict[str, Any], Dict[str, Any]]]:
    try:
        faction = planner.repository.read_json(_ACADEMY_FACTION_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("academy_team_reconciliation_faction_invalid") from exc
    plan = faction.get("faction", {}).get("plan_state") if isinstance(faction, Mapping) else None
    refs = plan.get("autonomous_team_refs") if isinstance(plan, Mapping) else None
    if not isinstance(refs, list):
        raise CommandRejectedError("academy_team_reconciliation_faction_invalid")

    rows: list[tuple[CampaignTime, str, str, Dict[str, Any], Dict[str, Any]]] = []
    seen_students: set[str] = set()
    for team_ref in refs:
        if not isinstance(team_ref, str) or not team_ref.startswith(_GENERATED_PREFIX):
            continue
        try:
            path, team, history = _missionless_team(planner, team_ref)
        except CommandRejectedError:
            continue
        leader = team.get("leader_ref")
        members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
        students = [ref for ref in members if ref != leader]
        overlap = set(students) & desired_students
        if not overlap:
            continue
        if len(students) != 3 or not set(students) <= desired_students:
            raise CommandRejectedError("academy_team_reconciliation_roster_conflict")
        activation = team.get("activation")
        try:
            at = CampaignTime.parse(activation.get("at") if isinstance(activation, Mapping) else None)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("academy_team_reconciliation_activation_invalid") from exc
        rows.append((at, team_ref, path, team, history))
        seen_students.update(students)

    if len(rows) != 3 or seen_students != desired_students:
        raise CommandRejectedError("academy_team_reconciliation_roster_conflict")
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def _desired_assignments(
    groups: Sequence[Mapping[str, Any]],
) -> list[tuple[str, tuple[str, str, str]]]:
    desired: list[tuple[str, tuple[str, str, str]]] = []
    for group in groups:
        students = tuple(group["student_refs"])
        instructor = tuple(group["preferred_instructor_refs"])[0]
        desired.append((instructor, students))
    return desired


def _team_profile(planner: Any, team_type: str) -> Mapping[str, Any]:
    try:
        return planner._autonomy_policy_book().team_profile(team_type)
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("academy_team_reconciliation_policy_invalid") from exc


def _rebuild_team(
    planner: Any,
    *,
    team_ref: str,
    team_path: str,
    team: Dict[str, Any],
    history: Dict[str, Any],
    leader_ref: str,
    member_refs: Sequence[str],
    approved_by: str,
    current_time: CampaignTime,
    record_writes: Dict[str, Dict[str, Any]],
) -> Mapping[str, Any]:
    profiles: Dict[str, Any] = {}
    for person_ref in member_refs:
        _person_path, person = _load_owner(planner, person_ref)
        profile = capability_profile_from_record(person_ref, person)
        if not profile.available:
            raise CommandRejectedError("academy_team_reconciliation_member_unavailable")
        profiles[person_ref] = profile

    old_members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
    old_leader = team.get("leader_ref")
    roles = derive_member_roles(tuple(profiles[ref] for ref in member_refs), leader_ref=leader_ref)
    nonleaders = [ref for ref in member_refs if ref != leader_ref]
    deputy = max(
        nonleaders,
        key=lambda ref: (profiles[ref].scores.get("leadership", 0), ref),
        default=None,
    )
    team["leader_ref"] = leader_ref
    team["deputy_ref"] = deputy
    team["member_refs"] = list(member_refs)
    team["roles"] = roles
    training = team.get("training")
    if not isinstance(training, dict):
        raise CommandRejectedError("academy_team_reconciliation_training_invalid")
    training["instructor_refs"] = [leader_ref]
    record_writes[team_path] = team

    team_type = str(team.get("team_type") or "default")
    profile = _team_profile(planner, team_type)
    focus = profile.get("training_focus")
    if not isinstance(focus, list):
        focus = ["team coordination", "mission fundamentals", "recovery discipline"]
    doctrine_identity = profile.get("doctrine_identity")
    motto = profile.get("motto")
    if not isinstance(doctrine_identity, str) or not doctrine_identity:
        doctrine_identity = doctrine_seed(tuple(profiles.values()))[0]
    if not isinstance(motto, str) or not motto:
        motto = doctrine_seed(tuple(profiles.values()))[1]
    doctrine = build_compact_doctrine(
        team,
        profiles,
        at=current_time,
        doctrine_identity=doctrine_identity,
        motto=motto,
        training_focus=focus,
    )
    doctrine["approved_by"] = approved_by
    doctrine_ref = team.get("doctrine_ref")
    if not isinstance(doctrine_ref, str):
        raise CommandRejectedError("academy_team_reconciliation_doctrine_invalid")
    try:
        doctrine_path, _digest, _view = planner._resolve_covered_owner_view(
            doctrine_ref, cache=_OwnerResolutionCache()
        )
    except CommandRejectedError as exc:
        raise CommandRejectedError("academy_team_reconciliation_doctrine_invalid") from exc
    record_writes[doctrine_path] = doctrine

    former = history.setdefault("former_member_refs", [])
    if not isinstance(former, list):
        raise CommandRejectedError("academy_team_reconciliation_history_invalid")
    changed = old_leader != leader_ref or old_members != list(member_refs)
    if changed:
        for ref in old_members:
            if ref not in member_refs and ref not in former:
                former.append(ref)
        del former[:-32]
        history["replacement_events"] = int(history.get("replacement_events", 0)) + 1
        history["as_of"] = str(current_time)
        record_writes[planner._team_history_path(team_ref)] = history
    return {
        "team_ref": team_ref,
        "previous_leader_ref": old_leader,
        "previous_member_refs": old_members,
        "leader_ref": leader_ref,
        "member_refs": list(member_refs),
        "changed": changed,
    }


def _academy_team_assignment_reconciliation(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    if command.payload:
        raise CommandRejectedError("academy_team_reconciliation_payload_fields_invalid")

    academy_spec, groups = _academy_policy(self)
    academy_desired = _desired_assignments(groups)
    desired_students = {ref for _instructor, students in academy_desired for ref in students}
    academy_current = _current_academy_teams(self, desired_students=desired_students)
    bounded_desired = _bounded_policy_assignments(self)

    repair_refs = {row[1] for row in academy_current}
    repair_refs.update(str(row["team_id"]) for row in bounded_desired)
    occupied_elsewhere = _outside_active_members(self, repair_refs)

    all_desired_members = set(desired_students)
    all_desired_members.update(instructor for instructor, _students in academy_desired)
    for row in bounded_desired:
        all_desired_members.update(row["member_refs"])
    if all_desired_members & occupied_elsewhere:
        raise CommandRejectedError("academy_team_reconciliation_external_assignment_conflict")

    # Validate Academy roles before mutating any after-image.
    for instructor_ref, students in academy_desired:
        _path, instructor = _load_owner(self, instructor_ref)
        rank = _rank(instructor)
        profile = capability_profile_from_record(instructor_ref, instructor)
        if ("jonin" not in rank and "jōnin" not in rank) or not profile.available:
            raise CommandRejectedError("academy_team_reconciliation_instructor_unavailable")
        for student_ref in students:
            _student_path, student = _load_owner(self, student_ref)
            if "genin" not in _rank(student) or "academy" in _rank(student):
                raise CommandRejectedError("academy_team_reconciliation_student_invalid")

    record_writes: Dict[str, Dict[str, Any]] = {}
    results: list[Mapping[str, Any]] = []

    # First restore explicit bounded teams. This releases Kakashi from the
    # historically corrupted ANBU roster before the Academy after-image is built.
    for desired in bounded_desired:
        team_ref = str(desired["team_id"])
        team_path, team, history = _missionless_team(self, team_ref)
        results.append(
            _rebuild_team(
                self,
                team_ref=team_ref,
                team_path=team_path,
                team=team,
                history=history,
                leader_ref=str(desired["leader_ref"]),
                member_refs=tuple(desired["member_refs"]),
                approved_by=str(desired["faction_id"]),
                current_time=current_time,
                record_writes=record_writes,
            )
        )

    for row, target in zip(academy_current, academy_desired):
        _activation_at, team_ref, team_path, team, history = row
        instructor_ref, students = target
        results.append(
            _rebuild_team(
                self,
                team_ref=team_ref,
                team_path=team_path,
                team=team,
                history=history,
                leader_ref=instructor_ref,
                member_refs=(instructor_ref, *students),
                approved_by=_ACADEMY_FACTION_ID,
                current_time=current_time,
                record_writes=record_writes,
            )
        )

    changed_results = [row for row in results if row.get("changed") is True]
    if not changed_results:
        raise CommandRejectedError("academy_team_assignment_already_reconciled")

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="konoha_team_assignments_reconciled",
        at=current_time,
        host_refs=tuple(sorted(repair_refs)),
        actor_refs=(command.actor_id,),
        affected_owner_refs=tuple(sorted(record_writes)),
        material_consequence_refs=tuple(
            f"{entry['team_ref']}:{entry['leader_ref']}" for entry in changed_results
        ),
        classification="restricted",
        audience_refs=(command.actor_id,),
        reducer_ref="shinobi_runtime.commands.academy_team_assignment_reconciliation",
    )

    for entry in changed_results:
        history_path = self._team_history_path(str(entry["team_ref"]))
        history = record_writes.get(history_path)
        notable = history.get("notable_event_refs") if isinstance(history, dict) else None
        if isinstance(notable, list) and event_id not in notable:
            notable.append(event_id)
            del notable[:-24]

    writes: Dict[str, bytes] = {
        path: _json_bytes(record) for path, record in record_writes.items()
    }
    writes[self.meta_path] = _json_bytes(
        self._meta_after(meta, command, world_time=current_time)
    )
    writes.update(self._world_event_writes(world_events))
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_json: Dict[str, Any] = {}
    for path, raw in writes.items():
        try:
            expected_json[path] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CommandRejectedError("academy_team_reconciliation_after_image_invalid") from exc

    desired_by_team: Dict[str, tuple[str, ...]] = {}
    for row in bounded_desired:
        desired_by_team[str(row["team_id"])] = tuple(row["member_refs"])
    for row, target in zip(academy_current, academy_desired):
        desired_by_team[row[1]] = (target[0], *target[1])

    def validate(overlay: Any, manifest: Any) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("team assignment reconciliation write set changed after planning")
        staged_meta = overlay.read_json(self.meta_path)
        if (
            staged_meta.get("campaign_id") != command.campaign_id
            or staged_meta.get("revision") != command.expected_revision + 1
            or staged_meta.get("time") != str(current_time)
            or manifest.base_revision != command.expected_revision
            or manifest.target_revision != command.expected_revision + 1
        ):
            raise ValueError("team assignment reconciliation changed campaign clock or revision law")
        for path, expected in expected_json.items():
            if overlay.read_json(path) != expected:
                raise ValueError("team assignment reconciliation after-image differs from plan")

        assigned: set[str] = set()
        for team_ref, desired_members in desired_by_team.items():
            path = next(
                (
                    candidate
                    for candidate, expected in expected_json.items()
                    if isinstance(expected, Mapping) and expected.get("id") == team_ref
                ),
                None,
            )
            if path is None:
                raise ValueError("team assignment reconciliation omitted repaired team")
            team = overlay.read_json(path)
            members = team.get("member_refs")
            if tuple(members) != desired_members or len(set(members)) != len(members):
                raise ValueError("team assignment reconciliation produced invalid roster")
            if any(ref in assigned for ref in members):
                raise ValueError("team assignment reconciliation duplicated active membership")
            assigned.update(members)
        if assigned & occupied_elsewhere:
            raise ValueError("team assignment reconciliation created external membership conflict")

    return _BuiltPlan(
        code="academy_team_assignment_reconciliation_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "world_time_unchanged": str(current_time),
            "event_id": event_id,
            "teams": changed_results,
            "preserved_individual_training": True,
            "team_guy_untouched": True,
            "reconciled_bounded_policy_teams": [
                row["team_id"] for row in bounded_desired
            ],
        },
        validator=validate,
    )


def install_academy_team_assignment_reconciliation() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner

    if _COMMAND not in COMMAND_SPECS:
        COMMAND_SPECS[_COMMAND] = CommandSpec(
            (),
            summary=(
                "Reconcile the current missionless Konoha exact teams affected by the "
                "historical over-broad autonomous roster selector without advancing campaign time."
            ),
            availability="maintenance_only_when_academy_assignment_debt_exists",
        )
    RepositoryCommandPlanner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    setattr(RepositoryCommandPlanner, "_" + _COMMAND, _academy_team_assignment_reconciliation)
    _INSTALLED = True


__all__ = [
    "install_academy_team_assignment_reconciliation",
    "_desired_assignments",
]
