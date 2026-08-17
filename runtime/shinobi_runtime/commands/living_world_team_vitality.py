from __future__ import annotations

from shinobi_runtime.commands.living_world_support import *
from shinobi_runtime.commands.team_checkin_records import snapshot_refs
from shinobi_runtime.commands.team_leadership_context import (
    leadership_topic_cues,
    relationship_contact_mode,
    topic_ownership_cues,
)

# Backwards-compatible source-level helper name used by focused tests and
# existing callers. The implementation owner is team_leadership_context.py.
_leadership_topic_cues = leadership_topic_cues


class LivingWorldTeamVitalityMixin:
    """Let player-led teams initiate bounded contact without choosing for Wei.

    Generic team autonomy correctly refuses to revise doctrine or training on a
    player-led team because those are consequential command choices. This
    overlay preserves that agency boundary while allowing non-player teammates
    to raise routine field, readiness, training, delegation, doctrine-integration,
    relationship-shaped communication, and after-action matters as a durable
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

        # Use the established deputy as the normal command-channel contact when
        # one exists. Stable selection remains the fallback for teams without a
        # non-player deputy. This makes delegation structure visible without
        # inventing a new command hierarchy.
        deputy_ref = team.get("deputy_ref")
        if isinstance(deputy_ref, str) and deputy_ref in nonplayer_members:
            contact_actor = deputy_ref
            contact_basis = "established_deputy"
        else:
            contact_actor = nonplayer_members[
                _stable_roll(team_id, at, "player-led-contact-actor", modulo=len(nonplayer_members))
            ]
            contact_basis = "stable_team_member"

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

        history: Mapping[str, Any] | None = None
        history_path_resolver = getattr(self, "_team_history_path", None)
        if callable(history_path_resolver):
            history_path = history_path_resolver(team_id)
            staged_history = record_writes.get(history_path)
            if isinstance(staged_history, Mapping):
                history = staged_history
            elif self.repository.read_optional_bytes(history_path) is not None:
                try:
                    loaded_history = self.repository.read_json(history_path)
                except (FileNotFoundError, ValueError):
                    loaded_history = None
                if isinstance(loaded_history, Mapping):
                    history = loaded_history

        topic_cues = leadership_topic_cues(team, profile, doctrine, history)
        ownership_cues = topic_ownership_cues(topic_cues)
        contact_mode = relationship_contact_mode(
            self.repository,
            contact_actor,
            command.actor_id,
        )

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
            # opportunity. Snapshot refs preserve the exact generated agenda,
            # ownership boundary, and observable relationship-shaped contact
            # mode without creating a second writable check-in or personality
            # registry.
            material_consequence_refs=(
                contact_opportunity_ref,
                *snapshot_refs(
                    stable_team_name,
                    topic_cues,
                    ownership_cues=ownership_cues,
                    contact_mode=contact_mode,
                ),
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
            "contact_basis": contact_basis,
            "contact_mode": contact_mode,
            "topic_cues": topic_cues,
            "ownership_cues": ownership_cues,
            "compacted_reviews": compacted,
        }


__all__ = ["LivingWorldTeamVitalityMixin", "_leadership_topic_cues"]
