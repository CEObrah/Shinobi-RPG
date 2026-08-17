from __future__ import annotations
from shinobi_runtime.commands.living_world_support import *

class LivingWorldAssignmentMixin:
    def _active_autonomous_team(self, faction_record: Mapping[str, Any], record_writes: Mapping[str, Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
        if getattr(self, "_living_world_team_already_routed", False):
            return None
        return super()._active_autonomous_team(faction_record, record_writes)

    def _mission_objective_kind(self, payload: Mapping[str, Any], faction_id: str, at: CampaignTime) -> str:
        cycle = [value for value in payload.get("mission_objective_cycle", []) if isinstance(value, str) and value]
        if not cycle:
            return "investigate"
        return cycle[_stable_roll(faction_id, at, "objective", modulo=len(cycle))]

    def _team_reputation_reliability(self, team_ref: str, faction_id: str) -> int:
        try:
            profile = self._reputation_profile_for(team_ref, faction_id)
        except Exception:
            profile = None
        if not isinstance(profile, Mapping):
            return 50
        dimensions = profile.get("dimensions")
        axis = dimensions.get("mission_reliability") if isinstance(dimensions, Mapping) else None
        score = axis.get("score") if isinstance(axis, Mapping) else None
        return score if isinstance(score, int) and not isinstance(score, bool) else 50

    def _team_assignment_score(self, team: Mapping[str, Any], *, objective_kind: str, faction_id: str, memory: Mapping[str, Any], record_writes: Mapping[str, Mapping[str, Any]]) -> int:
        members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
        if not members:
            return -10_000
        base = self._autonomous_mission_resolution_score(members, objective_kind)
        profiles = {ref: self._living_member_profile(ref, record_writes=record_writes) for ref in members}
        dimensions = _OBJECTIVE_DIMENSIONS.get(objective_kind, ("leadership", "control", "support"))
        strength = sum(max((profile.scores.get(dim, 0) for profile in profiles.values() if profile is not None), default=0) for dim in dimensions) // max(1, len(dimensions))
        familiarity_bonus = 0
        doctrine_ref = team.get("doctrine_ref")
        if isinstance(doctrine_ref, str):
            try:
                _path, _digest, doctrine = self._resolve_covered_owner_view(doctrine_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                doctrine = None
            familiarity = doctrine.get("familiarity") if isinstance(doctrine, Mapping) else None
            values = [value for ref, value in familiarity.items() if ref in members and isinstance(value, int)] if isinstance(familiarity, Mapping) else []
            if values:
                familiarity_bonus = min(10, sum(values) // len(values) // 10)
        performance = memory.get("team_performance") if isinstance(memory, Mapping) else None
        perf = performance.get(str(team.get("id"))) if isinstance(performance, Mapping) else None
        perf_bonus = 0
        if isinstance(perf, Mapping):
            missions = perf.get("missions_total", 0)
            successes = perf.get("missions_succeeded", 0)
            if isinstance(missions, int) and missions > 0 and isinstance(successes, int):
                perf_bonus = max(-8, min(8, ((successes * 100 // missions) - 50) // 6))
        reputation_bonus = (self._team_reputation_reliability(str(team.get("id")), faction_id) - 50) // 5
        supply_bonus = self._team_supply_readiness(members, record_writes=record_writes) if hasattr(self, "_team_supply_readiness") else 0
        return base * 4 + strength + familiarity_bonus + perf_bonus + reputation_bonus + supply_bonus

    def _eligible_mission_team(self, team: Mapping[str, Any], *, player_id: str, busy_team_refs: set[str], record_writes: Mapping[str, Mapping[str, Any]]) -> bool:
        team_id = team.get("id")
        if team.get("status") != "active" or not isinstance(team_id, str) or team_id in busy_team_refs:
            return False
        lifecycle = team.get("lifecycle")
        if isinstance(lifecycle, Mapping) and lifecycle.get("purpose_status") not in (None, "active"):
            return False
        members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
        if player_id in members or not members:
            return False
        return all((profile := self._living_member_profile(ref, record_writes=record_writes)) is not None and profile.available for ref in members)

    def _select_mission_team(self, *, faction_id: str, payload: Mapping[str, Any], objective_kind: str, at: CampaignTime, command: CommandEnvelope, record_writes: Dict[str, Dict[str, Any]]) -> Optional[Tuple[str, Dict[str, Any]]]:
        memory = self._faction_memory(faction_id, at=at, record_writes=record_writes)
        mapping = memory.get("active_mission_team_refs")
        busy = set(value for value in mapping.values() if isinstance(value, str)) if isinstance(mapping, Mapping) else set()
        refs: list[str] = []
        for key in ("formed_autonomous_team_refs", "team_refs"):
            for value in payload.get(key, []):
                if isinstance(value, str) and value not in refs:
                    refs.append(value)
        ranked = []
        for team_ref in refs:
            try:
                _path, team = self._living_team_view(team_ref, record_writes=record_writes)
            except CommandRejectedError:
                continue
            if not self._eligible_mission_team(team, player_id=command.actor_id, busy_team_refs=busy, record_writes=record_writes):
                continue
            ranked.append((self._team_assignment_score(team, objective_kind=objective_kind, faction_id=faction_id, memory=memory, record_writes=record_writes), team_ref, team))
        if not ranked:
            return None
        _score, team_ref, team = max(ranked, key=lambda row: (row[0], row[1]))
        return team_ref, team

    def _mission_capacity(self, payload: Mapping[str, Any]) -> Optional[int]:
        """Compatibility hook: autonomous mission demand has no fictional fixed ceiling."""
        return None

