from __future__ import annotations

from shinobi_runtime.commands.living_world_support import *
from shinobi_runtime.commands.team_checkin_records import snapshot_refs


def _append_topic(topics: list[str], value: object) -> None:
    if isinstance(value, str) and value and value not in topics:
        topics.append(value)


def _leadership_topic_cues(
    team: Mapping[str, Any],
    profile: Mapping[str, Any],
    doctrine: Mapping[str, Any] | None,
) -> list[str]:
    """Derive a bounded leadership agenda from existing exact-team truth.

    The agenda is descriptive only. It never changes doctrine, assigns roles, or
    decides how Wei responds. The durable ready event snapshots these cues so a
    later conversation does not drift with newer team state.
    """

    topics: list[str] = []
    assignment_ref = team.get("current_assignment_ref")
    if isinstance(assignment_ref, str) and assignment_ref:
        _append_topic(topics, "current assignment readiness, delegation, and contingencies")

    familiarity: Mapping[str, Any] | None = None
    doctrine_training: Mapping[str, Any] | None = None
    if isinstance(doctrine, Mapping):
        raw_familiarity = doctrine.get("familiarity")
        familiarity = raw_familiarity if isinstance(raw_familiarity, Mapping) else None
        raw_training = doctrine.get("training")
        doctrine_training = raw_training if isinstance(raw_training, Mapping) else None

    members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
    if isinstance(familiarity, Mapping):
        values = [
            value
            for ref in members
            for value in [familiarity.get(ref)]
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        if values and (min(values) < 50 or max(values) - min(values) >= 20):
            _append_topic(topics, "uneven doctrine familiarity and where leadership attention is needed")

    training = team.get("training")
    recent = training.get("recent_sessions") if isinstance(training, Mapping) else None
    if isinstance(recent, list) and recent:
        latest = recent[-1]
        targets = latest.get("targets") if isinstance(latest, Mapping) else None
        if isinstance(targets, Mapping):
            distinct = {
                value for value in targets.values()
                if isinstance(value, str) and value
            }
            if len(distinct) > 1:
                _append_topic(topics, "integrating recent individual training into team coordination")
            elif distinct:
                _append_topic(topics, "transferring the latest training block into field execution")

    if isinstance(doctrine_training, Mapping):
        role_focus = doctrine_training.get("role_focus")
        if isinstance(role_focus, Mapping):
            active_focus = {
                value for value in role_focus.values()
                if isinstance(value, str) and value
            }
            if len(active_focus) > 1:
                _append_topic(topics, "role cross-coverage, deputy initiative, and succession under pressure")

    training_focus = profile.get("training_focus", [])
    if isinstance(training_focus, list):
        for value in training_focus:
            _append_topic(topics, value)
            if len(topics) >= 3:
                break

    if not isinstance(assignment_ref, str) or not assignment_ref:
        _append_topic(topics, "next training block, readiness, and what the team can own without Wei")

    if not topics:
        topics.append("readiness, role coverage, and the next training block")
    return topics[:3]


class LivingWorldTeamVitalityMixin:
    """Let player-led teams initiate bounded contact without choosing for Wei.

    Generic team autonomy correctly refuses to revise doctrine or training on a
    player-led team because those are consequential command choices. The old
    behavior stopped there, which made exactly the teams closest to the player
    socially inert during autonomous review. This overlay preserves the agency
    boundary while allowing non-player teammates to raise routine field,
    readiness, training, delegation, and doctrine-integration matters as a
    player-visible event.
    """

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
        team = record_writes.get(owner_ref)
        if team is None:
            try:
                loaded = self.repository.read_json(owner_ref)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("team_owner_invalid") from exc
            if not isinstance(loaded, Mapping):
                raise CommandRejectedError("team_owner_invalid")
            team = loaded

        if team.get("leader_ref") != command.actor_id:
            return super()._apply_team_autonomy_review(
                owner_ref=owner_ref,
                at=at,
                compacted=compacted,
                command=command,
                scheduler=scheduler,
                policy_book=policy_book,
                world_events=world_events,
                record_writes=record_writes,
            )

        team_id = team.get("id")
        team_type = team.get("team_type")
        members = team.get("member_refs")
        if (
            team.get("status") != "active"
            or not isinstance(team_id, str)
            or not team_id
            or not isinstance(team_type, str)
            or not team_type
            or not isinstance(members, list)
            or any(not isinstance(ref, str) or not ref for ref in members)
        ):
            return {
                "team_id": team_id,
                "skipped": "player_led_team_inactive_or_invalid",
            }

        nonplayer_members = [ref for ref in members if ref != command.actor_id]
        if not nonplayer_members:
            return {
                "team_id": team_id,
                "skipped": "player_led_team_no_nonplayer_members",
            }

        try:
            profile = policy_book.team_profile(team_type)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("autonomy_policy_invalid") from exc
        chance = profile.get("player_led_contact_chance_milli", 500)
        if isinstance(chance, bool) or not isinstance(chance, int) or not 0 <= chance <= 1000:
            raise CommandRejectedError("autonomy_policy_invalid")
        effective_chance = min(950, chance + max(0, min(4, compacted - 1)) * 100)
        if _stable_roll(team_id, at, "player-led-checkin", modulo=1000) >= effective_chance:
            return {
                "team_id": team_id,
                "skipped": "player_led_team_routine_self_managed",
                "compacted_reviews": compacted,
            }

        deputy_ref = team.get("deputy_ref")
        if isinstance(deputy_ref, str) and deputy_ref in nonplayer_members:
            contact_actor = deputy_ref
        else:
            contact_actor = nonplayer_members[
                _stable_roll(team_id, at, "player-led-contact-actor", modulo=len(nonplayer_members))
            ]

        doctrine: Mapping[str, Any] | None = None
        doctrine_ref = team.get("doctrine_ref")
        if isinstance(doctrine_ref, str) and doctrine_ref:
            try:
                doctrine_path, _digest, doctrine_view = self._resolve_covered_owner_view(
                    doctrine_ref,
                    cache=_OwnerResolutionCache(),
                )
            except CommandRejectedError:
                doctrine_view = None
                doctrine_path = None
            staged_doctrine = (
                record_writes.get(doctrine_path)
                if isinstance(doctrine_path, str)
                else None
            )
            if isinstance(staged_doctrine, Mapping):
                doctrine = staged_doctrine
            elif isinstance(doctrine_view, Mapping):
                doctrine = doctrine_view

        topic_cues = _leadership_topic_cues(team, profile, doctrine)

        classification = team.get("classification")
        if classification not in ("public", "restricted", "secret"):
            classification = "restricted"
        team_name = team.get("name")
        stable_team_name = team_name if isinstance(team_name, str) and team_name else team_id
        contact_opportunity_ref = f"player_led_team_checkin:{team_id}:{contact_actor}"
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{team_id}:{at}:player-led-checkin",
            kind="player_led_team_checkin_ready",
            at=at,
            host_refs=(team_id,),
            actor_refs=(contact_actor,),
            affected_owner_refs=(),
            # The ready event is the durable authority for the player-facing
            # opportunity. Snapshot refs preserve the exact generated agenda
            # without creating a second writable check-in registry.
            material_consequence_refs=(
                contact_opportunity_ref,
                *snapshot_refs(stable_team_name, topic_cues),
            ),
            classification=classification,
            audience_refs=(command.actor_id,),
            source_refs=(contact_actor,),
            reducer_ref="shinobi_runtime.commands.living_world.player_led_team_checkin",
        )
        return {
            "kind": "player_led_team_checkin",
            "team_id": team_id,
            "team_name": stable_team_name,
            "event_id": event_id,
            "contact_actor_ref": contact_actor,
            "topic_cues": topic_cues,
            "compacted_reviews": compacted,
        }


__all__ = ["LivingWorldTeamVitalityMixin", "_leadership_topic_cues"]
