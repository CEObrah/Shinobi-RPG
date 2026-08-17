from __future__ import annotations

from shinobi_runtime.commands.constants import TERMINAL_MISSION_STATES as _TERMINAL_MISSION_STATES
from shinobi_runtime.commands.living_world_support import *
from shinobi_runtime.commands.team_composition import player_controlled_record
from shinobi_runtime.domain import LocationGraph


def _development_cursor_skip(cursor: CampaignTime, interval_start: CampaignTime) -> str | None:
    if cursor < interval_start:
        return "development_backlog_requires_reconciliation"
    if cursor > interval_start:
        return "development_window_already_partially_settled"
    return None


class LivingWorldTrainingMixin:
    """Canonical exact-team offscreen training behavior.

    Team reviews may settle bounded routine training for lawful non-player
    participants, including members of a player-led team. The authenticated
    player is never silently trained or used as an autonomous instructor.
    Missions, assignments, health, location, instructor authority, facility
    constraints, and the shared development cursor remain hard prerequisites.
    """

    def _autonomous_training_target(
        self,
        team: Mapping[str, Any],
        person_ref: str,
        person: Mapping[str, Any],
    ) -> str:
        if person_ref == team.get("leader_ref") or person_ref == team.get("deputy_ref"):
            candidate = "operational_skills.leadership"
        else:
            role = (
                str((team.get("roles") or {}).get(person_ref, "")).lower()
                if isinstance(team.get("roles"), Mapping)
                else ""
            )
            candidate = "operational_skills.team_coordination"
            for needles, target in _ROLE_TRAINING_TARGETS:
                if any(needle in role for needle in needles):
                    candidate = target
                    break
        try:
            self._training_target(person, candidate)
            return candidate
        except CommandRejectedError:
            return "operational_skills.team_coordination"

    def _autonomous_team_training_profile(self, team: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            mechanics = self.repository.read_json("game/data/mechanics/training.json")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("training_mechanics_invalid") from exc
        profiles = mechanics.get("autonomous_team_training") if isinstance(mechanics, Mapping) else None
        if not isinstance(profiles, Mapping):
            raise CommandRejectedError("training_mechanics_invalid")
        team_type = team.get("team_type")
        profile = profiles.get(team_type) if isinstance(team_type, str) else None
        if not isinstance(profile, Mapping):
            profile = profiles.get("default")
        hours = profile.get("active_hours_per_week") if isinstance(profile, Mapping) else None
        target_cycle = profile.get("target_cycle") if isinstance(profile, Mapping) else None
        if (
            isinstance(hours, bool)
            or not isinstance(hours, int)
            or not 0 < hours <= 48
            or not isinstance(target_cycle, list)
            or not target_cycle
            or any(not isinstance(target, str) or not target for target in target_cycle)
        ):
            raise CommandRejectedError("training_mechanics_invalid")
        return profile

    def _team_active_mission_ref(
        self,
        *,
        scheduler: CausalSchedulerRegistry,
        member_refs: Sequence[str],
    ) -> Optional[str]:
        members = set(member_refs)
        for _host_id, wrapper in sorted(scheduler.hosts.items()):
            if getattr(wrapper, "authority_kind", None) != "mission":
                continue
            owner_path = getattr(wrapper, "owner_ref", None)
            if not isinstance(owner_path, str) or not owner_path:
                continue
            try:
                owner = MissionOwner.from_record(self.repository.read_json(owner_path))
            except (FileNotFoundError, TypeError, ValueError):
                continue
            if owner.mission.state in _TERMINAL_MISSION_STATES or owner.starts_at is None:
                continue
            if members.intersection(owner.mission.participant_refs):
                return owner.mission_id
        return None

    def _training_candidates(
        self,
        *,
        team: Mapping[str, Any],
        person_ref: str,
        person: Mapping[str, Any],
        policy_cycle: Sequence[str],
    ) -> Tuple[str, ...]:
        ordered: list[str] = []
        preferred = self._autonomous_training_target(team, person_ref, person)
        for target in (preferred, *policy_cycle, "operational_skills.team_coordination", "martial_skills.movement"):
            if target in ordered:
                continue
            try:
                self._training_target(dict(person), target)
            except CommandRejectedError:
                continue
            ordered.append(target)
        return tuple(ordered)

    def _autonomous_training_hours_by_target(
        self,
        *,
        team: Mapping[str, Any],
        person_ref: str,
        person: Mapping[str, Any],
        candidates: Sequence[str],
        policy_cycle: Sequence[str],
        interval_start: CampaignTime,
        reviews: int,
        weekly_hours: Decimal,
    ) -> tuple[Dict[str, Decimal], str]:
        """Allocate each routine review without hiding extra development volume.

        Base teams rotate one target per weekly review. Standing policies may
        override this hook to split the same bounded weekly envelope between
        shared team work and individualized development.
        """
        if not candidates:
            raise CommandRejectedError("no_eligible_training_targets")
        start_index = _stable_roll(str(team.get("id")), person_ref, interval_start, modulo=len(candidates))
        hours_by_target: Dict[str, Decimal] = {}
        latest_target = candidates[(start_index + reviews - 1) % len(candidates)]
        for review_index in range(reviews):
            target = candidates[(start_index + review_index) % len(candidates)]
            hours_by_target[target] = hours_by_target.get(target, Decimal(0)) + weekly_hours
        return hours_by_target, latest_target

    def _eligible_autonomous_group(
        self,
        *,
        team: Mapping[str, Any],
        record_writes: Mapping[str, Mapping[str, Any]],
    ) -> Optional[Tuple[str, Mapping[str, Any], str, list[Tuple[str, str, Dict[str, Any]]], str]]:
        training = team.get("training")
        members = team.get("member_refs")
        instructors = training.get("instructor_refs") if isinstance(training, Mapping) else None
        facilities = training.get("facility_refs") if isinstance(training, Mapping) else None
        if not isinstance(members, list) or not isinstance(instructors, list):
            return None
        if facilities is not None and (
            not isinstance(facilities, list)
            or any(not isinstance(ref, str) or not ref for ref in facilities)
        ):
            raise CommandRejectedError("team_training_contract_invalid")

        try:
            graph = LocationGraph(self.repository.read_json("state/world/routes-and-settlements.json"))
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise CommandRejectedError("training_facility_registry_invalid") from exc

        member_rows: list[Tuple[str, str, Dict[str, Any]]] = []
        for member_ref in members:
            if not isinstance(member_ref, str):
                continue
            try:
                path, _digest, view = self._resolve_covered_owner_view(
                    member_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError:
                continue
            record = record_writes.get(path)
            if record is None:
                if not isinstance(view, Mapping):
                    continue
                resolved = copy.deepcopy(dict(view))
            else:
                resolved = copy.deepcopy(dict(record))
            if player_controlled_record(resolved):
                continue
            profile = capability_profile_from_record(member_ref, resolved)
            location = resolved.get("current_location_id")
            if not profile.available or not isinstance(location, str) or not location:
                continue
            if graph.place(location) is None:
                continue
            member_rows.append((member_ref, path, resolved))

        best: Optional[Tuple[str, Mapping[str, Any], str, list[Tuple[str, str, Dict[str, Any]]], str]] = None
        for instructor_ref in sorted(value for value in instructors if isinstance(value, str)):
            try:
                _path, _digest, view = self._resolve_covered_owner_view(
                    instructor_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError:
                continue
            if not isinstance(view, Mapping) or player_controlled_record(view):
                continue
            profile = capability_profile_from_record(instructor_ref, view)
            location = view.get("current_location_id")
            if not profile.available or not isinstance(location, str) or not location:
                continue
            if graph.place(location) is None:
                continue
            instructor_anchor = graph.anchor(location)

            # Exact co-location remains the strongest basis. A routine team with
            # no facility-specific contract may also assemble across local
            # sublocations that share one route anchor, e.g. household districts
            # inside the same hidden village. This is local weekly commuting, not
            # strategic travel, and never crosses a route anchor.
            exact_group = [
                row for row in member_rows
                if row[2].get("current_location_id") == location
            ]
            assembly_basis = "exact_colocation"
            group = exact_group
            training_location = location
            if len(group) < 2 and not facilities:
                local_group = []
                for row in member_rows:
                    member_location = row[2].get("current_location_id")
                    if not isinstance(member_location, str):
                        continue
                    if graph.place(member_location) is not None and graph.anchor(member_location) == instructor_anchor:
                        local_group.append(row)
                if len(local_group) >= 2:
                    group = local_group
                    training_location = instructor_anchor
                    assembly_basis = "shared_route_anchor"
            if len(group) < 2:
                continue
            candidate = (instructor_ref, view, training_location, group, assembly_basis)
            if best is None or len(group) > len(best[3]) or (
                len(group) == len(best[3]) and instructor_ref < best[0]
            ):
                best = candidate
        return best

    def _apply_team_autonomy_review(
        self,
        *,
        owner_ref: str,
        at: CampaignTime,
        compacted: int,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        policy_book: AutonomousPolicyBook,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        try:
            before_team = self.repository.read_json(owner_ref)
            before_members = (
                [ref for ref in before_team.get("member_refs", []) if isinstance(ref, str)]
                if isinstance(before_team, Mapping)
                else []
            )
        except (FileNotFoundError, ValueError):
            before_members = []

        result = super()._apply_team_autonomy_review(
            owner_ref=owner_ref,
            at=at,
            compacted=compacted,
            command=command,
            scheduler=scheduler,
            policy_book=policy_book,
            world_events=world_events,
            record_writes=record_writes,
        )
        team = record_writes.get(owner_ref)
        if not isinstance(team, dict) or team.get("schema") != "exact-team":
            return result

        team_id = str(team.get("id"))
        history = self._team_history(team_id, at=at, record_writes=record_writes)
        after_members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
        removed = [ref for ref in before_members if ref not in after_members]
        added = [ref for ref in after_members if ref not in before_members]
        if removed or added:
            former = history.setdefault("former_member_refs", [])
            for ref in removed:
                if ref not in former:
                    former.append(ref)
            del former[:-32]
            history["replacement_events"] = int(history.get("replacement_events", 0)) + 1
            event_ref = result.get("event_id") if isinstance(result, Mapping) else None
            if isinstance(event_ref, str):
                notable = history.setdefault("notable_event_refs", [])
                if event_ref not in notable:
                    notable.append(event_ref)
                    del notable[:-_MAX_TEAM_HISTORY_EVENTS]

        # Base autonomy may grant abstract familiarity when no exact training is
        # resolved. Remove that provisional gain before applying real training so
        # the same review never grants both abstract and mechanical credit.
        gain = result.get("routine_familiarity_gain", 0) if isinstance(result, Mapping) else 0
        if isinstance(gain, int) and gain > 0:
            doctrine_ref = team.get("doctrine_ref")
            if isinstance(doctrine_ref, str):
                try:
                    doctrine_path, _digest, doctrine_view = self._resolve_covered_owner_view(
                        doctrine_ref, cache=_OwnerResolutionCache()
                    )
                    doctrine = record_writes.get(doctrine_path)
                    if doctrine is None and isinstance(doctrine_view, Mapping):
                        doctrine = copy.deepcopy(dict(doctrine_view))
                        record_writes[doctrine_path] = doctrine
                    familiarity = doctrine.get("familiarity") if isinstance(doctrine, dict) else None
                    if isinstance(familiarity, dict):
                        for member in after_members:
                            value = familiarity.get(member)
                            if isinstance(value, int) and not isinstance(value, bool):
                                familiarity[member] = max(0, value - gain)
                except CommandRejectedError:
                    pass

        training = self._apply_autonomous_team_training(
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
        return {**dict(result), "routine_familiarity_gain": 0, "actual_training": training}

    def _apply_autonomous_team_training(
        self,
        *,
        team: Dict[str, Any],
        owner_ref: str,
        at: CampaignTime,
        compacted: int,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        policy_book: AutonomousPolicyBook,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        team_id = team.get("id")
        team_type = team.get("team_type")
        members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
        if not isinstance(team_id, str) or not isinstance(team_type, str) or not members:
            return {"skipped": "team_invalid"}
        if team.get("status") != "active":
            return {"skipped": "team_inactive"}
        if team.get("current_assignment_ref") is not None:
            return {"skipped": "active_assignment_preempts_training"}

        lifecycle = team.get("lifecycle")
        autonomy_owner = lifecycle.get("autonomy_owner_ref") if isinstance(lifecycle, Mapping) else None
        if isinstance(autonomy_owner, str):
            memory_path = self._faction_memory_path(autonomy_owner)
            memory = record_writes.get(memory_path)
            if memory is None and self.repository.read_optional_bytes(memory_path) is not None:
                try:
                    memory = self.repository.read_json(memory_path)
                except (FileNotFoundError, ValueError):
                    memory = None
            mapping = memory.get("active_mission_team_refs") if isinstance(memory, Mapping) else None
            if isinstance(mapping, Mapping) and team_id in {
                value for value in mapping.values() if isinstance(value, str)
            }:
                return {"skipped": "team_on_active_mission"}

        mission_ref = self._team_active_mission_ref(scheduler=scheduler, member_refs=members)
        if mission_ref is not None:
            return {"skipped": "active_mission_preempts_training", "mission_ref": mission_ref}

        training = team.get("training")
        if not isinstance(training, Mapping) or training.get("model_ref") != "training.team":
            return {"skipped": "team_training_contract_missing"}

        policy = self._autonomous_team_training_profile(team)
        weekly_hours = Decimal(policy["active_hours_per_week"])
        policy_cycle = tuple(policy["target_cycle"])
        reviews = max(1, compacted)
        interval_start = at.add_seconds(-reviews * 7 * 24 * 60 * 60)
        latest_session_start = at.add_seconds(-int(weekly_hours * Decimal(3600)))

        group = self._eligible_autonomous_group(team=team, record_writes=record_writes)
        if group is None:
            return {"skipped": "no_nonplayer_instructor_and_local_group"}
        instructor_ref, instructor_record, location_ref, member_rows, assembly_basis = group

        try:
            banks = record_writes.get(DEVELOPMENT_BANK_PATH)
            if banks is None:
                banks = copy.deepcopy(self.repository.read_json(DEVELOPMENT_BANK_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("development_bank_invalid") from exc
        entries = banks.get("entries") if isinstance(banks, dict) else None
        if not isinstance(entries, dict):
            raise CommandRejectedError("development_bank_invalid")

        # A partially settled development interval may contain explicit player or
        # other domain activity. Fail closed for that member rather than overlap
        # autonomous team credit with an already-resolved cursor.
        eligible_rows: list[Tuple[str, str, Dict[str, Any]]] = []
        cursor_skips: Dict[str, str] = {}
        for member_ref, member_path, record in member_rows:
            entry = entries.get(member_ref)
            if entry is not None:
                if not isinstance(entry, dict) or not isinstance(entry.get("credits"), dict):
                    raise CommandRejectedError("development_bank_invalid")
                try:
                    cursor = CampaignTime.parse(entry.get("resolved_through"))
                except (TypeError, ValueError) as exc:
                    raise CommandRejectedError("development_bank_invalid") from exc
                cursor_skip = _development_cursor_skip(cursor, interval_start)
                if cursor_skip is not None:
                    cursor_skips[member_ref] = cursor_skip
                    continue
            eligible_rows.append((member_ref, member_path, record))
        if len(eligible_rows) < 2:
            return {
                "skipped": "insufficient_unsettled_nonplayer_members",
                "member_skips": cursor_skips,
            }

        model = self._training_model("training.team")
        factors = model.get("base_factors")
        if not isinstance(factors, Mapping):
            raise CommandRejectedError("training_model_registry_invalid")

        plans: Dict[str, Dict[str, Any]] = {}
        all_targets: set[str] = set()
        for member_ref, _member_path, record in eligible_rows:
            candidates = self._training_candidates(
                team=team,
                person_ref=member_ref,
                person=record,
                policy_cycle=policy_cycle,
            )
            if not candidates:
                cursor_skips[member_ref] = "no_eligible_training_targets"
                continue
            hours_by_target, latest_target = self._autonomous_training_hours_by_target(
                team=team,
                person_ref=member_ref,
                person=record,
                candidates=candidates,
                policy_cycle=policy_cycle,
                interval_start=interval_start,
                reviews=reviews,
                weekly_hours=weekly_hours,
            )
            if not hours_by_target or sum(hours_by_target.values(), Decimal(0)) != weekly_hours * Decimal(reviews):
                raise CommandRejectedError("training_hour_allocation_invalid")
            all_targets.update(hours_by_target)
            plans[member_ref] = {
                "candidates": candidates,
                "hours_by_target": hours_by_target,
                "latest_target": latest_target,
            }

        trained_rows = [row for row in eligible_rows if row[0] in plans]
        if len(trained_rows) < 2:
            return {
                "skipped": "insufficient_members_with_valid_targets",
                "member_skips": cursor_skips,
            }

        facilities = [ref for ref in training.get("facility_refs", []) if isinstance(ref, str)]
        try:
            facility_slots, facility_quality_factor = self._training_facility_capacity(
                location_ref,
                required_slots=len(trained_rows),
                base_quality_factor=factors["facility_quality"],
                required_categories=tuple(
                    sorted({"team_drill", *(self._training_category_for_target(target) for target in all_targets)})
                ),
                module_required=bool(facilities),
            )
        except CommandRejectedError as exc:
            return {"skipped": str(exc)}

        latest_targets = {member_ref: plans[member_ref]["latest_target"] for member_ref, _p, _r in trained_rows}
        session_ref = "training.session.autonomy." + hashlib.sha256(
            f"{command.digest}\x00{team_id}\x00{at}".encode()
        ).hexdigest()[:24]
        try:
            self._record_team_training_session(
                team,
                session_ref=session_ref,
                member_targets=latest_targets,
                instructor_ref=instructor_ref,
                started_at=latest_session_start,
                ended_at=at,
                active_hours=weekly_hours,
            )
        except CommandRejectedError as exc:
            return {"skipped": str(exc)}

        outcomes: Dict[str, Any] = {}
        changed_refs: list[str] = []
        member_paths: Dict[str, str] = {}
        total_member_hours = Decimal(0)
        for member_ref, member_path, record in trained_rows:
            entry = entries.get(member_ref)
            if entry is None:
                entry = {
                    "owner_type": "character",
                    "resolved_through": str(interval_start),
                    "credits": {},
                }
                entries[member_ref] = entry
            member_outcomes: list[Dict[str, Any]] = []
            for target, active_hours in sorted(plans[member_ref]["hours_by_target"].items()):
                container, leaf, starting_value = self._training_target(record, target)
                aptitude = self._training_aptitude(record, target)
                health_factor, recovery_factor = self._health_recovery_factor(record)
                instructor_aptitude = self._training_aptitude(instructor_record, target)
                instructor_quality = max(
                    Decimal("0.85"),
                    min(
                        Decimal("1.20"),
                        Decimal("0.90") + Decimal(instructor_aptitude) / Decimal(500),
                    ),
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
                            required_slots=str(len(trained_rows)),
                            equipment_sets=str(len(trained_rows)),
                            required_sets=str(len(trained_rows)),
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
                total_member_hours += active_hours
                row = {
                    "target": target,
                    "active_hours": format(active_hours, "f"),
                    "starting_value": starting_value,
                    "ending_value": outcome.ending_value,
                    "points_gained": outcome.points_gained,
                    "residual_units": str(outcome.residual_units),
                }
                member_outcomes.append(row)
                if outcome.ending_value != starting_value:
                    changed_refs.append(
                        f"training:{member_ref}:{target}:{starting_value}->{outcome.ending_value}"
                    )
            entry["resolved_through"] = str(at)
            record_writes[member_path] = record
            member_paths[member_ref] = member_path
            outcomes[member_ref] = member_outcomes

        record_writes[owner_ref] = team
        record_writes[DEVELOPMENT_BANK_PATH] = banks

        doctrine_path = None
        doctrine_ref = team.get("doctrine_ref")
        _cycle, _weekly, _recovery, _recent, familiarity_rate = self._team_training_schedule_limits()
        familiarity_gain = int(weekly_hours * Decimal(reviews) * familiarity_rate)
        trained_member_refs = sorted(outcomes)
        if isinstance(doctrine_ref, str) and doctrine_ref and familiarity_gain > 0:
            try:
                doctrine_path, _digest, doctrine_view = self._resolve_covered_owner_view(
                    doctrine_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("team_doctrine_invalid") from exc
            doctrine = record_writes.get(doctrine_path)
            if doctrine is None:
                if not isinstance(doctrine_view, Mapping) or doctrine_view.get("schema") != "team-doctrine":
                    raise CommandRejectedError("team_doctrine_invalid")
                doctrine = copy.deepcopy(dict(doctrine_view))
            familiarity = doctrine.get("familiarity") if isinstance(doctrine, dict) else None
            if not isinstance(familiarity, dict):
                raise CommandRejectedError("team_doctrine_invalid")
            for member_ref in trained_member_refs:
                current = familiarity.get(member_ref, 0)
                if isinstance(current, bool) or not isinstance(current, int):
                    raise CommandRejectedError("team_doctrine_invalid")
                familiarity[member_ref] = min(100, max(0, current) + familiarity_gain)
            record_writes[doctrine_path] = doctrine

        history = self._team_history(team_id, at=at, record_writes=record_writes)
        history["training_sessions"] = int(history.get("training_sessions", 0)) + reviews
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{team_id}:{at}:training",
            kind="team_training_session_resolved",
            at=at,
            host_refs=(team_id,),
            actor_refs=tuple(sorted(set(trained_member_refs + [instructor_ref]))),
            place_refs=(location_ref,),
            affected_owner_refs=tuple(
                sorted(
                    set(member_paths.values())
                    | {owner_ref, DEVELOPMENT_BANK_PATH, self._team_history_path(team_id)}
                    | ({doctrine_path} if doctrine_path else set())
                )
            ),
            material_consequence_refs=tuple(changed_refs)
            or (f"training_hours:{format(total_member_hours, 'f')}",),
            classification=str(team.get("classification", "restricted")),
            audience_refs=tuple(trained_member_refs),
            source_refs=(instructor_ref,),
        )
        notable = history.setdefault("notable_event_refs", [])
        if event_id not in notable:
            notable.append(event_id)
            del notable[:-_MAX_TEAM_HISTORY_EVENTS]
        self._apply_team_relationship_event(
            trained_member_refs,
            event_ref=event_id,
            interaction_kind="shared_training",
            summary=f"Shared team training for {team_id}",
            at=at,
            record_writes=record_writes,
            player_id=command.actor_id,
        )
        return {
            "event_id": event_id,
            "active_hours_per_review": format(weekly_hours, "f"),
            "compacted_reviews": reviews,
            "total_member_hours": format(total_member_hours, "f"),
            "instructor_ref": instructor_ref,
            "assembly_basis": assembly_basis,
            "training_location_ref": location_ref,
            "latest_targets": latest_targets,
            "outcomes": outcomes,
            "member_skips": cursor_skips,
            "familiarity_gain": familiarity_gain,
        }
