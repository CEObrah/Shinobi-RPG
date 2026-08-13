from __future__ import annotations
from shinobi_runtime.commands.living_world_support import *

_LIVING_WORLD_POLICY_PATH = "game/rules/autonomy/living-world.json"


class LivingWorldPolicyMixin:
    def _autonomy_policy_book(self) -> AutonomousPolicyBook:
        book = super()._autonomy_policy_book()
        try:
            overlay = self.repository.read_json(_LIVING_WORLD_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("living_world_policy_invalid") from exc
        if not isinstance(overlay, Mapping) or overlay.get("schema") != "living-world-autonomy-policy":
            raise CommandRejectedError("living_world_policy_invalid")
        assignment_overrides = overlay.get("faction_assignments")
        team_overrides = overlay.get("team_profiles")
        if not isinstance(assignment_overrides, Mapping) or not isinstance(team_overrides, Mapping):
            raise CommandRejectedError("living_world_policy_invalid")
        assignments: Dict[str, Mapping[str, Any]] = {key: dict(value) for key, value in book.faction_assignments.items()}
        for faction_id, patch in assignment_overrides.items():
            if not isinstance(faction_id, str) or not isinstance(patch, Mapping):
                raise CommandRejectedError("living_world_policy_invalid")
            merged = dict(assignments.get(faction_id, {}))
            merged.update(dict(patch))
            assignments[faction_id] = merged
        team_profiles: Dict[str, Mapping[str, Any]] = {key: dict(value) for key, value in book.team_profiles.items()}
        for team_type, patch in team_overrides.items():
            if not isinstance(team_type, str) or not isinstance(patch, Mapping):
                raise CommandRejectedError("living_world_policy_invalid")
            merged = dict(team_profiles.get(team_type, {}))
            merged.update(dict(patch))
            team_profiles[team_type] = merged
        return AutonomousPolicyBook(
            profiles=book.profiles,
            faction_assignments=assignments,
            team_profiles=team_profiles,
            institution_assignments=book.institution_assignments,
        )
