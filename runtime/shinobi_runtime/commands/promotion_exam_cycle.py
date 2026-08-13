"""Evidence-gated exact promotion and persistent Konoha Chunin exam cycles.

The exam cadence is campaign institutional procedure, not a forced future-canon
outcome.  Opening a cycle never chooses a named shinobi's result.  Exact rank
changes still use career_status_resolution, now guarded by persisted eligibility,
one-rank progression, and conserved aggregate rank accounting.
"""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes
from shinobi_runtime.commands.domains.social import SocialCommandsMixin
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.sim.events import CampaignTime

_CAREER_PATH = "state/reg/shinobi-career-pipeline.json"
_EXAM_RULES_PATH = "game/rules/career/promotion-exams.json"
_TEAM_REGISTRY_PATH = "state/team/registry.json"
_RANKS = ("genin", "chunin", "jonin")
_MAX_HISTORY = 512
_MAX_TEAM_SCAN = 256
_INSTALLED = False
_PROJECTION_INSTALLED = False


def _rank_key(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip().lower().replace("ū", "u").replace("ō", "o")
    return value if value in _RANKS else None


def _validate_exact_promotion_transition(
    record: Mapping[str, Any], *, target_rank_or_status: object
) -> tuple[str, str]:
    source = _rank_key(record.get("official_rank_or_status"))
    target = _rank_key(target_rank_or_status)
    if source is None or target is None:
        raise CommandRejectedError("career_rank_transition_unknown")
    if source == "jonin" or _RANKS.index(target) != _RANKS.index(source) + 1:
        raise CommandRejectedError("career_rank_skip_forbidden")
    career = record.get("career_state")
    if not isinstance(career, Mapping) or career.get("promotion_eligible") is not True:
        raise CommandRejectedError("career_promotion_evidence_required")
    return source, target


def _json_write(writes: Mapping[str, bytes], path: str) -> Optional[Dict[str, Any]]:
    raw = writes.get(path)
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise CommandRejectedError("career_after_image_invalid") from exc
    return value if isinstance(value, dict) else None


def _career_pipeline(repository: Any, writes: Mapping[str, bytes]) -> Dict[str, Any]:
    value = _json_write(writes, _CAREER_PATH)
    if value is None:
        try:
            loaded = repository.read_json(_CAREER_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
        if not isinstance(loaded, dict):
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
        value = copy.deepcopy(loaded)
    if value.get("schema") != "shinobi-career-pipeline" or value.get("version") != 1:
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    if not isinstance(value.get("villages"), dict) or not isinstance(value.get("history"), list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    return value


def _service_village(record: Mapping[str, Any], pipeline: Mapping[str, Any]) -> str:
    affiliation = record.get("village_or_affiliation")
    villages = pipeline.get("villages")
    if not isinstance(affiliation, str) or not isinstance(villages, Mapping):
        raise CommandRejectedError("career_service_village_unresolved")
    matches = [key for key in villages if isinstance(key, str) and key.lower() in affiliation.lower()]
    if len(matches) != 1:
        raise CommandRejectedError("career_service_village_unresolved")
    return matches[0]


def _shift_pipeline_rank(pipeline: Dict[str, Any], *, village: str, source: str, target: str) -> None:
    villages = pipeline["villages"]
    row = villages.get(village)
    counts = row.get("rank_counts") if isinstance(row, dict) else None
    if not isinstance(counts, dict):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    for rank in _RANKS:
        if isinstance(counts.get(rank), bool) or not isinstance(counts.get(rank), int) or counts[rank] < 0:
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
    if counts[source] <= 0:
        raise CommandRejectedError("shinobi_career_headcount_not_conserved")
    before = sum(counts[rank] for rank in _RANKS)
    counts[source] -= 1
    counts[target] += 1
    if sum(counts[rank] for rank in _RANKS) != before:
        raise CommandRejectedError("shinobi_career_headcount_not_conserved")


def _append_history(pipeline: Dict[str, Any], row: Mapping[str, Any]) -> None:
    history = pipeline["history"]
    history.append(dict(row))
    if len(history) > _MAX_HISTORY:
        del history[:-_MAX_HISTORY]


def _exam_profiles(repository: Any) -> tuple[Mapping[str, Any], ...]:
    try:
        rules = repository.read_json(_EXAM_RULES_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("promotion_exam_rules_invalid") from exc
    profiles = rules.get("profiles") if isinstance(rules, Mapping) else None
    if rules.get("schema") != "promotion-exam-rules" or rules.get("version") != 1 or not isinstance(profiles, Mapping):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    return tuple(
        profile for key, profile in sorted(profiles.items())
        if isinstance(profile, Mapping) and profile.get("id") == key and profile.get("enabled") is True
    )


def _exam_boundaries(profile: Mapping[str, Any], *, after: CampaignTime, through: CampaignTime) -> tuple[CampaignTime, ...]:
    months = profile.get("cycle_months")
    if not isinstance(months, list) or not months or any(not isinstance(m, int) or isinstance(m, bool) or not 1 <= m <= 12 for m in months):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    day = int(profile.get("cycle_day", 1)); hour = int(profile.get("cycle_hour", 9))
    result = []
    for year in range(after.year, through.year + 1):
        for month in sorted(set(months)):
            point = CampaignTime.parse(f"SE-{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00")
            if after < point <= through:
                result.append(point)
    return tuple(result)


class _BaseOverlay:
    def __init__(self, overlay: Any, base_writes: Mapping[str, bytes]) -> None:
        self._overlay = overlay; self._base = dict(base_writes)
        self.changed_paths = tuple(sorted(base_writes))
    def read_json(self, path: str) -> Any:
        raw = self._base.get(path)
        return json.loads(raw.decode("utf-8")) if raw is not None else self._overlay.read_json(path)
    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def _install_career_guard() -> None:
    original = SocialCommandsMixin._career_status_resolution
    if getattr(original, "_career_integrity_guard", False): return

    @wraps(original)
    def wrapped(self: Any, command: Any, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        action = command.payload.get("action"); subject_ref = command.payload.get("subject_ref")
        source = target = None; before = None
        if action in ("promote", "demote") and isinstance(subject_ref, str):
            _path, _digest, view = self._resolve_covered_owner_view(subject_ref, cache=_OwnerResolutionCache())
            if isinstance(view, Mapping) and view.get("schema") == "shinobi_character":
                before = view; source = _rank_key(view.get("official_rank_or_status")); target = _rank_key(command.payload.get("target_rank_or_status"))
                if action == "promote" and source and target:
                    _validate_exact_promotion_transition(view, target_rank_or_status=command.payload.get("target_rank_or_status"))
                if action == "demote" and source and target and _RANKS.index(source) - _RANKS.index(target) != 1:
                    raise CommandRejectedError("career_rank_skip_forbidden")
        base = original(self, command, meta, current_time)
        if not (source and target and before) or source == target: return base
        pipeline = _career_pipeline(self.repository, base.writes); village = _service_village(before, pipeline)
        _shift_pipeline_rank(pipeline, village=village, source=source, target=target)
        _append_history(pipeline, {"kind":"exact_rank_accounting","at":str(current_time),"subject_ref":subject_ref,"village":village,"source_rank":source,"target_rank":target,"action":action})
        writes = dict(base.writes); writes[_CAREER_PATH] = _json_bytes(pipeline); writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes)); base_writes = dict(base.writes)
        def validate(overlay: Any, manifest: Any) -> None:
            base.validator(_BaseOverlay(overlay, base_writes), manifest)
            if overlay.changed_paths != expected or overlay.read_json(_CAREER_PATH) != pipeline:
                raise ValueError("exact career accounting mismatch")
        result = dict(base.result); result["aggregate_rank_accounting"] = {"village":village,"conserved":True}
        return _BuiltPlan(base.code, expected, writes, result, validate)
    wrapped._career_integrity_guard = True  # type: ignore[attr-defined]
    SocialCommandsMixin._career_status_resolution = wrapped


def _install_exam_cycle() -> None:
    original = TimeCommandsMixin._advance_time
    if getattr(original, "_promotion_exam_cycle", False): return

    @wraps(original)
    def wrapped(self: Any, command: Any, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        base = original(self, command, meta, current_time)
        reached_raw = base.result.get("world_time")
        if not isinstance(reached_raw, str): return base
        reached = CampaignTime.parse(reached_raw)
        crossings = [(at, p) for p in _exam_profiles(self.repository) for at in _exam_boundaries(p, after=current_time, through=reached)]
        if not crossings: return base
        pipeline = _career_pipeline(self.repository, base.writes); cycles = []
        try: registry = self.repository.read_json(_TEAM_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc: raise CommandRejectedError("team_registry_invalid") from exc
        refs = registry.get("active_teams") if isinstance(registry, Mapping) else None
        if not isinstance(refs, list) or len(refs) > _MAX_TEAM_SCAN: raise CommandRejectedError("team_registry_invalid")
        for at, profile in crossings:
            cycle_id = f"promotion_exam_cycle.{profile['id']}.{at.year:04d}-{at.month:02d}-{at.day:02d}"
            if any(isinstance(row, Mapping) and row.get("cycle_id") == cycle_id for row in pipeline["history"]): continue
            eligible = []; player_team_eligible = []
            for team_ref in refs:
                if not isinstance(team_ref, str): continue
                try: _team_path, team = self._exact_team(team_ref)
                except CommandRejectedError: continue
                members = team.get("member_refs") if isinstance(team, Mapping) else None
                if team.get("status") != "active" or not isinstance(members, list): continue
                for person_ref in members:
                    if not isinstance(person_ref, str) or person_ref == command.actor_id: continue
                    try: _path, _digest, person = self._resolve_covered_owner_view(person_ref, cache=_OwnerResolutionCache())
                    except CommandRejectedError: continue
                    career = person.get("career_state") if isinstance(person, Mapping) else None
                    if _rank_key(person.get("official_rank_or_status")) == "genin" and isinstance(career, Mapping) and career.get("promotion_eligible") is True:
                        if person_ref not in eligible: eligible.append(person_ref)
                        if team.get("leader_ref") == command.actor_id and person_ref not in player_team_eligible: player_team_eligible.append(person_ref)
            _append_history(pipeline, {"kind":"promotion_exam_cycle_opened","at":str(at),"cycle_id":cycle_id,"profile_ref":profile.get("id"),"eligible_exact_refs":eligible,"player_team_eligible_refs":player_team_eligible,"canon_status":"campaign_institutional_not_future_canon"})
            cycles.append({"kind":"promotion_exam_cycle_opened","cycle_id":cycle_id,"at":str(at),"eligible_count":len(eligible),"player_team_eligible_refs":player_team_eligible})
        if not cycles: return base
        writes = dict(base.writes); writes[_CAREER_PATH] = _json_bytes(pipeline); writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes)); base_writes = dict(base.writes)
        def validate(overlay: Any, manifest: Any) -> None:
            base.validator(_BaseOverlay(overlay, base_writes), manifest)
            if overlay.changed_paths != expected or overlay.read_json(_CAREER_PATH) != pipeline:
                raise ValueError("promotion exam cycle after-image mismatch")
        result = dict(base.result); result["promotion_exam_cycles"] = cycles
        return _BuiltPlan(base.code, expected, writes, result, validate)
    wrapped._promotion_exam_cycle = True  # type: ignore[attr-defined]
    TimeCommandsMixin._advance_time = wrapped


def install_promotion_exam_cycle() -> None:
    global _INSTALLED
    if _INSTALLED: return
    _install_career_guard(); _install_exam_cycle(); _INSTALLED = True


def install_promotion_exam_projection() -> None:
    global _PROJECTION_INSTALLED
    if _PROJECTION_INSTALLED: return
    from shinobi_runtime.commands import campaign_runtime_planner as module
    original = module._fresh_player_facing_time_handoff
    @wraps(original)
    def wrapped(result: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        pressures, reports, approaching = original(result)
        for cycle in result.get("promotion_exam_cycles", []) if isinstance(result.get("promotion_exam_cycles"), list) else []:
            if isinstance(cycle, Mapping) and cycle.get("player_team_eligible_refs"):
                text = "Konoha has opened a Chunin Examination cycle with eligible members of your team."
                if text not in pressures: pressures.append(text)
                report = "The Hokage Administration has Chunin Examination registration and evaluation business for your team."
                if report not in reports: reports.append(report)
        return pressures[:12], reports[:6], approaching[:8]
    wrapped._promotion_exam_projection = True  # type: ignore[attr-defined]
    module._fresh_player_facing_time_handoff = wrapped; _PROJECTION_INSTALLED = True


__all__ = ["install_promotion_exam_cycle","install_promotion_exam_projection","_exam_boundaries","_shift_pipeline_rank","_validate_exact_promotion_transition"]
