"""Bounded autonomous use of the authoritative exact personal combat resolver.

The simulation is used for autonomous NPC contacts and may also drive a player
only when the caller explicitly supplies a delegated actor for a bounded combat
window. Direct player exchanges remain command-driven. No combat history is
persisted by this helper; callers keep current injuries/equipment and a compact
result only.
"""
from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .exact_combat import automatic_resource_policy, combatant_active, currently_visible_enemies, default_action_for, initialize_combat, resolve_exchange
from .health import settle_physiology
from .equipment_state import compact_equipment_ledger, hydrate_equipment_ledger
from .poison import poison_onset_seconds, poison_peak_seconds
from .relationships import apply_relationship_event
from .social_causality import (
    apply_martial_events, breach_hostile_commitments, hostile_target_pressure,
    prune_incidental_martial_familiarity, vow_conflicts,
)


def finalize_autonomous_lethality(
    people: Mapping[str, Mapping[str, Any]], *, targeting_intent: str,
) -> dict[str, dict[str, Any]]:
    """Close unattended physiologically fatal casualties after autonomous combat.

    Targeting intent governs what an autonomous combatant tries to do; it never
    overrides the injury that physically occurred. Exact player combat may keep
    a ``dying`` casualty open because the player can intervene immediately. An
    autonomous contact has no same-frontier treatment decision, so a casualty
    whose current physiology is already ``dying`` must close as death before the
    after-image persists, even when the declared intent was ``disable``. This
    preserves genuinely nonlethal contacts while allowing accidental fatalities
    from catastrophic trauma instead of keeping impossible bleeding casualties
    alive until a monthly recovery tick.
    """
    out = {str(ref): copy.deepcopy(dict(person)) for ref, person in people.items()}
    for ref, person in out.items():
        health = copy.deepcopy(dict(person.get("health", {}))) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") == "dead":
            continue
        wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
        if not wounds:
            continue
        attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
        physiology = settle_physiology(
            body_mass_kg=float(person.get("body_mass_kg", 70)),
            wounds=[w for w in wounds if isinstance(w, Mapping)],
            blood_lost_ml=max(0, int(health.get("blood_lost_ml", 0))),
            elapsed_seconds=0,
            endurance=max(0, int(attrs.get("endurance", 0))),
            willpower=max(0, int(attrs.get("willpower", 0))),
        )
        if physiology.get("lethal_state") != "dying":
            continue
        # A live autonomous lethal contact has no same-frontier treatment owner.
        # Fatal physiology must close here instead of being reclassified as a
        # month-long recoverable incapacity.
        health["status"] = "dead"
        health["consciousness"] = 0
        health["shock"] = max(int(health.get("shock", 0)), int(physiology.get("shock", 0)))
        health["blood_lost_ml"] = max(int(health.get("blood_lost_ml", 0)), int(physiology.get("blood_lost_ml", 0)))
        person["health"] = health
        out[ref] = person
    return out


def _rebase_autonomous_person_timestamps(
    person: Mapping[str, Any], *, frontier_at: str, before: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collapse autonomous combat microtime back onto its scheduler frontier.

    Background combat uses an exact local millisecond clock for reach, defense,
    fatigue, bleeding, poison delivery and other within-fight physics, but it is
    evaluated atomically at one world-scheduler frontier.  Local microtime must
    therefore never leak into persisted person timestamps, or a later sparse
    physiology wake can appear to run *before* medicine/wound state that the
    same frontier already wrote.

    Physiological consequences remain real.  Only persisted chronology is
    rebased.  Medicine has no combat-side administration path, so any change to
    its state during an autonomous fight is elapsed-time settlement and may be
    restored from the pre-fight snapshot.  Newly created/updated wound clocks,
    dying clocks and delayed poison onset are anchored to the world frontier.
    """
    out=copy.deepcopy(dict(person)); prior=before if isinstance(before,Mapping) else {}
    frontier=datetime.fromisoformat(str(frontier_at).removeprefix("SE-")); frontier_iso=frontier.isoformat()

    # Autonomous combat never administers medicine. Any mutation here came
    # only from advancing the local combat microclock, which is not world time.
    prior_medicine=prior.get("medicine_state") if isinstance(prior.get("medicine_state"),Mapping) else None
    if prior_medicine is not None:
        out["medicine_state"]=copy.deepcopy(dict(prior_medicine))
    else:
        medicine=out.get("medicine_state") if isinstance(out.get("medicine_state"),Mapping) else None
        if medicine is not None:
            medicine=copy.deepcopy(dict(medicine)); raw=medicine.get("last_settled_at")
            if isinstance(raw,str):
                try:
                    if datetime.fromisoformat(raw.removeprefix("SE-"))>frontier: medicine["last_settled_at"]=frontier_iso
                except ValueError:
                    pass
            out["medicine_state"]=medicine

    health=copy.deepcopy(dict(out.get("health",{}))) if isinstance(out.get("health"),Mapping) else {}
    prior_health=prior.get("health",{}) if isinstance(prior.get("health"),Mapping) else {}
    prior_wounds=prior_health.get("injuries",[]) if isinstance(prior_health.get("injuries"),list) else []
    # Preserve opaque pre-existing fixture/history labels, but any newly written
    # combat-relative millisecond stamp or future ISO timestamp is world-anchored.
    prior_created=Counter(
        str(w.get("created_at")) for w in prior_wounds
        if isinstance(w,Mapping) and w.get("created_at") is not None
    )
    injuries=[]
    for raw_wound in health.get("injuries",[]) if isinstance(health.get("injuries"),list) else []:
        if not isinstance(raw_wound,Mapping): continue
        wound=copy.deepcopy(dict(raw_wound)); raw=wound.get("created_at")
        if raw is not None:
            text=str(raw); unchanged=prior_created.get(text,0)>0
            if unchanged:
                prior_created[text]-=1
            else:
                rebase=False
                try: rebase=datetime.fromisoformat(text.removeprefix("SE-"))>frontier
                except ValueError: rebase=text.isdigit()
                if rebase: wound["created_at"]=frontier_iso
        injuries.append(wound)
    if injuries or isinstance(health.get("injuries"),list): health["injuries"]=injuries

    dying_since=health.get("dying_since")
    if isinstance(dying_since,str):
        try:
            if datetime.fromisoformat(dying_since.removeprefix("SE-"))>frontier: health["dying_since"]=frontier_iso
        except ValueError:
            pass
    if health: out["health"]=health

    pending=out.get("pending_poison_burdens")
    prior_pending=prior.get("pending_poison_burdens") if isinstance(prior.get("pending_poison_burdens"),Mapping) else {}
    if isinstance(pending,Mapping):
        rebased={}
        for storage_key,raw_row in pending.items():
            if not isinstance(raw_row,Mapping):
                continue
            row=copy.deepcopy(dict(raw_row)); raw=row.get("activates_at",row.get("due_at"))
            poison_ref=str(row.get("poison_ref") or str(storage_key).split("#",1)[0])
            if isinstance(raw,str):
                try:
                    due=datetime.fromisoformat(raw.removeprefix("SE-"))
                    old_row=prior_pending.get(storage_key) if isinstance(prior_pending,Mapping) else None
                    old_raw=old_row.get("activates_at",old_row.get("due_at")) if isinstance(old_row,Mapping) else None
                    old_due=None
                    if isinstance(old_raw,str):
                        try: old_due=datetime.fromisoformat(old_raw.removeprefix("SE-"))
                        except ValueError: old_due=None
                    # Existing pending exposure keeps its world clock. A new
                    # exposure created on combat microtime is anchored to the
                    # scheduler frontier plus the registered onset. Separate
                    # pending rows prevent one dose from pulling another dose's
                    # clock forward or postponing it.
                    canonical=frontier+timedelta(seconds=poison_onset_seconds(poison_ref))
                    row["activates_at"]=(old_due if old_due is not None else min(due,canonical)).isoformat()
                    peak_raw=row.get("peaks_at")
                    old_peak_raw=old_row.get("peaks_at") if isinstance(old_row,Mapping) else None
                    peak=None; old_peak=None
                    if isinstance(peak_raw,str):
                        try: peak=datetime.fromisoformat(peak_raw.removeprefix("SE-"))
                        except ValueError: peak=None
                    if isinstance(old_peak_raw,str):
                        try: old_peak=datetime.fromisoformat(old_peak_raw.removeprefix("SE-"))
                        except ValueError: old_peak=None
                    if peak is not None or old_peak is not None or str(row.get("stage") or "") in {"onset","peak"}:
                        canonical_peak=frontier+timedelta(seconds=poison_peak_seconds(poison_ref))
                        row["peaks_at"]=(old_peak if old_peak is not None else min(x for x in (peak,canonical_peak) if x is not None)).isoformat()
                    row["poison_ref"]=poison_ref
                    row.pop("due_at",None)
                except (KeyError,ValueError):
                    pass
            rebased[str(storage_key)]=row
        if rebased: out["pending_poison_burdens"]=rebased
        else: out.pop("pending_poison_burdens",None)
    return out


def simulate_exact_combat(
    *, combat_ref: str, side_a_refs: Sequence[str], side_b_refs: Sequence[str],
    people: Mapping[str, Mapping[str, Any]], equipment_ledger: Mapping[str, Any],
    doctrines: Mapping[str, Mapping[str, Any]], zone_ref: str, started_at: str,
    objective: Mapping[str, Any], targeting_intent: str = "lethal", max_exchanges: int = 160,
    obstacles: Sequence[Mapping[str, Any]] = (), environment: Mapping[str, Any] | None = None,
    mount_assignments: Mapping[str, Mapping[str, Any]] | None = None, initial_range_band: int = 1,
    social_state: Mapping[str, Any] | None = None, delegated_actor_ref: str | None = None,
    player_retinue_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    initial_people={str(ref):copy.deepcopy(dict(p)) for ref,p in people.items()}
    persons={ref:copy.deepcopy(person) for ref,person in initial_people.items()}
    # Hydrate the sparse custody ledger once for the whole bounded autonomous fight.
    # Re-hydrating and re-compacting it on every exchange makes local combat scale
    # with the campaign-wide equipment ledger rather than with its participants.
    ledger = hydrate_equipment_ledger(equipment_ledger)
    logical_loadouts=ledger.setdefault("person_loadouts",{})
    if isinstance(logical_loadouts,dict):
        for ref in sorted(set(map(str,side_a_refs))|set(map(str,side_b_refs))):
            logical_loadouts.setdefault(ref,{"items":{},"condition_milli":{}})
    social = copy.deepcopy(dict(social_state)) if isinstance(social_state, Mapping) else {}
    initial_familiarity_refs = {
        str(ref) for ref in social.get("martial_familiarity", {})
    } if isinstance(social.get("martial_familiarity"), Mapping) else set()
    side_by_ref = {str(ref): "side_a" for ref in side_a_refs}
    side_by_ref.update({str(ref): "side_b" for ref in side_b_refs})
    combat = initialize_combat(
        combat_ref=combat_ref,
        side_a_refs=list(side_a_refs), side_b_refs=list(side_b_refs), people=persons,
        zone_ref=zone_ref, started_at=started_at, objective=objective,
        awareness_mode="mutual", initial_range_band=initial_range_band, equipment_ledger=ledger,
        obstacles=obstacles, environment=environment, mount_assignments=mount_assignments,
    )
    # Background fights are bounded local physics evaluated inside one scheduler
    # frontier. The marker is transient and never survives combat compaction.
    combat["_atomic_frontier_time"]=True
    exchanges = 0
    last_events: list[Mapping[str, Any]] = []
    while combat.get("status") == "active" and exchanges < max(1, int(max_exchanges)):
        states = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
        active_a = [
            str(r) for r in side_a_refs
            if r in persons and isinstance(states.get(r), Mapping) and combatant_active(persons[r], states[r])
        ]
        active_b = [
            str(r) for r in side_b_refs
            if r in persons and isinstance(states.get(r), Mapping) and combatant_active(persons[r], states[r])
        ]
        if not active_a or not active_b:
            break
        driver_candidates = (
            [str(delegated_actor_ref)]
            if isinstance(delegated_actor_ref,str) and delegated_actor_ref in active_a
            else sorted(active_a)
        )
        driver = ""
        visible_targets: list[str] = []
        for candidate in driver_candidates:
            current = currently_visible_enemies(
                combat, actor_ref=candidate, enemy_refs=active_b, people=persons,
            )
            if current:
                driver = candidate
                visible_targets = current
                break
        if not driver or not visible_targets:
            # Encounter memory is not permission to attack an exact hidden
            # coordinate.  With no current lawful contact this bounded autonomous
            # slice stops unresolved instead of burning exchanges on omniscient
            # target choices.
            break
        target = min(
            visible_targets,
            key=lambda ref: (
                -hostile_target_pressure(
                    social, actor_ref=driver, target_ref=ref,
                    target_faction_ref=str(persons[ref].get("faction_ref") or ""),
                ),
                (int(combat["positions"][driver]["x_mm"]) - int(combat["positions"][ref]["x_mm"])) ** 2
                + (int(combat["positions"][driver]["y_mm"]) - int(combat["positions"][ref]["y_mm"])) ** 2,
                ref,
            ),
        )
        driver_intent = targeting_intent
        if driver_intent == "lethal" and vow_conflicts(
            social, person_ref=driver, action_kind="attack", target_ref=target,
            target_faction_ref=str(persons[target].get("faction_ref") or ""),
            targeting_intent=driver_intent,
        ):
            driver_intent = "disable"
        action_kind, weapon_ref = default_action_for(
            combat=combat, people=persons, equipment_ledger=ledger,
            actor_ref=driver, target_ref=target, martial_familiarity=social,
        )
        driver_faction_doctrine=doctrines.get(str(persons[driver].get("faction_ref") or ""),{})
        resource_policy=automatic_resource_policy(
            combat=combat,actor_ref=driver,target_ref=target,people=persons,equipment_ledger=ledger,
            faction_doctrine=driver_faction_doctrine,action_kind=action_kind,weapon_ref=weapon_ref,intent=driver_intent,
            social_state=social,
        )
        resolved = resolve_exchange(
            combat=combat, people=persons, equipment_ledger=ledger, doctrines=doctrines,
            player_ref=driver, player_action_kind=action_kind, player_target_ref=target,
            player_weapon_ref=weapon_ref, player_hit_zone="auto", player_target_structure_ref="auto",
            player_targeting_intent=driver_intent, player_poison_ref=resource_policy.get("poison_ref"),
            player_qi_allocation_milli=resource_policy.get("qi_allocation_milli",{}),
            player_qi_reserve_milli=int(resource_policy.get("qi_reserve_milli",0)),
            npc_targeting_intent=targeting_intent, martial_familiarity=social,
            player_retinue_context=(player_retinue_context if driver==delegated_actor_ref else None),
            equipment_ledger_hydrated=True,compact_equipment_result=False,mutate_equipment_ledger=True,
            mutate_state=True,
        )
        combat = resolved["combat_after"]
        persons = resolved["people_after"]
        ledger = resolved["equipment_ledger_after"]
        last_events = [e for e in resolved.get("events", []) if isinstance(e, Mapping)]
        if social_state is not None:
            social = apply_martial_events(social, last_events, side_by_ref=side_by_ref)
            combatants_after = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
            rejected_results = {
                "invalid_target", "friendly_target_rejected", "target_unavailable", "action_rejected",
                "target_not_observed", "no_lawfully_known_target",
            }
            for event in last_events:
                if not isinstance(event, Mapping) or str(event.get("result") or "") in rejected_results:
                    continue
                actor_ref = str(event.get("actor_ref") or "")
                intended_ref = str(event.get("intended_ref") or "")
                if not actor_ref or not intended_ref or actor_ref == intended_ref:
                    continue
                effective_intent = driver_intent if actor_ref == driver else "disable"
                breached = breach_hostile_commitments(
                    social, actor_ref=actor_ref, target_ref=intended_ref,
                    target_faction_ref=str(persons.get(intended_ref, {}).get("faction_ref") or ""),
                    targeting_intent=effective_intent,poison_ref=str(event.get("poison_ref") or ""),
                )
                social = breached["state_after"]
                if breached.get("broken_obligation_refs"):
                    defense = event.get("defense") if isinstance(event.get("defense"), Mapping) else {}
                    actual_ref = str(event.get("actual_ref") or "")
                    detected_this_attack = bool(actual_ref == intended_ref and defense.get("detected") is True)
                    freshly_visible = False
                    if (
                        intended_ref in persons and actor_ref in persons
                        and intended_ref in combatants_after and actor_ref in combatants_after
                    ):
                        freshly_visible = actor_ref in currently_visible_enemies(
                            combat, actor_ref=intended_ref, enemy_refs=[actor_ref], people=persons,
                        )
                    if detected_this_attack or freshly_visible:
                        social = apply_relationship_event(
                            social, observer_ref=intended_ref, subject_ref=actor_ref,
                            event_kind="oath_breach", observer_knows=True, severity_milli=1000,
                        )["state_after"]
        exchanges += 1
    persons = finalize_autonomous_lethality(persons, targeting_intent=targeting_intent)
    persons={
        ref:_rebase_autonomous_person_timestamps(person,frontier_at=started_at,before=initial_people.get(ref))
        for ref,person in persons.items()
    }
    if social_state is not None:
        # Pairwise martial memory is valuable in duels/small recurring fights,
        # not as a permanent matrix of everybody who shared a battlefield.
        # Large contacts may still deepen an already-established rivalry, but
        # they do not materialize new pairwise profiles for the whole frontage.
        participant_count = len(set(side_by_ref))
        keep_refs = None if participant_count <= 4 else initial_familiarity_refs
        social = prune_incidental_martial_familiarity(social, keep_refs=keep_refs)
    return {
        "resolved": combat.get("status") == "resolved",
        "winner_side": combat.get("winner_side"),
        "combat_elapsed_ms": max(0, int(combat.get("elapsed_ms", 0))),
        "exchanges": exchanges,
        "people_after": persons,
        "equipment_ledger_after": compact_equipment_ledger(ledger),
        "social_state_after": social,
        "last_events": last_events,
    }


__all__ = ["finalize_autonomous_lethality", "simulate_exact_combat"]
