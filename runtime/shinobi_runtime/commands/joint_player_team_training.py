"""Joint player participation for the two Sword Manor exact teams.

Routine autonomous team reviews continue to train non-player members under Zhu or
Linh. When Wei is home and available, one bounded joint session represents his
shared command work across both teams and grants one personal development target,
so the same hours are never credited twice merely because he belongs to two teams.
Compacted historical reviews never backfill player attendance that current state
cannot prove; only the latest bounded weekly block may receive player credit.
"""
from __future__ import annotations

import copy
import hashlib
from decimal import Decimal
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands.global_team_training_load import assert_global_team_training_load
from shinobi_runtime.commands.paths import DEVELOPMENT_BANK_PATH
from shinobi_runtime.reducers import TrainingInputs, settle_training

_POLICY = "game/rules/training/autonomy-participation.json"
_INSTALLED = False


def _policy(repository: Any, team_ref: str) -> Mapping[str, Any] | None:
    try:
        registry = repository.read_json(_POLICY)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("team_training_participation_policy_invalid") from exc
    policies = registry.get("policies") if isinstance(registry, Mapping) else None
    row = policies.get(team_ref) if isinstance(policies, Mapping) else None
    return row if isinstance(row, Mapping) else None


def _joint_config(repository: Any, team_ref: str) -> Mapping[str, Any] | None:
    row = _policy(repository, team_ref)
    if not isinstance(row, Mapping) or row.get("joint_player_training_credit_owner") is not True:
        return None
    refs = row.get("joint_player_training_team_refs")
    cycle = row.get("player_joint_target_cycle")
    hours = row.get("player_joint_active_hours_per_week")
    if (
        not isinstance(refs, list)
        or len(refs) < 2
        or len(set(refs)) != len(refs)
        or team_ref not in refs
        or any(not isinstance(ref, str) or not ref.startswith("team.") for ref in refs)
        or not isinstance(cycle, list)
        or not cycle
        or any(not isinstance(target, str) or not target for target in cycle)
        or isinstance(hours, bool)
        or not isinstance(hours, int)
        or not 0 < hours <= 48
    ):
        raise CommandRejectedError("team_training_participation_policy_invalid")
    return row


def _staged_owner(self: Any, owner_ref: str, record_writes: Mapping[str, Mapping[str, Any]]) -> tuple[str, dict[str, Any]]:
    path, _digest, view = self._resolve_covered_owner_view(owner_ref, cache=_OwnerResolutionCache())
    staged = record_writes.get(path)
    if isinstance(staged, Mapping):
        return path, copy.deepcopy(dict(staged))
    if not isinstance(view, Mapping):
        raise CommandRejectedError("training_actor_unresolved")
    return path, copy.deepcopy(dict(view))


def _select_joint_target(self: Any, player: Mapping[str, Any], cycle: list[str], *, team_ref: str, at: Any, team: Mapping[str, Any]) -> str:
    training = team.get("training")
    recent = training.get("recent_sessions") if isinstance(training, Mapping) else None
    if not isinstance(recent, list):
        raise CommandRejectedError("team_training_history_invalid")
    prior = sum(
        1
        for row in recent
        if isinstance(row, Mapping)
        and isinstance(row.get("session_ref"), str)
        and row["session_ref"].startswith("training.session.joint-command.")
    )
    ordered: list[str] = []
    for target in cycle:
        try:
            self._training_target(dict(player), target)
        except CommandRejectedError:
            continue
        if target not in ordered:
            ordered.append(target)
    if not ordered:
        raise CommandRejectedError("no_eligible_training_targets")
    return ordered[prior % len(ordered)]


def install_joint_player_team_training() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    planner = module.CampaignCommandPlanner
    original = planner._apply_autonomous_team_training
    if getattr(original, "_joint_player_team_training", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, *, team: dict[str, Any], owner_ref: str, at: Any, compacted: int, command: Any, scheduler: Any, policy_book: Any, world_events: dict[str, Any], record_writes: dict[str, dict[str, Any]]) -> Mapping[str, Any]:
        base = original(
            self,
            team=team,
            owner_ref=owner_ref,
            at=at,
            compacted=compacted,
            command=command,
            scheduler=scheduler,
            policy_book=policy_book,
            world_events=world_events,
            record_writes=record_writes,
        )
        team_ref = team.get("id")
        if not isinstance(team_ref, str):
            return base
        config = _joint_config(self.repository, team_ref)
        if config is None:
            return base
        player_ref = config.get("participant_ref")
        joint_refs = tuple(config.get("joint_player_training_team_refs", ()))
        assembly_ref = config.get("assembly_location_ref")
        if not isinstance(player_ref, str) or not isinstance(assembly_ref, str):
            raise CommandRejectedError("team_training_participation_policy_invalid")
        if self._team_active_mission_ref(scheduler=scheduler, member_refs=(player_ref,)) is not None:
            return {**dict(base), "player_joint_training_skipped": "player_on_active_mission"}

        player_path, player = _staged_owner(self, player_ref, record_writes)
        if player.get("life_status") not in ("active", "alive") or player.get("current_location_id") != assembly_ref:
            return {**dict(base), "player_joint_training_skipped": "player_not_available_at_assembly"}
        condition = player.get("condition")
        if isinstance(condition, Mapping) and condition.get("readiness") not in (None, "ready"):
            return {**dict(base), "player_joint_training_skipped": "player_not_ready"}

        joint_teams: list[tuple[str, str, dict[str, Any]]] = []
        union_members: set[str] = set()
        for ref in joint_refs:
            path, view = self._exact_team(ref)
            staged = record_writes.get(path)
            resolved = copy.deepcopy(dict(staged if isinstance(staged, Mapping) else view))
            members = resolved.get("member_refs")
            training = resolved.get("training")
            if (
                resolved.get("status") != "active"
                or resolved.get("current_assignment_ref") is not None
                or not isinstance(members, list)
                or player_ref not in members
                or not isinstance(training, Mapping)
                or training.get("model_ref") != "training.team"
            ):
                return {**dict(base), "player_joint_training_skipped": f"joint_team_unavailable:{ref}"}
            union_members.update(value for value in members if isinstance(value, str))
            joint_teams.append((ref, path, resolved))

        instructors = config.get("instructor_refs")
        if not isinstance(instructors, list) or not instructors:
            raise CommandRejectedError("team_training_participation_policy_invalid")
        instructor_ref = None
        instructor_record = None
        for candidate in instructors:
            if not isinstance(candidate, str):
                continue
            _path, record = _staged_owner(self, candidate, record_writes)
            if record.get("life_status") in ("active", "alive") and record.get("current_location_id") == assembly_ref:
                instructor_ref = candidate
                instructor_record = record
                break
        if instructor_ref is None or instructor_record is None:
            return {**dict(base), "player_joint_training_skipped": "no_joint_instructor"}

        for member_ref in sorted(union_members):
            _path, record = _staged_owner(self, member_ref, record_writes)
            if record.get("current_location_id") != assembly_ref:
                return {**dict(base), "player_joint_training_skipped": "joint_roster_not_colocated"}

        # Current location/readiness proves only the latest review interval. A
        # compacted scheduler batch must never manufacture Wei's attendance in
        # earlier weeks that were not individually observed.
        active_hours = Decimal(int(config["player_joint_active_hours_per_week"]))
        started_at = at.add_seconds(-int(active_hours * Decimal(3600)))
        try:
            assert_global_team_training_load(
                self.repository,
                (player_ref,),
                started_at=started_at,
                ended_at=at,
                active_hours=active_hours,
            )
        except CommandRejectedError as exc:
            code = str(exc)
            if code in {"team_training_weekly_limit_exceeded", "team_training_recovery_required"}:
                return {**dict(base), "player_joint_training_skipped": code}
            raise

        try:
            banks = record_writes.get(DEVELOPMENT_BANK_PATH)
            if banks is None:
                banks = copy.deepcopy(self.repository.read_json(DEVELOPMENT_BANK_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("development_bank_invalid") from exc
        entries = banks.get("entries") if isinstance(banks, dict) else None
        if not isinstance(entries, dict):
            raise CommandRejectedError("development_bank_invalid")
        entry = entries.get(player_ref)
        if entry is None:
            entry = {"owner_type": "character", "resolved_through": str(started_at), "credits": {}}
            entries[player_ref] = entry
        if not isinstance(entry, dict) or not isinstance(entry.get("credits"), dict):
            raise CommandRejectedError("development_bank_invalid")
        try:
            cursor = type(at).parse(entry.get("resolved_through"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("development_bank_invalid") from exc
        if cursor > started_at:
            return {**dict(base), "player_joint_training_skipped": "development_window_already_partially_settled"}

        cycle = list(config["player_joint_target_cycle"])
        target = _select_joint_target(self, player, cycle, team_ref=team_ref, at=at, team=team)
        model = self._training_model("training.team")
        factors = model.get("base_factors")
        if not isinstance(factors, Mapping):
            raise CommandRejectedError("training_model_registry_invalid")
        required_categories = (
            "team_drill",
            self._training_category_for_target(target),
        )
        try:
            facility_slots, facility_quality_factor = self._training_facility_capacity(
                assembly_ref,
                required_slots=max(1, len(union_members)),
                base_quality_factor=factors["facility_quality"],
                required_categories=tuple(sorted(set(required_categories))),
                module_required=True,
            )
        except CommandRejectedError as exc:
            return {**dict(base), "player_joint_training_skipped": str(exc)}

        container, leaf, starting_value = self._training_target(player, target)
        aptitude = self._training_aptitude(player, target)
        health_factor, recovery_factor = self._health_recovery_factor(player)
        instructor_aptitude = self._training_aptitude(instructor_record, target)
        instructor_quality = max(
            Decimal("0.85"),
            min(Decimal("1.20"), Decimal("0.90") + Decimal(instructor_aptitude) / Decimal(500)),
        )
        residual = entry["credits"].get(target, 0)
        try:
            outcome = settle_training(
                TrainingInputs(
                    scheduled_hours=str(active_hours),
                    attendance="1",
                    available_instructor_hours=str(active_hours),
                    required_instructor_hours=str(active_hours),
                    facility_slots=facility_slots,
                    required_slots=str(max(1, len(union_members))),
                    equipment_sets=str(max(1, len(union_members))),
                    required_sets=str(max(1, len(union_members))),
                    instructor_quality_factor=str(instructor_quality),
                    facility_quality_factor=facility_quality_factor,
                    equipment_factor=factors["equipment"],
                    health_factor=health_factor,
                    recovery_factor=recovery_factor,
                    relevance_factor=factors["relevance"],
                    difficulty_fit_factor=factors["difficulty_fit"],
                    aptitude=aptitude,
                    experience_modifier="1",
                    current_value=starting_value,
                    residual_units=residual,
                    representation="exact",
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("training_resolution_invalid") from exc
        container[leaf] = outcome.ending_value
        entry["credits"][target] = float(outcome.residual_units)
        entry["resolved_through"] = str(at)

        session_ref = "training.session.joint-command." + hashlib.sha256(
            f"{command.digest}\x00{team_ref}\x00{at}".encode()
        ).hexdigest()[:24]
        self._record_team_training_session(
            team,
            session_ref=session_ref,
            member_targets={player_ref: target},
            instructor_ref=instructor_ref,
            started_at=started_at,
            ended_at=at,
            active_hours=active_hours,
        )
        record_writes[owner_ref] = team
        record_writes[player_path] = player
        record_writes[DEVELOPMENT_BANK_PATH] = banks

        affected: set[str] = {owner_ref, player_path, DEVELOPMENT_BANK_PATH}
        _cycle_days, _weekly, _recovery, _recent, familiarity_rate = self._team_training_schedule_limits()
        familiarity_gain = int(active_hours * familiarity_rate)
        for _ref, path, joined_team in joint_teams:
            doctrine_ref = joined_team.get("doctrine_ref")
            if not isinstance(doctrine_ref, str) or not doctrine_ref or familiarity_gain <= 0:
                continue
            doctrine_path, _digest, doctrine_view = self._resolve_covered_owner_view(
                doctrine_ref, cache=_OwnerResolutionCache()
            )
            doctrine = record_writes.get(doctrine_path)
            if doctrine is None:
                doctrine = copy.deepcopy(dict(doctrine_view))
            familiarity = doctrine.get("familiarity") if isinstance(doctrine, Mapping) else None
            if not isinstance(familiarity, dict):
                raise CommandRejectedError("team_doctrine_invalid")
            current = familiarity.get(player_ref, 0)
            if isinstance(current, bool) or not isinstance(current, int):
                raise CommandRejectedError("team_doctrine_invalid")
            familiarity[player_ref] = min(100, max(0, current) + familiarity_gain)
            record_writes[doctrine_path] = doctrine
            affected.add(doctrine_path)
            affected.add(path)

        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"joint-player-training:{team_ref}:{at}",
            kind="joint_team_command_training_resolved",
            at=at,
            host_refs=joint_refs,
            actor_refs=(player_ref, instructor_ref),
            place_refs=(assembly_ref,),
            affected_owner_refs=tuple(sorted(affected)),
            material_consequence_refs=(
                f"training:{player_ref}:{target}:{starting_value}->{outcome.ending_value}",
                f"joint_team_hours:{active_hours}",
            ),
            classification="restricted",
            audience_refs=tuple(sorted(union_members)),
            source_refs=(instructor_ref,),
            reducer_ref="shinobi_runtime.commands.joint_player_team_training",
        )
        return {
            **dict(base),
            "player_joint_training": {
                "event_id": event_id,
                "team_refs": list(joint_refs),
                "active_hours": format(active_hours.normalize(), "f"),
                "compacted_reviews_seen": max(1, compacted),
                "historical_player_weeks_backfilled": False,
                "instructor_ref": instructor_ref,
                "target": target,
                "starting_value": starting_value,
                "ending_value": outcome.ending_value,
                "points_gained": outcome.points_gained,
                "residual_units": str(outcome.residual_units),
            },
        }

    wrapped._joint_player_team_training = True  # type: ignore[attr-defined]
    planner._apply_autonomous_team_training = wrapped
    _INSTALLED = True


__all__ = ["install_joint_player_team_training"]
