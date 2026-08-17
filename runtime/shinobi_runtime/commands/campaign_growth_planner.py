"""Production team-training policy extensions."""
from __future__ import annotations
from typing import Any, Mapping, Sequence, Tuple
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner as _Base
from shinobi_runtime.commands.team_composition import player_controlled_record

_POLICY_PATH = "game/rules/training/autonomy-participation.json"


def _number_at(record: Mapping[str, Any], path: str) -> float | None:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _unique(values: Sequence[str]) -> Tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return tuple(out)


class CampaignCommandPlanner(_Base):
    """Production planner with registered intensive diagnostic team training."""

    def _growth_training_policy(self, team: Mapping[str, Any]) -> Mapping[str, Any] | None:
        team_id = team.get("id")
        if not isinstance(team_id, str):
            return None
        try:
            registry = self.repository.read_json(_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("team_training_participation_policy_invalid") from exc
        policies = registry.get("policies") if isinstance(registry, Mapping) else None
        policy = policies.get(team_id) if isinstance(policies, Mapping) else None
        if policy is None or not isinstance(policy, Mapping) or policy.get("enabled") is not True:
            return None
        return policy

    def _autonomous_team_training_profile(self, team: Mapping[str, Any]) -> Mapping[str, Any]:
        policy = self._growth_training_policy(team)
        if policy is not None and "active_hours_per_week" in policy:
            hours = policy.get("active_hours_per_week")
            cycle = policy.get("team_target_cycle")
            if isinstance(hours, bool) or not isinstance(hours, int) or not 0 < hours <= 48:
                raise CommandRejectedError("team_training_participation_policy_invalid")
            if not isinstance(cycle, list) or not cycle or any(not isinstance(x, str) or not x for x in cycle):
                raise CommandRejectedError("team_training_participation_policy_invalid")
            return {"active_hours_per_week": hours, "target_cycle": list(cycle)}
        return super()._autonomous_team_training_profile(team)

    def _training_candidates(
        self,
        *,
        team: Mapping[str, Any],
        person_ref: str,
        person: Mapping[str, Any],
        policy_cycle: Sequence[str],
    ) -> Tuple[str, ...]:
        base = super()._training_candidates(
            team=team, person_ref=person_ref, person=person, policy_cycle=policy_cycle
        )
        policy = self._growth_training_policy(team)
        if policy is None or policy.get("target_strategy") != "weakness_strength_balanced" or player_controlled_record(person):
            return base
        paths = policy.get("assessment_paths")
        if not isinstance(paths, list) or not paths or any(not isinstance(x, str) or not x for x in paths):
            raise CommandRejectedError("team_training_participation_policy_invalid")
        scored: list[tuple[float, str]] = []
        for target in paths:
            try:
                self._training_target(dict(person), target)
            except CommandRejectedError:
                continue
            value = _number_at(person, target)
            if value is not None:
                scored.append((value, target))
        if not scored:
            return base
        weakest = min(scored, key=lambda row: (row[0], row[1]))[1]
        strongest = max(scored, key=lambda row: (row[0], row[1]))[1]
        preferred = base[0] if base else weakest
        return _unique((weakest, preferred, strongest, *base))


__all__ = ["CampaignCommandPlanner"]
