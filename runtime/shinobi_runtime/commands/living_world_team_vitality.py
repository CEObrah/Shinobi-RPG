from __future__ import annotations

from shinobi_runtime.commands.living_world_support import *


class LivingWorldTeamVitalityMixin:
    """Let player-led teams initiate bounded contact without choosing for Wei.

    Generic team autonomy correctly refuses to revise doctrine or training on a
    player-led team because those are consequential command choices. The old
    behavior stopped there, which made exactly the teams closest to the player
    socially inert during autonomous review. This overlay preserves the agency
    boundary while allowing non-player teammates to raise routine field,
    readiness, and training matters as a player-visible event.
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

        training_focus = profile.get("training_focus", [])
        topic_cues = [
            str(value)
            for value in training_focus
            if isinstance(value, str) and value
        ][:2]
        if isinstance(team.get("current_assignment_ref"), str):
            topic_cues.append("current assignment readiness")
        else:
            topic_cues.append("readiness, equipment, and the next training block")

        classification = team.get("classification")
        if classification not in ("public", "restricted", "secret"):
            classification = "restricted"
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{team_id}:{at}:player-led-checkin",
            kind="player_led_team_checkin_ready",
            at=at,
            host_refs=(team_id,),
            actor_refs=(contact_actor,),
            affected_owner_refs=(),
            material_consequence_refs=(),
            classification=classification,
            audience_refs=(command.actor_id,),
            source_refs=(contact_actor,),
            reducer_ref="shinobi_runtime.commands.living_world.player_led_team_checkin",
        )
        team_name = team.get("name")
        return {
            "kind": "player_led_team_checkin",
            "team_id": team_id,
            "team_name": team_name if isinstance(team_name, str) else team_id,
            "event_id": event_id,
            "contact_actor_ref": contact_actor,
            "topic_cues": topic_cues[:3],
            "compacted_reviews": compacted,
        }


__all__ = ["LivingWorldTeamVitalityMixin"]
