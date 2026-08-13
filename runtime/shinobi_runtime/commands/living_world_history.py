from __future__ import annotations
from shinobi_runtime.commands.living_world_support import *
from shinobi_runtime.commands.team_composition import player_controlled_record

class LivingWorldHistoryMixin:
    @staticmethod
    def _team_history_path(team_id: str) -> str:
        return f"{_TEAM_HISTORY_ROOT}/{_slug(team_id)}.json"

    @staticmethod
    def _faction_memory_path(faction_id: str) -> str:
        return f"{_FACTION_MEMORY_ROOT}/{_slug(faction_id)}.json"

    def _load_write(self, record_writes: Dict[str, Dict[str, Any]], path: str) -> Dict[str, Any]:
        existing = record_writes.get(path)
        if isinstance(existing, dict):
            return existing
        try:
            loaded = self.repository.read_json(path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("living_world_owner_invalid") from exc
        if not isinstance(loaded, dict):
            raise CommandRejectedError("living_world_owner_invalid")
        record_writes[path] = copy.deepcopy(loaded)
        return record_writes[path]

    def _faction_memory(self, faction_id: str, *, at: CampaignTime, record_writes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        path = self._faction_memory_path(faction_id)
        if path in record_writes:
            memory = record_writes[path]
        else:
            raw = self.repository.read_optional_bytes(path)
            if raw is None:
                memory = {"schema":"faction-operational-memory","faction_id":faction_id,"as_of":str(at),"active_mission_team_refs":{},"team_performance":{},"recent_report_refs":[]}
                record_writes[path] = memory
            else:
                try:
                    loaded = self.repository.read_json(path)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("faction_operational_memory_invalid") from exc
                if not isinstance(loaded, dict) or loaded.get("faction_id") != faction_id:
                    raise CommandRejectedError("faction_operational_memory_invalid")
                memory = copy.deepcopy(loaded)
                record_writes[path] = memory
        if not isinstance(memory.get("active_mission_team_refs"), dict) or not isinstance(memory.get("team_performance"), dict) or not isinstance(memory.get("recent_report_refs"), list):
            raise CommandRejectedError("faction_operational_memory_invalid")
        memory["as_of"] = str(at)
        return memory

    def _team_history(self, team_id: str, *, at: CampaignTime, record_writes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        path = self._team_history_path(team_id)
        if path in record_writes:
            history = record_writes[path]
        else:
            raw = self.repository.read_optional_bytes(path)
            if raw is None:
                history = {"schema":"team-operational-history","team_id":team_id,"as_of":str(at),"missions_total":0,"missions_succeeded":0,"missions_failed":0,"training_sessions":0,"casualty_events":0,"replacement_events":0,"former_member_refs":[],"notable_event_refs":[],"last_mission_ref":None,"last_result_at":None}
                record_writes[path] = history
            else:
                try:
                    loaded = self.repository.read_json(path)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("team_operational_history_invalid") from exc
                if not isinstance(loaded, dict) or loaded.get("team_id") != team_id:
                    raise CommandRejectedError("team_operational_history_invalid")
                history = copy.deepcopy(loaded)
                record_writes[path] = history
        history["as_of"] = str(at)
        return history

    def _register_exact_team_state(self, *args: Any, **kwargs: Any):
        path, team = super()._register_exact_team_state(*args, **kwargs)
        at = kwargs.get("at")
        record_writes = kwargs.get("record_writes")
        if isinstance(at, CampaignTime) and isinstance(record_writes, dict):
            self._team_history(str(team.get("id")), at=at, record_writes=record_writes)
        return path, team

    def _living_team_view(self, team_ref: str, *, record_writes: Dict[str, Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
        try:
            path, _digest, view = self._resolve_covered_owner_view(team_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError as exc:
            raise CommandRejectedError("autonomous_team_invalid") from exc
        if path in record_writes:
            team = record_writes[path]
        else:
            if not isinstance(view, Mapping):
                raise CommandRejectedError("autonomous_team_invalid")
            team = copy.deepcopy(dict(view))
        if team.get("schema") != "exact-team" or team.get("id") != team_ref:
            raise CommandRejectedError("autonomous_team_invalid")
        return path, team

    def _living_member_profile(self, person_ref: str, *, record_writes: Mapping[str, Mapping[str, Any]]):
        try:
            path, _digest, view = self._resolve_covered_owner_view(person_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError:
            return None
        record = record_writes.get(path, view)
        if not isinstance(record, Mapping) or record.get("schema") not in ("shinobi_character", "person"):
            return None
        profile = capability_profile_from_record(person_ref, record)
        if player_controlled_record(record):
            return type(profile)(person_ref, False, "player_agency_protected", profile.scores)
        return profile
