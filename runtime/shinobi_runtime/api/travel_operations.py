"""Travel, combat-observation, GM-director, and public-place projections.

Mechanical physical presence remains owned by the exact route/custody/combat
resolvers. Player-facing knowledge stays bounded, while explicitly marked
``gm_private`` director context may carry richer current-scene truth so the AI
can stage coherent people and physical action without turning that private truth
into Tang Wei's knowledge or into a second mechanical authority.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.combat.geometry import line_of_sight_clear
from shinobi_runtime.api.encounter_causality import resolved_contact_causality
from shinobi_runtime.api.operations import CampaignOperations, OperationError, _gm_private_person_cognition
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.exact_combat import currently_visible_enemies
from shinobi_runtime.martial_world.physical_presence import (
    active_combat_for_person,
    active_route_for_person,
    combat_person_arrived,
    same_effective_location,
)


def _unique_person_refs(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def public_site_scene_projection(scene: Mapping[str, Any], *, sample_limit: int = 8) -> dict[str, Any] | None:
    """Summarize deterministic public-site attendance into a bounded GM handoff.

    ``derived_present_person_ids`` is already a player-safe read-time attendance
    projection. It can be large, so expose its exact count plus a small,
    deterministic, namespace-diverse sample for progressive person reads. Site
    attendance proves shared public venue presence only; it does not establish
    close adjacency, line of sight, conversation, private knowledge or combat
    access.
    """
    site_ref = scene.get("location_id")
    if not isinstance(site_ref, str) or not site_ref.startswith("site."):
        return None
    attendees = _unique_person_refs(scene.get("derived_present_person_ids"))
    if not attendees:
        return None

    limit = max(0, min(16, int(sample_limit)))
    samples: list[str] = []
    if limit:
        seen_namespaces: set[str] = set()
        for ref in attendees:
            namespace = ref.rsplit(".", 1)[0] if "." in ref else ref
            if namespace in seen_namespaces:
                continue
            seen_namespaces.add(namespace)
            samples.append(ref)
            if len(samples) >= limit:
                break
        if len(samples) < limit:
            for ref in attendees:
                if ref in samples:
                    continue
                samples.append(ref)
                if len(samples) >= limit:
                    break

    return {
        "site_ref": site_ref,
        "derived_attendee_count": len(attendees),
        "sample_person_ids": samples,
        "presence_semantics": "shared_public_site_only",
    }


def combat_observation_scene_projection(
    *,
    read_json: Callable[[str], Any],
    player_id: str,
    ally_limit: int = 16,
    sheet_resolver: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Project arrived friendly cast and observer-specific hostile counts safely.

    Exact combat persists each combatant's ``observed_refs`` and may register
    future reinforcements before they reach the local geometry. The projection
    exposes only friendly members whose reinforcement clock has arrived. Enemy
    identities and hidden opposing roster size remain private, and an ally's
    observations remain that ally's knowledge until communicated in-scene.
    """
    active = active_combat_for_person(read_json, player_id)
    if active is None:
        return None
    combat_ref, combat = active
    if not isinstance(combat, Mapping):
        return None

    sides = combat.get("sides", {})
    combatants = combat.get("combatants", {})
    if not isinstance(sides, Mapping) or not isinstance(combatants, Mapping):
        return None

    player_side_ref: str | None = None
    player_side_members: list[str] = []
    enemy_refs: list[str] = []
    for side_ref, raw_members in sides.items():
        members = _unique_person_refs(raw_members)
        if player_id in members:
            player_side_ref = str(side_ref)
            player_side_members = members
            break
    if player_side_ref is None:
        return None
    for side_ref, raw_members in sides.items():
        if str(side_ref) == player_side_ref:
            continue
        for ref in _unique_person_refs(raw_members):
            if ref not in enemy_refs:
                enemy_refs.append(ref)
    enemy_set = set(enemy_refs)
    friendly_present = [
        ref for ref in player_side_members if combat_person_arrived(combat, ref)
    ]
    if player_id not in friendly_present:
        # The active-combat resolver itself requires the player to have arrived,
        # so this is defensive against malformed legacy state only.
        friendly_present.insert(0, player_id)

    positions = combat.get("positions") if isinstance(combat.get("positions"), Mapping) else {}
    obstacles = combat.get("obstacles") if isinstance(combat.get("obstacles"), list) else []

    def observer_summary(observer_ref: str) -> dict[str, Any]:
        state = combatants.get(observer_ref, {})
        observed = _unique_person_refs(state.get("observed_refs")) if isinstance(state, Mapping) else []
        confirmed = [ref for ref in observed if ref in enemy_set]
        # Current visibility must use the exact combat detection authority, not
        # cumulative encounter memory. Production get_play_context supplies the
        # exact sheet resolver, which adds concealment/detection to LOS. Tiny
        # read-only unit fixtures may omit it and retain the older LOS fallback.
        candidate_refs = []
        for ref in confirmed:
            enemy_state = combatants.get(ref, {})
            statuses = {
                str(x) for x in enemy_state.get("status_families", []) if isinstance(x, str)
            } if isinstance(enemy_state, Mapping) else set()
            if statuses & {"escaped", "reinforcing"}:
                continue
            if combat_person_arrived(combat, ref):
                candidate_refs.append(ref)
        currently_visible: list[str]
        visibility_semantics: str
        if sheet_resolver is not None:
            people: dict[str, Mapping[str, Any]] = {}
            for ref in [observer_ref, *candidate_refs]:
                try:
                    row = sheet_resolver(ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    row = None
                if isinstance(row, Mapping):
                    people[ref] = row
            visible_candidates = [ref for ref in candidate_refs if ref in people]
            currently_visible = currently_visible_enemies(
                combat, actor_ref=observer_ref, enemy_refs=visible_candidates, people=people,
            ) if observer_ref in people else []
            visibility_semantics = "fresh_exact_detection_and_line_of_sight_from_lawfully_observed_contacts"
        else:
            currently_visible = [
                ref for ref in candidate_refs
                if line_of_sight_clear(positions, actor_ref=observer_ref, target_ref=ref, obstacles=obstacles)
            ]
            visibility_semantics = "fresh_line_of_sight_fallback_without_sheet_resolver"
        return {
            "observer_person_id": observer_ref,
            "confirmed_observed_hostile_count": len(confirmed),
            "confirmed_observed_hostile_count_cumulative": len(confirmed),
            "currently_visible_hostile_count": len(currently_visible),
            "visibility_semantics": visibility_semantics,
        }

    player_observation = observer_summary(player_id)
    limit = max(0, min(24, int(ally_limit)))
    ally_observers: list[dict[str, Any]] = []
    if limit:
        for ref in friendly_present:
            if ref == player_id:
                continue
            ally_observers.append(observer_summary(ref))
            if len(ally_observers) >= limit:
                break

    return {
        "combat_ref": combat_ref,
        "friendly_participant_person_ids": friendly_present,
        "friendly_participant_count": len(friendly_present),
        "friendly_presence_semantics": "arrived_exact_combat_participants_only",
        "player_observation": player_observation,
        "ally_observer_summaries": ally_observers,
        "knowledge_semantics": "observer_specific_not_automatically_shared",
        "count_semantics": "confirmed_observed_hostiles_not_total_force",
        "current_count_semantics": "cumulative_observation_is_encounter_memory; current_visibility_is_not_a_hidden_force_census",
    }


def _injury_director_summary(person: Mapping[str, Any]) -> dict[str, Any]:
    health = person.get("health") if isinstance(person.get("health"), Mapping) else {}
    injuries = health.get("injuries", []) if isinstance(health, Mapping) else []
    rows: list[dict[str, Any]] = []
    if isinstance(injuries, list):
        for raw in injuries[:12]:
            if not isinstance(raw, Mapping):
                continue
            row: dict[str, Any] = {}
            for key in (
                "zone", "structure_ref", "side", "severity", "bleeding_ml_per_min",
                "fracture", "tendon_damage", "nerve_damage", "organ_trauma",
                "function_loss_pct", "pain", "treated",
            ):
                value = raw.get(key)
                if isinstance(value, (str, int, bool)) and not (isinstance(value, str) and not value):
                    row[key] = value
            if row:
                rows.append(row)
    out: dict[str, Any] = {
        "status": health.get("status") if isinstance(health, Mapping) else None,
        "shock": health.get("shock") if isinstance(health, Mapping) else None,
        "injuries": rows,
        "injury_count": len(injuries) if isinstance(injuries, list) else 0,
    }
    return {key: value for key, value in out.items() if value not in (None, [], {})}


def gm_private_combat_director_projection(
    *,
    read_json: Callable[[str], Any],
    sheet_resolver: Callable[[str], Mapping[str, Any]],
    player_id: str,
    participant_limit: int = 128,
    focus_limit: int = 8,
) -> dict[str, Any] | None:
    """Return exact-combat director truth without turning a large fight into a world dump.

    The resolver continues to use full exact person/combat state for every body.
    This is narration transport only: every admitted participant remains indexed,
    while full character sheets are concentrated on the current focal actors.
    """
    active = active_combat_for_person(read_json, player_id)
    if active is None:
        return None
    combat_ref, combat = active
    if not isinstance(combat, Mapping):
        return None
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    combatants = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    team_plans = combat.get("team_plans", {}) if isinstance(combat.get("team_plans"), Mapping) else {}
    player_side_ref = next((str(side) for side, refs in sides.items() if isinstance(refs, list) and player_id in refs), None)
    player_state = combatants.get(player_id, {}) if isinstance(combatants.get(player_id), Mapping) else {}
    player_observed = {str(x) for x in player_state.get("observed_refs", []) if isinstance(x, str)}

    refs: list[tuple[str, str]] = []
    for side_ref, raw_refs in sides.items():
        if not isinstance(raw_refs, list):
            continue
        for ref in raw_refs:
            if isinstance(ref, str) and ref and all(existing_ref != ref for _side, existing_ref in refs):
                refs.append((str(side_ref), ref))
    limit = max(1, min(128, int(participant_limit)))
    admitted = refs[:limit]
    try:
        equipment_ledger = read_json("state/martial-world/equipment-ledger.json")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        equipment_ledger = {}

    assignments: dict[str, dict[str, Any]] = {}
    plan_summaries: dict[str, dict[str, Any]] = {}
    primary_threats: set[str] = set()
    targeting_player: set[str] = set()
    for side_ref, raw_plan in team_plans.items():
        if not isinstance(raw_plan, Mapping):
            continue
        primary = raw_plan.get("primary_threat_ref")
        if isinstance(primary, str) and primary:
            primary_threats.add(primary)
        raw_assignments = raw_plan.get("assignments", {}) if isinstance(raw_plan.get("assignments"), Mapping) else {}
        role_counts: dict[str, int] = {}
        for actor_ref, raw_assignment in raw_assignments.items():
            if not isinstance(actor_ref, str) or not isinstance(raw_assignment, Mapping):
                continue
            assignment = {
                key: raw_assignment.get(key)
                for key in ("role", "target_ref", "preferred_action", "requires_line_of_sight")
                if raw_assignment.get(key) not in (None, "", [], {})
            }
            assignments[actor_ref] = assignment
            role = str(raw_assignment.get("role") or "unassigned")
            role_counts[role] = role_counts.get(role, 0) + 1
            if raw_assignment.get("target_ref") == player_id:
                targeting_player.add(actor_ref)
        plan_summaries[str(side_ref)] = {
            key: raw_plan.get(key)
            for key in (
                "plan_id", "objective_kind", "primary_threat_ref", "tactical_problem",
                "desired_states", "coordination_latency_ms", "replan_reasons",
            )
            if raw_plan.get(key) not in (None, "", [], {})
        }
        plan_summaries[str(side_ref)]["assignment_role_counts"] = role_counts

    records: dict[str, dict[str, Any]] = {}
    for side_ref, ref in admitted:
        state = combatants.get(ref, {}) if isinstance(combatants.get(ref), Mapping) else {}
        position = positions.get(ref, {}) if isinstance(positions.get(ref), Mapping) else {}
        try:
            person = sheet_resolver(ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            person = {}
        if not isinstance(person, Mapping):
            person = {}
        try:
            loadout = effective_person_loadout(equipment_ledger, ref) if isinstance(equipment_ledger, Mapping) else {}
        except (KeyError, TypeError, ValueError):
            loadout = {}
        records[ref] = {
            "side_ref": side_ref,
            "state": state,
            "position": position,
            "person": person,
            "loadout": loadout,
        }

    player_position = positions.get(player_id, {}) if isinstance(positions.get(player_id), Mapping) else {}
    px = int(player_position.get("x_mm", 0)); py = int(player_position.get("y_mm", 0))
    def distance_key(ref: str) -> tuple[int, str]:
        row = records.get(ref, {}).get("position", {})
        if not isinstance(row, Mapping):
            return (10**30, ref)
        dx = int(row.get("x_mm", 0)) - px; dy = int(row.get("y_mm", 0)) - py
        return (dx * dx + dy * dy, ref)

    focus_max = max(1, min(16, int(focus_limit)))
    focus: list[str] = []
    def add_focus(ref: str) -> None:
        if ref in records and ref not in focus and len(focus) < focus_max:
            focus.append(ref)
    add_focus(player_id)
    same_side = sorted(
        (ref for side, ref in admitted if side == player_side_ref and ref != player_id),
        key=distance_key,
    )
    for ref in same_side[:2]:
        add_focus(ref)
    hostile_priority = sorted(
        (ref for side, ref in admitted if side != player_side_ref and (ref in targeting_player or ref in primary_threats)),
        key=lambda ref: (0 if ref in targeting_player else 1, *distance_key(ref)),
    )
    for ref in hostile_priority:
        add_focus(ref)
    nearest = sorted((ref for _side, ref in admitted if ref != player_id), key=distance_key)
    for ref in nearest:
        add_focus(ref)
    detailed_refs = [ref for _side, ref in admitted] if len(admitted) <= 12 else focus

    participant_index: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    for side_ref, ref in admitted:
        record = records[ref]
        state = record["state"] if isinstance(record["state"], Mapping) else {}
        position = record["position"] if isinstance(record["position"], Mapping) else {}
        person = record["person"] if isinstance(record["person"], Mapping) else {}
        loadout = record["loadout"] if isinstance(record["loadout"], Mapping) else {}
        injury = _injury_director_summary(person)
        compact_health = {
            key: injury.get(key)
            for key in ("status", "shock", "injury_count")
            if injury.get(key) not in (None, "", [], {})
        }
        compact_state = {
            key: state.get(key)
            for key in ("status_families", "ready_weapon_ref", "weapon_position")
            if state.get(key) not in (None, "", [], {})
        }
        compact_position = {
            key: position.get(key)
            for key in ("x_mm", "y_mm", "elevation_mm", "stance")
            if position.get(key) is not None
        }
        index_row: dict[str, Any] = {
            "person_ref": ref,
            "name": person.get("name"),
            "side_ref": side_ref,
            "relation_to_player": "same_side" if side_ref == player_side_ref else "opposing_side",
            "arrived": bool(combat_person_arrived(combat, ref)),
            "player_has_observed_person": bool(ref == player_id or side_ref == player_side_ref or ref in player_observed),
            "health": compact_health,
            "position": compact_position,
            "combat_state": compact_state,
            "team_assignment": assignments.get(ref),
        }
        participant_index.append({key: value for key, value in index_row.items() if value not in (None, {}, [])})
        if ref not in detailed_refs:
            continue
        detail: dict[str, Any] = {
            "person_ref": ref,
            "name": person.get("name"),
            "faction_ref": person.get("faction_ref"),
            "membership_grade": person.get("membership_grade"),
            "side_ref": side_ref,
            "relation_to_player": "same_side" if side_ref == player_side_ref else "opposing_side",
            "arrived": bool(combat_person_arrived(combat, ref)),
            "player_has_observed_person": bool(ref == player_id or side_ref == player_side_ref or ref in player_observed),
            "fatigue_milli": person.get("fatigue_milli"),
            "attributes": dict(person.get("attributes", {})) if isinstance(person.get("attributes"), Mapping) else {},
            "martial_skills": dict(person.get("martial_skills", {})) if isinstance(person.get("martial_skills"), Mapping) else {},
            "qi": person.get("qi"),
            "qi_control": person.get("qi_control"),
            "current_qi_milli": person.get("current_qi_milli"),
            "health": injury,
            "equipment": {
                "items": dict(loadout.get("items", {})) if isinstance(loadout.get("items"), Mapping) else {},
                "condition_milli": dict(loadout.get("condition_milli", {})) if isinstance(loadout.get("condition_milli"), Mapping) else {},
            },
            "position": {
                key: position.get(key)
                for key in ("x_mm", "y_mm", "elevation_mm", "facing_mdeg", "vx_mmps", "vy_mmps", "stance", "cover_milli")
                if position.get(key) is not None
            },
            "combat_state": {
                key: state.get(key)
                for key in (
                    "status_families", "balance_milli", "limb_commitment_milli", "recovery_until_ms",
                    "weapon_position", "ready_weapon_ref", "surprise_milli", "awareness_confidence_milli",
                    "concealment_milli", "qi_allocation_milli",
                )
                if state.get(key) not in (None, [], {})
            },
            "team_assignment": assignments.get(ref),
        }
        cognition = _gm_private_person_cognition(person, {})
        if cognition:
            detail["gm_private_cognition"] = cognition
        detail_rows.append({key: value for key, value in detail.items() if value not in (None, {}, [])})

    obstacle_rows = combat.get("obstacles", []) if isinstance(combat.get("obstacles"), list) else []
    environment = dict(combat.get("environment", {})) if isinstance(combat.get("environment"), Mapping) else {}
    environment.pop("obstacles", None)
    encounter_causality: dict[str, Any] = {}
    try:
        route_ops = read_json("state/martial-world/route-operations.json")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        route_ops = {}
    contacts = route_ops.get("contacts", {}) if isinstance(route_ops, Mapping) else {}
    if isinstance(contacts, Mapping):
        active_contact = next(
            (
                row for row in contacts.values()
                if isinstance(row, Mapping) and row.get("status") == "active" and row.get("combat_ref") == combat_ref
            ),
            None,
        )
        if isinstance(active_contact, Mapping):
            causal, causal_source = resolved_contact_causality(active_contact, route_ops, read_json=read_json)
            attacker_refs = [str(ref) for ref in causal.get("attacker_refs", []) if isinstance(ref, str)] if isinstance(causal.get("attacker_refs"), list) else []
            encounter_causality = {
                key: causal.get(key)
                for key in (
                    "movement_ref", "route_ref", "attacker_faction_ref", "attacker_intent", "motive_kind",
                    "gm_private_decision_context",
                )
                if causal.get(key) not in (None, "", [], {})
            }
            if attacker_refs:
                encounter_causality["attacker_count"] = len(attacker_refs)
                if len(admitted) <= 12:
                    encounter_causality["attacker_refs"] = attacker_refs
            if encounter_causality:
                encounter_causality["source"] = causal_source

    observed_hostile = sorted(ref for ref in player_observed if any(side != player_side_ref and r == ref for side, r in refs))
    observation_boundary: dict[str, Any] = {
        "observed_hostile_person_count": len(observed_hostile),
        "rule": "Use the private packet to direct coherent action, but narrate hidden identities, positions, motives, injuries, plans, or capabilities only after Wei can perceive/infer them or another lawful source communicates them.",
    }
    if len(admitted) <= 12:
        observation_boundary["observed_hostile_person_refs"] = observed_hostile

    large = len(admitted) > 12
    packet: dict[str, Any] = {
        "privacy": "gm_private_scene_bounded_omniscient_truth_not_player_knowledge",
        "combat_ref": combat_ref,
        "world_truth_scope": "exact_active_combat_only",
        "elapsed_ms": combat.get("elapsed_ms"),
        "zone_ref": combat.get("zone_ref"),
        "objective": dict(combat.get("objective", {})) if isinstance(combat.get("objective"), Mapping) else combat.get("objective"),
        "environment": environment,
        "team_plans": dict(team_plans) if not large else plan_summaries,
        "team_plan_projection_mode": "full_small_combat" if not large else "compact_large_combat",
        "participants": detail_rows if not large else participant_index,
        "focus_participants": detail_rows,
        "focus_participant_refs": detailed_refs,
        "participant_projection_mode": "full_small_combat" if not large else "compact_large_combat_with_focal_full_sheets",
        "participant_count": len(refs),
        "participant_index_count": len(admitted),
        "participants_truncated": len(refs) > limit,
        "omitted_participant_count": max(0, len(refs) - limit),
        "obstacles": [dict(row) for row in obstacle_rows[:32] if isinstance(row, Mapping)],
        "obstacle_count": len(obstacle_rows),
        "encounter_causality": encounter_causality,
        "player_observation_boundary": observation_boundary,
        "director_rule": (
            "The exact resolver still uses full sheets and exact combat state for every participant. This packet is narration transport only. "
            "In large fights, participants is a complete compact tactical index for the bounded combat roster and focus_participants contains richer sheets for the immediate focal actors. "
            "Use current-transition combat_narrative material beats as the primary causal chronology when available. Hidden truth remains director context, not Wei knowledge."
        ),
    }
    return {key: value for key, value in packet.items() if value not in (None, [], {})}


def movement_scene_projection(
    *,
    read_json: Callable[[str], Any],
    sheet_resolver: Callable[[str], Mapping[str, Any]],
    player_id: str,
    player_sheet: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project exact co-traveling participants for the player's route owner.

    Sharing a route identifier is insufficient. A person is returned only when
    they are explicitly a participant in the same active route movement and the
    universal physical-presence resolver confirms the same exact movement space.
    """
    active = active_route_for_person(read_json, player_id)
    if active is None:
        return None
    movement_ref, movement = active
    participant_refs = _unique_person_refs(movement.get("participant_refs"))
    if player_id not in participant_refs:
        participant_refs.insert(0, player_id)

    present: list[str] = []
    for ref in participant_refs:
        try:
            other = player_sheet if ref == player_id else sheet_resolver(ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            continue
        if not isinstance(other, Mapping):
            continue
        if same_effective_location(
            read_json,
            player_id,
            ref,
            left_person=player_sheet,
            right_person=other,
        ):
            present.append(ref)

    if player_id not in present:
        present.insert(0, player_id)

    context: dict[str, Any] = {
        "movement_ref": movement_ref,
        "participant_person_ids": present,
        "participant_count": len(present),
    }
    for key in (
        "movement_kind",
        "status",
        "route_ref",
        "source_place_ref",
        "destination_place_ref",
        "started_at",
        "last_progress_at",
        "rest_place_ref",
    ):
        value = movement.get(key)
        if isinstance(value, str) and value:
            context[key] = value

    elapsed = movement.get("elapsed_seconds")
    required = movement.get("required_seconds")
    if isinstance(elapsed, int) and not isinstance(elapsed, bool) and elapsed >= 0:
        context["elapsed_seconds"] = elapsed
    if isinstance(required, int) and not isinstance(required, bool) and required > 0:
        context["required_seconds"] = required
        if isinstance(elapsed, int) and not isinstance(elapsed, bool) and elapsed >= 0:
            context["progress_milli"] = min(1000, elapsed * 1000 // required)

    return context


def _enrich_public_site_context(base: dict[str, Any]) -> None:
    scene = dict(base.get("scene", {})) if isinstance(base.get("scene"), Mapping) else {}
    public_context = public_site_scene_projection(scene)
    if public_context is None:
        return
    scene["public_site_context"] = public_context
    base["scene"] = scene

    person_reads = dict(base.get("person_reads", {})) if isinstance(base.get("person_reads"), Mapping) else {}
    suggested = _unique_person_refs(person_reads.get("suggested_owner_ids"))
    for ref in _unique_person_refs(public_context.get("sample_person_ids")):
        if ref not in suggested:
            suggested.append(ref)
    person_reads["suggested_owner_ids"] = suggested
    person_reads["public_site_sample_use"] = (
        "Sample IDs are deterministic player-safe public attendees for progressive reads; "
        "attendance does not imply direct interaction or combat adjacency."
    )
    base["person_reads"] = person_reads


def _validate_play_context(base: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_bounded_json(base, label="play context", allow_float=True)
    except ValueError as exc:
        raise OperationError(503, "play_context_out_of_bounds") from exc
    return base


class TravelAwareCampaignOperations(CampaignOperations):
    """Campaign operations with route-party, combat-presence and public-place context."""

    def play_context(self) -> Mapping[str, Any]:
        # Public-site attendance is already part of the base snapshot, so it can
        # be summarized without another state read. Route and combat projections
        # are read under the same revision/root check as the base context.
        for _attempt in range(2):
            base = dict(super().play_context())
            _enrich_public_site_context(base)
            campaign = base.get("campaign")
            if not isinstance(campaign, Mapping):
                return _validate_play_context(base)
            player_id = str(campaign.get("player_id") or "")
            if not player_id:
                return _validate_play_context(base)
            try:
                with self._locked():
                    self.coordinator.git.assert_pristine()
                    before = self._read_fingerprint()
                    meta = self.repository.read_json("state/meta.json")
                    if (
                        not isinstance(meta, Mapping)
                        or int(meta.get("revision", -1)) != int(campaign.get("revision", -2))
                        or str(meta.get("campaign_id") or "") != str(campaign.get("campaign_id") or "")
                        or before[1] != str(campaign.get("state_root") or "")
                    ):
                        continue
                    player_sheet = self.sheet_resolver(player_id)
                    if not isinstance(player_sheet, Mapping):
                        return _validate_play_context(base)
                    movement = movement_scene_projection(
                        read_json=self.repository.read_json,
                        sheet_resolver=self.sheet_resolver,
                        player_id=player_id,
                        player_sheet=player_sheet,
                    )
                    combat_observation = combat_observation_scene_projection(
                        read_json=self.repository.read_json,
                        player_id=player_id,
                        sheet_resolver=self.sheet_resolver,
                    )
                    gm_private_combat = gm_private_combat_director_projection(
                        read_json=self.repository.read_json,
                        sheet_resolver=self.sheet_resolver,
                        player_id=player_id,
                    )
                    self._require_read_only(before, "play_context_travel_projection_mutated_campaign")
            except OperationError:
                raise
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                return _validate_play_context(base)

            scene = dict(base.get("scene", {})) if isinstance(base.get("scene"), Mapping) else {}
            person_reads = dict(base.get("person_reads", {})) if isinstance(base.get("person_reads"), Mapping) else {}
            suggested = _unique_person_refs(person_reads.get("suggested_owner_ids"))

            if combat_observation is not None:
                scene["combat_observation_context"] = combat_observation
                if gm_private_combat is not None:
                    director = dict(scene.get("gm_private_director_context", {})) if isinstance(scene.get("gm_private_director_context"), Mapping) else {}
                    director["combat"] = gm_private_combat
                    scene["gm_private_director_context"] = director
                combat_present = _unique_person_refs(
                    combat_observation.get("friendly_participant_person_ids")
                )
                present: list[str] = []
                existing_present = scene.get("present_person_ids", [])
                for ref in ([*existing_present] if isinstance(existing_present, list) else []) + combat_present:
                    if isinstance(ref, str) and ref and ref not in present:
                        present.append(ref)
                scene["present_person_ids"] = present
                scene["combat_present_person_ids"] = combat_present
                for ref in combat_present:
                    if ref not in suggested:
                        suggested.append(ref)
                person_reads["combat_participant_use"] = (
                    "combat_present_person_ids are exact friendly members whose combat-arrival clock has fired. "
                    "Use them as the current friendly battle cast; registered future reinforcements are not co-present yet."
                )
                person_reads["combat_observer_use"] = (
                    "Ally observer counts are that ally's exact stored combat observation, not automatically "
                    "Wei's knowledge. If a co-present ally reports what they saw, use the confirmed observed count "
                    "without treating it as the total hostile force."
                )

            if movement is not None:
                ids = _unique_person_refs(movement.get("participant_person_ids"))
                present = []
                existing_present = scene.get("present_person_ids", [])
                for ref in ([*existing_present] if isinstance(existing_present, list) else []) + ids:
                    if isinstance(ref, str) and ref and ref not in present:
                        present.append(ref)
                scene["present_person_ids"] = present
                # Exact movement ownership establishes co-presence, not line of
                # sight. Keep the narrower existing visible projection unchanged;
                # scouts or convoy elements may share a movement while out of view.
                scene["movement_present_person_ids"] = ids
                scene["movement_context"] = movement
                for ref in ids:
                    if ref not in suggested:
                        suggested.append(ref)

            base["scene"] = scene
            person_reads["suggested_owner_ids"] = suggested
            base["person_reads"] = person_reads
            return _validate_play_context(base)

        raise OperationError(503, "play_context_state_changed_during_travel_projection")


__all__ = [
    "TravelAwareCampaignOperations",
    "combat_observation_scene_projection",
    "gm_private_combat_director_projection",
    "movement_scene_projection",
    "public_site_scene_projection",
]
