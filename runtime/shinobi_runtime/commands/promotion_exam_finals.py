"""Persistent Chunin Examination finals bracket and autonomous duel settlement.

Finals are not another capability-score gate.  They are public, staged,
nonlethal tournament bouts resolved through the shared combat kernel.  The
player may cause the institution to settle an observable bout, but never chooses
an NPC's tactics, technique, opponent, winner, injury, or promotion outcome.
The career pipeline owns the durable bracket/result evidence.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.combat.models import (
    CapabilityProfile,
    CombatContract,
    CombatIntent,
    CombatObjective,
    CombatTiming,
    Engagement,
    InformationState,
    Participant,
    PersonnelState,
    PositionState,
)
from shinobi_runtime.combat.resolver import required_draw_count, resolve_combat
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import FORMATION_RESOLUTION_MECHANICS_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.rng import CounterRNG
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

from shinobi_runtime.commands.promotion_exam_evaluation import promotion_exam_evaluation_rows
from shinobi_runtime.commands.promotion_exam_scheduler import (
    _CANON_STATUS,
    _CAREER,
    _CURSOR,
    _load_pipeline,
    _profile_for_cycle,
    active_promotion_exam_cycles,
    promotion_exam_profiles,
    registered_candidate_refs,
)

_INSTALLED = False


def promotion_exam_bout_rows(
    pipeline: Mapping[str, Any], cycle_id: str
) -> tuple[Mapping[str, Any], ...]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    rows: list[Mapping[str, Any]] = []
    for row in history:
        if not (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_bout"
            and row.get("cycle_id") == cycle_id
        ):
            continue
        bout_ref = row.get("bout_ref")
        candidates = row.get("candidate_refs")
        winner = row.get("winner_ref")
        loser = row.get("loser_ref")
        round_index = row.get("round_index")
        match_index = row.get("match_index")
        if (
            not isinstance(bout_ref, str)
            or not bout_ref
            or not isinstance(candidates, list)
            or len(candidates) != 2
            or any(not isinstance(ref, str) or not ref for ref in candidates)
            or winner not in candidates
            or loser not in candidates
            or winner == loser
            or isinstance(round_index, bool)
            or not isinstance(round_index, int)
            or round_index < 1
            or isinstance(match_index, bool)
            or not isinstance(match_index, int)
            or match_index < 0
        ):
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
        rows.append(row)
    return tuple(rows)


def promotion_exam_finals_candidate_refs(
    pipeline: Mapping[str, Any], cycle_id: str
) -> tuple[str, ...]:
    registered = set(registered_candidate_refs(pipeline, cycle_id))
    field_rows = promotion_exam_evaluation_rows(
        pipeline, cycle_id, phase="field_evaluation"
    )
    if field_rows:
        registered.intersection_update(
            row["candidate_ref"]
            for row in field_rows
            if row.get("outcome") == "pass"
        )
    return tuple(sorted(registered))


def _seeded_candidates(cycle_id: str, candidates: tuple[str, ...]) -> list[str]:
    return sorted(
        candidates,
        key=lambda ref: hashlib.sha256(f"{cycle_id}|{ref}".encode("utf-8")).hexdigest(),
    )


def _bout_ref(cycle_id: str, round_index: int, match_index: int) -> str:
    digest = hashlib.sha256(cycle_id.encode("utf-8")).hexdigest()[:12]
    return f"promotion_exam_bout.{digest}.r{round_index}.m{match_index}"


def promotion_exam_finals_state(
    pipeline: Mapping[str, Any],
    profile: Mapping[str, Any],
    cycle_id: str,
) -> Mapping[str, Any]:
    config = profile.get("finals_format")
    if not isinstance(config, Mapping) or config.get("model") != "single_elimination":
        raise CommandRejectedError("promotion_exam_finals_rules_invalid")
    entrants = _seeded_candidates(
        cycle_id, promotion_exam_finals_candidate_refs(pipeline, cycle_id)
    )
    settled = {row["bout_ref"]: row for row in promotion_exam_bout_rows(pipeline, cycle_id)}
    if len(entrants) <= 1:
        return {
            "candidate_refs": entrants,
            "open_bouts": [],
            "settled_bouts": list(settled.values()),
            "complete": True,
            "champion_ref": entrants[0] if entrants else None,
        }

    contenders = entrants
    round_index = 1
    while len(contenders) > 1:
        next_round: list[str] = []
        open_bouts: list[dict[str, Any]] = []
        for match_index, start in enumerate(range(0, len(contenders), 2)):
            pair = contenders[start : start + 2]
            if len(pair) == 1:
                next_round.append(pair[0])
                continue
            bout_ref = _bout_ref(cycle_id, round_index, match_index)
            row = settled.get(bout_ref)
            if row is None:
                open_bouts.append(
                    {
                        "bout_ref": bout_ref,
                        "round_index": round_index,
                        "match_index": match_index,
                        "candidate_refs": list(pair),
                    }
                )
            else:
                if list(row.get("candidate_refs", ())) != list(pair):
                    raise CommandRejectedError("promotion_exam_bout_bracket_conflict")
                next_round.append(str(row["winner_ref"]))
        if open_bouts:
            return {
                "candidate_refs": entrants,
                "open_bouts": open_bouts,
                "settled_bouts": list(settled.values()),
                "complete": False,
                "champion_ref": None,
            }
        contenders = next_round
        round_index += 1
    return {
        "candidate_refs": entrants,
        "open_bouts": [],
        "settled_bouts": list(settled.values()),
        "complete": True,
        "champion_ref": contenders[0] if contenders else None,
    }


def promotion_exam_finals_complete(
    pipeline: Mapping[str, Any], profile: Mapping[str, Any], cycle_id: str
) -> bool:
    return bool(promotion_exam_finals_state(pipeline, profile, cycle_id)["complete"])


def _metric(container: Mapping[str, Any], root: str, key: str) -> int:
    row = container.get(root)
    value = row.get(key) if isinstance(row, Mapping) else 0
    return max(0, min(200, value if isinstance(value, int) and not isinstance(value, bool) else 0))


def _peak(container: Mapping[str, Any], root: str) -> tuple[str | None, int]:
    row = container.get(root)
    if not isinstance(row, Mapping):
        return None, 0
    choices = [
        (str(key), int(value))
        for key, value in row.items()
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
    ]
    if not choices:
        return None, 0
    key, value = max(choices, key=lambda item: (item[1], item[0]))
    return key, max(0, min(200, value))


def _featured_method(person: Mapping[str, Any]) -> str | None:
    repertoire = person.get("repertoire")
    mastery = repertoire.get("method_mastery") if isinstance(repertoire, Mapping) else None
    if not isinstance(mastery, Mapping):
        return None
    ignored = {
        "basic_chakra_molding",
        "transformation",
        "clone_illusion",
        "tree_surface_adherence",
        "water_surface_adherence",
        "standard_thrown_tools",
    }
    rows = [
        (str(ref), int(value))
        for ref, value in mastery.items()
        if isinstance(ref, str)
        and ref not in ignored
        and isinstance(value, int)
        and not isinstance(value, bool)
    ]
    return max(rows, key=lambda item: (item[1], item[0]))[0] if rows else None


def _exam_capability(self: Any, person: Mapping[str, Any]) -> tuple[CapabilityProfile, Mapping[str, Any], int]:
    base = self._combat_capability(person)
    martial_ref, martial_peak = _peak(person, "martial_skills")
    domain_ref, domain_peak = _peak(person, "domain_proficiencies")
    coordination = _metric(person, "attributes", "coordination")
    agility = _metric(person, "attributes", "agility")
    awareness = _metric(person, "attributes", "awareness")
    composure = _metric(person, "attributes", "composure")
    control = _metric(person, "chakra_dimensions", "control")
    output = _metric(person, "chakra_dimensions", "output")
    tactics = _metric(person, "operational_skills", "tactics")
    teamwork = _metric(person, "operational_skills", "team_coordination")
    values = base.to_record()
    values["offense"] = max(values["offense"], (martial_peak * 3 + domain_peak * 2 + output + coordination) // 7)
    values["defense"] = max(values["defense"], (agility * 2 + awareness + composure + control) // 5)
    values["control"] = max(values["control"], (control * 2 + tactics * 2 + teamwork + domain_peak) // 6)
    values["mobility"] = max(values["mobility"], agility)
    values["perception"] = max(values["perception"], awareness)
    values["capture"] = max(values["capture"], (martial_peak + control + tactics) // 3)
    judge_score = (composure * 2 + awareness * 2 + tactics * 2 + teamwork + control + coordination) // 9
    style = {
        "martial_focus": martial_ref,
        "domain_focus": domain_ref,
        "featured_method_ref": _featured_method(person),
    }
    return CapabilityProfile(**values), style, judge_score


def _plan_promotion_exam_bout_resolution(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("cycle_id", "team_ref", "bout_ref"), command.command_type)
    cycle_id = _stable_id(command.payload.get("cycle_id"), "promotion_exam_cycle_ref_invalid", prefix="promotion_exam_cycle.")
    team_ref = _stable_id(command.payload.get("team_ref"), "promotion_exam_team_ref_invalid", prefix="team.")
    bout_ref = _stable_id(command.payload.get("bout_ref"), "promotion_exam_bout_ref_invalid", prefix="promotion_exam_bout.")

    pipeline = _load_pipeline(self.repository)
    profiles = promotion_exam_profiles(self.repository)
    cycle = next((row for row in active_promotion_exam_cycles(pipeline, profiles) if row.get("cycle_id") == cycle_id), None)
    if not isinstance(cycle, Mapping) or cycle.get("phase") != "finals":
        raise CommandRejectedError("promotion_exam_finals_not_active")
    profile = _profile_for_cycle(profiles, cycle)
    config = profile.get("finals_format")
    if not isinstance(config, Mapping) or config.get("model") != "single_elimination":
        raise CommandRejectedError("promotion_exam_finals_rules_invalid")
    venue_ref = config.get("venue_ref")
    if not isinstance(venue_ref, str) or not venue_ref:
        raise CommandRejectedError("promotion_exam_finals_rules_invalid")

    try:
        _team_path, team = self._exact_team(team_ref)
    except CommandRejectedError as exc:
        raise CommandRejectedError("promotion_exam_team_invalid") from exc
    registrations = [
        row for row in pipeline.get("history", [])
        if isinstance(row, Mapping)
        and row.get("kind") == "promotion_exam_registration"
        and row.get("cycle_id") == cycle_id
        and row.get("team_ref") == team_ref
    ]
    if (
        team.get("status") != "active"
        or team.get("leader_ref") != command.actor_id
        or team.get("assignment_authority_ref") != profile.get("institution_ref")
        or not registrations
    ):
        raise CommandRejectedError("promotion_exam_bout_observer_authority_required")

    state = promotion_exam_finals_state(pipeline, profile, cycle_id)
    bout = next((row for row in state["open_bouts"] if row.get("bout_ref") == bout_ref), None)
    if not isinstance(bout, Mapping):
        raise CommandRejectedError("promotion_exam_bout_not_open")
    candidate_refs = tuple(bout["candidate_refs"])

    scene = copy.deepcopy(self._scene_base(current_time))
    if scene.get("location_id") != venue_ref:
        raise CommandRejectedError("promotion_exam_bout_wrong_venue")

    cache = _OwnerResolutionCache()
    exact: dict[str, tuple[str, Mapping[str, Any]]] = {}
    styles: dict[str, Mapping[str, Any]] = {}
    judge_scores: dict[str, int] = {}
    participants = []
    objectives = []
    engagements = []
    side_by_candidate: dict[str, str] = {}
    candidate_by_side: dict[str, str] = {}
    for sequence, candidate_ref in enumerate(candidate_refs):
        try:
            path, _digest, person = self._resolve_covered_owner_view(candidate_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("promotion_exam_candidate_unresolved") from exc
        if (
            not isinstance(person, Mapping)
            or person.get("life_status") != "alive"
            or person.get("current_location_id") != venue_ref
        ):
            raise CommandRejectedError("promotion_exam_candidate_unavailable")
        condition = person.get("condition")
        if isinstance(condition, Mapping) and condition.get("readiness") not in (None, "ready"):
            raise CommandRejectedError("promotion_exam_candidate_unavailable")
        exact[candidate_ref] = (path, person)
        side_ref = f"side:promotion_exam:{sequence}"
        side_by_candidate[candidate_ref] = side_ref
        candidate_by_side[side_ref] = candidate_ref
        opponent = candidate_refs[1 - sequence]
        objective_ref = f"objective:promotion_exam:{bout_ref}:{sequence}"
        capability, style, judge_score = _exam_capability(self, person)
        styles[candidate_ref] = style
        judge_scores[candidate_ref] = judge_score
        participants.append(
            Participant(
                participant_ref=candidate_ref,
                authoritative_owner_ref=path,
                side_ref=side_ref,
                sequence=sequence,
                representation="exact",
                capability=capability,
                personnel=PersonnelState(total=1, active=1),
                position=PositionState(zone_ref=venue_ref),
                information=InformationState(observed_refs=(opponent,)),
                intent=CombatIntent(
                    action="capture",
                    objective_ref=objective_ref,
                    target_refs=(opponent,),
                    commitment_milli=1000,
                    lethal_force_milli=0,
                    resource_costs=(),
                ),
                initiative=max(1, min(200, (_metric(person, "attributes", "agility") + _metric(person, "attributes", "awareness") + _metric(person, "operational_skills", "tactics")) // 3)),
                readiness=100,
                morale=100,
                cohesion=100,
                resources=(),
                effective_range_bands=(0, 1, 2, 3),
                named_actor_refs=(candidate_ref,),
                unusual_technique_refs=tuple(ref for ref in (style.get("featured_method_ref"),) if isinstance(ref, str)),
                unusual_equipment_refs=(),
                detailed_injury_refs=tuple(str(value) for value in (condition.get("injuries", []) if isinstance(condition, Mapping) else []) if isinstance(value, str)),
            )
        )
        objectives.append(
            CombatObjective(
                objective_ref=objective_ref,
                side_ref=side_ref,
                kind="capture",
                required_progress=1,
                current_progress=0,
                target_refs=(opponent,),
                primary=True,
                deadline_tick=None,
                zone_ref=None,
            )
        )
        engagements.append(
            Engagement(
                engagement_ref=f"engagement:{command.digest[:12]}:{sequence}",
                actor_ref=candidate_ref,
                target_ref=opponent,
                range_band=int(config.get("range_band", 1)),
                line_of_sight=True,
                frontage_milli=1000,
                timing_delay_ms=0,
            )
        )

    max_ticks = config.get("max_exchange_ticks", 5)
    if isinstance(max_ticks, bool) or not isinstance(max_ticks, int) or not 2 <= max_ticks <= 20:
        raise CommandRejectedError("promotion_exam_finals_rules_invalid")
    mechanics = self.repository.read_json(FORMATION_RESOLUTION_MECHANICS_PATH)
    contract = CombatContract(
        combat_ref=f"combat.exam.{bout_ref}",
        transaction_ref="tx.gameplay." + command.digest,
        scale="duel",
        participants=tuple(participants),
        objectives=tuple(objectives),
        engagements=tuple(engagements),
        terrain=self._terrain_state_for_location(
            location_ref=venue_ref,
            side_refs=tuple(sorted(candidate_by_side)),
            mechanics=mechanics,
        ),
        timing=CombatTiming(current_tick=0, exchange_seconds=6, max_ticks=max_ticks),
        rng_stream=f"promotion_exam:{bout_ref}",
    )
    world_seed = meta.get("world_seed")
    if not isinstance(world_seed, str) or not world_seed:
        raise CommandRejectedError("campaign_rng_seed_invalid")
    rng = CounterRNG(world_seed=world_seed, transaction_id=contract.transaction_ref, stream=contract.rng_stream)
    for _ in range(required_draw_count(contract)):
        rng.draw_u64()
    effect_plan = resolve_combat(contract, rng.receipts)

    victorious = [ref for ref in effect_plan.victorious_side_refs if ref in candidate_by_side]
    if len(victorious) == 1:
        winner_ref = candidate_by_side[victorious[0]]
        resolution_method = "combat_stoppage"
    else:
        winner_ref = max(candidate_refs, key=lambda ref: (judge_scores[ref], ref))
        resolution_method = "judges_decision"
    loser_ref = next(ref for ref in candidate_refs if ref != winner_ref)

    row = {
        "kind": "promotion_exam_bout",
        "at": str(current_time),
        "cycle_id": cycle_id,
        "profile_ref": profile["id"],
        "phase": "finals",
        "bout_ref": bout_ref,
        "round_index": int(bout["round_index"]),
        "match_index": int(bout["match_index"]),
        "candidate_refs": list(candidate_refs),
        "winner_ref": winner_ref,
        "loser_ref": loser_ref,
        "resolution_method": resolution_method,
        "resolution_mode": effect_plan.resolution_mode,
        "victorious_side_refs": list(effect_plan.victorious_side_refs),
        "judge_scores": judge_scores,
        "styles": styles,
        "combat_record": effect_plan.to_record(),
        "duration_seconds": contract.timing.exchange_seconds * contract.timing.max_ticks,
        "examiner_ref": profile["authority_ref"],
        "canon_status": _CANON_STATUS,
    }
    pipeline["history"].append(row)
    if len(pipeline["history"]) > _CURSOR:
        del pipeline["history"][:-_CURSOR]

    scene["scene_summary"] = f"Chunin Examination finals bout {bout_ref} resolves at {venue_ref}."
    scene["decision_required"] = None
    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="promotion_exam_finals_bout_resolved",
        at=current_time,
        host_refs=(str(profile["institution_ref"]), bout_ref),
        actor_refs=candidate_refs,
        place_refs=(venue_ref,),
        causal_refs=(cycle_id,),
        affected_owner_refs=(_CAREER,),
        material_consequence_refs=(f"promotion_exam_bout:{bout_ref}:{winner_ref}",),
        classification="public",
        audience_refs=tuple(dict.fromkeys((command.actor_id, *candidate_refs))),
        source_refs=(str(profile["institution_ref"]), str(profile["authority_ref"])),
        reducer_ref="shinobi_runtime.commands.promotion_exam_finals.promotion_exam_bout_resolution",
    )
    writes = {
        self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
        self.scene_path: _json_bytes(scene),
        _CAREER: _json_bytes(pipeline),
        **self._world_event_writes(world_events),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_pipeline = copy.deepcopy(pipeline)

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("promotion exam bout write set changed after planning")
        self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
        if overlay.read_json(_CAREER) != expected_pipeline:
            raise ValueError("promotion exam bout after-image differs from plan")

    refreshed = promotion_exam_finals_state(pipeline, profile, cycle_id)
    return _BuiltPlan(
        code="promotion_exam_bout_resolution_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "cycle_id": cycle_id,
            "profile_ref": profile["id"],
            "bout_ref": bout_ref,
            "round_index": row["round_index"],
            "match_index": row["match_index"],
            "candidate_refs": list(candidate_refs),
            "winner_ref": winner_ref,
            "loser_ref": loser_ref,
            "resolution_method": resolution_method,
            "duration_seconds": row["duration_seconds"],
            "styles": styles,
            "combat_record": row["combat_record"],
            "finals_complete": refreshed["complete"],
            "champion_ref": refreshed["champion_ref"],
            "next_open_bouts": refreshed["open_bouts"],
            "semantic_event_id": event_id,
        },
        validator=validate,
    )


def _install_command() -> None:
    from shinobi_runtime.commands import campaign_player_handoffs as module

    COMMAND_SPECS.setdefault(
        "promotion_exam_bout_resolution",
        CommandSpec(
            ("cycle_id", "team_ref", "bout_ref"),
            (),
            "Resolve one currently open Chunin Examination finals bout through an autonomous nonlethal combat kernel; the caller cannot choose the fighters' tactics or the winner.",
            {
                "cycle_id": "promotion_exam_cycle.<id>",
                "team_ref": "team.<id> whose leader is observing the registered cycle",
                "bout_ref": "promotion_exam_bout.<id> from the current finals handoff",
            },
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_promotion_exam_bout_resolution", _plan_promotion_exam_bout_resolution)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)


def _install_phase_gate() -> None:
    from shinobi_runtime.commands import promotion_exam_pacing as pacing

    original = pacing._next_phase_due
    if getattr(original, "_promotion_exam_finals_gate", False):
        return

    def gated(profile: Mapping[str, Any], pipeline: Mapping[str, Any], cycle: Mapping[str, Any]) -> Any:
        if cycle.get("phase") == "finals" and isinstance(cycle.get("cycle_id"), str):
            config = profile.get("finals_format")
            if isinstance(config, Mapping) and not promotion_exam_finals_complete(
                pipeline, profile, str(cycle["cycle_id"])
            ):
                return None
        return original(profile, pipeline, cycle)

    gated._promotion_exam_finals_gate = True  # type: ignore[attr-defined]
    pacing._next_phase_due = gated


def install_promotion_exam_finals() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_command()
    _install_phase_gate()
    _INSTALLED = True


__all__ = [
    "install_promotion_exam_finals",
    "promotion_exam_bout_rows",
    "promotion_exam_finals_candidate_refs",
    "promotion_exam_finals_state",
    "promotion_exam_finals_complete",
]
