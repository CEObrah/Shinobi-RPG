"""Authoritative deterministic exact combat for the Jianghu campaign.

Combat is resolved from one shared millisecond timeline. Intent and side
membership establish legality, not contact. Timing, movement, facing, body and
weapon commitment, projectile flight, obstacles, anatomy, current injuries and
lawful information establish the outcome. This module is the sole live personal
combat resolver.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

from shinobi_runtime.combat.geometry import (
    angular_difference_mdeg,
    cover_milli_between,
    facing_to_target_mdeg,
    initial_positions,
    line_of_sight_clear,
    open_retreat_corridors,
    path_clear,
    planar_distance_mm,
    trace_attack_geometry,
)
from shinobi_runtime.combat.models import (
    ActionProfile,
    CapabilityProfile,
    CombatIntent,
    InformationState,
    Participant,
    PersonnelState,
    PositionState,
    ReactiveDefense,
)
from shinobi_runtime.combat.physical_defense import (
    close_attacker_into_reach,
    contact_after_defense,
    movement_speed_mmps,
    physical_reach_mm,
    select_physical_defense,
    status_action_allowed,
)
from shinobi_runtime.combat.team_tactics import plan_player_retinue_exchange, plan_team_exchange, replan_reasons, threat_score

from .combat import active_defense_available, allocate_qi, commit_active_defense, control_efficiency_milli
from .combat_exertion import action_work_points, apply_exertion, defense_work_points, fatigue_performance_milli, guard_work_fraction, movement_work_points
from .equipment import (
    bow_shot_profile, carried_mass_kg, encumbrance_effects, projectile_contact_profile,
    resolve_equipment_item, transition_seconds, weapon_contact_profile,
)
from .equipment_state import compact_equipment_ledger, effective_person_loadout, hydrate_equipment_ledger
from .equipment_lifecycle import apply_wear
from .health import (
    combat_status_families,
    dying_deadline_minutes,
    functional_capacity_factors,
    functional_penalties,
    settle_physiology,
    record_current_wound,
    structure_definition,
    target_zone,
    vision_state,
    wound_from_contact,
)
from .poison import activate_due_poison_exposures, apply_poison, current_poison_effects, pending_poison_burden, queue_progressive_poison_exposure
from .physiology_frontier import physiology_needed, settle_person_physiology_event
from .medicine import active_recovery_modifiers, toxicity_consequences_current
from .doctrines import resolve_faction_force_intent, resolve_force_intent, resolve_individual_doctrine, resolve_player_retinue_doctrine
from .qi import person_current_qi_milli, safe_flow_milli_per_second, set_person_current_qi_milli
from .targeting import doctrine_target, intent_target
from .mounts import mount_contact_result, mounted_motion_profile
from .social_causality import hostile_target_pressure, martial_profile, vow_conflicts

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game" / "data" / "martial-world"


@lru_cache(maxsize=None)
def _load(name: str) -> Mapping[str, Any]:
    """Load immutable static combat data once per process.

    Exact combat asks for the same action/equipment tables thousands of times in
    a multi-actor exchange. Re-reading and decoding those authored JSON files is
    not simulation work and must not scale with the number of attacks. Callers
    treat the returned mappings as read-only.
    """
    return json.loads((_MW / name).read_text(encoding="utf-8"))


def _equipment_catalog() -> Mapping[str, Any]:
    return _load("equipment.json")


def _combat_rules() -> Mapping[str, Any]:
    return _load("combat.json")


def _attrs(person: Mapping[str, Any]) -> Mapping[str, Any]:
    value = person.get("attributes")
    return value if isinstance(value, Mapping) else {}


def _skills(person: Mapping[str, Any]) -> Mapping[str, Any]:
    value = person.get("martial_skills")
    return value if isinstance(value, Mapping) else {}


def _wounds(person: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    health = person.get("health")
    rows = health.get("injuries", []) if isinstance(health, Mapping) else []
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _refresh_structural_statuses(combatant_state: dict[str, Any], person: Mapping[str, Any]) -> None:
    """Refresh wound-derived status families without deleting transient combat state."""
    statuses={str(x) for x in combatant_state.get("status_families",[]) if isinstance(x,str)}
    statuses.update(combat_status_families(_wounds(person)))
    combatant_state["status_families"]=sorted(statuses)


def _discipline_for_action(action_kind: str, weapon: Mapping[str, Any] | None = None) -> str:
    if isinstance(weapon, Mapping) and isinstance(weapon.get("discipline"), str):
        return str(weapon["discipline"])
    aliases = {
        "cut": "sword",
        "thrust": "sword",
        "unarmed_strike": "unarmed",
        "staff_strike": "spear",
        "staff_sweep": "spear",
        "staff_thrust": "spear",
        "staff_butt_strike": "spear",
        "bow_shot": "bow",
        "hidden_weapon_throw": "hidden_weapons",
    }
    return aliases.get(action_kind, "unarmed")


def capability_from_person(person: Mapping[str, Any], *, action_skill: str | None = None) -> CapabilityProfile:
    attrs = _attrs(person)
    skills = _skills(person)
    strength = max(0, int(attrs.get("strength", 0)))
    speed = max(0, int(attrs.get("speed", 0)))
    dexterity = max(0, int(attrs.get("dexterity", 0)))
    perception = max(0, int(attrs.get("perception", 0)))
    endurance = max(0, int(attrs.get("endurance", 0)))
    intelligence = max(0, int(attrs.get("intelligence", 0)))
    willpower = max(0, int(attrs.get("willpower", 0)))
    qi_control = max(0, int(person.get("qi_control", 0)))
    poison_effects = current_poison_effects(person.get("poison_burdens") if isinstance(person.get("poison_burdens"), Mapping) else {})
    speed = max(0, speed - max(0, int(poison_effects.get("speed_penalty", 0))))
    dexterity = max(0, dexterity - max(0, int(poison_effects.get("dexterity_penalty", 0))))
    perception = max(0, perception - max(0, int(poison_effects.get("perception_penalty", 0))))
    endurance = max(0, endurance - max(0, int(poison_effects.get("endurance_penalty", 0))))
    fatigue = max(0, int(person.get("fatigue_milli", 0)))
    skill_ref = action_skill or "sword"
    action_rule = _combat_rules().get("actions", {}).get(skill_ref)
    if isinstance(action_rule, Mapping):
        skill_ref = str(action_rule.get("skill", skill_ref))
    skill_ref = _discipline_for_action(skill_ref)

    wounds = _wounds(person)
    impair = functional_penalties(wounds)
    capacity = functional_capacity_factors(wounds)
    perception = perception * max(0, 100 - int(impair.get("vision", 0))) // 100
    dexterity = dexterity * max(0, 100 - int(impair.get("weapon_control", 0)) // 2) // 100
    endurance = endurance * max(0, 100 - max(int(impair.get("core", 0)), int(impair.get("breathing", 0))) // 2) // 100

    # Permanent disability never edits learned skill.  Weapon usability and
    # current body function instead determine how much of that skill can be
    # expressed in this specific action.
    skill = max(0, int(skills.get(skill_ref, 0)))
    if skill_ref == "bow":
        two_hand = max(
            int(impair.get("weapon_control_left", 0)), int(impair.get("weapon_control_right", 0)),
            int(impair.get("grip_left", 0)), int(impair.get("grip_right", 0)), int(impair.get("depth_perception", 0)),
        )
        skill = skill * max(0, 100 - two_hand) // 100
    elif skill_ref == "spear":
        two_hand = max(int(impair.get("weapon_control_left", 0)), int(impair.get("weapon_control_right", 0)))
        skill = skill * max(0, 100 - two_hand) // 100
    else:
        skill = skill * max(0, 100 - int(impair.get("weapon_control", 0))) // 100

    movement_factor = max(0, min(1000, int(capacity.get("combat_movement_milli", 1000))))
    standing_factor = max(0, min(1000, int(capacity.get("standing_milli", 1000))))
    running_factor = max(0, min(1000, int(capacity.get("running_milli", 1000))))
    fatigue_factor = fatigue_performance_milli(fatigue)
    base_mobility = (speed * 60 + dexterity * 40) // 100
    mobility = base_mobility * movement_factor // 1000 * fatigue_factor // 1000
    reaction_base = (speed * 35 + dexterity * 30 + perception * 25 + intelligence * 10) // 100
    reaction_posture = 500 + standing_factor // 2
    reaction = reaction_base * reaction_posture // 1000 * fatigue_factor // 1000
    best_guard_skill = max([0] + [max(0, int(skills.get(key, 0))) for key in ("sword", "spear", "unarmed")])
    response = (best_guard_skill * 40 + dexterity * 25 + perception * 20 + intelligence * 5 + endurance * 10) // 100
    response = response * (500 + standing_factor // 2) // 1000
    response = response * fatigue_factor // 1000
    control = (skill * 35 + dexterity * 25 + perception * 15 + intelligence * 10 + qi_control * 15) // 100
    control = control * (750 + standing_factor // 4) // 1000
    control = control * fatigue_factor // 1000
    offense = (skill * 50 + strength * 18 + dexterity * 22 + perception * 10) // 100
    if skill_ref in {"sword", "spear", "unarmed"}:
        offense = offense * (650 + movement_factor * 350 // 1000) // 1000
    elif skill_ref == "hidden_weapons":
        offense = offense * (800 + standing_factor // 5) // 1000
    offense = offense * fatigue_factor // 1000
    capture = (max(0, int(skills.get("unarmed", 0))) * 50 + strength * 35 + willpower * 15) // 100
    capture = capture * (500 + standing_factor // 2) // 1000
    capture = capture * fatigue_factor // 1000
    escape_base = (speed * 45 + dexterity * 35 + perception * 10 + intelligence * 10) // 100
    escape = escape_base * running_factor // 1000 * fatigue_factor // 1000
    return CapabilityProfile(
        offense=offense, defense=response, control=control, mobility=mobility, perception=perception,
        stealth=max(0, int(skills.get("stealth_scouting", 0))) * max(250, movement_factor) // 1000 * (500 + fatigue_factor // 2) // 1000,
        capture=capture, escape=escape, reaction=reaction,
    )


def _pos(record: Mapping[str, Any]) -> PositionState:
    return PositionState(
        zone_ref=str(record.get("zone_ref", "local")), elevation_mm=int(record.get("elevation_mm", 0)),
        cover_milli=max(0, min(1000, int(record.get("cover_milli", 0)))), x_mm=int(record.get("x_mm", 0)),
        y_mm=int(record.get("y_mm", 0)), facing_mdeg=int(record.get("facing_mdeg", 0)) % 360000,
        body_radius_mm=max(50, int(record.get("body_radius_mm", 300))), vx_mmps=int(record.get("vx_mmps", 0)),
        vy_mmps=int(record.get("vy_mmps", 0)), stance=str(record.get("stance", "ready")),
    )


def _loadout_items(equipment_ledger: Mapping[str, Any], person_ref: str) -> Mapping[str, int]:
    rows = equipment_ledger.get("person_loadouts", {})
    row = rows.get(person_ref) if isinstance(rows, Mapping) else None
    if isinstance(row, Mapping) and isinstance(row.get("items"), Mapping):
        return row["items"]
    # Sparse policy assignments remain authoritative. Resolve only the requested
    # person's logical custody rather than expanding the entire 11,691-person
    # equipment ledger merely to answer one availability question.
    try:
        items = effective_person_loadout(equipment_ledger, person_ref).get("items", {})
    except ValueError:
        return {}
    return items if isinstance(items, Mapping) else {}


def _combat_capability(
    person_ref: str, person: Mapping[str, Any], equipment_ledger: Mapping[str, Any],
    *, action_skill: str | None = None,
) -> CapabilityProfile:
    """Capability with live physical carried-mass penalties applied."""
    base = capability_from_person(person, action_skill=action_skill)
    items = _loadout_items(equipment_ledger, person_ref)
    mass = carried_mass_kg(items, _equipment_catalog())
    attrs = _attrs(person)
    load = encumbrance_effects(
        total_mass_kg=mass, strength=int(attrs.get("strength", 0)), endurance=int(attrs.get("endurance", 0))
    )
    move = int(load["movement_factor_milli"]); react = int(load["reaction_factor_milli"])
    return CapabilityProfile(
        offense=base.offense, defense=base.defense, control=base.control,
        mobility=base.mobility * move // 1000, perception=base.perception,
        stealth=base.stealth, capture=base.capture,
        escape=base.escape * move // 1000, reaction=base.reaction * react // 1000,
    )



def _mount_motion_for_state(
    person_ref: str,
    person: Mapping[str, Any],
    equipment_ledger: Mapping[str, Any],
    combatant_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    state = combatant_state if isinstance(combatant_state, Mapping) else {}
    mount = state.get("mount") if isinstance(state.get("mount"), Mapping) else None
    if not isinstance(mount, Mapping) or not bool(mount.get("active", True)) or str(mount.get("status", "active")) != "active":
        return {"mounted": False, "control_milli": 0, "effective_speed_mmps": 0, "condition_milli": 0}
    try:
        loadout = _loadout_items(equipment_ledger, person_ref)
        mass = carried_mass_kg(loadout, _equipment_catalog())
    except (KeyError, TypeError, ValueError):
        mass = 0.0
    return mounted_motion_profile(person, carried_mass_kg=mass, mount_state=mount, terrain_milli=max(250, int(state.get("environment_mounted_milli", 1000))))


def _combat_capability_for_state(
    person_ref: str,
    person: Mapping[str, Any],
    equipment_ledger: Mapping[str, Any],
    combatant_state: Mapping[str, Any] | None,
    *,
    action_skill: str | None = None,
) -> CapabilityProfile:
    base = _combat_capability(person_ref, person, equipment_ledger, action_skill=action_skill)
    state = combatant_state if isinstance(combatant_state, Mapping) else {}
    move_env = max(250, min(1200, int(state.get("environment_movement_milli", 1000))))
    vis_env = max(200, min(1200, int(state.get("environment_visibility_milli", 1000))))
    motion = _mount_motion_for_state(person_ref, person, equipment_ledger, combatant_state)
    if not bool(motion.get("mounted")):
        return CapabilityProfile(
            offense=base.offense, defense=base.defense, control=base.control,
            mobility=base.mobility * move_env // 1000,
            perception=base.perception * vis_env // 1000, stealth=base.stealth,
            capture=base.capture, escape=base.escape * move_env // 1000, reaction=base.reaction,
        )
    control = max(0, min(1450, int(motion.get("control_milli", 0))))
    # Horse speed improves translation and escape, but weapon judgment and
    # reaction remain the rider's existing capabilities constrained by how well
    # the rider can physically control the mount. No Riding skill is introduced.
    mounted_mobility = max(0, (min(6500, int(motion.get("effective_speed_mmps", 0))) - 1500) // 25)
    posture_milli = max(650, min(1050, 650 + control * 350 // 1000))
    return CapabilityProfile(
        offense=base.offense * posture_milli // 1000,
        defense=base.defense * posture_milli // 1000,
        control=base.control * posture_milli // 1000,
        mobility=max(base.mobility, mounted_mobility),
        perception=base.perception,
        stealth=base.stealth,
        capture=base.capture,
        escape=max(base.escape, mounted_mobility),
        reaction=base.reaction * posture_milli // 1000,
    )


def _movement_speed_for_state(
    person_ref: str,
    person: Mapping[str, Any],
    equipment_ledger: Mapping[str, Any],
    combatant_state: Mapping[str, Any] | None,
    capability: CapabilityProfile,
) -> int:
    foot = movement_speed_mmps(capability)
    motion = _mount_motion_for_state(person_ref, person, equipment_ledger, combatant_state)
    return max(foot, int(motion.get("effective_speed_mmps", 0))) if bool(motion.get("mounted")) else foot


def _mounted_weapon_motion_milli(
    person_ref: str,
    person: Mapping[str, Any],
    equipment_ledger: Mapping[str, Any],
    combatant_state: Mapping[str, Any] | None,
    *,
    action_kind: str,
    weapon: Mapping[str, Any] | None,
) -> int:
    if action_kind in {"bow_shot", "hidden_weapon_throw", "unarmed_strike"}:
        return 1000
    motion = _mount_motion_for_state(person_ref, person, equipment_ledger, combatant_state)
    if not bool(motion.get("mounted")):
        return 1000
    speed = max(0, int(motion.get("effective_speed_mmps", 0)))
    control = max(0, min(1450, int(motion.get("control_milli", 0))))
    discipline = str((weapon or {}).get("discipline", "")) if isinstance(weapon, Mapping) else ""
    physical_drive = speed * min(1200, control) // 1200
    if discipline == "spear":
        return max(1000, min(1500, 1000 + physical_drive // 24))
    if discipline == "sword":
        return max(1000, min(1250, 1000 + physical_drive // 42))
    return 1000

def _weapon_condition_milli(equipment_ledger: Mapping[str, Any], person_ref: str, weapon_ref: str) -> int:
    if weapon_ref == "body_unarmed":
        return 1000
    try:
        row = effective_person_loadout(equipment_ledger, person_ref)
    except ValueError:
        return 0
    items = row.get("items", {}) if isinstance(row.get("items"), Mapping) else {}
    if max(0, int(items.get(weapon_ref, 0))) <= 0:
        return 0
    condition = row.get("condition_milli", {}) if isinstance(row.get("condition_milli"), Mapping) else {}
    return max(0, min(1000, int(condition.get(weapon_ref, 1000))))


def improvised_weapon_state_from_scene(
    *, fact_ref: str, holder_ref: str, source_object_fact_ref: str, source_session_ref: str,
    source_location_ref: str, summary: str, form: str, material: str, condition: str,
    condition_milli: int | None = None,
) -> dict[str, Any]:
    """Derive conservative transient combat physics for one established scene prop.

    The scene provides only categorical form/material/condition.  All mass,
    reach, force, handling and durability values are server-owned.  The result
    is combat-local state, never inventory, money, market value, or a durable
    equipment record.
    """
    bases: dict[str, dict[str, Any]] = {
        "small_rigid": {"discipline":"unarmed","mass_kg":0.42,"hands_required":1,"reach_m":0.38,"ideal_range_m":[0.0,0.34],"impact":38,"cut":0,"pierce":0,"penetration":4,"precision":52,"control":46,"guard":28,"recovery_ms":330,"strength_requirement":0,"ready_seconds":0.18,"stow_seconds":0.16,"interception_area_milli":360},
        "short_rigid": {"discipline":"spear","mass_kg":0.90,"hands_required":1,"reach_m":0.72,"ideal_range_m":[0.18,0.62],"impact":46,"cut":0,"pierce":4,"penetration":8,"precision":48,"control":48,"guard":36,"recovery_ms":390,"strength_requirement":10,"ready_seconds":0.22,"stow_seconds":0.20,"interception_area_milli":520},
        "long_rigid": {"discipline":"spear","mass_kg":1.55,"hands_required":2,"reach_m":1.45,"ideal_range_m":[0.55,1.30],"impact":50,"cut":0,"pierce":5,"penetration":9,"precision":45,"control":54,"guard":52,"recovery_ms":470,"strength_requirement":20,"ready_seconds":0.35,"stow_seconds":0.38,"interception_area_milli":900},
        "heavy_rigid": {"discipline":"unarmed","mass_kg":2.60,"hands_required":2,"reach_m":0.62,"ideal_range_m":[0.15,0.54],"impact":60,"cut":0,"pierce":2,"penetration":12,"precision":34,"control":32,"guard":34,"recovery_ms":580,"strength_requirement":32,"ready_seconds":0.42,"stow_seconds":0.48,"interception_area_milli":520},
        "sharp_fragment": {"discipline":"sword","mass_kg":0.18,"hands_required":1,"reach_m":0.34,"ideal_range_m":[0.0,0.28],"impact":16,"cut":34,"pierce":30,"penetration":25,"precision":55,"control":42,"guard":12,"recovery_ms":310,"strength_requirement":0,"ready_seconds":0.16,"stow_seconds":0.12,"interception_area_milli":120},
    }
    base = bases.get(str(form))
    material_factor = {"bamboo":820,"wood":880,"bone":860,"ceramic":900,"metal":1000,"stone":1040}.get(str(material))
    mass_factor = {"bamboo":620,"wood":780,"bone":720,"ceramic":880,"metal":1180,"stone":1350}.get(str(material))
    condition_factor = {"intact":1000,"worn":950,"cracked":820,"broken_piece":800}.get(str(condition))
    starting = {"intact":1000,"worn":780,"cracked":460,"broken_piece":350}.get(str(condition))
    if base is None or material_factor is None or mass_factor is None or condition_factor is None or starting is None:
        raise ValueError("improvised scene prop classification unsupported")
    profile = copy.deepcopy(base)
    profile["mass_kg"] = round(float(profile["mass_kg"]) * mass_factor / 1000.0, 4)
    for key in ("impact","cut","pierce","penetration"):
        profile[key] = max(0, int(profile.get(key,0)) * material_factor * condition_factor // 1_000_000)
    profile.update({
        "combat_identity_kind":"scene_improvised_prop",
        "source_scene_fact_ref":str(fact_ref),
        "holder_ref":str(holder_ref),
        "material":str(material),
        "form":str(form),
    })
    current = max(0, min(1000, int(starting if condition_milli is None else condition_milli)))
    return {
        "kind":"scene_improvised_weapon_state",
        "fact_ref":str(fact_ref),
        "holder_ref":str(holder_ref),
        "source_object_fact_ref":str(source_object_fact_ref),
        "source_session_ref":str(source_session_ref),
        "source_location_ref":str(source_location_ref),
        "summary":str(summary)[:1500],
        "form":str(form),"material":str(material),"condition":str(condition),
        "condition_milli":current,
        "status":"held" if current > 0 else "broken",
        "durable_item_created":False,
        "weapon":profile,
    }


def _improvised_weapon_state(combatant_state: Mapping[str, Any] | None, person_ref: str, weapon_ref: str | None = None) -> Mapping[str, Any] | None:
    if not isinstance(combatant_state, Mapping):
        return None
    row = combatant_state.get("improvised_weapon_state")
    if not isinstance(row, Mapping) or row.get("kind") != "scene_improvised_weapon_state":
        return None
    if str(row.get("holder_ref") or "") != str(person_ref) or str(row.get("status") or "") != "held" or int(row.get("condition_milli",0)) <= 0:
        return None
    if weapon_ref is not None and str(row.get("fact_ref") or "") != str(weapon_ref):
        return None
    return row


def _weapon_owned(equipment_ledger: Mapping[str, Any], person_ref: str, weapon_ref: str, combatant_state: Mapping[str, Any] | None = None) -> bool:
    if _improvised_weapon_state(combatant_state, person_ref, weapon_ref) is not None:
        return True
    return weapon_ref == "body_unarmed" or (
        int(_loadout_items(equipment_ledger, person_ref).get(weapon_ref, 0)) > 0
        and _weapon_condition_milli(equipment_ledger, person_ref, weapon_ref) > 0
    )


def _weapon(weapon_ref: str | None) -> Mapping[str, Any] | None:
    if not weapon_ref or weapon_ref == "body_unarmed":
        return None
    row = _equipment_catalog().get("weapon_catalog", {}).get(weapon_ref)
    return row if isinstance(row, Mapping) else None


def _weapon_for_holder(equipment_ledger: Mapping[str, Any], person_ref: str, weapon_ref: str | None, combatant_state: Mapping[str, Any] | None = None) -> Mapping[str, Any] | None:
    transient = _improvised_weapon_state(combatant_state, person_ref, weapon_ref)
    if isinstance(transient, Mapping):
        base = transient.get("weapon") if isinstance(transient.get("weapon"), Mapping) else None
        if not isinstance(base, Mapping):
            return None
        condition = max(0, min(1000, int(transient.get("condition_milli", 0))))
        out = copy.deepcopy(dict(base))
        if condition < 1000:
            for key in ("impact", "cut", "pierce", "penetration", "precision", "control", "guard", "interception_area_milli"):
                if key in out:
                    out[key] = max(0, int(out.get(key, 0)) * condition // 1000)
        return out
    base = _weapon(weapon_ref)
    if not isinstance(base, Mapping) or not isinstance(weapon_ref, str):
        return base
    condition = _weapon_condition_milli(equipment_ledger, person_ref, weapon_ref)
    if condition <= 0:
        return None
    if condition >= 1000:
        return base
    out = copy.deepcopy(dict(base))
    # Integrity is structural usability. Keep mass/reach fixed, but damaged
    # edges, shafts, guards and launch mechanisms transmit/control less cleanly.
    for key in ("impact", "cut", "pierce", "penetration", "precision", "control", "guard", "interception_area_milli"):
        if key in out:
            out[key] = max(0, int(out.get(key, 0)) * condition // 1000)
    if "projectile_speed_mps" in out:
        out["projectile_speed_mps"] = max(1.0, float(out.get("projectile_speed_mps", 1.0)) * condition / 1000.0)
    return out


def _apply_combat_weapon_wear(
    equipment_ledger: dict[str, Any], *, person_ref: str, weapon_ref: str | None,
    combatant_state: dict[str, Any] | None, event_kind: str,
) -> dict[str, Any] | None:
    transient = _improvised_weapon_state(combatant_state, person_ref, weapon_ref)
    if isinstance(transient, Mapping) and isinstance(combatant_state, dict):
        row = combatant_state.get("improvised_weapon_state")
        if not isinstance(row, dict):
            return None
        before = max(0, min(1000, int(row.get("condition_milli", 0))))
        base_loss = {"weapon_contact_light":70,"weapon_contact_heavy":145}.get(str(event_kind),55)
        material_factor = {"metal":450,"wood":800,"bamboo":850,"bone":1050,"stone":1200,"ceramic":2200}.get(str(row.get("material") or ""),1000)
        form_factor = {"heavy_rigid":700,"short_rigid":900,"small_rigid":1000,"long_rigid":1200,"sharp_fragment":1300}.get(str(row.get("form") or ""),1000)
        loss = max(1, base_loss * material_factor * form_factor // 1_000_000)
        after = max(0, before - loss)
        row["condition_milli"] = after
        if after <= 0:
            row["status"] = "broken"
            if combatant_state.get("ready_weapon_ref") == weapon_ref:
                combatant_state["ready_weapon_ref"] = None
                combatant_state.pop("ready_hands_required", None)
        return {"weapon_ref":weapon_ref,"condition_before_milli":before,"condition_after_milli":after,"event_kind":event_kind,"transient":True}
    return _apply_weapon_wear(equipment_ledger, person_ref=person_ref, weapon_ref=weapon_ref, event_kind=event_kind)


def _apply_weapon_wear(equipment_ledger: dict[str, Any], *, person_ref: str, weapon_ref: str | None, event_kind: str) -> dict[str, Any] | None:
    if not isinstance(weapon_ref, str) or weapon_ref in {"", "body_unarmed"}:
        return None
    loadouts = equipment_ledger.get("person_loadouts", {})
    row = loadouts.get(person_ref) if isinstance(loadouts, Mapping) else None
    if not isinstance(row, dict):
        return None
    items = row.get("items", {}) if isinstance(row.get("items"), Mapping) else {}
    if max(0, int(items.get(weapon_ref, 0))) <= 0:
        return None
    conditions = row.setdefault("condition_milli", {})
    if not isinstance(conditions, dict):
        raise ValueError("jianghu equipment condition invalid")
    before = max(0, min(1000, int(conditions.get(weapon_ref, 1000))))
    after = apply_wear(integrity_milli=before, event_kind=event_kind)
    conditions[weapon_ref] = after
    return {"weapon_ref": weapon_ref, "condition_before_milli": before, "condition_after_milli": after, "event_kind": event_kind}


def _usable_hand_count(person: Mapping[str, Any]) -> int:
    impair = functional_penalties(_wounds(person))
    left = max(int(impair.get("grip_left", 0)), int(impair.get("weapon_control_left", 0))) < 90
    right = max(int(impair.get("grip_right", 0)), int(impair.get("weapon_control_right", 0))) < 90
    return int(left) + int(right)


def _weapon_ready_delay_ms(person: Mapping[str, Any], current_ref: str | None, requested_ref: str, combatant_state: Mapping[str, Any] | None = None) -> int:
    if requested_ref == "body_unarmed" or requested_ref == current_ref:
        return 0
    dexterity = max(0, int(_attrs(person).get("dexterity", 0)))
    speed_factor_milli = max(600, min(1600, 800 + dexterity * 4))
    delay_ms = 0
    transient = _improvised_weapon_state(combatant_state, str(combatant_state.get("improvised_weapon_state",{}).get("holder_ref") or "") if isinstance(combatant_state,Mapping) else "", current_ref)
    current = transient.get("weapon") if isinstance(transient, Mapping) and isinstance(transient.get("weapon"), Mapping) else _weapon(current_ref)
    if isinstance(current, Mapping):
        delay_ms += int(round(transition_seconds(current, action="stow") * 1000))
    requested_transient = None
    if isinstance(combatant_state, Mapping):
        holder = combatant_state.get("improvised_weapon_state",{}).get("holder_ref") if isinstance(combatant_state.get("improvised_weapon_state"),Mapping) else None
        if isinstance(holder,str):
            requested_transient = _improvised_weapon_state(combatant_state, holder, requested_ref)
    requested = requested_transient.get("weapon") if isinstance(requested_transient, Mapping) and isinstance(requested_transient.get("weapon"), Mapping) else _weapon(requested_ref)
    if not isinstance(requested, Mapping):
        return delay_ms
    delay_ms += int(round(transition_seconds(requested, action="ready") * 1000))
    return delay_ms * 1000 // speed_factor_milli


def _ready_melee_weapon_ref(person_ref: str, person: Mapping[str, Any], equipment_ledger: Mapping[str, Any], combatant_state: Mapping[str, Any]) -> str | None:
    explicit = combatant_state.get("ready_weapon_ref")
    if isinstance(explicit, str) and _weapon_owned(equipment_ledger, person_ref, explicit, combatant_state):
        row = _weapon_for_holder(equipment_ledger, person_ref, explicit, combatant_state)
        if isinstance(row, Mapping) and int(row.get("interception_area_milli",0)) > 0:
            return explicit
    return None


def _guard_profile(person_ref: str, person: Mapping[str, Any], equipment_ledger: Mapping[str, Any], combatant_state: Mapping[str, Any]) -> ActionProfile | None:
    ref = _ready_melee_weapon_ref(person_ref, person, equipment_ledger, combatant_state)
    row = _weapon_for_holder(equipment_ledger, person_ref, ref, combatant_state)
    if not isinstance(row, Mapping): return None
    reach = max(0.2, float(row.get("reach_m", 1.0)))
    return ActionProfile(method_ref="guard", effect_kind="physical", delivery="direct", startup_ms=0, external_contact=True,
                         speed_score=_combat_capability(person_ref, person, equipment_ledger, action_skill=str(row.get("discipline", "sword"))).reaction,
                         effect_parameters={"physical_reach_m": reach, "geometry": {"shape":"direct","width_m":0.35,"length_m":reach}})


def _action_rule(action_kind: str, weapon: Mapping[str, Any] | None) -> Mapping[str, Any]:
    rules = _combat_rules().get("actions", {})
    row = rules.get(action_kind) if isinstance(rules, Mapping) else None
    if isinstance(row, Mapping): return row
    if action_kind == "improvised_strike" and isinstance(weapon, Mapping):
        return {"skill":str(weapon.get("discipline") or "unarmed"),"startup_ms":360,"commitment_milli":500,"channels":["blunt","cut","pierce","penetration"]}
    fallback = {
        "staff_strike": {"skill":"spear","startup_ms":350,"commitment_milli":480,"channels":["blunt"]},
        "staff_sweep": {"skill":"spear","startup_ms":470,"commitment_milli":650,"channels":["blunt"]},
        "staff_thrust": {"skill":"spear","startup_ms":330,"commitment_milli":440,"channels":["blunt","pierce"]},
        "staff_butt_strike": {"skill":"spear","startup_ms":280,"commitment_milli":390,"channels":["blunt"]},
        "hidden_weapon_throw": {"skill":"hidden_weapons","startup_ms":430,"commitment_milli":320,"channels":["pierce","penetration"]},
    }
    if action_kind in fallback: return fallback[action_kind]
    if isinstance(weapon, Mapping) and weapon.get("discipline") == "hidden_weapons": return fallback["hidden_weapon_throw"]
    raise ValueError("unsupported physical action")


def _action_profile(action_kind: str, actor: Mapping[str, Any], weapon_ref: str | None, target_position: Mapping[str, Any], actor_position: Mapping[str, Any], *, actor_ref: str | None = None, equipment_ledger: Mapping[str, Any] | None = None, combatant_state: Mapping[str, Any] | None = None) -> tuple[ActionProfile, Mapping[str, Any] | None]:
    weapon = _weapon_for_holder(equipment_ledger, actor_ref, weapon_ref, combatant_state) if isinstance(actor_ref, str) and isinstance(equipment_ledger, Mapping) else _weapon(weapon_ref)
    if weapon_ref not in (None, "", "body_unarmed") and not isinstance(weapon, Mapping): raise ValueError("weapon unresolved")
    rule = _action_rule(action_kind, weapon); discipline = str(rule.get("skill", _discipline_for_action(action_kind, weapon)))
    startup = max(1, int(rule.get("startup_ms", 300))); speed_score = capability_from_person(actor, action_skill=discipline).reaction
    params: dict[str, Any] = {"commitment_milli": max(0, min(1000, int(rule.get("commitment_milli", 400))))}; delivery = "direct"
    if action_kind == "unarmed_strike":
        params.update(physical_reach_m=0.65, geometry={"shape":"direct","width_m":0.35,"length_m":0.85})
    elif action_kind in {"cut","thrust","staff_strike","staff_thrust","staff_butt_strike","improvised_strike"}:
        if not isinstance(weapon, Mapping): raise ValueError("physical weapon required")
        if action_kind.startswith("staff") and weapon_ref != "weapon_staff": raise ValueError("staff action requires staff")
        reach=float(weapon.get("reach_m",1.0)); reach=min(reach,0.85) if action_kind=="staff_butt_strike" else reach
        shape="arc" if action_kind in {"cut","staff_strike","improvised_strike"} else "direct"; geom={"shape":shape,"length_m":reach,"width_m":0.35}
        if shape=="arc": geom["half_angle_deg"]=65
        params.update(physical_reach_m=reach, geometry=geom)
    elif action_kind == "staff_sweep":
        if weapon_ref != "weapon_staff" or not isinstance(weapon, Mapping): raise ValueError("staff sweep requires staff")
        reach=float(weapon.get("reach_m",2.0)); params.update(physical_reach_m=reach, geometry={"shape":"arc","length_m":reach,"width_m":0.42,"half_angle_deg":95})
    elif action_kind in {"bow_shot","hidden_weapon_throw"}:
        if not isinstance(weapon, Mapping): raise ValueError("projectile weapon required")
        expected="bow" if action_kind=="bow_shot" else "hidden_weapons"
        if weapon.get("discipline") != expected: raise ValueError("projectile action discipline mismatch")
        delivery="projectile" if action_kind=="bow_shot" else "thrown"; max_range=float(weapon.get("maximum_range_m",0)); width=0.12 if action_kind=="bow_shot" else 0.16
        projectile_item = _equipment_catalog().get("ammunition_catalog",{}).get("item_arrow") if action_kind=="bow_shot" else weapon
        if not isinstance(projectile_item,Mapping): raise ValueError("projectile definition missing")
        params.update(
            maximum_range_m=max_range, geometry={"shape":"direct","width_m":width,"length_m":max_range},
            projectile_ref="item_arrow" if action_kind=="bow_shot" else weapon_ref,
            projectile_visibility_milli=max(50,min(1000,int(projectile_item.get("visibility_milli",700)))),
            projectile_width_mm=max(1,int(projectile_item.get("width_mm",8))),
            projectile_length_mm=max(1,int(projectile_item.get("length_mm",80))),
            projectile_interception_difficulty_milli=max(200,int(projectile_item.get("interception_difficulty_milli",900))),
            release_concealment_milli=min(800,max(0,int(_skills(actor).get("stealth_scouting",0))*5)) if action_kind=="hidden_weapon_throw" else 0,
        )
        dx=int(target_position.get("x_mm",0))-int(actor_position.get("x_mm",0)); dy=int(target_position.get("y_mm",0))-int(actor_position.get("y_mm",0)); dz=int(target_position.get("elevation_mm",0))-int(actor_position.get("elevation_mm",0))
        dist=max(1, math.isqrt(dx*dx+dy*dy+dz*dz)); projectile_speed=max(1,int(round(float(weapon.get("projectile_speed_mps",1))*1000))); params["projectile"]={"flight_time_ms":dist*1000//projectile_speed}
    else: raise ValueError("unsupported physical action")
    return ActionProfile(method_ref=action_kind,effect_kind="physical",delivery=delivery,startup_ms=startup,external_contact=True,speed_score=speed_score,damage_channels=tuple(str(x) for x in rule.get("channels",[])),effect_parameters=params), weapon


def _participant(ref: str, person: Mapping[str, Any], *, side_ref: str, position: Mapping[str, Any], known_refs: Sequence[str], combatant_state: Mapping[str, Any], action_profile: ActionProfile | None, equipment_ledger: Mapping[str, Any], at_ms: int = 0, intent: str = "attack") -> Participant:
    cap=_combat_capability_for_state(ref,person,equipment_ledger,combatant_state,action_skill=(action_profile.method_ref if action_profile else None)); ready_ref=_ready_melee_weapon_ref(ref,person,equipment_ledger,combatant_state); skills=_skills(person)
    prefs=["evade","reposition"]; reactive: tuple[ReactiveDefense,...]=()
    if ready_ref:
        prefs.extend(["parry","deflect","block","counter_intercept","brace"]); reactive=(ReactiveDefense(defense_ref=ready_ref,defense_kind="weapon_guard"),)
    elif int(skills.get("unarmed",0))>0:
        prefs.extend(["block","brace","counter_intercept"]); reactive=(ReactiveDefense(defense_ref="body_unarmed",defense_kind="physical_guard"),)
    personal=resolve_individual_doctrine(person.get("combat_doctrine_ref")) if isinstance(person.get("combat_doctrine_ref"),str) else None
    defense_doctrine=personal.get("defense",{}) if isinstance(personal,Mapping) and isinstance(personal.get("defense"),Mapping) else {}
    primary=str(defense_doctrine.get("primary_response") or "adaptive")
    counterattack_posture=str(defense_doctrine.get("counterattack_posture") or "selective")
    preferred={
        "distance": ("reposition","evade"),
        "dodge": ("evade","reposition"),
        "parry": ("parry","deflect"),
        "block": ("block","brace"),
    }.get(primary,())
    if preferred:
        prefs=[*[response for response in preferred if response in prefs],*[response for response in prefs if response not in preferred]]
    saved_status=[str(x) for x in combatant_state.get("status_families",[]) if isinstance(x,str)]; wounds=_wounds(person); status=tuple(dict.fromkeys(saved_status+list(combat_status_families(wounds))))
    health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
    return Participant(participant_ref=ref,authoritative_owner_ref=ref,side_ref=side_ref,sequence=0,representation="exact",capability=cap,personnel=PersonnelState(total=1,active=1),position=_pos(position),
        information=InformationState(observed_refs=tuple(dict.fromkeys(str(x) for x in known_refs if isinstance(x,str))),confidence_milli=max(0,min(1000,int(combatant_state.get("awareness_confidence_milli",1000)))),concealment_milli=max(0,min(1000,int(combatant_state.get("concealment_milli",0)))),surprise_milli=max(0,min(1000,int(combatant_state.get("surprise_milli",0))))),
        intent=CombatIntent(action=intent),initiative=max(0,cap.reaction+cap.mobility),readiness=100,morale=100,cohesion=100,action_profile=action_profile,reactive_defenses=reactive,
        active_defense_load_milli=max(0,min(1000,int(combatant_state.get("defense_state",{}).get("load_milli",0)))),balance_milli=max(0,min(1000,int(combatant_state.get("balance_milli",1000)))),limb_commitment_milli=max(0,min(1000,int(combatant_state.get("limb_commitment_milli",0)))),
        recovery_remaining_ms=max(0,int(combatant_state.get("recovery_until_ms",0))-max(0,int(at_ms))),weapon_position=str(combatant_state.get("weapon_position","guard")),status_families=status,physical_defense_preferences=tuple(dict.fromkeys(prefs)),counterattack_posture=counterattack_posture,
        health_model="anatomy",body_mass_grams=max(1000,int(float(person.get("body_mass_kg",70))*1000)),physiology_endurance=max(0,int(_attrs(person).get("endurance",0))),physiology_willpower=max(0,int(_attrs(person).get("willpower",0))),blood_lost_ml=max(0,int(health.get("blood_lost_ml",0))),wounds=tuple(wounds))


def _default_weapon_for(person_ref: str, person: Mapping[str, Any], equipment_ledger: Mapping[str, Any], *, target_distance_mm: int, role: str | None = None) -> tuple[str, str]:
    items=_loadout_items(equipment_ledger,person_ref); weapons=_equipment_catalog().get("weapon_catalog",{})
    engagement=_engagement_doctrine_for(person)
    range_preference=str(engagement.get("range_preference") or "adaptive")
    movement_economy=str(engagement.get("movement_economy") or "balanced")
    movement_shift={"minimal_required":-500,"balanced":0,"mobile":500}.get(movement_economy,0)
    bow_threshold=max(700,{"ranged":1200,"adaptive":3500,"reach":5000,"close":9000}.get(range_preference,3500)+movement_shift)
    thrown_threshold=max(500,{"ranged":700,"adaptive":2500,"reach":4200,"close":7500}.get(range_preference,2500)+movement_shift)
    bows=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline")=="bow" and _weapon_condition_milli(equipment_ledger,person_ref,str(ref))>0]
    def conditioned(ref: str) -> Mapping[str, Any]:
        row=_weapon_for_holder(equipment_ledger,person_ref,ref)
        return row if isinstance(row,Mapping) else {}
    def effective_skill(ref: str) -> int:
        discipline=str(weapons[ref].get("discipline") or "")
        return int(_skills(person).get(discipline,0))*_weapon_condition_milli(equipment_ledger,person_ref,ref)//1000
    def projectile_reaches(ref: str) -> bool:
        row=conditioned(ref)
        try:
            maximum_range_mm=max(0,int(round(float(row.get("maximum_range_m",0))*1000)))
        except (TypeError,ValueError):
            return False
        return maximum_range_mm>0 and target_distance_mm<=maximum_range_mm
    bows=[ref for ref in bows if projectile_reaches(ref)]
    if target_distance_mm>bow_threshold and bows and int(items.get("item_arrow",0))>0:
        return "bow_shot", max(bows,key=lambda ref:(effective_skill(ref),int(conditioned(ref).get("precision",0)),ref))
    thrown=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline")=="hidden_weapons" and _weapon_condition_milli(equipment_ledger,person_ref,str(ref))>0 and projectile_reaches(str(ref))]
    if target_distance_mm>thrown_threshold and thrown:
        return "hidden_weapon_throw", max(thrown,key=lambda ref:(effective_skill(ref),int(conditioned(ref).get("precision",0)),ref))
    melee=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline") in {"sword","spear"} and _weapon_condition_milli(equipment_ledger,person_ref,str(ref))>0]
    if melee:
        if range_preference=="reach":
            ref=max(melee,key=lambda item_ref:(float(weapons[item_ref].get("reach_m",0)),effective_skill(item_ref),int(conditioned(item_ref).get("control",0)),item_ref))
        else:
            ref=max(melee,key=lambda item_ref:(effective_skill(item_ref),int(conditioned(item_ref).get("control",0)),item_ref))
        profile=conditioned(ref)
        if ref=="weapon_staff": return ("staff_sweep" if role in {"control","shape"} else "staff_strike"),ref
        return ("thrust" if int(profile.get("pierce",0))>=int(profile.get("cut",0)) else "cut"),ref
    if thrown:
        return "hidden_weapon_throw", max(thrown,key=lambda ref:(effective_skill(ref),int(conditioned(ref).get("precision",0)),ref))
    return "unarmed_strike","body_unarmed"


def _hold_position_weapon_for(
    person_ref: str, person: Mapping[str, Any], equipment_ledger: Mapping[str, Any], *, target_distance_mm: int
) -> tuple[str, str] | None:
    """Choose an attack that does not require leaving the actor's current position.

    Used by Wei's outnumbered retinue sector defense. A held sector may strike a
    target already inside melee reach or throw a carried hidden weapon within
    its physical range, but it will not chase merely to create contact.
    """
    items=_loadout_items(equipment_ledger,person_ref); weapons=_equipment_catalog().get("weapon_catalog",{})
    melee=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline") in {"sword","spear"} and _weapon_condition_milli(equipment_ledger,person_ref,str(ref))>0]
    reachable=[]
    for ref in melee:
        reach=max(0,int(round(float(weapons[ref].get("reach_m",0))*1000)))
        if target_distance_mm<=reach:
            reachable.append(ref)
    if reachable:
        ref=max(reachable,key=lambda item_ref:(int(_skills(person).get(str(weapons[item_ref].get("discipline")),0)),int(weapons[item_ref].get("control",0)),item_ref))
        if ref=="weapon_staff": return "staff_strike",ref
        return ("thrust" if int(weapons[ref].get("pierce",0))>=int(weapons[ref].get("cut",0)) else "cut"),ref
    thrown=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline")=="hidden_weapons" and target_distance_mm<=max(0,int(round(float(weapons[ref].get("maximum_range_m",0))*1000)))]
    if thrown:
        return "hidden_weapon_throw",max(thrown,key=lambda ref:(int(_skills(person).get("hidden_weapons",0)),int(weapons[ref].get("precision",0)),ref))
    return None


def _npc_poison_for(
    person_ref: str, target: Mapping[str, Any], equipment_ledger: Mapping[str, Any], *,
    action_kind: str, weapon_ref: str, intent: str,
) -> str | None:
    """Choose a carried poison deterministically, or deliberately use a plain projectile.

    Carrying poison is the capability gate. Lethal intent prefers the strongest
    lethal registered preparation; disable intent prefers control preparations.
    Once the chosen poison already has a substantial active/pending burden on the
    target, the actor conserves doses and throws plain needles instead.
    """
    if action_kind != "hidden_weapon_throw" or weapon_ref not in {"weapon_needle", "weapon_throwing_knife"}:
        return None
    items = _loadout_items(equipment_ledger, person_ref)
    registered = _load("poisons.json").get("poisons", {})
    if not isinstance(registered, Mapping):
        return None
    priorities = (
        ("cardiotoxic", "neurotoxic", "anticoagulant", "paralytic", "sedative")
        if intent == "lethal"
        else ("paralytic", "sedative")
    )
    active = target.get("poison_burdens", {}) if isinstance(target.get("poison_burdens"), Mapping) else {}
    pending = target.get("pending_poison_burdens", {}) if isinstance(target.get("pending_poison_burdens"), Mapping) else {}
    for poison_ref in priorities:
        if poison_ref not in registered or int(items.get(f"poison_{poison_ref}", 0)) <= 0:
            continue
        active_burden = max(0, int(active.get(poison_ref, 0)))
        pending_burden = pending_poison_burden(pending, poison_ref)
        if active_burden + pending_burden >= 100:
            continue
        return poison_ref
    return None


def initialize_combat(*, combat_ref: str, side_a_refs: Sequence[str], side_b_refs: Sequence[str], people: Mapping[str, Mapping[str, Any]], zone_ref: str, started_at: str, objective: Mapping[str, Any], awareness_mode: str = "mutual", initial_range_band: int = 1, obstacles: Sequence[Mapping[str, Any]] = (), awareness_evidence: Mapping[str, Any] | None = None, equipment_ledger: Mapping[str, Any] | None = None, initial_ready_weapons: Mapping[str, str] | None = None, mount_assignments: Mapping[str, Mapping[str, Any]] | None = None, environment: Mapping[str, Any] | None = None, reinforcement_delays_ms: Mapping[str, int] | None = None) -> dict[str, Any]:
    a=tuple(dict.fromkeys(str(x) for x in side_a_refs)); b=tuple(dict.fromkeys(str(x) for x in side_b_refs))
    if not a or not b or set(a)&set(b): raise ValueError("combat sides invalid")
    if any(ref not in people for ref in a+b): raise ValueError("combat participant unresolved")
    if awareness_mode not in {"mutual","side_a_ambush","side_b_ambush"}: raise ValueError("awareness mode invalid")
    if awareness_mode!="mutual" and not isinstance(awareness_evidence,Mapping): raise ValueError("ambush requires derived awareness evidence")
    side_map={ref:"side_a" for ref in a}; side_map.update({ref:"side_b" for ref in b}); positions=initial_positions(side_by_participant=side_map,zone_ref=zone_ref,initial_range_band=initial_range_band); state={}
    env=copy.deepcopy(dict(environment)) if isinstance(environment,Mapping) else {}
    env_obstacles=env.get("obstacles",[]) if isinstance(env.get("obstacles"),Sequence) and not isinstance(env.get("obstacles"),(str,bytes,bytearray)) else []
    combined_obstacles=[copy.deepcopy(dict(row)) for row in obstacles if isinstance(row,Mapping)] + [copy.deepcopy(dict(row)) for row in env_obstacles if isinstance(row,Mapping)]
    terrain=str(env.get("terrain") or "plain")
    elevation_step={"hills":500,"mountain":1800,"highland":900}.get(terrain,0)
    if elevation_step:
        for ref in a: positions[ref]["elevation_mm"] = elevation_step
    explicit_ready=initial_ready_weapons if isinstance(initial_ready_weapons,Mapping) else {}
    mounts=mount_assignments if isinstance(mount_assignments,Mapping) else {}
    reinforcements=reinforcement_delays_ms if isinstance(reinforcement_delays_ms,Mapping) else {}
    if any(str(ref) not in set(a+b) for ref in mounts): raise ValueError("mount assignment participant unresolved")
    if any(str(ref) not in set(a+b) for ref in reinforcements): raise ValueError("reinforcement participant unresolved")
    for ref in a+b:
        enemy_refs=list(b if ref in a else a); observed=enemy_refs if awareness_mode=="mutual" else []
        surprised=(awareness_mode=="side_a_ambush" and ref in b) or (awareness_mode=="side_b_ambush" and ref in a)
        if not surprised and awareness_mode!="mutual": observed=enemy_refs
        ready_ref=None
        requested=explicit_ready.get(ref)
        if isinstance(requested,str) and isinstance(equipment_ledger,Mapping) and _weapon_owned(equipment_ledger,ref,requested):
            ready_ref=requested
        elif not surprised and isinstance(equipment_ledger,Mapping):
            nearest=min((planar_distance_mm(positions[ref],positions[enemy]) for enemy in enemy_refs),default=0)
            _kind,candidate=_default_weapon_for(ref,people[ref],equipment_ledger,target_distance_mm=nearest)
            if candidate!="body_unarmed" and _weapon_owned(equipment_ledger,ref,candidate): ready_ref=candidate
        reinforce=max(0,int(reinforcements.get(ref,0)))
        status_families=["reinforcing"] if reinforce>0 else []
        state[ref]={"defense_state":{"load_milli":0,"last_at_ms":-1_000_000_000,"recent_attackers":{}},"balance_milli":1000,"limb_commitment_milli":0,"recovery_until_ms":0,"weapon_position":"guard","ready_weapon_ref":ready_ref,"status_families":status_families,"reinforcement_at_ms":reinforce,"surprise_milli":700 if surprised else 0,"observed_refs":([] if reinforce>0 else observed),"awareness_confidence_milli":0 if reinforce>0 else (1000 if observed else 0),"qi_allocation_milli":{},"environment_movement_milli":max(250,int(env.get("movement_milli",1000))),"environment_mounted_milli":max(250,int(env.get("mounted_milli",1000))),"environment_visibility_milli":max(200,int(env.get("visibility_milli",1000))),"concealment_milli":max(0,int(env.get("concealment_milli",0)))}
        mount=mounts.get(ref)
        if isinstance(mount,Mapping):
            owner=str(mount.get("owner_faction_ref") or people[ref].get("faction_ref") or "")
            if not owner: raise ValueError("mount owner faction unresolved")
            state[ref]["mount"]={
                "kind":"riding_horse","owner_faction_ref":owner,"condition_milli":max(1,min(1000,int(mount.get("condition_milli",1000)))),
                "status":"active","active":True,"inventory_debited":bool(mount.get("inventory_debited",False)),"service_loss_pending":False,
            }
    return {"combat_id":combat_ref,"status":"active","started_at":started_at,"elapsed_ms":0,"zone_ref":zone_ref,"sides":{"side_a":list(a),"side_b":list(b)},"objective":copy.deepcopy(dict(objective)),"positions":positions,"obstacles":combined_obstacles,"environment":env,"combatants":state,"team_plans":{},"awareness_mode":awareness_mode}


def _active(person: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}; statuses=set(state.get("status_families",[]))
    return health.get("status") not in {"dead","incapacitated"} and int(health.get("consciousness",100))>0 and not ({"dead","unconscious","incapacitated","escaped","reinforcing"}&statuses)


def _side_of(combat: Mapping[str, Any], ref: str) -> str:
    for side,refs in combat.get("sides",{}).items():
        if ref in refs: return str(side)
    raise KeyError(ref)


def _observe_visible_enemies(combat: dict[str, Any], *, actor_ref: str, enemy_refs: Sequence[str], people: Mapping[str, Mapping[str, Any]], at_ms: int) -> list[str]:
    state=combat["combatants"][actor_ref]; known=set(str(x) for x in state.get("observed_refs",[]) if isinstance(x,str)); actor_cap=capability_from_person(people[actor_ref])
    for enemy_ref in enemy_refs:
        if enemy_ref in known: continue
        if not line_of_sight_clear(combat["positions"],actor_ref=actor_ref,target_ref=enemy_ref,obstacles=combat.get("obstacles",[])): continue
        enemy_cap=capability_from_person(people[enemy_ref]); distance_m=planar_distance_mm(combat["positions"][actor_ref],combat["positions"][enemy_ref])/1000.0
        concealment=max(0,int(combat["combatants"][enemy_ref].get("concealment_milli",0))+enemy_cap.stealth*3)
        visibility=max(200,min(1200,int((combat.get("environment") or {}).get("visibility_milli",1000)))) if isinstance(combat.get("environment"),Mapping) else 1000
        detection=(actor_cap.perception*5+actor_cap.reaction*2-int(distance_m*5))*visibility//1000
        if detection>=concealment: known.add(enemy_ref)
    state["observed_refs"]=sorted(known)
    if known: state["awareness_confidence_milli"]=1000; state["surprise_milli"]=max(0,int(state.get("surprise_milli",0))-max(200,at_ms//5))
    return sorted(known & set(enemy_refs))


def _refresh_team_plan(combat: dict[str, Any], *, side: str, people: Mapping[str, Mapping[str, Any]], doctrine: Mapping[str, Any] | None) -> dict[str, Any]:
    members=[ref for ref in combat["sides"][side] if _active(people[ref],combat["combatants"][ref])]; other="side_b" if side=="side_a" else "side_a"; enemies=[ref for ref in combat["sides"][other] if _active(people[ref],combat["combatants"][ref])]
    known:set[str]=set(); at_ms=int(combat.get("elapsed_ms",0))
    for member in members: known.update(_observe_visible_enemies(combat,actor_ref=member,enemy_refs=enemies,people=people,at_ms=at_ms))
    previous=combat.get("team_plans",{}).get(side); reasons=replan_reasons(previous,active_member_refs=members,known_enemy_refs=sorted(known),positions=combat["positions"],objective_kind=str(combat.get("objective",{}).get("kind","eliminate")))
    if reasons:
        plan=plan_team_exchange(side_ref=side,member_refs=members,known_enemy_refs=sorted(known),records=people,positions=combat["positions"],obstacles=combat.get("obstacles",[]),objective_kind=str(combat.get("objective",{}).get("kind","eliminate")),doctrine=doctrine,at_ms=at_ms); plan["replan_reasons"]=list(reasons); combat.setdefault("team_plans",{})[side]=plan
    return combat.get("team_plans",{}).get(side,{})


def _ready_team_assignment(plan: Mapping[str, Any] | None, actor_ref: str, *, at_ms: int) -> Mapping[str, Any]:
    """Return a compiled assignment only after team communication is ready.

    Coordination latency belongs to the shared plan, not to an individual's
    physical action timeline. Before the plan is ready, fighters may still act
    from their own lawful perception/defaults; afterward the team assignment
    can guide target/action selection.
    """
    if not isinstance(plan, Mapping):
        return {}
    generated_at = max(0, int(plan.get("generated_at_ms", 0)))
    latency = max(0, int(plan.get("coordination_latency_ms", 0)))
    if max(0, int(at_ms)) < generated_at + latency:
        return {}
    assignments = plan.get("assignments")
    if not isinstance(assignments, Mapping):
        return {}
    row = assignments.get(actor_ref)
    return row if isinstance(row, Mapping) else {}


def _apply_physiology(person: dict[str, Any], *, elapsed_seconds: int, at_iso: str | None = None) -> dict[str, Any]:
    """Project immediate physiology without inventing contact time.

    Elapsed combat physiology is owned by ``_settle_combat_physiology_until``.
    Contact callers use zero elapsed seconds here so a hit can change anatomy and
    lethal state immediately without also adding a synthetic second of bleeding.
    """
    health=copy.deepcopy(person.get("health",{})); wounds=health.get("injuries",[]) if isinstance(health.get("injuries"),list) else []
    poison_effects=current_poison_effects(person.get("poison_burdens") if isinstance(person.get("poison_burdens"),Mapping) else {})
    medicine=person.get("medicine_state") if isinstance(person.get("medicine_state"),Mapping) else None
    modifiers=active_recovery_modifiers(medicine,at=at_iso) if medicine is not None and isinstance(at_iso,str) else {}
    toxicity=toxicity_consequences_current(medicine)
    if max(0,int(toxicity.get("shock_contribution",0)))>0:
        poison_effects=dict(poison_effects); poison_effects["shock_pressure"]=max(0,int(poison_effects.get("shock_pressure",0)))+max(0,int(toxicity.get("shock_contribution",0)))
    physiology=settle_physiology(
        body_mass_kg=float(person.get("body_mass_kg",70)),wounds=wounds,blood_lost_ml=int(health.get("blood_lost_ml",0)),
        elapsed_seconds=max(0,elapsed_seconds),endurance=int(_attrs(person).get("endurance",0)),willpower=int(_attrs(person).get("willpower",0)),
        medicine_modifiers=modifiers,poison_effects=poison_effects,
    )
    health["injuries"]=[copy.deepcopy(dict(w)) for w in physiology.get("wounds_after",wounds) if isinstance(w,Mapping)]
    health["blood_lost_ml"]=physiology["blood_lost_ml"]; health["shock"]=physiology["shock"]; health["consciousness"]=max(0,min(100,physiology["consciousness"])); lethal=physiology["lethal_state"]
    dying_raw=health.get("dying_since") if isinstance(health.get("dying_since"),str) else None
    if lethal=="dead":
        health["status"]="dead"; health["consciousness"]=0; health.pop("dying_since",None)
    elif lethal=="dying" or dying_raw is not None:
        if dying_raw is None and isinstance(at_iso,str):
            dying_raw=at_iso; health["dying_since"]=at_iso
        if isinstance(dying_raw,str) and isinstance(at_iso,str):
            try:
                now=datetime.fromisoformat(at_iso.removeprefix("SE-")); dying=datetime.fromisoformat(dying_raw.removeprefix("SE-"))
            except ValueError:
                now=dying=None
            if now is not None and dying is not None and now>=dying+timedelta(minutes=dying_deadline_minutes()):
                health["status"]="dead"; health["consciousness"]=0; health.pop("dying_since",None)
            else:
                health["status"]="incapacitated"
        else:
            health["status"]="incapacitated"
    elif lethal in {"critical","unconscious"}:
        health["status"]="incapacitated"; health.pop("dying_since",None)
    elif health["injuries"] or int(health.get("blood_lost_ml",0))>0:
        health["status"]="injured"; health.pop("dying_since",None)
    else:
        health["status"]="ready"; health.pop("dying_since",None)
    person["health"]=health; return physiology


def _target_difficulty(*, structure_ref: str | None, hit_zone: str, distance_m: float, target_speed: int, visibility_milli: int, action_kind: str) -> int:
    structure_penalty=0
    if structure_ref:
        row=structure_definition(structure_ref); zone=str(row.get("zone","")) if isinstance(row,Mapping) else ""
        structure_penalty={"eyes":95,"throat":80,"wrist":65,"hand":62,"elbow":55,"knee":50,"ankle":58,"heart":75}.get(structure_ref,{"eyes":95,"neck":75,"wrist":65,"hands":62,"elbow":55,"knee":50,"ankle":58,"chest":45}.get(zone,40))
    zone_penalty={"eyes":65,"neck":45,"wrist":40,"hands":38,"knee":32,"ankle":35,"mount":-18}.get(hit_zone,0); distance_penalty=int(max(0.0,distance_m-1.0)*(2.0 if action_kind=="bow_shot" else 8.0)); movement_penalty=max(0,target_speed)//5; visibility_penalty=max(0,1000-max(0,min(1000,visibility_milli)))//10
    return structure_penalty+zone_penalty+distance_penalty+movement_penalty+visibility_penalty


def _precision_margin(*, actor: Mapping[str, Any], weapon: Mapping[str, Any] | None, action_kind: str, structure_ref: str | None, hit_zone: str, distance_m: float, target: Mapping[str, Any], visibility_milli: int, bow_accuracy_score: int | None = None, target_speed: int | None = None, mounted_control_milli: int | None = None, qi_result: Mapping[str, Any] | None = None) -> int:
    attrs=_qi_effective_attrs(actor,qi_result); skills=_skills(actor); discipline=_discipline_for_action(action_kind,weapon); base=(int(skills.get(discipline,0))*45+int(attrs.get("dexterity",0))*25+int(attrs.get("perception",0))*25+int(attrs.get("intelligence",0))*5)//100
    if isinstance(weapon,Mapping): base += int(weapon.get("precision",0))//3
    if bow_accuracy_score is not None: base=(base+max(0,int(bow_accuracy_score)))//2
    base=base*fatigue_performance_milli(int(actor.get("fatigue_milli",0)))//1000
    if action_kind=="bow_shot" and mounted_control_milli is not None:
        control=max(0,min(1450,int(mounted_control_milli)))
        # Shooting from a moving platform is harder, but existing Dexterity,
        # Perception and body function can reduce the penalty. There is no
        # mounted-archery or Riding proficiency.
        base-=max(0,42-control//30)
    speed=capability_from_person(target).mobility if target_speed is None else max(0,int(target_speed))
    return base-_target_difficulty(structure_ref=structure_ref,hit_zone=hit_zone,distance_m=distance_m,target_speed=speed,visibility_milli=visibility_milli,action_kind=action_kind)


def _deterministic_sign(*parts: object) -> int:
    return -1 if hashlib.sha256("|".join(str(part) for part in parts).encode()).digest()[0]&1 else 1


def _trajectory_with_error(trajectory: Mapping[str, Any], *, error_mm: int, seed_parts: Sequence[object]) -> dict[str, int]:
    out={key:int(value) for key,value in trajectory.items()}
    if error_mm<=0: return out
    dx=out["aim_x_mm"]-out["launch_x_mm"]; dy=out["aim_y_mm"]-out["launch_y_mm"]; length=max(1,math.isqrt(dx*dx+dy*dy)); sign=_deterministic_sign(*seed_parts); out["aim_x_mm"] += -dy*error_mm*sign//length; out["aim_y_mm"] += dx*error_mm*sign//length; return out


def _commit_projectile_resources(
    equipment_ledger: dict[str, Any], *, actor_ref: str, action_kind: str,
    weapon_ref: str, poison_ref: str | None,
) -> dict[str, Any]:
    row=equipment_ledger.get("person_loadouts",{}).get(actor_ref)
    if not isinstance(row,dict): return {"ok":False,"reason":"loadout_unresolved"}
    items=dict(row.get("items",{})); projectile_ref="item_arrow" if action_kind=="bow_shot" else weapon_ref
    if int(items.get(projectile_ref,0))<=0: return {"ok":False,"reason":"no_ammunition","projectile_ref":projectile_ref}
    poison_item=None
    if poison_ref not in (None,""):
        if action_kind!="hidden_weapon_throw" or weapon_ref not in {"weapon_needle","weapon_throwing_knife"}:
            return {"ok":False,"reason":"poison_projectile_incompatible"}
        poison_item=f"poison_{poison_ref}"
        if int(items.get(poison_item,0))<=0: return {"ok":False,"reason":"poison_dose_unavailable","poison_item_ref":poison_item}
    # Resource commitment happens at release.  Poison is consumed even when the
    # subsequently traced projectile misses, is evaded, or is intercepted.
    items[projectile_ref]=int(items[projectile_ref])-1
    if items[projectile_ref]<=0: items.pop(projectile_ref,None)
    if poison_item:
        items[poison_item]=int(items[poison_item])-1
        if items[poison_item]<=0: items.pop(poison_item,None)
    row["items"]=items
    return {"ok":True,"projectile_ref":projectile_ref,"poison_ref":poison_ref,"poison_dose_consumed":bool(poison_item)}


def _projectile_interception(
    *, defender_ref: str, defender: Mapping[str, Any], defender_state: Mapping[str, Any],
    defender_capability: CapabilityProfile, equipment_ledger: Mapping[str, Any],
    decision: Any, profile: ActionProfile, trajectory: Mapping[str, Any],
    combat_id: str, attacker_ref: str, at_ms: int,
) -> dict[str, Any]:
    if not decision.detected or decision.response not in {"parry","deflect","block","counter_intercept"}:
        return {"outcome":"not_attempted","trajectory":dict(trajectory),"speed_factor_milli":1000}
    ready_ref=_ready_melee_weapon_ref(defender_ref,defender,equipment_ledger,defender_state)
    ready=_weapon_for_holder(equipment_ledger, defender_ref, ready_ref, defender_state)
    if not isinstance(ready,Mapping):
        return {"outcome":"not_attempted","trajectory":dict(trajectory),"speed_factor_milli":1000}
    params=profile.effect_parameters if isinstance(profile.effect_parameters,Mapping) else {}
    difficulty=max(200,int(params.get("projectile_interception_difficulty_milli",900)))
    area=max(0,int(ready.get("interception_area_milli",0)))
    discipline=str(ready.get("discipline","unarmed")); skill=max(0,int(_skills(defender).get(discipline,0)))
    score=(skill*5+int(defender_capability.reaction)*4+int(defender_capability.control)*3+max(0,int(decision.detection_margin))*2+area//5)
    # Interception quality consumes the same shared reaction budget as every
    # other physical defense. A rapid barrage or conflicting attackers can
    # therefore make even a skilled weapon interceptor late or incomplete.
    availability=max(60,min(1000,int(getattr(decision,"reaction_availability_milli",1000))))
    score=score*availability//1000
    margin=score-difficulty
    if margin>=180:
        return {"outcome":"clean","weapon_ref":ready_ref,"margin":margin,"trajectory":None,"speed_factor_milli":0}
    if margin>=0:
        # A partial contact redirects the projectile physically and sheds speed;
        # the altered line is retraced, so it may hit terrain/another body/nothing.
        error=max(220,900-margin*3)
        altered=_trajectory_with_error(trajectory,error_mm=error,seed_parts=(combat_id,attacker_ref,defender_ref,at_ms,"partial_intercept"))
        return {"outcome":"partial","weapon_ref":ready_ref,"margin":margin,"trajectory":altered,"speed_factor_milli":max(250,700-margin)}
    return {"outcome":"failed","weapon_ref":ready_ref,"margin":margin,"trajectory":dict(trajectory),"speed_factor_milli":1000}



def _combat_timestamp(combat: Mapping[str, Any], at_ms: int) -> str:
    raw=str(combat.get("started_at") or "").removeprefix("SE-")
    try:
        base=datetime.fromisoformat(raw)
    except ValueError:
        # Unit fixtures may use opaque labels. Production commands always pass
        # a campaign ISO timestamp; use a deterministic neutral epoch in tests.
        base=datetime(1,1,1)
    return (base+timedelta(milliseconds=max(0,int(at_ms)))).isoformat()


def _combat_load_factor(person_ref: str, person: Mapping[str, Any], equipment_ledger: Mapping[str, Any]) -> int:
    try:
        items=effective_person_loadout(equipment_ledger,person_ref).get("items",{})
        mass=carried_mass_kg(items if isinstance(items,Mapping) else {},_equipment_catalog())
    except (KeyError,TypeError,ValueError):
        mass=0.0
    attrs=_attrs(person)
    effects=encumbrance_effects(total_mass_kg=mass,strength=int(attrs.get("strength",0)),endurance=int(attrs.get("endurance",0)))
    return max(500,min(5000,int(effects.get("fatigue_cost_milli",1000))))


def _combat_exertion(*, person_ref: str, person: dict[str, Any], equipment_ledger: Mapping[str, Any],
    commitment_milli: int, movement_mm: int=0, action_kind: str | None=None, weapon: Mapping[str,Any] | None=None, mounted: bool=False) -> dict[str,int]:
    raw=movement_work_points(distance_mm=max(0,int(movement_mm)),mounted=bool(mounted))
    if action_kind:
        raw+=action_work_points(action_kind=action_kind,person=person,weapon=weapon,commitment_milli=commitment_milli)
    else:
        raw+=4+(max(0,min(1000,int(commitment_milli)))+159)//160
    load=_combat_load_factor(person_ref,person,equipment_ledger)
    added=apply_exertion(person,raw_work_points=raw,load_factor_milli=load)
    return {"raw_work_points":raw,"load_factor_milli":load,"added_milli":added}


def _defense_exertion(*, person_ref: str, person: dict[str,Any], equipment_ledger: Mapping[str,Any],
    response: str, movement_mm: int) -> dict[str,int]:
    raw=defense_work_points(response=response,movement_mm=movement_mm)
    load=_combat_load_factor(person_ref,person,equipment_ledger)
    added=apply_exertion(person,raw_work_points=raw,load_factor_milli=load) if raw>0 else 0
    return {"raw_work_points":raw,"load_factor_milli":load,"added_milli":added}


def _settle_guard_exertion(*, combat: dict[str,Any], people: dict[str,dict[str,Any]], equipment_ledger: Mapping[str,Any], elapsed_ms: int) -> None:
    if elapsed_ms<=0:return
    for ref,person in people.items():
        state=combat.get("combatants",{}).get(ref)
        if not isinstance(state,dict) or not _active(person,state):continue
        ready_ref=_ready_melee_weapon_ref(ref,person,equipment_ledger,state)
        ready=_weapon_for_holder(equipment_ledger, ref, ready_ref, state) if ready_ref else None
        held_mass=max(0.0,float(ready.get("mass_kg",0) or 0)) if isinstance(ready,Mapping) else 0.0
        attrs=_attrs(person); load=_combat_load_factor(ref,person,equipment_ledger)
        contribution=guard_work_fraction(elapsed_ms=elapsed_ms,endurance=max(1,int(attrs.get("endurance",0))),held_mass_kg=held_mass,load_factor_milli=load)
        frac_num=max(0,int(state.get("guard_exertion_fraction_numerator",0)))
        frac_den=max(1,int(state.get("guard_exertion_fraction_denominator",1)))
        accumulator=Fraction(max(0,int(state.get("guard_exertion_milli",0))),1)+Fraction(frac_num,frac_den)+contribution
        whole=int(accumulator//1000); remainder=accumulator-Fraction(whole*1000,1); rem=int(remainder); fractional=remainder-rem
        if whole:person["fatigue_milli"]=max(0,int(person.get("fatigue_milli",0)))+whole
        if rem:state["guard_exertion_milli"]=rem
        else:state.pop("guard_exertion_milli",None)
        if fractional:
            state["guard_exertion_fraction_numerator"]=int(fractional.numerator)
            state["guard_exertion_fraction_denominator"]=int(fractional.denominator)
        else:
            state.pop("guard_exertion_fraction_numerator",None); state.pop("guard_exertion_fraction_denominator",None)



def _settle_combat_physiology_until(
    combat: dict[str, Any], people: dict[str, dict[str, Any]], *, target_ms: int,
    equipment_ledger: Mapping[str, Any] | None = None,
) -> None:
    """Advance guard and bodies on one authoritative combat microclock.

    Guard expenditure uses exact milliseconds. Body physiology advances only on
    whole-second crossings of that same clock, preventing each weapon contact or
    short exchange from inventing an extra physiological second.
    """
    prior_ms=max(0,int(combat.get("elapsed_ms",0))); target=max(0,int(target_ms))
    if target<prior_ms: raise ValueError("combat physiology time regressed")
    delta_ms=target-prior_ms
    if delta_ms>0:
        # Qi allocation is a whole-body flow over real combat time. Settle it
        # exactly once per participant for each authoritative clock interval;
        # individual attacks/defenses consume the already-settled flow rather
        # than creating overlapping duplicate 250 ms budgets.
        for ref, person in people.items():
            state=combat.get("combatants",{}).get(ref)
            if not isinstance(state,dict) or not _active(person,state):
                continue
            _qi_effect(person=person,combatant_state=state,duration_ms=delta_ms)
        _settle_guard_exertion(combat=combat,people=people,equipment_ledger=equipment_ledger if isinstance(equipment_ledger,Mapping) else {},elapsed_ms=delta_ms)
    prior_second=prior_ms//1000; target_second=target//1000
    if target_second>prior_second:
        from_iso=_combat_timestamp(combat,prior_second*1000); to_iso=_combat_timestamp(combat,target_second*1000)
        for ref,person in people.items():
            combatants=combat.get("combatants",{})
            if ref not in combatants or not isinstance(person,dict): continue
            state=combatants[ref]
            if not isinstance(state,dict) or not physiology_needed(person): continue
            event={
                "event_id":f"combat_physiology:{ref}","kind":"person_physiology_due","owner_ref":ref,
                "due_at":to_iso,"last_settled_at":from_iso,
                "recovery_carry_minutes":max(0,int(state.get("physiology_recovery_carry_minutes",0)))%60,
                "poison_clearance_carry_minutes":max(0,int(state.get("poison_clearance_carry_minutes",0)))%60,
                "requires_player_decision":False,
            }
            settled=settle_person_physiology_event(person,event,at=to_iso)
            person.clear(); person.update(copy.deepcopy(dict(settled["person_after"])))
            replacement=settled.get("next_event")
            if isinstance(replacement,Mapping):
                recovery=max(0,int(replacement.get("recovery_carry_minutes",0)))%60
                poison=max(0,int(replacement.get("poison_clearance_carry_minutes",0)))%60
                if recovery: state["physiology_recovery_carry_minutes"]=recovery
                else: state.pop("physiology_recovery_carry_minutes",None)
                if poison: state["poison_clearance_carry_minutes"]=poison
                else: state.pop("poison_clearance_carry_minutes",None)
            else:
                state.pop("physiology_recovery_carry_minutes",None); state.pop("poison_clearance_carry_minutes",None)
            _refresh_structural_statuses(state,person)
            health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
            if health.get("status") in {"dead","incapacitated"} and not isinstance(state.get("incapacitated_at_ms"),int):
                state["incapacitated_at_ms"]=target_second*1000
    combat["elapsed_ms"]=target

def _qi_preview(*, person: Mapping[str, Any], combatant_state: Mapping[str, Any], duration_ms: int) -> dict[str, Any]:
    allocations=combatant_state.get("qi_allocation_milli",{})
    current_milli=person_current_qi_milli(person)
    reserve_milli=max(0,min(current_milli,int(combatant_state.get("qi_reserve_milli",0))))
    available_milli=max(0,current_milli-reserve_milli)
    efficiency=control_efficiency_milli(int(person.get("qi_control",0)))
    if not isinstance(allocations,Mapping) or not allocations or available_milli<=0:
        return {"requested_allocations_milli":{str(k):max(0,int(v)) for k,v in allocations.items()} if isinstance(allocations,Mapping) else {},
                "allocations_milli":{},"requested_flow_milli_per_second":sum(max(0,int(v)) for v in allocations.values()) if isinstance(allocations,Mapping) else 0,
                "delivered_flow_milli_per_second":0,"current_qi_milli_spent":0,
                "current_qi_milli_before":current_milli,"current_qi_milli_after":current_milli,
                "current_qi_before":current_milli//1000,"current_qi_after":current_milli//1000,
                "qi_reserve_milli":reserve_milli,"strain_milli_added":0,"control_efficiency_milli":efficiency,
                "resource_limited":bool(isinstance(allocations,Mapping) and allocations and available_milli<=0)}
    result=allocate_qi(qi=max(0,int(person.get("qi",0))),qi_control=max(0,int(person.get("qi_control",0))),
        current_qi_milli=available_milli,allocations_milli={str(k):max(0,int(v)) for k,v in allocations.items()},
        duration_ms=max(0,duration_ms),carry_milli_ms=max(0,int(combatant_state.get("qi_flow_carry_milli_ms",0))))
    after_total=reserve_milli+int(result["current_qi_milli_after"])
    return {**result,"current_qi_milli_before":current_milli,"current_qi_milli_after":after_total,
            "current_qi_before":current_milli//1000,"current_qi_after":after_total//1000,
            "qi_reserve_milli":reserve_milli,"control_efficiency_milli":efficiency}


def _qi_effect(*, person: dict[str, Any], combatant_state: dict[str, Any], duration_ms: int) -> dict[str, Any]:
    result=_qi_preview(person=person,combatant_state=combatant_state,duration_ms=duration_ms)
    after_milli=set_person_current_qi_milli(person,int(result["current_qi_milli_after"]))
    carry=max(0,min(999,int(result.get("qi_flow_carry_milli_ms_after",0))))
    if carry: combatant_state["qi_flow_carry_milli_ms"]=carry
    else: combatant_state.pop("qi_flow_carry_milli_ms",None)
    strain=max(0,int(result.get("strain_milli_added",0)))
    if strain:person["fatigue_milli"]=max(0,int(person.get("fatigue_milli",0)))+strain
    return {**result,"current_qi_milli_after":after_milli,"current_qi_after":after_milli//1000}


def _qi_channel_effect_milli(qi_result: Mapping[str, Any] | None, channel: str, *, maximum: int) -> int:
    if not isinstance(qi_result,Mapping): return 0
    allocations=qi_result.get("allocations_milli",{})
    if not isinstance(allocations,Mapping): return 0
    delivered=max(0,int(allocations.get(channel,0)))
    efficiency=max(0,min(1000,int(qi_result.get("control_efficiency_milli",0))))
    return min(max(0,int(maximum)),delivered*efficiency//1000)


def _qi_attribute_multipliers(qi_result: Mapping[str, Any] | None) -> dict[str, int]:
    """Temporary combat-time attribute multipliers from delivered Qi flow.

    Qi never adds an independent damage channel and never reinforces a weapon
    object. It only improves the fighter's own ordinary physical/sensory
    capabilities for the current interval. Existing deterministic movement,
    weapon-contact, defense, anatomy, protection, and fatigue rules then consume
    those enhanced capabilities exactly as they consume ordinary attributes.
    """
    movement=_qi_channel_effect_milli(qi_result,"movement",maximum=600)
    body=_qi_channel_effect_milli(qi_result,"body",maximum=400)
    sensing=_qi_channel_effect_milli(qi_result,"sensing",maximum=500)
    return {
        "strength":1000+body,
        "speed":1000+movement,
        "dexterity":1000+movement//2+body//4+sensing//5,
        "perception":1000+sensing,
    }


def _qi_effective_attrs(person: Mapping[str, Any], qi_result: Mapping[str, Any] | None) -> dict[str, int]:
    attrs=_attrs(person); mult=_qi_attribute_multipliers(qi_result)
    out={str(k):max(0,int(v)) for k,v in attrs.items() if isinstance(v,(int,float)) and not isinstance(v,bool)}
    for key,factor in mult.items():
        out[key]=max(0,int(attrs.get(key,0)))*factor//1000
    return out


def _qi_enhanced_capability(capability: CapabilityProfile, qi_result: Mapping[str, Any] | None) -> CapabilityProfile:
    # Capability profiles are already derived from ordinary attributes. Apply
    # the same temporary body/movement/sensing boosts to their downstream
    # expressions; direct contact damage is handled separately from boosted
    # strength/dexterity, never by an extra Qi damage multiplier.
    movement=_qi_channel_effect_milli(qi_result,"movement",maximum=600)
    body=_qi_channel_effect_milli(qi_result,"body",maximum=400)
    sensing=_qi_channel_effect_milli(qi_result,"sensing",maximum=500)
    return CapabilityProfile(
        offense=capability.offense*(1000+body//3+movement//6)//1000,
        defense=capability.defense*(1000+body//3+movement//5+sensing//5)//1000,
        control=capability.control*(1000+body//5+movement//5+sensing//3)//1000,
        mobility=capability.mobility*(1000+movement)//1000,
        perception=capability.perception*(1000+sensing)//1000,
        stealth=capability.stealth,
        capture=capability.capture*(1000+body//3)//1000,
        escape=capability.escape*(1000+movement)//1000,
        reaction=capability.reaction*(1000+sensing//2+movement//3+body//6)//1000,
    )


def _npc_qi_conservation_percent(
    person: Mapping[str, Any], faction_doctrine: Mapping[str, Any] | None, *, conservation_floor: int | None = None,
) -> int:
    conservation=75
    if isinstance(faction_doctrine,Mapping):
        raw=faction_doctrine.get("qi_conservation")
        if isinstance(raw,int) and not isinstance(raw,bool): conservation=max(0,min(100,raw))
    personal=resolve_individual_doctrine(person.get("combat_doctrine_ref")) if isinstance(person.get("combat_doctrine_ref"),str) else None
    if isinstance(personal,Mapping):
        resources=personal.get("resource_discipline",{})
        raw=resources.get("qi_conservation") if isinstance(resources,Mapping) else None
        if isinstance(raw,int) and not isinstance(raw,bool): conservation=max(conservation,max(0,min(100,raw)))
    if isinstance(conservation_floor, int) and not isinstance(conservation_floor, bool):
        conservation=max(conservation,max(0,min(100,conservation_floor)))
    return conservation


def _npc_qi_reserve_milli(
    person: Mapping[str, Any], faction_doctrine: Mapping[str, Any] | None, *, conservation_floor: int | None = None,
) -> int:
    return max(0,int(person.get("qi",0)))*1000*_npc_qi_conservation_percent(
        person,faction_doctrine,conservation_floor=conservation_floor
    )//100


def _npc_qi_allocation(
    person: Mapping[str, Any], faction_doctrine: Mapping[str, Any] | None, *, conservation_floor: int | None = None,
) -> dict[str, int]:
    qi=max(0,int(person.get("qi",0))); control=max(0,int(person.get("qi_control",0)))
    capacity=qi*1000; current=person_current_qi_milli(person)
    if qi<=0 or control<=0 or current<=0 or capacity<=0: return {}
    conservation=_npc_qi_conservation_percent(person,faction_doctrine,conservation_floor=conservation_floor)
    reserve=capacity*conservation//100
    spendable=max(0,current-reserve)
    if spendable<=0: return {}
    safe=max(0,safe_flow_milli_per_second(qi,control))
    intensity=max(15,min(100,120-conservation))
    flow=min(1000,safe*intensity//100,spendable)
    if flow<=0:return {}
    skills=_skills(person)
    discipline=max(("sword","spear","unarmed","bow","hidden_weapons"),key=lambda ref:(max(0,int(skills.get(ref,0))),ref))
    if discipline=="unarmed": weights=(("body",50),("movement",30),("sensing",20))
    elif discipline in {"bow","hidden_weapons"}: weights=(("sensing",45),("movement",35),("body",20))
    else: weights=(("body",45),("movement",35),("sensing",20))
    out:dict[str,int]={}; remaining=flow
    for index,(channel,weight) in enumerate(weights):
        amount=remaining if index==len(weights)-1 else flow*weight//100
        if amount>0:out[channel]=amount
        remaining-=amount
    return out


def _engagement_doctrine_for(person: Mapping[str, Any]) -> Mapping[str, Any]:
    doctrine_ref=person.get("combat_doctrine_ref") if isinstance(person.get("combat_doctrine_ref"),str) else None
    doctrine=resolve_individual_doctrine(doctrine_ref) if doctrine_ref else None
    engagement=doctrine.get("engagement",{}) if isinstance(doctrine,Mapping) else {}
    return engagement if isinstance(engagement,Mapping) else {}


def _engagement_band(distance_mm: int, *, disengaging: bool = False) -> int:
    """Geometry-first target band used before social/vengeance preference.

    The numbers are broad human combat envelopes rather than weapon reach. Exact
    reach still belongs to action scheduling. The purpose here is only to stop a
    hated or withdrawing remote target from outranking somebody who is physically
    threatening the actor now.
    """
    distance = max(0, int(distance_mm))
    if distance <= 2500:
        band = 0  # immediate contact / one-step melee threat
    elif distance <= 6000:
        band = 1  # near engagement
    elif distance <= 12000:
        band = 2  # tactical engagement / closing distance
    elif distance <= 25000:
        band = 3  # pursuit-scale separation
    else:
        band = 4  # remote
    if disengaging and band < 4:
        band += 1
    return band


def _target_has_finishing_opening(target: Mapping[str, Any]) -> bool:
    health=target.get("health",{}) if isinstance(target.get("health"),Mapping) else {}
    wounds=health.get("injuries",[]) if isinstance(health.get("injuries"),list) else []
    factors=functional_capacity_factors([row for row in wounds if isinstance(row,Mapping)])
    return (
        max(0,int(health.get("blood_lost_ml",0)))>=400
        or max(0,int(health.get("consciousness",100)))<75
        or min(
            max(0,int(factors.get("combat_movement_milli",1000))),
            max(0,int(factors.get("manual_milli",1000))),
            max(0,int(factors.get("vision_milli",1000))),
            max(0,int(factors.get("respiratory_milli",1000))),
        )<800
    )


def _fatigue_commitment_factor_milli(person: Mapping[str, Any]) -> int:
    resources=_resource_discipline_for(person)
    raw=resources.get("fatigue_reserve") if isinstance(resources,Mapping) else None
    if not isinstance(raw,int) or isinstance(raw,bool):
        return 1000
    reserve=max(0,min(100,raw))
    # Exact combat capability already bottoms out across the first 3000
    # fatigue-milli. Treat that same physical span as the available fatigue
    # headroom rather than inventing a second stamina capacity.
    used_percent=max(0,min(100,max(0,int(person.get("fatigue_milli",0)))*100//3000))
    preferred_use=max(0,100-reserve)
    if used_percent<=preferred_use:
        return 1000
    over=used_percent-preferred_use
    return max(650,1000-over*8)


def _resource_discipline_for(person: Mapping[str, Any]) -> Mapping[str, Any]:
    doctrine_ref=person.get("combat_doctrine_ref") if isinstance(person.get("combat_doctrine_ref"),str) else None
    doctrine=resolve_individual_doctrine(doctrine_ref) if doctrine_ref else None
    resources=doctrine.get("resource_discipline",{}) if isinstance(doctrine,Mapping) else {}
    return resources if isinstance(resources,Mapping) else {}


def _resource_threat_percent(actor: Mapping[str, Any], target: Mapping[str, Any]) -> int:
    """Return target threat as a percentage of the actor's standing threat.

    This intentionally uses the same compact threat score as team planning so
    resource escalation is deterministic and does not create a second hidden
    combat-rating authority. Current wounds/fatigue still govern actual contact
    through exact-combat capability; this ratio only answers whether a scarce
    resource is worth escalating.
    """
    return max(0, threat_score(target)*100//max(1,threat_score(actor)))


def _aggregate_resource_danger(
    combat: Mapping[str, Any], *, actor_ref: str, people: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Detect material group pressure for an actor with a personal resource doctrine.

    A fighter may rationally escalate against several individually weaker
    enemies. This gate is deliberately conservative. Raw headcount alone is
    not material danger: numerical disadvantage must also carry at least 90%
    of the allied aggregate threat. A hostile group at 125% or more aggregate
    threat is dangerous regardless of headcount.
    """
    try:
        side=_side_of(combat,actor_ref)
    except (KeyError,ValueError):
        return False
    enemy_side="side_b" if side=="side_a" else "side_a"
    states=combat.get("combatants",{}) if isinstance(combat.get("combatants"),Mapping) else {}
    allies=[
        str(ref) for ref in combat.get("sides",{}).get(side,[])
        if isinstance(ref,str) and ref in people and _active(people[ref],states.get(ref,{}))
    ]
    enemies=[
        str(ref) for ref in combat.get("sides",{}).get(enemy_side,[])
        if isinstance(ref,str) and ref in people and _active(people[ref],states.get(ref,{}))
    ]
    if not enemies:
        return False
    allied_threat=sum(threat_score(people[ref]) for ref in allies)
    enemy_threat=sum(threat_score(people[ref]) for ref in enemies)
    if enemy_threat*100>=max(1,allied_threat)*125:
        return True
    return (
        len(enemies)>max(1,len(allies))
        and enemy_threat*100>=max(1,allied_threat)*90
    )


def automatic_resource_policy(
    *, combat: Mapping[str, Any], actor_ref: str, target_ref: str,
    people: Mapping[str, Mapping[str, Any]], equipment_ledger: Mapping[str, Any],
    faction_doctrine: Mapping[str, Any] | None, action_kind: str, weapon_ref: str, intent: str,
    team_resource_discipline: Mapping[str, Any] | None = None, team_escalation_override: bool = False,
    social_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose Qi/poison use under standing resource doctrine.

    This is the resource selector for NPC actions and for player attacks where
    the player did not explicitly specify Qi or poison. A high-level intent
    such as ``attack`` therefore uses Wei's standing doctrine immediately; an
    explicit Qi allocation or poison choice overrides only that resource.
    """
    actor=people.get(actor_ref,{}) if isinstance(people.get(actor_ref,{}),Mapping) else {}
    target=people.get(target_ref,{}) if isinstance(people.get(target_ref,{}),Mapping) else {}
    personal=_resource_discipline_for(actor)
    team=team_resource_discipline if isinstance(team_resource_discipline,Mapping) else {}
    threat_percent=_resource_threat_percent(actor,target)

    qi_thresholds=[]
    poison_thresholds=[]
    for row in (personal,team):
        raw=row.get("qi_trigger_threat_percent") if isinstance(row,Mapping) else None
        if isinstance(raw,int) and not isinstance(raw,bool): qi_thresholds.append(max(0,min(200,raw)))
        raw=row.get("poison_trigger_threat_percent") if isinstance(row,Mapping) else None
        if isinstance(raw,int) and not isinstance(raw,bool): poison_thresholds.append(max(0,min(200,raw)))
    # Multiple standing policies compose conservatively. If both Wei's personal
    # policy and a team policy apply, the stricter threshold wins.
    qi_threshold=max(qi_thresholds) if qi_thresholds else None
    poison_threshold=max(poison_thresholds) if poison_thresholds else None
    personal_danger=bool(personal) and _aggregate_resource_danger(combat,actor_ref=actor_ref,people=people)
    escalation_override=bool(team_escalation_override or personal_danger)

    qi_allowed=qi_threshold is None or threat_percent>=qi_threshold or escalation_override
    poison_allowed=poison_threshold is None or threat_percent>=poison_threshold or escalation_override
    force_context=combat_force_context(combat)
    if force_context in {"formal_spar","tournament_nonlethal"}:
        allow_formal=bool(team.get("formal_nonlethal_poison",False)) if team else False
        poison_allowed=poison_allowed and allow_formal

    team_conservation=team.get("qi_conservation") if isinstance(team,Mapping) else None
    conservation_floor=(
        max(0,min(100,team_conservation))
        if isinstance(team_conservation,int) and not isinstance(team_conservation,bool) else None
    )
    reserve=_npc_qi_reserve_milli(actor,faction_doctrine,conservation_floor=conservation_floor)
    allocation=(
        _npc_qi_allocation(actor,faction_doctrine,conservation_floor=conservation_floor)
        if qi_allowed else {}
    )
    poison_ref=(
        _npc_poison_for(actor_ref,target,equipment_ledger,action_kind=action_kind,weapon_ref=weapon_ref,intent=intent)
        if poison_allowed else None
    )
    poison_vow_blocked=False
    if poison_ref and isinstance(social_state,Mapping):
        conflicts=vow_conflicts(
            social_state,person_ref=actor_ref,action_kind="combat",target_ref=target_ref,
            target_faction_ref=str(target.get("faction_ref") or ""),targeting_intent=intent,
            poison_ref=poison_ref,
        )
        poison_vow_blocked=any(
            isinstance(row,Mapping) and str(row.get("kind") or "")=="no_poison"
            for row in conflicts
        )
        if poison_vow_blocked:
            poison_ref=None
    return {
        "threat_percent":threat_percent,
        "escalation_override":escalation_override,
        "qi_trigger_threat_percent":qi_threshold,
        "poison_trigger_threat_percent":poison_threshold,
        "qi_allowed":qi_allowed,
        "poison_allowed":poison_allowed,
        "poison_vow_blocked":poison_vow_blocked,
        "qi_reserve_milli":reserve,
        "qi_allocation_milli":allocation,
        "poison_ref":poison_ref,
    }


def _contact_damage(
    *,
    actor: dict[str, Any],
    defender: dict[str, Any],
    weapon: Mapping[str, Any] | None,
    weapon_ref: str,
    action_kind: str,
    range_m: float,
    defense_force_milli: int,
    hit_zone: str,
    target_structure_ref: str | None,
    created_at: str,
    projectile_profile: Mapping[str, Any] | None = None,
    precision_margin: int = 0,
    qi_result: Mapping[str, Any] | None = None,
    motion_milli: int = 1000,
    mount_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    attrs=_qi_effective_attrs(actor,qi_result); skills=_skills(actor); profile: Mapping[str, Any]
    if action_kind=="unarmed_strike":
        physical={"discipline":"unarmed","reach_m":0.65,"ideal_range_m":[0.0,0.65],"impact":55,"cut":0,"pierce":0,"penetration":5,"precision":65,"control":70,"guard":30,"recovery_ms":300,"strength_requirement":0,"mass_kg":0}; profile=weapon_contact_profile(physical,skill=max(0,int(skills.get("unarmed",0))),strength=int(attrs.get("strength",0)),dexterity=int(attrs.get("dexterity",0)),range_m=max(0.0,float(range_m)))
    elif action_kind in {"bow_shot","hidden_weapon_throw"}:
        if not isinstance(projectile_profile,Mapping): raise ValueError("projectile contact profile missing")
        profile=projectile_profile
    else:
        if not isinstance(weapon,Mapping): raise ValueError("physical weapon missing")
        discipline=str(weapon.get("discipline","unarmed")); profile=weapon_contact_profile(weapon,skill=max(0,int(skills.get(discipline,0))),strength=int(attrs.get("strength",0)),dexterity=int(attrs.get("dexterity",0)),range_m=max(0.0,float(range_m)))
        if not bool(profile.get("in_reach")): return {"outcome":"out_of_reach","wound":None,"weapon_profile":profile}
    transmission=max(0,min(1000,int(defense_force_milli)))
    cut=int(profile.get("cut_score",0))*transmission//1000
    pierce=int(profile.get("pierce_score",0))*transmission//1000
    blunt=int(profile.get("blunt_score",0))*transmission//1000
    penetration=int(profile.get("penetration_score",0))*transmission//1000
    if action_kind != "bow_shot":
        output_milli=fatigue_performance_milli(int(actor.get("fatigue_milli",0)))
        cut=cut*output_milli//1000; pierce=pierce*output_milli//1000
        blunt=blunt*output_milli//1000; penetration=penetration*output_milli//1000
    # Qi has already been expressed through temporary effective attributes
    # above. Do not add a second, weapon-specific damage or penetration boost.
    motion=max(800,min(1600,int(motion_milli)))
    if action_kind not in {"bow_shot","hidden_weapon_throw","unarmed_strike"} and motion!=1000:
        # Mounted motion is physical platform momentum. It amplifies delivered
        # force/penetration but does not grant free precision or learned skill.
        blunt=blunt*motion//1000
        pierce=pierce*motion//1000
        penetration=penetration*motion//1000
        cut=cut*(1000+(motion-1000)//2)//1000
    channels={"cut":cut,"pierce":pierce,"blunt":blunt,"penetration":penetration}
    if hit_zone=="mount":
        if not isinstance(mount_state,Mapping) or not bool(mount_state.get("active",True)) or str(mount_state.get("status","active"))!="active":
            return {"outcome":"mount_target_unavailable","wound":None,"weapon_profile":copy.deepcopy(dict(profile)),"precision_margin":precision_margin,"transmitted_channels":channels}
        mount_result=mount_contact_result(mount_state,cut=cut,pierce=pierce,blunt=blunt,penetration=penetration)
        return {"outcome":"mount_contact","wound":None,"weapon_profile":copy.deepcopy(dict(profile)),"target_structure_intended":None,"target_structure_contacted":"mount","precision_margin":precision_margin,"transmitted_channels":channels,"mount_result":mount_result}
    resolved_zone=target_zone(zone=hit_zone if hit_zone and hit_zone!="auto" else None,structure_ref=target_structure_ref)
    resolved_structure=target_structure_ref if precision_margin>=0 else None
    wound=wound_from_contact(zone=resolved_zone,structure_ref=resolved_structure,cut=cut,pierce=pierce,blunt=blunt,penetration=penetration,created_at=created_at) if any((cut,pierce,blunt,penetration)) else None
    return {"outcome":"contact","wound":wound,"weapon_profile":copy.deepcopy(dict(profile)),"target_structure_intended":target_structure_ref,"target_structure_contacted":resolved_structure,"precision_margin":precision_margin,"transmitted_channels":channels,"motion_milli":motion}

def _decay_defense_state(state: dict[str, Any], *, attacker_ref: str, at_ms: int, reaction_score: int, angle_deg: int = 0) -> dict[str, Any]:
    available=active_defense_available(state.get("defense_state",{}),attacker_ref=attacker_ref,at_ms=at_ms,reaction_score=max(1,reaction_score),angle_deg=angle_deg,balance_milli=int(state.get("balance_milli",1000)),limb_commitment_milli=int(state.get("limb_commitment_milli",0))); state["defense_state"]=copy.deepcopy(dict(available["state_after_decay"])); return available


@dataclass(frozen=True)
class _ScheduledAction:
    actor_ref: str
    target_ref: str
    action_kind: str
    weapon_ref: str
    poison_ref: str | None
    hit_zone: str
    target_structure_ref: str | None
    decision_origin: str
    declared_at_ms: int
    start_at_ms: int
    commit_at_ms: int
    release_at_ms: int
    contact_at_ms: int
    recovery_end_ms: int
    ready_delay_ms: int
    previous_ready_weapon_ref: str | None
    profile: ActionProfile
    weapon: Mapping[str, Any] | None
    trajectory: Mapping[str, int]


def _schedule_action(*, combat: Mapping[str, Any], actor_ref: str, target_ref: str, action_kind: str, weapon_ref: str, poison_ref: str | None, hit_zone: str, target_structure_ref: str | None, decision_origin: str, people: Mapping[str, Mapping[str, Any]], equipment_ledger: Mapping[str, Any]) -> _ScheduledAction:
    declared = int(combat.get("elapsed_ms", 0))
    actor_state = combat["combatants"][actor_ref]
    actor_position = combat["positions"][actor_ref]
    target_position = combat["positions"][target_ref]
    if hit_zone == "mount":
        target_state = combat["combatants"][target_ref]
        target_mount = target_state.get("mount") if isinstance(target_state.get("mount"), Mapping) else None
        if not isinstance(target_mount, Mapping) or not bool(target_mount.get("active", True)) or str(target_mount.get("status", "active")) != "active":
            raise ValueError("mount target unavailable")
        if target_structure_ref not in {None, "", "auto"}:
            raise ValueError("mount target does not use human structure ref")
    profile, weapon = _action_profile(action_kind, people[actor_ref], weapon_ref, target_position, actor_position, actor_ref=actor_ref, equipment_ledger=equipment_ledger, combatant_state=actor_state)
    cap = _combat_capability_for_state(actor_ref, people[actor_ref], equipment_ledger, actor_state, action_skill=_discipline_for_action(action_kind, weapon))
    mount_at_declaration = _mount_motion_for_state(actor_ref, people[actor_ref], equipment_ledger, actor_state)
    engagement=_engagement_doctrine_for(people[actor_ref])
    params = dict(profile.effect_parameters)
    params["mounted_at_declaration"] = bool(mount_at_declaration.get("mounted"))
    commitment_factor={"measured":850,"balanced":1000,"committed":1150}.get(str(engagement.get("commitment_posture") or "balanced"),1000)
    if str(engagement.get("finishing_window") or "cautious")=="commit_decisively" and _target_has_finishing_opening(people[target_ref]):
        commitment_factor=max(commitment_factor,1050)
    commitment_factor=commitment_factor*_fatigue_commitment_factor_milli(people[actor_ref])//1000
    params["commitment_milli"]=max(120,min(1000,int(params.get("commitment_milli",400))*commitment_factor//1000))
    profile = ActionProfile(**{**profile.__dict__, "effect_parameters": params})
    ready_at = max(declared, int(actor_state.get("recovery_until_ms", 0)))
    decision_ms = max(20, 95_000 // max(90, cap.reaction + cap.perception // 2 + 80))
    initiative_factor={"reactive":1100,"balanced":1000,"assertive":900}.get(str(engagement.get("initiative_posture") or "balanced"),1000)
    decision_ms=max(20,decision_ms*initiative_factor//1000)
    current_ready = actor_state.get("ready_weapon_ref") if isinstance(actor_state.get("ready_weapon_ref"), str) else None
    ready_delay_ms = _weapon_ready_delay_ms(people[actor_ref], current_ready, weapon_ref, actor_state)
    approach_ms = 0
    approach_distance_mm = 0
    if profile.delivery not in {"projectile", "ranged", "thrown"}:
        distance = planar_distance_mm(actor_position, target_position)
        # ``physical_reach_mm`` is the same center-to-center contact envelope
        # consumed by exact melee geometry and weapon contact. Do not add body
        # radii here or the approach stops outside the distance at which the
        # strike can actually intersect and transmit damage.
        required = max(0, distance - physical_reach_mm(profile))
        maximum_approach_ms=max(250,int(_combat_rules().get("maximum_melee_approach_ms",2000)))
        base_speed=max(1,_movement_speed_for_state(actor_ref,people[actor_ref],equipment_ledger,actor_state,cap))
        base_approach_ms=min(maximum_approach_ms,required*1000//base_speed)
        qi_preview=_qi_preview(
            person=people[actor_ref],combatant_state=actor_state,
            duration_ms=max(1,decision_ms+ready_delay_ms+base_approach_ms+int(profile.startup_ms)),
        )
        movement_cap=_qi_enhanced_capability(cap,qi_preview)
        movement_speed=max(1,_movement_speed_for_state(actor_ref,people[actor_ref],equipment_ledger,actor_state,movement_cap))
        approach_distance_mm=min(required,movement_speed*maximum_approach_ms//1000)
        approach_ms=approach_distance_mm*1000//movement_speed if approach_distance_mm>0 else 0
        params = dict(profile.effect_parameters)
        params["approach_distance_mm"] = approach_distance_mm
        params["approach_time_ms"] = approach_ms
        params["approach_budget_limited"] = bool(required>approach_distance_mm)
        params["committed_melee_trajectory"] = {
            "launch_x_mm": int(actor_position["x_mm"]),
            "launch_y_mm": int(actor_position["y_mm"]),
            "launch_elevation_mm": int(actor_position.get("elevation_mm", 0)),
            "aim_x_mm": int(target_position["x_mm"]),
            "aim_y_mm": int(target_position["y_mm"]),
            "aim_elevation_mm": int(target_position.get("elevation_mm", 0)),
        }
        params["intended_target_ref"] = target_ref
        profile = ActionProfile(**{**profile.__dict__, "effect_parameters": params})
    projectile = profile.effect_parameters.get("projectile")
    flight_ms = max(0, int(projectile.get("flight_time_ms", 0))) if isinstance(projectile, Mapping) else 0
    start_at = ready_at + decision_ms + ready_delay_ms
    release_at = start_at + approach_ms + int(profile.startup_ms)
    commitment = int(profile.effect_parameters.get("commitment_milli", 400))
    commit_at = start_at + approach_ms + max(20, int(profile.startup_ms) * max(250, commitment) // 1000)
    contact_at = release_at + flight_ms
    recovery = int(weapon.get("reload_ms", 0)) if action_kind == "bow_shot" and isinstance(weapon, Mapping) else int(weapon.get("recovery_ms", 0)) if isinstance(weapon, Mapping) else 300
    if action_kind == "hidden_weapon_throw":
        recovery = max(300, int(profile.startup_ms) // 2)
    recovery_end = max(contact_at, release_at + max(90, recovery))
    trajectory = {
        "launch_x_mm": int(actor_position["x_mm"]),
        "launch_y_mm": int(actor_position["y_mm"]),
        "launch_elevation_mm": int(actor_position.get("elevation_mm", 0)),
        "aim_x_mm": int(target_position["x_mm"]),
        "aim_y_mm": int(target_position["y_mm"]),
        "aim_elevation_mm": int(target_position.get("elevation_mm", 0)),
    }
    return _ScheduledAction(
        actor_ref, target_ref, action_kind, weapon_ref, poison_ref, hit_zone, target_structure_ref,
        decision_origin, declared, start_at, commit_at, release_at, contact_at,
        recovery_end, ready_delay_ms, current_ready, profile,
        copy.deepcopy(dict(weapon)) if isinstance(weapon, Mapping) else None, trajectory,
    )


def _interception_damage(*, defender_ref: str, attacker_ref: str, people: dict[str, dict[str, Any]], equipment_ledger: dict[str, Any], combat: dict[str, Any], at_ms: int) -> dict[str, Any] | None:
    defender_state=combat["combatants"][defender_ref]; weapon_ref=_ready_melee_weapon_ref(defender_ref,people[defender_ref],equipment_ledger,defender_state); weapon=_weapon_for_holder(equipment_ledger,defender_ref,weapon_ref,defender_state)
    if weapon_ref is None: action_kind="unarmed_strike"; weapon_ref="body_unarmed"
    else: action_kind="thrust" if int(weapon.get("pierce",0))>=int(weapon.get("cut",0)) else "cut"
    distance_m=planar_distance_mm(combat["positions"][defender_ref],combat["positions"][attacker_ref])/1000.0
    if weapon is not None and distance_m>float(weapon.get("reach_m",0)): return None
    result=_contact_damage(actor=people[defender_ref],defender=people[attacker_ref],weapon=weapon,weapon_ref=weapon_ref,action_kind=action_kind,range_m=distance_m,defense_force_milli=620,hit_zone="forearm",target_structure_ref=None,created_at=str(at_ms),precision_margin=0); wound=result.get("wound")
    if isinstance(wound,Mapping):
        health=copy.deepcopy(people[attacker_ref].get("health",{})); health["injuries"]=record_current_wound(health.get("injuries",[]) if isinstance(health.get("injuries"),list) else [],wound); people[attacker_ref]["health"]=health; _apply_physiology(people[attacker_ref],elapsed_seconds=0,at_iso=_combat_timestamp(combat,at_ms))
    return result


def _pending_action_record(action: _ScheduledAction) -> dict[str, Any]:
    return {
        "actor_ref": action.actor_ref,
        "target_ref": action.target_ref,
        "commit_at_ms": int(action.commit_at_ms),
        "release_at_ms": int(action.release_at_ms),
        "contact_at_ms": int(action.contact_at_ms),
        "recovery_end_ms": int(action.recovery_end_ms),
        "commitment_milli": max(0, int(action.profile.effect_parameters.get("commitment_milli", 400))),
        "delivery": str(action.profile.delivery),
    }


def _pending_attack_commitment(combat: Mapping[str, Any], defender_ref: str, *, at_ms: int) -> dict[str, Any] | None:
    pending = combat.get("_pending_actions", {}) if isinstance(combat.get("_pending_actions"), Mapping) else {}
    row = pending.get(defender_ref) if isinstance(pending, Mapping) else None
    if not isinstance(row, Mapping):
        return None
    # Before release, the defender is physically preparing/committing the attack.
    # A projectile already released is no longer cancellable by a later defense.
    if int(row.get("commit_at_ms", 0)) <= int(at_ms) < int(row.get("release_at_ms", 0)):
        return dict(row)
    return None


def _record_defensive_interruption(
    combat: dict[str, Any], *, defender_ref: str, attacker_ref: str, response: str,
    response_start_ms: int, response_contact_ms: int,
) -> None:
    pending = combat.get("_pending_actions", {}) if isinstance(combat.get("_pending_actions"), Mapping) else {}
    row = pending.get(defender_ref) if isinstance(pending, Mapping) else None
    if not isinstance(row, Mapping):
        return
    if int(response_start_ms) >= int(row.get("release_at_ms", 0)):
        return
    interruptions = combat.setdefault("_defense_interruptions", {})
    current = interruptions.get(defender_ref) if isinstance(interruptions, Mapping) else None
    record = {
        "response": str(response),
        "attacker_ref": str(attacker_ref),
        "started_at_ms": int(response_start_ms),
        "contact_at_ms": int(response_contact_ms),
    }
    if not isinstance(current, Mapping) or int(record["started_at_ms"]) < int(current.get("started_at_ms", 10**18)):
        interruptions[defender_ref] = record


def _defensive_action_interruption(combat: Mapping[str, Any], action: _ScheduledAction) -> dict[str, Any] | None:
    rows = combat.get("_defense_interruptions", {}) if isinstance(combat.get("_defense_interruptions"), Mapping) else {}
    row = rows.get(action.actor_ref) if isinstance(rows, Mapping) else None
    if not isinstance(row, Mapping):
        return None
    started = int(row.get("started_at_ms", 10**18))
    if started >= int(action.release_at_ms):
        return None
    return {
        "result": "action_interrupted_by_defense_before_commitment" if started <= int(action.commit_at_ms) else "action_disrupted_by_defense_after_commitment",
        "defensive_response": str(row.get("response") or "defense"),
        "defensive_attacker_ref": row.get("attacker_ref"),
        "defense_started_at_ms": started,
    }


def _resolve_scheduled_action(*, combat: dict[str, Any], action: _ScheduledAction, people: dict[str, dict[str, Any]], equipment_ledger: dict[str, Any]) -> dict[str, Any]:
    actor_ref=action.actor_ref; target_ref=action.target_ref; event_base={"actor_ref":actor_ref,"intended_ref":target_ref,"action_kind":action.action_kind,"weapon_ref":action.weapon_ref,"poison_ref":action.poison_ref,"hit_zone":action.hit_zone,"target_structure_ref":action.target_structure_ref,"decision_origin":action.decision_origin,"declared_at_ms":action.declared_at_ms,"start_at_ms":action.start_at_ms,"ready_delay_ms":action.ready_delay_ms,"previous_ready_weapon_ref":action.previous_ready_weapon_ref,"commit_at_ms":action.commit_at_ms,"release_at_ms":action.release_at_ms,"contact_at_ms":action.contact_at_ms,"recovery_end_ms":action.recovery_end_ms}
    if actor_ref not in people or target_ref not in people: return {**event_base,"result":"invalid_target"}
    target_state_pre=combat.get("combatants",{}).get(target_ref,{})
    escaped_at=target_state_pre.get("escaped_at_ms") if isinstance(target_state_pre,Mapping) else None
    # A target that physically cleared the fight before this attack committed
    # ends the uncommitted chase at that frontier. Already-committed/released
    # attacks remain on the shared timeline and resolve against moved geometry.
    if isinstance(escaped_at,int) and escaped_at<int(action.commit_at_ms):
        if int(combat.get("elapsed_ms",0))<escaped_at:
            _settle_combat_physiology_until(combat,people,target_ms=escaped_at,equipment_ledger=equipment_ledger)
        return {**event_base,"result":"target_escaped_before_commitment","escaped_at_ms":escaped_at}
    _settle_combat_physiology_until(combat,people,target_ms=action.contact_at_ms,equipment_ledger=equipment_ledger)
    actor_state=combat["combatants"][actor_ref]; target_state=combat["combatants"][target_ref]; disabled_at=actor_state.get("incapacitated_at_ms")
    declared_mounted=bool(action.profile.effect_parameters.get("mounted_at_declaration",False))
    actor_mount=actor_state.get("mount") if isinstance(actor_state.get("mount"),Mapping) else None
    mount_disabled_at=actor_mount.get("disabled_at_ms") if isinstance(actor_mount,Mapping) else None
    if declared_mounted and isinstance(mount_disabled_at,int) and mount_disabled_at<action.release_at_ms:
        return {
            **event_base,
            "result":"action_interrupted_by_mount_loss_before_commitment" if mount_disabled_at<=action.commit_at_ms else "action_disrupted_by_mount_loss_after_commitment",
            "mount_disabled_at_ms":mount_disabled_at,
        }
    defense_interruption = _defensive_action_interruption(combat, action)
    if defense_interruption is not None:
        return {**event_base, **defense_interruption}
    if isinstance(disabled_at,int) and disabled_at<=action.commit_at_ms:
        if action.weapon_ref!="body_unarmed" and disabled_at>=action.start_at_ms:
            actor_state["ready_weapon_ref"]=action.weapon_ref
        return {**event_base,"result":"action_interrupted_before_commitment"}
    if isinstance(disabled_at,int) and disabled_at<action.release_at_ms:
        if action.weapon_ref!="body_unarmed" and disabled_at>=action.start_at_ms:
            actor_state["ready_weapon_ref"]=action.weapon_ref
        return {**event_base,"result":"action_disrupted_after_commitment_before_release","incapacitated_at_ms":disabled_at}
    if not status_action_allowed(actor_state.get("status_families",[]),"attack") and not (isinstance(disabled_at,int) and disabled_at>=action.release_at_ms): return {**event_base,"result":"status_blocks_action"}
    if _side_of(combat,actor_ref)==_side_of(combat,target_ref): return {**event_base,"result":"friendly_target_rejected"}
    if not _weapon_owned(equipment_ledger,actor_ref,action.weapon_ref,actor_state): return {**event_base,"result":"weapon_not_owned"}
    wounds=_wounds(people[actor_ref])
    if action.action_kind in {"bow_shot","hidden_weapon_throw"} and vision_state(wounds).get("state")=="blind": return {**event_base,"result":"visual_targeting_unavailable"}
    if isinstance(action.weapon,Mapping):
        hands_required=max(1,int(action.weapon.get("hands_required",1)))
        usable_hands=_usable_hand_count(people[actor_ref])
        if usable_hands<hands_required:
            return {**event_base,"result":"weapon_hand_control_unavailable","hands_required":hands_required,"usable_hands":usable_hands}
    at_ms=action.contact_at_ms; actor_state["weapon_position"]="extended_attack"; actor_state["limb_commitment_milli"]=int(action.profile.effect_parameters.get("commitment_milli",400)); actor_state["balance_milli"]=max(350,int(actor_state.get("balance_milli",1000))-int(actor_state["limb_commitment_milli"])//5); actor_state["recovery_until_ms"]=max(int(actor_state.get("recovery_until_ms",0)),action.recovery_end_ms)
    if action.weapon_ref!="body_unarmed":
        actor_state["ready_weapon_ref"]=action.weapon_ref
        actor_state["ready_hands_required"]=max(1,int((action.weapon or {}).get("hands_required",1)))
    positions=combat["positions"]; start_actor=copy.deepcopy(positions[actor_ref]); start_target=copy.deepcopy(positions[target_ref]); profile=action.profile; approach_distance_mm=0; body_refs=[ref for refs in combat["sides"].values() for ref in refs]
    qi_preview=_qi_preview(person=people[actor_ref],combatant_state=actor_state,duration_ms=max(1,action.release_at_ms-action.start_at_ms))
    actor_cap=_qi_enhanced_capability(_combat_capability_for_state(actor_ref,people[actor_ref],equipment_ledger,actor_state,action_skill=_discipline_for_action(action.action_kind,action.weapon)),qi_preview)
    if profile.delivery not in {"projectile","ranged","thrown"}:
        params=dict(profile.effect_parameters)
        params["intended_target_ref"]=target_ref
        profile=ActionProfile(**{**profile.__dict__,"effect_parameters":params})
        moved,approach=close_attacker_into_reach(
            attacker_ref=actor_ref,defender_ref=target_ref,positions=positions,
            attacker_position=_pos(start_actor),defender_position=_pos(start_target),
            attacker_capability=actor_cap,profile=profile,body_refs=body_refs,
            obstacles=combat.get("obstacles",[]),
        )
        if approach.get("moved"):
            positions[actor_ref]=moved.to_record()
            positions[actor_ref]["elevation_mm"]=int(start_actor.get("elevation_mm",0))
            approach_distance_mm=max(0,int(approach.get("distance_mm",0)))
            params=dict(profile.effect_parameters)
            params["approach_time_ms"]=int(approach.get("approach_time_ms",0))
            params["approach_distance_mm"]=approach_distance_mm
            profile=ActionProfile(**{**profile.__dict__,"effect_parameters":params})
        melee_distance_mm=planar_distance_mm(positions[actor_ref],positions[target_ref])
        melee_reach_mm=physical_reach_mm(profile)
        if melee_reach_mm>0 and melee_distance_mm>melee_reach_mm:
            chase_exertion=_combat_exertion(
                person_ref=actor_ref,person=people[actor_ref],equipment_ledger=equipment_ledger,
                commitment_milli=int(action.profile.effect_parameters.get("commitment_milli",400)),
                movement_mm=approach_distance_mm,action_kind=None,weapon=action.weapon,
                mounted=bool(isinstance(actor_state.get("mount"),Mapping) and actor_state.get("mount",{}).get("active")),
            )
            approach_reason=str(approach.get("reason") or "")
            budget_limited=bool(action.profile.effect_parameters.get("approach_budget_limited",False))
            result_kind=(
                "melee_approach_in_progress"
                if budget_limited and approach_reason=="partial_committed_approach"
                else "target_outpaced_committed_approach"
                if approach_reason in {"partial_committed_approach","target_moved_beyond_committed_approach"}
                else "melee_approach_blocked"
            )
            return {
                **event_base,"result":result_kind,"approach":approach,
                "distance_mm":melee_distance_mm,"reach_mm":melee_reach_mm,
                "fatigue":chase_exertion,"qi":qi_preview,
            }
        actor_release=positions[actor_ref]
        target_release=positions[target_ref]
        params=dict(profile.effect_parameters)
        params["committed_melee_trajectory"]={
            "launch_x_mm":int(actor_release["x_mm"]),
            "launch_y_mm":int(actor_release["y_mm"]),
            "launch_elevation_mm":int(actor_release.get("elevation_mm",0)),
            "aim_x_mm":int(target_release["x_mm"]),
            "aim_y_mm":int(target_release["y_mm"]),
            "aim_elevation_mm":int(target_release.get("elevation_mm",0)),
        }
        params["intended_target_ref"]=target_ref
        profile=ActionProfile(**{**profile.__dict__,"effect_parameters":params})
    distance_m=planar_distance_mm(positions[actor_ref],positions[target_ref])/1000.0; visibility=1000-cover_milli_between(positions,actor_ref=actor_ref,target_ref=target_ref,obstacles=combat.get("obstacles",[])); bow_profile=None; trajectory=copy.deepcopy(dict(action.trajectory))
    if action.action_kind=="bow_shot":
        fatigue_output=fatigue_performance_milli(int(people[actor_ref].get("fatigue_milli",0)))
        qi_attrs=_qi_effective_attrs(people[actor_ref],qi_preview)
        bow_profile=bow_shot_profile(action.weapon or {},bow_skill=int(_skills(people[actor_ref]).get("bow",0)),strength=int(qi_attrs.get("strength",0))*fatigue_output//1000,dexterity=int(qi_attrs.get("dexterity",0)),perception=int(qi_attrs.get("perception",0)),distance_m=distance_m,crosswind_mps_tenths=int(combat.get("crosswind_mps_tenths",0)))
        if not bow_profile.get("can_draw"): return {**event_base,"result":"strength_draw_requirement_not_met","bow_profile":bow_profile}
    target_cap_for_precision=_combat_capability_for_state(target_ref,people[target_ref],equipment_ledger,target_state)
    actor_mount_motion=_mount_motion_for_state(actor_ref,people[actor_ref],equipment_ledger,actor_state)
    precision=_precision_margin(
        actor=people[actor_ref],weapon=action.weapon,action_kind=action.action_kind,structure_ref=action.target_structure_ref,
        hit_zone=action.hit_zone,distance_m=distance_m,target=people[target_ref],visibility_milli=visibility,
        bow_accuracy_score=int(bow_profile.get("accuracy_score",0)) if isinstance(bow_profile,Mapping) else None,
        target_speed=target_cap_for_precision.mobility,
        mounted_control_milli=(int(actor_mount_motion.get("control_milli",0)) if bool(actor_mount_motion.get("mounted")) else None),
        qi_result=qi_preview,
    )
    if profile.delivery in {"projectile","ranged","thrown"}:
        wind_error=int(round(float(bow_profile.get("wind_drift_m",0))*1000)) if isinstance(bow_profile,Mapping) else 0; aim_error=max(0,-precision)*18+wind_error; trajectory=_trajectory_with_error(trajectory,error_mm=aim_error,seed_parts=(combat.get("combat_id"),actor_ref,target_ref,action.contact_at_ms)); resource_commit=_commit_projectile_resources(equipment_ledger,actor_ref=actor_ref,action_kind=action.action_kind,weapon_ref=action.weapon_ref,poison_ref=action.poison_ref)
        if not resource_commit.get("ok"): return {**event_base,"result":str(resource_commit.get("reason") or "projectile_resource_unavailable"),"resource_commit":resource_commit}
        if action.action_kind=="hidden_weapon_throw":
            actor_state["ready_weapon_ref"]=None
            actor_state.pop("ready_hands_required",None)
            actor_state["weapon_position"]="released_projectile"
    # The shared combat clock already paid Qi flow through this contact time.
    # This one-millisecond preview describes the flow still physically available
    # at contact without spending the same interval again.
    qi_result=_qi_preview(person=people[actor_ref],combatant_state=actor_state,duration_ms=1)
    action_exertion=_combat_exertion(person_ref=actor_ref,person=people[actor_ref],equipment_ledger=equipment_ledger,
        commitment_milli=int(action.profile.effect_parameters.get("commitment_milli",400)),movement_mm=approach_distance_mm,
        action_kind=action.action_kind,weapon=action.weapon,mounted=bool(isinstance(actor_state.get("mount"),Mapping) and actor_state.get("mount",{}).get("active")))
    geometry=profile.effect_parameters.get("geometry"); channel="projectile" if profile.delivery in {"projectile","ranged","thrown"} else "melee"; melee_trajectory=profile.effect_parameters.get("committed_melee_trajectory") if isinstance(profile.effect_parameters.get("committed_melee_trajectory"),Mapping) else None; trace=trace_attack_geometry(positions,actor_ref=actor_ref,aim_ref=target_ref,body_refs=body_refs,geometry=geometry,obstacles=combat.get("obstacles",[]),target_limit=1,maximum_range_m=(profile.effect_parameters.get("maximum_range_m") if channel=="projectile" else profile.effect_parameters.get("physical_reach_m")),channel=channel,trajectory=trajectory if channel=="projectile" else melee_trajectory); contacts=trace.get("contacts",[]) if isinstance(trace,Mapping) else []; actual_ref=contacts[0].get("participant_ref") if contacts and isinstance(contacts[0],Mapping) else None
    if actual_ref is None and channel=="projectile": return {**event_base,"result":"miss_no_spatial_intersection","trace":trace,"precision_margin":precision,"bow_profile":bow_profile,"resource_commit":resource_commit,"fatigue":action_exertion,"qi":qi_result}
    if actual_ref is None: actual_ref=target_ref
    if actual_ref not in people: return {**event_base,"result":"no_contact","trace":trace,"fatigue":action_exertion,"qi":qi_result}
    defender=people[actual_ref]; defender_state=combat["combatants"][actual_ref]
    defense_qi_result=_qi_preview(person=defender,combatant_state=defender_state,duration_ms=1)
    defender_cap=_qi_enhanced_capability(_combat_capability_for_state(actual_ref,defender,equipment_ledger,defender_state),defense_qi_result)
    defender_position_before=_pos(positions[actual_ref])
    incoming_bearing=facing_to_target_mdeg(defender_position_before.to_record(),positions[actor_ref])
    incoming_delta_deg=angular_difference_mdeg(defender_position_before.facing_mdeg,incoming_bearing)//1000
    defense_pressure=_decay_defense_state(defender_state,attacker_ref=actor_ref,at_ms=at_ms,reaction_score=defender_cap.reaction,angle_deg=incoming_delta_deg)
    # The current raw load is durable state.  Conflict angle, distinct attackers,
    # balance and limb commitment are transient pressure for this exact incoming
    # attack.  Project that pressure into the participant's effective defense
    # load without writing the transient penalty back as a second authority.
    decision_state=copy.deepcopy(defender_state)
    decision_defense=dict(decision_state.get("defense_state",{}))
    pressure_load=max(0,1000-max(0,min(1000,int(defense_pressure.get("available_milli",1000)))))
    decision_defense["load_milli"]=max(int(decision_defense.get("load_milli",0)),pressure_load)
    decision_state["defense_state"]=decision_defense
    pending_attack = _pending_attack_commitment(combat, actual_ref, at_ms=at_ms)
    if pending_attack is not None:
        decision_state["limb_commitment_milli"] = max(
            int(decision_state.get("limb_commitment_milli", 0)),
            int(pending_attack.get("commitment_milli", 0)),
        )
        decision_state["weapon_position"] = "committed_attack"
    guard_profile=_guard_profile(actual_ref,defender,equipment_ledger,defender_state); observed=list(defender_state.get("observed_refs",[])); attacker_part=_participant(actor_ref,people[actor_ref],side_ref=_side_of(combat,actor_ref),position=positions[actor_ref],known_refs=[actual_ref],combatant_state=actor_state,action_profile=profile,equipment_ledger=equipment_ledger,at_ms=at_ms,intent="attack"); defender_part=_participant(actual_ref,defender,side_ref=_side_of(combat,actual_ref),position=positions[actual_ref],known_refs=observed,combatant_state=decision_state,action_profile=guard_profile,equipment_ledger=equipment_ledger,at_ms=at_ms,intent="attack" if guard_profile is not None else "hold"); original_defender_position=defender_position_before; los=line_of_sight_clear(positions,actor_ref=actor_ref,target_ref=actual_ref,obstacles=combat.get("obstacles",[])); decision=select_physical_defense(attacker=attacker_part,defender=defender_part,attacker_position=_pos(positions[actor_ref]),defender_position=original_defender_position,attacker_capability=actor_cap,defender_capability=defender_cap,profile=profile,line_of_sight=los,participant_positions=positions,body_refs=body_refs,obstacles=combat.get("obstacles",[]),at_ms=at_ms)
    if decision.detected:
        known=set(str(x) for x in defender_state.get("observed_refs",[]) if isinstance(x,str)); known.add(actor_ref); defender_state["observed_refs"]=sorted(known); defender_state["surprise_milli"]=0
    positions[actual_ref]=decision.after_position.to_record(); positions[actual_ref]["elevation_mm"]=int(start_target.get("elevation_mm",0)); defender_state["balance_milli"]=decision.balance_after_milli; defender_state["limb_commitment_milli"]=decision.limb_commitment_after_milli; defender_state["weapon_position"]=decision.weapon_position_after; defender_state["recovery_until_ms"]=max(int(defender_state.get("recovery_until_ms",0)),at_ms+decision.recovery_ms)
    defense_move_mm=planar_distance_mm(original_defender_position.to_record(),decision.after_position.to_record())
    defense_exertion=_defense_exertion(person_ref=actual_ref,person=defender,equipment_ledger=equipment_ledger,response=str(decision.response),movement_mm=defense_move_mm)
    defender_weapon_ref=_ready_melee_weapon_ref(actual_ref,defender,equipment_ledger,defender_state)
    weapon_clash=(
        channel=="melee" and decision.detected
        and str(decision.response) in {"parry","deflect","block","counter_intercept"}
        and isinstance(defender_weapon_ref,str)
    )
    if weapon_clash:
        _apply_combat_weapon_wear(equipment_ledger,person_ref=actor_ref,weapon_ref=action.weapon_ref,combatant_state=actor_state,event_kind="weapon_contact_heavy")
        _apply_combat_weapon_wear(equipment_ledger,person_ref=actual_ref,weapon_ref=defender_weapon_ref,combatant_state=defender_state,event_kind="weapon_contact_heavy")
    if decision.detected and decision.response != "none":
        response_start_ms=max(int(combat.get("_exchange_declared_at_ms",0)),at_ms-max(0,int(decision.reaction_delay_ms)))
        _record_defensive_interruption(
            combat, defender_ref=actual_ref, attacker_ref=actor_ref, response=decision.response,
            response_start_ms=response_start_ms, response_contact_ms=at_ms,
        )
    defense_commit=commit_active_defense(defender_state.get("defense_state",{}),attacker_ref=actor_ref,at_ms=at_ms,threat_speed=max(1,int(profile.speed_score)),reaction_score=max(1,int(defender_cap.reaction)),body_commitment_milli=int(decision.limb_commitment_after_milli))
    defender_state["defense_state"]=copy.deepcopy(dict(defense_commit["state_after"]))
    if decision.interrupts_attacker and action.commit_at_ms>=at_ms-decision.reaction_delay_ms:
        counter=_interception_damage(defender_ref=actual_ref,attacker_ref=actor_ref,people=people,equipment_ledger=equipment_ledger,combat=combat,at_ms=at_ms); return {**event_base,"actual_ref":actual_ref,"result":"counter_intercepted","defense":decision.trace(),"defense_pressure":defense_pressure,"counter_contact":counter,"trace":trace,"resource_commit":resource_commit if channel=="projectile" else None,"fatigue":action_exertion,"defense_fatigue":defense_exertion,"qi":qi_result,"defense_qi":defense_qi_result}
    interception={"outcome":"not_applicable"}
    if channel=="projectile":
        interception=_projectile_interception(defender_ref=actual_ref,defender=defender,defender_state=defender_state,defender_capability=defender_cap,equipment_ledger=equipment_ledger,decision=decision,profile=profile,trajectory=trajectory,combat_id=str(combat.get("combat_id","")),attacker_ref=actor_ref,at_ms=at_ms)
        if interception.get("outcome")=="clean":
            _apply_combat_weapon_wear(equipment_ledger,person_ref=actual_ref,weapon_ref=defender_weapon_ref,combatant_state=defender_state,event_kind="weapon_contact_light")
            return {**event_base,"actual_ref":actual_ref,"result":"projectile_intercepted_clean","defense":decision.trace(),"defense_pressure":defense_pressure,"interception":interception,"trace":trace,"resource_commit":resource_commit,"fatigue":action_exertion,"defense_fatigue":defense_exertion,"qi":qi_result,"defense_qi":defense_qi_result}
        if interception.get("outcome")=="partial":
            _apply_combat_weapon_wear(equipment_ledger,person_ref=actual_ref,weapon_ref=defender_weapon_ref,combatant_state=defender_state,event_kind="weapon_contact_light")
            trajectory=copy.deepcopy(interception.get("trajectory") or trajectory)
    contact=contact_after_defense(attacker_ref=actor_ref,defender_ref=actual_ref,positions=positions,profile=profile,obstacles=combat.get("obstacles",[]),trajectory=trajectory if channel=="projectile" else action.trajectory,tracking_milli=max(100,decision.tracking_milli+min(0,precision)*4),original_defender_position=original_defender_position,body_refs=body_refs)
    if not contact.get("contact"):
        return {**event_base,"actual_ref":actual_ref,"result":"defended_or_missed","defense":decision.trace(),"defense_pressure":defense_pressure,"interception":interception,"contact":contact,"precision_margin":precision,"trace":trace,"resource_commit":resource_commit if channel=="projectile" else None,"fatigue":action_exertion,"defense_fatigue":defense_exertion,"qi":qi_result,"defense_qi":defense_qi_result}
    redirected_ref=contact.get("contacted_ref")
    if channel=="projectile" and isinstance(redirected_ref,str) and redirected_ref in people and redirected_ref!=actual_ref:
        # A physically redirected projectile may strike a different body.  That
        # person did not receive the original defender's reaction for free.
        actual_ref=redirected_ref; defender=people[actual_ref]; defender_state=combat["combatants"][actual_ref]
        decision_force_milli=1000
    elif redirected_ref not in {None,actual_ref}:
        return {**event_base,"actual_ref":actual_ref,"result":"defended_or_missed","defense":decision.trace(),"defense_pressure":defense_pressure,"interception":interception,"contact":contact,"precision_margin":precision,"trace":trace,"resource_commit":resource_commit if channel=="projectile" else None,"fatigue":action_exertion,"defense_fatigue":defense_exertion,"qi":qi_result,"defense_qi":defense_qi_result}
    else:
        # Projectile mitigation is physical interception, not an invisible
        # generic defense multiplier. Clean interception already ended above;
        # partial interception changes trajectory/speed. If the projectile still
        # contacts this body, it carries its remaining physical force.
        decision_force_milli=1000 if channel=="projectile" else decision.force_transmission_milli
    if channel=="melee" and not weapon_clash:
        _apply_combat_weapon_wear(equipment_ledger,person_ref=actor_ref,weapon_ref=action.weapon_ref,combatant_state=actor_state,event_kind="weapon_contact_light")
    range_m=planar_distance_mm(positions[actor_ref],positions[actual_ref])/1000.0; projectile_profile=None
    if action.action_kind=="bow_shot":
        arrow=_equipment_catalog().get("ammunition_catalog",{}).get("item_arrow")
        if not isinstance(arrow,Mapping): raise ValueError("arrow definition missing")
        launch=float(bow_profile.get("launch_speed_mps",1)) if isinstance(bow_profile,Mapping) else 1.0; projectile_profile=projectile_contact_profile(arrow,launch_speed_mps=launch,impact_speed_mps=launch*max(0,int(interception.get("speed_factor_milli",1000)))/1000.0,accuracy_margin=precision)
    elif action.action_kind=="hidden_weapon_throw":
        launch=float((action.weapon or {}).get("projectile_speed_mps",1)); projectile_profile=projectile_contact_profile(action.weapon or {},launch_speed_mps=launch,impact_speed_mps=launch*max(0,int(interception.get("speed_factor_milli",1000)))/1000.0,accuracy_margin=precision)
    motion_milli=_mounted_weapon_motion_milli(
        actor_ref,people[actor_ref],equipment_ledger,actor_state,
        action_kind=action.action_kind,weapon=action.weapon,
    )
    damage=_contact_damage(
        actor=people[actor_ref],defender=defender,weapon=action.weapon,weapon_ref=action.weapon_ref,
        action_kind=action.action_kind,range_m=range_m,defense_force_milli=decision_force_milli,
        hit_zone=action.hit_zone,target_structure_ref=action.target_structure_ref,created_at=str(at_ms),
        projectile_profile=projectile_profile,precision_margin=precision,qi_result=qi_result,
        motion_milli=motion_milli,mount_state=(defender_state.get("mount") if isinstance(defender_state.get("mount"),Mapping) else None),
    )
    wound=damage.get("wound"); physiology=None; poison=None
    mount_result=damage.get("mount_result")
    if isinstance(mount_result,Mapping):
        mount=defender_state.get("mount") if isinstance(defender_state.get("mount"),dict) else None
        if isinstance(mount,dict):
            mount["condition_milli"]=max(0,min(1000,int(mount_result.get("condition_after_milli",mount.get("condition_milli",1000)))))
            mount["status"]=str(mount_result.get("status") or mount.get("status") or "active")
            if mount["status"] in {"disabled","dead"}:
                mount["active"]=False
                mount["disabled_at_ms"]=int(at_ms)
                if bool(mount_result.get("service_loss")):
                    mount["service_loss_pending"]=True
                # Losing the mount is a real shared-clock interruption, but it
                # is not itself a human wound. The rider remains at the same
                # world position, loses the mounted platform immediately, and
                # must recover posture before acting normally again.
                defender_state["balance_milli"]=min(int(defender_state.get("balance_milli",1000)),520)
                defender_state["limb_commitment_milli"]=max(int(defender_state.get("limb_commitment_milli",0)),500)
                defender_state["recovery_until_ms"]=max(int(defender_state.get("recovery_until_ms",0)),at_ms+550)
                positions[actual_ref]["stance"]="unhorsed"
    if isinstance(wound,Mapping):
        health=copy.deepcopy(defender.get("health",{})); health["injuries"]=record_current_wound(health.get("injuries",[]) if isinstance(health.get("injuries"),list) else [],wound); defender["health"]=health
        if action.poison_ref and any(int(wound.get(key,0))>0 for key in ("cut","pierce","penetration")):
            attrs=_attrs(defender); current=int((defender.get("poison_burdens",{}) or {}).get(action.poison_ref,0)) if isinstance(defender.get("poison_burdens"),Mapping) else 0
            poison=apply_poison(poison_ref=action.poison_ref,current_burden=current,doses=1,endurance=int(attrs.get("endurance",0)),qi=int(defender.get("qi",0)),qi_control=int(defender.get("qi_control",0)))
            added=max(0,int(poison.get("burden_added",0)))
            if added>0:
                exposed=_combat_timestamp(combat,at_ms)
                queued=queue_progressive_poison_exposure(
                    pending_burdens=defender.get("pending_poison_burdens") if isinstance(defender.get("pending_poison_burdens"),Mapping) else {},
                    poison_ref=action.poison_ref,burden_added=added,exposed_at=exposed,
                )
                defender["pending_poison_burdens"]=queued["pending_after"]
        physiology=_apply_physiology(defender,elapsed_seconds=0,at_iso=_combat_timestamp(combat,at_ms))
        # Structural losses must take effect during the same fight.  Capability
        # calculations already read wounds directly; refresh the derived status
        # families too so subsequent reactions/actions see blindness, lost limbs,
        # or severed tendons immediately instead of waiting for another load.
        _refresh_structural_statuses(defender_state, defender)
        if defender["health"].get("status") in {"dead","incapacitated"}:
            statuses=set(defender_state.get("status_families",[])); status="dead" if defender["health"].get("status")=="dead" else "incapacitated"; statuses.add(status); defender_state["status_families"]=sorted(statuses); defender_state.setdefault("incapacitated_at_ms",at_ms)
    if isinstance(mount_result,Mapping):
        result_kind="mount_disabled" if str(mount_result.get("status")) in {"disabled","dead"} else "mount_contact"
    else:
        result_kind="contact" if wound else "physical_contact_no_wound"
    return {**event_base,"actual_ref":actual_ref,"result":result_kind,"defense":decision.trace(),"defense_pressure":defense_pressure,"contact":contact,"damage":damage,"physiology":physiology,"poison":poison,"interception":interception,"resource_commit":resource_commit if channel=="projectile" else None,"precision_margin":precision,"bow_profile":bow_profile,"qi":qi_result,"defense_qi":defense_qi_result,"fatigue":action_exertion,"defense_fatigue":defense_exertion}


def _npc_target_structure(actor: Mapping[str, Any], target: Mapping[str, Any], *, intent: str, familiarity: int = 0) -> str | None:
    chosen=intent_target(actor,intent=intent,target=target)
    if chosen: return chosen
    attrs=_attrs(actor); skills=_skills(actor); tactical=(int(attrs.get("intelligence",0))+int(attrs.get("perception",0))+max(int(skills.get("sword",0)),int(skills.get("spear",0)),int(skills.get("unarmed",0)),int(skills.get("bow",0))))//3 + max(0,min(100,int(familiarity)))//3
    if tactical<55: return None
    damaged=[row for row in _wounds(target) if isinstance(row.get("structure_ref"),str) and int(row.get("structure_damage",row.get("severity",0)))>0]
    return str(max(damaged,key=lambda row:(int(row.get("structure_damage",row.get("severity",0))),str(row.get("structure_ref"))))["structure_ref"]) if damaged else None



def combat_force_context(combat: Mapping[str, Any]) -> str:
    """Map exact combat state to one closed force-policy context."""
    objective = combat.get("objective", {}) if isinstance(combat.get("objective"), Mapping) else {}
    kind = str(objective.get("kind") or "").lower()
    if kind in {"tournament_match", "spar", "training_match", "friendly_duel"}:
        return "tournament_nonlethal" if kind == "tournament_match" else "formal_spar"
    if kind in {"capture", "subdue", "escape_or_subdue", "arrest", "detain"}:
        return "capture_objective"
    awareness = str(combat.get("awareness_mode") or "mutual")
    if awareness in {"side_a_ambush", "side_b_ambush"}:
        return "ambush"
    if kind in {"battlefield", "faction_battle", "war_battle", "siege", "raid_battle"} or "war" in kind or "battle" in kind:
        return "battlefield"
    if kind in {"eliminate", "protect_cargo", "protect", "retain_seized_people_and_cargo", "hostile_contact", "assassination"}:
        return "lethal_attack"
    return "default"


def combat_default_targeting_intent(
    combat: Mapping[str, Any], *, doctrine_ref: str | None = None,
    faction_doctrine: Mapping[str, Any] | None = None,
) -> str:
    """Resolve an autonomous combatant's force intent from real doctrine.

    Explicit individual policy is the strongest standing authority.  People
    without a personal doctrine still inherit their faction's authored
    lethality propensity instead of every institution collapsing to the same
    hostile-context default.  Formal spar/tournament/capture contexts remain
    nonlethal regardless of institutional aggressiveness.
    """
    personal = resolve_individual_doctrine(doctrine_ref) if isinstance(doctrine_ref, str) and doctrine_ref else None
    context = combat_force_context(combat)
    if isinstance(personal, Mapping):
        return resolve_force_intent(personal, context)
    return resolve_faction_force_intent(faction_doctrine, context)


def _present_body_refs(combat: Mapping[str, Any]) -> list[str]:
    """Return bodies that are physically present on the combat field."""
    states = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    body: list[str] = []
    for refs in combat.get("sides", {}).values():
        for ref in refs:
            if not isinstance(ref, str) or ref not in positions:
                continue
            state = states.get(ref)
            statuses = {str(x) for x in state.get("status_families", []) if isinstance(x, str)} if isinstance(state, Mapping) else set()
            if "reinforcing" in statuses:
                continue
            body.append(ref)
    return body


def _npc_withdrawal_decision(*, combat: Mapping[str, Any], actor_ref: str, people: Mapping[str, Mapping[str, Any]], faction_doctrine: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a deterministic autonomous withdrawal declaration, if warranted."""
    states=combat.get("combatants",{}) if isinstance(combat.get("combatants"),Mapping) else {}
    state=states.get(actor_ref); person=people.get(actor_ref)
    if not isinstance(state,Mapping) or not isinstance(person,Mapping) or not _active(person,state): return None
    try: side=_side_of(combat,actor_ref)
    except KeyError: return None
    doctrine=faction_doctrine if isinstance(faction_doctrine,Mapping) else {}
    preservation=max(0,min(100,int(doctrine.get("casualty_preservation",55))))
    discipline=max(0,min(100,int(doctrine.get("withdrawal_discipline",50))))
    health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
    capacity=functional_capacity_factors(_wounds(person))
    function_floor=min(max(0,int(capacity.get(key,1000))) for key in ("combat_movement_milli","manual_milli","vision_milli","respiratory_milli"))
    consciousness=max(0,int(health.get("consciousness",100))); shock=max(0,int(health.get("shock",0))); blood_lost=max(0,int(health.get("blood_lost_ml",0)))
    critical=consciousness<=55 or shock>=60 or blood_lost>=700 or function_floor<500
    impaired=consciousness<80 or shock>=35 or blood_lost>=350 or function_floor<750
    arrived=[]
    for ref in combat.get("sides",{}).get(side,[]):
        ref_state=states.get(ref)
        if not isinstance(ref_state,Mapping): continue
        statuses={str(x) for x in ref_state.get("status_families",[]) if isinstance(x,str)}
        if "reinforcing" not in statuses: arrived.append(str(ref))
    active_arrived=[ref for ref in arrived if ref in people and _active(people[ref],states[ref])]
    losses=max(0,len(arrived)-len(active_arrived)); loss_percent=losses*100//max(1,len(arrived))
    collapse_threshold=max(20,min(75,90-preservation//2-discipline//3))
    side_collapse=len(arrived)>=2 and loss_percent>=collapse_threshold
    preservation_trigger=preservation>=70 and impaired
    formal=combat_force_context(combat) in {"formal_spar","tournament_nonlethal"}
    if not (critical if formal else (critical or side_collapse or preservation_trigger)): return None
    body=_present_body_refs(combat)
    if not list(open_retreat_corridors(combat.get("positions",{}),actor_ref=actor_ref,body_refs=body,obstacles=combat.get("obstacles",[]))): return None
    reason="critical_condition" if critical else "side_collapse" if side_collapse else "casualty_preservation"
    return {"reason":reason,"casualty_preservation":preservation,"withdrawal_discipline":discipline,"arrived_side_count":len(arrived),"active_arrived_count":len(active_arrived),"loss_percent":loss_percent,"collapse_threshold_percent":collapse_threshold,"condition":{"consciousness":consciousness,"shock":shock,"blood_lost_ml":blood_lost,"functional_floor_milli":function_floor}}


def _rally_withdrawal_attempt(
    *, leader_ref: str, ally_ref: str, people: Mapping[str, Mapping[str, Any]],
    withdrawal: Mapping[str, Any],
) -> dict[str, Any]:
    """Contest one noncritical allied withdrawal with existing human capability.

    This is deliberately not a morale subsystem. It derives one immediate
    leadership contest from the leader's real Command/Willpower/Intelligence,
    the ally's own resolve, and the already-authoritative withdrawal pressure.
    Critical medical/functional withdrawal remains non-negotiable.
    """
    leader=people.get(leader_ref,{}) if isinstance(people.get(leader_ref,{}),Mapping) else {}
    ally=people.get(ally_ref,{}) if isinstance(people.get(ally_ref,{}),Mapping) else {}
    leader_skills=_skills(leader); ally_skills=_skills(ally)
    leader_attrs=_attrs(leader); ally_attrs=_attrs(ally)
    leadership=(
        max(0,int(leader_skills.get("command",0)))*4
        + max(0,int(leader_attrs.get("willpower",0)))*2
        + max(0,int(leader_attrs.get("intelligence",0)))
        + max(0,int(ally_attrs.get("willpower",0)))
        + max(0,int(ally_skills.get("command",0)))
    )
    condition=withdrawal.get("condition",{}) if isinstance(withdrawal.get("condition"),Mapping) else {}
    reason=str(withdrawal.get("reason") or "")
    consciousness=max(0,min(100,int(condition.get("consciousness",100))))
    shock=max(0,int(condition.get("shock",0)))
    blood_lost=max(0,int(condition.get("blood_lost_ml",0)))
    functional_floor=max(0,min(1000,int(condition.get("functional_floor_milli",1000))))
    collapse_over=max(
        0,
        int(withdrawal.get("loss_percent",0))-int(withdrawal.get("collapse_threshold_percent",0)),
    )
    pressure=(
        260
        + collapse_over*5
        + shock*3
        + max(0,100-consciousness)*4
        + blood_lost//5
        + max(0,1000-functional_floor)//3
        + max(0,int(withdrawal.get("casualty_preservation",0))-50)
        + max(0,int(withdrawal.get("withdrawal_discipline",0))-50)//2
    )
    if reason=="casualty_preservation":
        pressure+=80
    if reason=="critical_condition":
        return {
            "attempted":True,"success":False,"reason":"critical_condition_not_overridable",
            "leadership_score":leadership,"withdrawal_pressure_score":max(pressure,1000),
        }
    success=leadership>=pressure
    return {
        "attempted":True,"success":success,
        "reason":"rally_strength_met_withdrawal_pressure" if success else "withdrawal_pressure_held",
        "leadership_score":leadership,"withdrawal_pressure_score":pressure,
    }


def _disengage_step(*, combat: dict[str, Any], actor_ref: str, people: Mapping[str, Mapping[str, Any]] | None, equipment_ledger: Mapping[str, Any] | None, duration_ms: int, start_ms: int) -> dict[str, Any]:
    """Move one fighter through a disengagement slice without advancing the clock."""
    if actor_ref not in combat.get("combatants",{}) or actor_ref not in combat.get("positions",{}): raise ValueError("combat actor unresolved")
    state=combat["combatants"][actor_ref]
    if not status_action_allowed(state.get("status_families",[]),"disengage"): return {"moved":False,"escaped":False,"reason":"status_blocks_disengagement"}
    body=_present_body_refs(combat)
    corridors=list(open_retreat_corridors(combat["positions"],actor_ref=actor_ref,body_refs=body,obstacles=combat.get("obstacles",[])))
    if not corridors: return {"moved":False,"escaped":False,"reason":"no_open_retreat_corridor"}
    chosen=sorted(corridors,key=lambda row:int(row.get("angle_mdeg",0)))[0]; row=combat["positions"][actor_ref]
    start_x,start_y=int(row["x_mm"]),int(row["y_mm"]); end_x,end_y=int(chosen["end_x_mm"]),int(chosen["end_y_mm"]); duration=max(1,int(duration_ms))
    if isinstance(people,Mapping) and actor_ref in people:
        cap=capability_from_person(people[actor_ref]); speed=movement_speed_mmps(cap)
        if isinstance(equipment_ledger,Mapping): speed=max(speed,_movement_speed_for_state(actor_ref,people[actor_ref],equipment_ledger,state,cap))
        maximum=max(0,speed)*duration//1000; dx,dy=end_x-start_x,end_y-start_y; distance=max(1,math.isqrt(dx*dx+dy*dy))
        if distance>maximum: end_x=start_x+dx*maximum//distance; end_y=start_y+dy*maximum//distance
    if not path_clear(combat["positions"],actor_ref=actor_ref,end_x_mm=end_x,end_y_mm=end_y,body_refs=body,obstacles=combat.get("obstacles",[])): return {"moved":False,"escaped":False,"reason":"retreat_path_became_blocked","corridor":chosen}
    row["x_mm"]=end_x; row["y_mm"]=end_y; row["facing_mdeg"]=int(chosen["angle_mdeg"])%360000; row["stance"]="disengaging"
    state["recovery_until_ms"]=max(int(state.get("recovery_until_ms",0)),int(start_ms)+duration+250)
    side=_side_of(combat,actor_ref); enemy_side="side_b" if side=="side_a" else "side_a"; enemies=[]
    for ref in combat.get("sides",{}).get(enemy_side,[]):
        if ref not in combat.get("positions",{}): continue
        enemy_state=combat.get("combatants",{}).get(ref,{})
        statuses={str(x) for x in enemy_state.get("status_families",[]) if isinstance(x,str)} if isinstance(enemy_state,Mapping) else set()
        if "ring_out" in statuses: continue
        if isinstance(people,Mapping) and ref in people and isinstance(enemy_state,Mapping):
            if not _active(people[ref],enemy_state): continue
        elif statuses & {"dead","unconscious","incapacitated","escaped","reinforcing"}: continue
        enemies.append(ref)
    nearest=min([planar_distance_mm(row,combat["positions"][ref]) for ref in enemies],default=999_999)
    escape_at=int(start_ms)+duration
    pending=combat.get("_pending_actions",{}) if isinstance(combat.get("_pending_actions"),Mapping) else {}
    committed_melee_pursuit=False
    if nearest>=6000:
        for attacker_ref,pending_action in pending.items():
            if not isinstance(attacker_ref,str) or not isinstance(pending_action,Mapping):
                continue
            if str(pending_action.get("target_ref") or "")!=actor_ref:
                continue
            try:
                if _side_of(combat,attacker_ref)==side:
                    continue
            except KeyError:
                continue
            if str(pending_action.get("delivery") or "direct") in {"projectile","ranged","thrown"}:
                continue
            if int(pending_action.get("commit_at_ms",10**18))>escape_at:
                continue
            if int(pending_action.get("contact_at_ms",-1))<escape_at:
                continue
            attacker_state=combat.get("combatants",{}).get(attacker_ref)
            if isinstance(people,Mapping) and attacker_ref in people and isinstance(attacker_state,Mapping):
                if not _active(people[attacker_ref],attacker_state):
                    continue
            committed_melee_pursuit=True
            break
    escaped=nearest>=6000 and not committed_melee_pursuit
    if escaped:
        statuses={str(x) for x in state.get("status_families",[]) if isinstance(x,str)}; statuses.add("escaped"); state["status_families"]=sorted(statuses)
        state["escaped_at_ms"]=escape_at
    reason=(
        "cleared_opponent_reach" if escaped
        else "retreat_contested_by_committed_melee" if nearest>=6000 and committed_melee_pursuit
        else "retreat_in_progress"
    )
    return {"moved":True,"escaped":escaped,"reason":reason,"corridor":chosen,"movement":{"start_x_mm":start_x,"start_y_mm":start_y,"end_x_mm":end_x,"end_y_mm":end_y,"duration_ms":duration,"nearest_enemy_mm":nearest}}


def _resolve_withdrawal_batch(*, combat: dict[str, Any], withdrawer_refs: Sequence[str], people: dict[str, dict[str, Any]], equipment_ledger: Mapping[str, Any], start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    """Settle concurrent autonomous withdrawals at one shared timeline boundary."""
    end=max(int(start_ms),int(end_ms))
    _settle_combat_physiology_until(combat,people,target_ms=max(int(combat.get("elapsed_ms",0)),end),equipment_ledger=equipment_ledger)
    events=[]
    for ref in withdrawer_refs:
        state=combat.get("combatants",{}).get(ref)
        if ref not in people or not isinstance(state,dict) or not _active(people[ref],state):
            interrupted_at=state.get("incapacitated_at_ms") if isinstance(state,Mapping) else None
            at=max(int(start_ms),min(end,int(interrupted_at))) if isinstance(interrupted_at,int) else end
            events.append({"actor_ref":ref,"result":"withdrawal_interrupted","decision_origin":"actor_ai","started_at_ms":int(start_ms),"ended_at_ms":at})
            continue
        step=_disengage_step(combat=combat,actor_ref=ref,people=people,equipment_ledger=equipment_ledger,duration_ms=max(1,end-int(start_ms)),start_ms=int(start_ms))
        events.append({"actor_ref":ref,"result":("withdrew_from_combat" if step.get("escaped") else "withdrawal_in_progress" if step.get("moved") else "withdrawal_blocked"),"decision_origin":"actor_ai","started_at_ms":int(start_ms),"ended_at_ms":end,"withdrawal":step})
    return events


def resolve_exchange(*, combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], equipment_ledger: Mapping[str, Any], doctrines: Mapping[str, Mapping[str, Any]], player_ref: str, player_action_kind: str, player_target_ref: str, player_weapon_ref: str, player_hit_zone: str = "chest", player_target_structure_ref: str | None = None, player_targeting_intent: str = "disable", player_poison_ref: str | None = None, player_qi_allocation_milli: Mapping[str, int] | None = None, player_qi_reserve_milli: int | None = None, player_auto_qi: bool = False, player_auto_poison: bool = False, npc_targeting_intent: str | None = None, martial_familiarity: Mapping[str, Any] | None = None, player_retinue_context: Mapping[str, Any] | None = None, player_improvised_weapon_state: Mapping[str, Any] | None = None, player_rally_allies: bool = False, equipment_ledger_hydrated: bool = False, compact_equipment_result: bool = True, mutate_equipment_ledger: bool = False, mutate_state: bool = False) -> dict[str, Any]:
    # Interactive/public callers keep copy-on-resolve semantics. Autonomous
    # bounded combat already owns private combat/person copies, so it may reuse
    # those objects across exchanges instead of cloning the whole local fight
    # every exchange. This changes allocation cost only, never combat rules.
    if mutate_state:
        if not isinstance(combat, dict) or not isinstance(people, dict):
            raise ValueError("mutable combat state must be dict-backed")
        if any(not isinstance(person, dict) for person in people.values()):
            raise ValueError("mutable combat people must be dict-backed")
        out=combat
        persons=people
    else:
        out=copy.deepcopy(dict(combat)); persons={ref:copy.deepcopy(dict(person)) for ref,person in people.items()}
    if equipment_ledger_hydrated:
        if mutate_equipment_ledger:
            if not isinstance(equipment_ledger,dict): raise ValueError("mutable hydrated equipment ledger must be dict")
            ledger=equipment_ledger
        else:
            ledger=copy.deepcopy(dict(equipment_ledger))
    else:
        ledger=hydrate_equipment_ledger(equipment_ledger)
    if out.get("status")!="active": raise ValueError("combat not active")
    if player_ref not in persons or player_ref not in out.get("combatants",{}): raise ValueError("player not in combat")
    if player_improvised_weapon_state is not None:
        if not isinstance(player_improvised_weapon_state, Mapping):
            raise ValueError("improvised weapon state invalid")
        incoming=copy.deepcopy(dict(player_improvised_weapon_state))
        if incoming.get("kind")!="scene_improvised_weapon_state" or str(incoming.get("holder_ref") or "")!=player_ref:
            raise ValueError("improvised weapon state invalid")
        state=out["combatants"][player_ref]
        existing=state.get("improvised_weapon_state") if isinstance(state,Mapping) else None
        if isinstance(existing,Mapping) and str(existing.get("fact_ref") or "")==str(incoming.get("fact_ref") or ""):
            # Never reset wear/breakage by resupplying the same scene fact.
            incoming=copy.deepcopy(dict(existing))
        state["improvised_weapon_state"]=incoming
    npc_targeting_intent_override=npc_targeting_intent
    if player_targeting_intent not in {"disable","lethal"}: raise ValueError("targeting intent invalid")
    if npc_targeting_intent_override is not None and npc_targeting_intent_override not in {"disable","lethal"}: raise ValueError("targeting intent invalid")
    if player_qi_allocation_milli is not None:
        if not isinstance(player_qi_allocation_milli, Mapping): raise ValueError("qi allocation invalid")
        allocation={str(k):max(0,int(v)) for k,v in player_qi_allocation_milli.items() if max(0,int(v))>0}
        if sum(allocation.values())>1000: raise ValueError("qi allocation exceeds whole-body share")
        out["combatants"][player_ref]["qi_allocation_milli"]=allocation
    # Explicit player allocations are never silently constrained by a standing
    # doctrine reserve. Autonomous/delegated callers may pass a reserve here;
    # shorthand player attacks instead compute their standing reserve below
    # after the retinue overlay is available.
    if player_qi_reserve_milli is None:
        out["combatants"][player_ref].pop("qi_reserve_milli",None)
    else:
        out["combatants"][player_ref]["qi_reserve_milli"]=max(0,int(player_qi_reserve_milli))
    now_ms=int(out.get("elapsed_ms",0))
    for ref,state in out.get("combatants",{}).items():
        if not isinstance(state,dict): continue
        statuses=list(state.get("status_families",[])) if isinstance(state.get("status_families"),list) else []
        if "reinforcing" in statuses and now_ms>=max(0,int(state.get("reinforcement_at_ms",0))):
            state["status_families"]=[x for x in statuses if x!="reinforcing"]
            enemy_side="side_b" if _side_of(out,str(ref))=="side_a" else "side_a"
            state["observed_refs"]=[str(x) for x in out.get("sides",{}).get(enemy_side,[])]
            state["awareness_confidence_milli"]=1000
    for side in ("side_a","side_b"):
        members=out.get("sides",{}).get(side,[]); faction_ref=persons[members[0]].get("faction_ref") if members else None; _refresh_team_plan(out,side=side,people=persons,doctrine=doctrines.get(str(faction_ref),{}))
    for ref,state in out.get("combatants",{}).items():
        if ref==player_ref or not isinstance(state,dict) or ref not in persons or not _active(persons[ref],state):
            continue
        faction_ref=str(persons[ref].get("faction_ref") or "")
        faction_doctrine=doctrines.get(faction_ref,{})
        state["qi_reserve_milli"]=_npc_qi_reserve_milli(persons[ref],faction_doctrine)
        state["qi_allocation_milli"]=_npc_qi_allocation(persons[ref],faction_doctrine)
    retinue_coordinated_refs: set[str] = set()
    retinue_resource_discipline: Mapping[str, Any] = {}
    retinue_resource_override = False
    if isinstance(player_retinue_context, Mapping):
        retinue_leader=str(player_retinue_context.get("leader_ref") or "")
        doctrine_ref=player_retinue_context.get("combat_doctrine_ref")
        member_refs=player_retinue_context.get("member_refs") if isinstance(player_retinue_context.get("member_refs"),list) else []
        temporary_member_refs=player_retinue_context.get("temporary_member_refs") if isinstance(player_retinue_context.get("temporary_member_refs"),list) else []
        member_roles=player_retinue_context.get("member_roles") if isinstance(player_retinue_context.get("member_roles"),Mapping) else {}
        if retinue_leader==player_ref and player_ref in out.get("combatants",{}):
            retinue_side=_side_of(out,player_ref); enemy_side="side_b" if retinue_side=="side_a" else "side_a"
            allied_refs={str(ref) for ref in out.get("sides",{}).get(retinue_side,[]) if isinstance(ref,str)}
            permanent_refs=[str(x) for x in member_refs if isinstance(x,str) and str(x) in allied_refs]
            temporary_refs=[str(x) for x in temporary_member_refs if isinstance(x,str) and str(x) in allied_refs]
            active_enemies=[ref for ref in out.get("sides",{}).get(enemy_side,[]) if _active(persons[ref],out["combatants"][ref])]
            retinue_doctrine=resolve_player_retinue_doctrine(str(doctrine_ref)) if isinstance(doctrine_ref,str) and doctrine_ref else None
            retinue_resource_discipline=(
                retinue_doctrine.get("resource_discipline",{})
                if isinstance(retinue_doctrine,Mapping) and isinstance(retinue_doctrine.get("resource_discipline"),Mapping) else {}
            )
            temporary_rule=retinue_doctrine.get("temporary_members",{}) if isinstance(retinue_doctrine,Mapping) and isinstance(retinue_doctrine.get("temporary_members"),Mapping) else {}
            temporary_inherit=bool(temporary_rule.get("inherit_retinue_coordination",False))
            coordinated_refs=[*permanent_refs,*(temporary_refs if temporary_inherit else [])]
            retinue_coordinated_refs=set(coordinated_refs)
            known:set[str]=set()
            for ref in [player_ref,*coordinated_refs]:
                if ref in out.get("combatants",{}) and ref in persons and _active(persons[ref],out["combatants"][ref]):
                    known.update(_observe_visible_enemies(out,actor_ref=ref,enemy_refs=active_enemies,people=persons,at_ms=now_ms))
            overlay=plan_player_retinue_exchange(
                side_ref=retinue_side,leader_ref=player_ref,permanent_member_refs=permanent_refs,
                temporary_member_refs=temporary_refs,
                member_roles={str(k):str(v) for k,v in member_roles.items()},known_enemy_refs=sorted(known),records=persons,
                positions=out["positions"],obstacles=out.get("obstacles",[]),doctrine=retinue_doctrine,at_ms=now_ms,
            )
            base=out.setdefault("team_plans",{}).setdefault(retinue_side,{})
            base_assignments=base.setdefault("assignments",{})
            if isinstance(base_assignments,dict):
                for ref,row in overlay.get("assignments",{}).items():
                    if ref in {player_ref,*coordinated_refs} and isinstance(row,Mapping):
                        base_assignments[ref]=copy.deepcopy(dict(row))
            # The persistent team-plan schema stays generic. Wei's special
            # doctrine is compiled into ordinary tactical fields rather than
            # adding a second mutable doctrine owner or ad-hoc overlay keys.
            for key in ("plan_id","primary_threat_ref","primary_threat_score","primary_position_snapshot","tactical_problem","coordination_latency_ms"):
                if key in overlay:
                    base[key]=copy.deepcopy(overlay[key])
            base["desired_states"]=list(dict.fromkeys([
                *[str(x) for x in base.get("desired_states",[]) if isinstance(x,str)],
                *[str(x) for x in overlay.get("desired_states",[]) if isinstance(x,str)],
            ]))
            active_core=[
                ref for ref in [player_ref,*coordinated_refs]
                if ref in persons and ref in out.get("combatants",{}) and _active(persons[ref],out["combatants"][ref])
            ]
            materially_dangerous=_aggregate_resource_danger(out,actor_ref=player_ref,people=persons)
            outnumbered=len(active_enemies)>max(1,len(active_core)) and materially_dangerous
            leader_assignment=overlay.get("assignments",{}).get(player_ref,{}) if isinstance(overlay.get("assignments"),Mapping) else {}
            principal_overflow=(
                materially_dangerous
                and isinstance(leader_assignment,Mapping)
                and str(leader_assignment.get("role") or "")!="reserve"
            )
            retinue_resource_override=(
                bool(retinue_resource_discipline.get("outnumbered_override",False)) and outnumbered
            ) or (
                bool(retinue_resource_discipline.get("principal_overflow_override",False)) and principal_overflow
            )
    # A terse player attack delegates only omitted tactical details. Resolve
    # automatic Qi/poison after the retinue overlay exists so Wei's personal
    # doctrine and the active team doctrine compose under one resource policy.
    if player_auto_qi or player_auto_poison:
        if player_target_ref not in persons:
            raise ValueError("player target unresolved")
        player_faction_doctrine=doctrines.get(str(persons[player_ref].get("faction_ref") or ""),{})
        player_resource_policy=automatic_resource_policy(
            combat=out,actor_ref=player_ref,target_ref=player_target_ref,people=persons,equipment_ledger=ledger,
            faction_doctrine=player_faction_doctrine,action_kind=player_action_kind,weapon_ref=player_weapon_ref,
            intent=player_targeting_intent,team_resource_discipline=retinue_resource_discipline,
            team_escalation_override=retinue_resource_override,social_state=martial_familiarity,
        )
        if player_auto_qi:
            out["combatants"][player_ref]["qi_allocation_milli"]=copy.deepcopy(dict(player_resource_policy.get("qi_allocation_milli",{})))
            out["combatants"][player_ref]["qi_reserve_milli"]=max(0,int(player_resource_policy.get("qi_reserve_milli",0)))
        if player_auto_poison:
            player_poison_ref=player_resource_policy.get("poison_ref")
    declared_exchange_ms=int(out.get("elapsed_ms",0))
    active_at_declaration=[ref for refs in out["sides"].values() for ref in refs if _active(persons[ref],out["combatants"][ref])]; scheduled=[]; declaration_events=[]; withdrawing=[]
    # Withdrawal is a declaration-time intent. Mark every withdrawing actor
    # before scheduling anyone else's action so pursuit doctrine sees the same
    # physical posture regardless of side/list iteration order.
    player_side=_side_of(out,player_ref)
    for actor_ref in active_at_declaration:
        if actor_ref==player_ref: continue
        withdrawal=_npc_withdrawal_decision(combat=out,actor_ref=actor_ref,people=persons,faction_doctrine=doctrines.get(str(persons[actor_ref].get("faction_ref") or ""),{}))
        if withdrawal is None: continue
        if bool(player_rally_allies) and _side_of(out,actor_ref)==player_side:
            rally=_rally_withdrawal_attempt(leader_ref=player_ref,ally_ref=actor_ref,people=persons,withdrawal=withdrawal)
            rally_success=bool(rally.get("success"))
            declaration_events.append({
                "actor_ref":actor_ref,"leader_ref":player_ref,
                "result":"rally_held_position" if rally_success else "rally_failed",
                "decision_origin":"player_command","declared_at_ms":declared_exchange_ms,
                "rally":rally,"withdrawal":withdrawal,
            })
            if rally_success:
                out["positions"][actor_ref]["stance"]="ready"
                out["positions"][actor_ref]["vx_mmps"]=0
                out["positions"][actor_ref]["vy_mmps"]=0
                continue
        withdrawing.append(actor_ref)
        out["positions"][actor_ref]["stance"]="disengaging"
        declaration_events.append({"actor_ref":actor_ref,"result":"withdrawal_declared","decision_origin":"actor_ai","declared_at_ms":declared_exchange_ms,"withdrawal":withdrawal})
    for actor_ref in active_at_declaration:
        if actor_ref in withdrawing: continue
        side=_side_of(out,actor_ref); enemy_side="side_b" if side=="side_a" else "side_a"; enemies=[ref for ref in out["sides"][enemy_side] if _active(persons[ref],out["combatants"][ref])]
        known=_observe_visible_enemies(out,actor_ref=actor_ref,enemy_refs=enemies,people=persons,at_ms=int(out.get("elapsed_ms",0)))
        if not known: declaration_events.append({"actor_ref":actor_ref,"result":"no_lawfully_known_target","decision_origin":"awareness"}); continue
        if actor_ref==player_ref:
            target=player_target_ref; kind=player_action_kind; weapon_ref=player_weapon_ref; poison_ref=player_poison_ref; provenance="player"; target_structure=player_target_structure_ref
            if target not in known: declaration_events.append({"actor_ref":actor_ref,"result":"target_not_observed","decision_origin":provenance}); continue
            if not _weapon_owned(ledger,actor_ref,weapon_ref,out["combatants"].get(actor_ref)):
                declaration_events.append({"actor_ref":actor_ref,"result":"weapon_not_owned","decision_origin":provenance,"weapon_ref":weapon_ref})
                continue
            if target_structure in {None,"","auto"} and player_hit_zone=="auto": target_structure=intent_target(persons[actor_ref],intent=player_targeting_intent,target=persons[target])
            hit_zone=player_hit_zone if player_hit_zone!="auto" else target_zone(structure_ref=target_structure) if target_structure else "chest"
        else:
            plan=out.get("team_plans",{}).get(side,{})
            assignment = _ready_team_assignment(
                plan if isinstance(plan, Mapping) else None,
                actor_ref,
                at_ms=int(out.get("elapsed_ms", 0)),
            )
            social_view=martial_familiarity or {}
            pressure_by_target={
                ref: hostile_target_pressure(
                    social_view, actor_ref=actor_ref, target_ref=ref,
                    target_faction_ref=str(persons[ref].get("faction_ref") or ""),
                )
                for ref in known
            }
            assigned_target=assignment.get("target_ref") if assignment.get("target_ref") in known else None
            # Geometry is the first targeting constraint. Team assignments and
            # personal hostility choose among physically comparable threats; they
            # do not make an actor ignore somebody already in immediate/near
            # engagement to chase a remote or withdrawing enemy.
            actor_position=out["positions"][actor_ref]
            def _ai_target_key(ref: str) -> tuple[Any, ...]:
                position=out["positions"][ref]
                distance=planar_distance_mm(actor_position,position)
                disengaging=str(position.get("stance") or "")=="disengaging"
                return (
                    _engagement_band(distance,disengaging=disengaging),
                    -pressure_by_target.get(ref,0),
                    distance,
                    ref,
                )
            best_geometry=min(_ai_target_key(ref)[0] for ref in known)
            if (
                assigned_target is not None
                and pressure_by_target.get(assigned_target,0)>-60
                and _ai_target_key(str(assigned_target))[0] <= best_geometry + 1
            ):
                target=str(assigned_target)
            else:
                target=min(known,key=_ai_target_key)
            role=str(assignment.get("role") or "pressure")
            profile=martial_profile(social_view,observer_ref=actor_ref,opponent_ref=target)
            exposure=max(0,int(profile.get("exposure",0))) if isinstance(profile,Mapping) else 0
            if isinstance(profile,Mapping) and exposure>=30 and role in {"pressure","screen","intercept"}:
                melee_pressure=max(0,int(profile.get("melee_pressure",0)))
                control_pressure=max(0,int(profile.get("control_pressure",0)))
                ranged_pressure=max(0,int(profile.get("ranged_pressure",0)))
                if melee_pressure+control_pressure>=ranged_pressure+15:
                    role="control"
            doctrine_ref=persons[actor_ref].get("combat_doctrine_ref") if isinstance(persons[actor_ref].get("combat_doctrine_ref"),str) else None
            actor_faction_doctrine=doctrines.get(str(persons[actor_ref].get("faction_ref") or ""),{})
            npc_intent=npc_targeting_intent_override if npc_targeting_intent_override is not None else combat_default_targeting_intent(
                out,doctrine_ref=doctrine_ref,faction_doctrine=actor_faction_doctrine,
            )
            if npc_intent=="lethal" and vow_conflicts(
                social_view, person_ref=actor_ref, action_kind="attack", target_ref=target,
                target_faction_ref=str(persons[target].get("faction_ref") or ""),
                targeting_intent=npc_intent,
            ):
                npc_intent="disable"
            target_distance_mm=planar_distance_mm(out["positions"][actor_ref],out["positions"][target])
            preferred_action=str(assignment.get("preferred_action") or "attack") if isinstance(assignment,Mapping) else "attack"
            if preferred_action=="hold":
                held=_hold_position_weapon_for(actor_ref,persons[actor_ref],ledger,target_distance_mm=target_distance_mm)
                if held is None:
                    retinue_ai_refs={str(x) for x in (player_retinue_context or {}).get("member_refs",[]) if isinstance(x,str)}
                    if isinstance(player_retinue_context,Mapping):
                        retinue_ai_refs.update(str(x) for x in player_retinue_context.get("temporary_member_refs",[]) if isinstance(x,str) and x in retinue_coordinated_refs)
                    hold_origin="player_retinue_ai" if actor_ref in retinue_ai_refs else "team_ai" if assignment else "actor_ai"
                    declaration_events.append({"actor_ref":actor_ref,"result":"holding_guard_position","decision_origin":hold_origin,"target_ref":target})
                    continue
                kind,weapon_ref=held
            else:
                kind,weapon_ref=_default_weapon_for(actor_ref,persons[actor_ref],ledger,target_distance_mm=target_distance_mm,role=role)
            team_resources=retinue_resource_discipline if actor_ref in retinue_coordinated_refs else None
            resource_policy=automatic_resource_policy(
                combat=out,actor_ref=actor_ref,target_ref=target,people=persons,equipment_ledger=ledger,
                faction_doctrine=actor_faction_doctrine,action_kind=kind,weapon_ref=weapon_ref,intent=npc_intent,
                team_resource_discipline=team_resources,
                team_escalation_override=(retinue_resource_override if actor_ref in retinue_coordinated_refs else False),
                social_state=martial_familiarity,
            )
            out["combatants"][actor_ref]["qi_reserve_milli"]=int(resource_policy["qi_reserve_milli"])
            out["combatants"][actor_ref]["qi_allocation_milli"]=copy.deepcopy(dict(resource_policy["qi_allocation_milli"]))
            poison_ref=resource_policy.get("poison_ref")
            retinue_ai_refs={str(x) for x in (player_retinue_context or {}).get("member_refs",[]) if isinstance(x,str)}
            if isinstance(player_retinue_context,Mapping):
                retinue_ai_refs.update(str(x) for x in player_retinue_context.get("temporary_member_refs",[]) if isinstance(x,str) and x in retinue_coordinated_refs)
            provenance="player_retinue_ai" if actor_ref in retinue_ai_refs else "team_ai" if assignment else "actor_ai"
            target_structure=_npc_target_structure(persons[actor_ref],persons[target],intent=npc_intent,familiarity=exposure)
            hit_zone=target_zone(structure_ref=target_structure) if target_structure else "chest"
        if target not in enemies: declaration_events.append({"actor_ref":actor_ref,"result":"target_unavailable","decision_origin":provenance}); continue
        try: scheduled.append(_schedule_action(combat=out,actor_ref=actor_ref,target_ref=target,action_kind=kind,weapon_ref=weapon_ref,poison_ref=poison_ref,hit_zone=hit_zone,target_structure_ref=target_structure,decision_origin=provenance,people=persons,equipment_ledger=ledger))
        except ValueError as exc: declaration_events.append({"actor_ref":actor_ref,"result":"action_rejected","reason":str(exc),"decision_origin":provenance})
    scheduled.sort(key=lambda row:(row.contact_at_ms,row.commit_at_ms,-_combat_capability_for_state(row.actor_ref,persons[row.actor_ref],ledger,out["combatants"].get(row.actor_ref,{})).reaction,row.actor_ref)); events=list(declaration_events); exchange_end=declared_exchange_ms
    out["_exchange_declared_at_ms"] = declared_exchange_ms
    out["_pending_actions"] = {action.actor_ref: _pending_action_record(action) for action in scheduled}
    out["_defense_interruptions"] = {}
    withdrawal_end=declared_exchange_ms+1000 if withdrawing else None
    withdrawal_pending=bool(withdrawing)
    for action in scheduled:
        # Retreat completes at its own one-second frontier. Contacts at the exact
        # same millisecond resolve first; contacts strictly later see the moved
        # target and may be cancelled if they had not yet committed.
        if withdrawal_pending and isinstance(withdrawal_end,int) and withdrawal_end<int(action.contact_at_ms):
            events.extend(_resolve_withdrawal_batch(combat=out,withdrawer_refs=withdrawing,people=persons,equipment_ledger=ledger,start_ms=declared_exchange_ms,end_ms=withdrawal_end))
            exchange_end=max(exchange_end,withdrawal_end); withdrawal_pending=False
        event=_resolve_scheduled_action(combat=out,action=action,people=persons,equipment_ledger=ledger); events.append(event)
        exchange_end=max(exchange_end,int(out.get("elapsed_ms",0)))
        pending = out.get("_pending_actions", {})
        if isinstance(pending, dict):
            pending.pop(action.actor_ref, None)
    if withdrawal_pending and isinstance(withdrawal_end,int):
        events.extend(_resolve_withdrawal_batch(combat=out,withdrawer_refs=withdrawing,people=persons,equipment_ledger=ledger,start_ms=declared_exchange_ms,end_ms=withdrawal_end))
        exchange_end=max(exchange_end,withdrawal_end)
    if exchange_end<=declared_exchange_ms:
        exchange_end=declared_exchange_ms+max(1,int(_combat_rules().get("minimum_exchange_advance_ms",250)))
    _settle_combat_physiology_until(out,persons,target_ms=max(int(out.get("elapsed_ms",0)),exchange_end),equipment_ledger=ledger)
    out.pop("_pending_actions", None)
    out.pop("_defense_interruptions", None)
    out.pop("_exchange_declared_at_ms", None)
    # Exchange events are returned to the caller for narration/effects but are
    # deliberately not persisted. Current combat geometry, injuries, readiness,
    # recovery and objective state are sufficient authority for the next exchange.
    objective=out.get("objective",{}) if isinstance(out.get("objective"),Mapping) else {}
    if bool(objective.get("ring_out_enabled")):
        radius=max(1,int(objective.get("ring_radius_mm",0)))
        if radius>0:
            for ref,state in out.get("combatants",{}).items():
                if not isinstance(state,dict) or not _active(persons.get(ref,{}),state):
                    continue
                pos=out.get("positions",{}).get(ref,{})
                if not isinstance(pos,Mapping):
                    continue
                if int(pos.get("x_mm",0))**2 + int(pos.get("y_mm",0))**2 > radius**2:
                    statuses=set(state.get("status_families",[])); statuses.add("ring_out")
                    state["status_families"]=sorted(statuses); state["ring_out_at_ms"]=int(out.get("elapsed_ms",0))
    active_a=[ref for ref in out["sides"]["side_a"] if _active(persons[ref],out["combatants"][ref]) and "ring_out" not in out["combatants"][ref].get("status_families",[])]; active_b=[ref for ref in out["sides"]["side_b"] if _active(persons[ref],out["combatants"][ref]) and "ring_out" not in out["combatants"][ref].get("status_families",[])]
    if not active_a or not active_b:
        out["status"]="resolved"; out["winner_side"]="side_a" if active_a else "side_b" if active_b else "none"
        if bool(objective.get("ring_out_enabled")):
            out["resolution_kind"]="ring_out" if active_a or active_b else out.get("resolution_kind","mutual_incapacitation")
    equipment_after=compact_equipment_ledger(ledger) if compact_equipment_result else ledger
    return {"combat_after":out,"people_after":persons,"equipment_ledger_after":equipment_after,"events":events,"active_side_a":active_a,"active_side_b":active_b}



def default_target_for(*, combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], actor_ref: str, martial_familiarity: Mapping[str, Any] | None = None) -> str:
    """Choose one lawfully observed enemy for a high-level attack intent.

    This is not omniscient target selection. It uses the actor's current
    observations, a ready team assignment when one already exists, personal
    hostility/vengeance pressure, distance, and a deterministic ID tie-break.
    """
    if actor_ref not in people or actor_ref not in combat.get("combatants", {}):
        raise ValueError("combat default target actor unresolved")
    side = _side_of(combat, actor_ref)
    enemy_side = "side_b" if side == "side_a" else "side_a"
    states = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    enemies = [
        str(ref) for ref in combat.get("sides", {}).get(enemy_side, [])
        if isinstance(ref, str) and ref in people and ref in states and _active(people[ref], states[ref])
    ]
    known = _observe_visible_enemies(
        combat, actor_ref=actor_ref, enemy_refs=enemies, people=people,
        at_ms=max(0, int(combat.get("elapsed_ms", 0))),
    )
    if not known:
        raise ValueError("no lawfully known combat target")
    plan = combat.get("team_plans", {}).get(side, {}) if isinstance(combat.get("team_plans"), Mapping) else {}
    assignment = _ready_team_assignment(
        plan if isinstance(plan, Mapping) else None, actor_ref,
        at_ms=max(0, int(combat.get("elapsed_ms", 0))),
    )
    social = martial_familiarity or {}
    pressure = {
        ref: hostile_target_pressure(
            social, actor_ref=actor_ref, target_ref=ref,
            target_faction_ref=str(people[ref].get("faction_ref") or ""),
        )
        for ref in known
    }
    assigned = assignment.get("target_ref") if isinstance(assignment, Mapping) else None
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    actor_position = positions.get(actor_ref, {}) if isinstance(positions.get(actor_ref), Mapping) else {}
    engagement=_engagement_doctrine_for(people[actor_ref])
    pursuit=str(engagement.get("pursuit_posture") or "balanced")
    finishing=str(engagement.get("finishing_window") or "cautious")
    def target_key(ref: str) -> tuple[Any, ...]:
        position=positions.get(ref,{}) if isinstance(positions.get(ref),Mapping) else {}
        disengaging=str(position.get("stance") or "")=="disengaging"
        distance_rank=planar_distance_mm(actor_position, position) if position else 10**12
        # Persistent pursuit removes only the extra *withdrawal* penalty. It does
        # not erase physical distance bands, so a fleeing target at 20-30 metres
        # still cannot outrank somebody threatening the actor at arm's reach.
        # When two targets are in the same physical envelope, doctrine may then
        # decide whether the actor keeps pressure on the one trying to escape.
        band=_engagement_band(distance_rank,disengaging=disengaging and pursuit!="persistent")
        # Pursuit doctrine matters inside a geometry class, never above an
        # immediate physical threat. A persistent pursuer is more likely to keep
        # a withdrawing target only when that target remains comparably engaged.
        pursuit_rank=(1 if disengaging else 0) if pursuit=="restrained" else (-1 if disengaging else 0) if pursuit=="persistent" else 0
        finish_rank=0 if finishing=="commit_decisively" and _target_has_finishing_opening(people[ref]) else 1
        return (band,finish_rank,pursuit_rank,-pressure.get(ref,0),distance_rank,ref)
    best_band=min(target_key(ref)[0] for ref in known)
    if assigned in known and pressure.get(str(assigned), 0) > -60 and target_key(str(assigned))[0] <= best_band + 1:
        return str(assigned)
    return min(known,key=target_key)


def default_weapon_for_action(*, people: Mapping[str, Mapping[str, Any]], equipment_ledger: Mapping[str, Any], actor_ref: str, action_kind: str) -> str:
    """Choose a carried weapon compatible with an explicitly named technique.

    The technique is already a player commitment. Doctrine may fill the omitted
    weapon, but it may not replace that technique with a generic attack choice.
    """
    if actor_ref not in people:
        raise ValueError("combat weapon actor unresolved")
    if action_kind == "unarmed_strike":
        return "body_unarmed"
    items = _loadout_items(equipment_ledger, actor_ref)
    weapons = _equipment_catalog().get("weapon_catalog", {})
    carried = [
        str(ref) for ref, quantity in items.items()
        if int(quantity) > 0 and isinstance(weapons.get(ref), Mapping) and _weapon_condition_milli(equipment_ledger, actor_ref, str(ref)) > 0
    ]
    def conditioned(ref: str) -> Mapping[str, Any]:
        row=_weapon_for_holder(equipment_ledger,actor_ref,ref)
        return row if isinstance(row,Mapping) else {}
    def effective_skill(ref: str) -> int:
        discipline=str(weapons[ref].get("discipline") or "")
        return int(_skills(people[actor_ref]).get(discipline,0))*_weapon_condition_milli(equipment_ledger,actor_ref,ref)//1000
    if action_kind == "bow_shot":
        bows = [ref for ref in carried if weapons[ref].get("discipline") == "bow"]
        if not bows or max(0, int(items.get("item_arrow", 0))) <= 0:
            raise ValueError("no carried bow with arrows")
        return max(bows, key=lambda ref: (effective_skill(ref), int(conditioned(ref).get("precision", 0)), ref))
    if action_kind == "hidden_weapon_throw":
        hidden = [ref for ref in carried if weapons[ref].get("discipline") == "hidden_weapons"]
        if not hidden:
            raise ValueError("no carried hidden weapon")
        return max(hidden, key=lambda ref: (effective_skill(ref), int(conditioned(ref).get("precision", 0)), ref))
    if action_kind in {"staff_strike", "staff_thrust", "staff_butt_strike", "staff_sweep"}:
        if max(0, int(items.get("weapon_staff", 0))) <= 0:
            raise ValueError("staff technique requires carried staff")
        return "weapon_staff"
    if action_kind in {"cut", "thrust"}:
        melee = [ref for ref in carried if weapons[ref].get("discipline") in {"sword", "spear"}]
        if not melee:
            raise ValueError("named melee technique has no compatible carried weapon")
        channel = "pierce" if action_kind == "thrust" else "cut"
        return max(melee, key=lambda ref: (int(conditioned(ref).get(channel, 0)), effective_skill(ref), int(conditioned(ref).get("control", 0)), ref))
    raise ValueError("unsupported explicit combat action")


def default_action_for(*, combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], equipment_ledger: Mapping[str, Any], actor_ref: str, target_ref: str, role: str | None = None, martial_familiarity: Mapping[str, Any] | None = None, preferred_weapon_ref: str | None = None) -> tuple[str, str]:
    """Return the deterministic physical action used for a high-level attack.

    ``preferred_weapon_ref`` lets a player say "attack with the staff/sword"
    without also micromanaging the exact strike.  The weapon choice is honored
    when it is actually carried and usable; doctrine/AI still chooses the
    technique for that weapon.
    """
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    if actor_ref not in people or target_ref not in people or actor_ref not in positions or target_ref not in positions:
        raise ValueError("combat default action participant unresolved")
    distance = planar_distance_mm(positions[actor_ref], positions[target_ref])
    if role is None:
        profile=martial_profile(martial_familiarity or {},observer_ref=actor_ref,opponent_ref=target_ref)
        if isinstance(profile,Mapping) and int(profile.get("exposure",0))>=30:
            if int(profile.get("melee_pressure",0))+int(profile.get("control_pressure",0))>=int(profile.get("ranged_pressure",0))+15:
                role="control"
    if isinstance(preferred_weapon_ref,str) and preferred_weapon_ref not in {'','auto'}:
        if preferred_weapon_ref=='body_unarmed':
            return 'unarmed_strike','body_unarmed'
        state=combat.get("combatants",{}).get(actor_ref,{}) if isinstance(combat.get("combatants"),Mapping) else {}
        if not _weapon_owned(equipment_ledger,actor_ref,preferred_weapon_ref,state):
            raise ValueError("preferred combat weapon not carried")
        weapon=_weapon_for_holder(equipment_ledger,actor_ref,preferred_weapon_ref,state)
        if not isinstance(weapon,Mapping):
            raise ValueError("preferred combat weapon unresolved")
        if weapon.get("combat_identity_kind")=="scene_improvised_prop":
            return 'improvised_strike',preferred_weapon_ref
        items=_loadout_items(equipment_ledger,actor_ref)
        discipline=str(weapon.get('discipline') or '')
        if discipline=='bow':
            if max(0,int(items.get('item_arrow',0)))<=0:
                raise ValueError("preferred bow has no arrows")
            return 'bow_shot',preferred_weapon_ref
        if discipline=='hidden_weapons':
            return 'hidden_weapon_throw',preferred_weapon_ref
        if preferred_weapon_ref=='weapon_staff':
            return ('staff_sweep' if role in {'control','shape'} else 'staff_strike'),preferred_weapon_ref
        if discipline in {'sword','spear'}:
            return ('thrust' if int(weapon.get('pierce',0))>=int(weapon.get('cut',0)) else 'cut'),preferred_weapon_ref
        raise ValueError("preferred combat weapon has no supported attack")
    return _default_weapon_for(actor_ref, people[actor_ref], equipment_ledger, target_distance_mm=distance, role=role)

def attempt_disengage(*, combat: Mapping[str, Any], actor_ref: str, people: Mapping[str, Mapping[str, Any]] | None = None, equipment_ledger: Mapping[str, Any] | None = None) -> dict[str, Any]:
    out=copy.deepcopy(dict(combat)); start_ms=int(out.get("elapsed_ms",0))
    step=_disengage_step(combat=out,actor_ref=actor_ref,people=people,equipment_ledger=equipment_ledger,duration_ms=1000,start_ms=start_ms)
    if not step.get("moved"):
        result={"combat_after":out,"escaped":False,"reason":str(step.get("reason") or "disengagement_failed")}
        if "corridor" in step: result["corridor"]=step["corridor"]
        return result
    out["elapsed_ms"]=start_ms+1000
    return {"combat_after":out,"escaped":bool(step.get("escaped")),"reason":str(step.get("reason") or "retreat_in_progress"),"corridor":step.get("corridor"),"movement":step.get("movement")}



__all__ = ["attempt_disengage", "capability_from_person", "default_action_for", "default_target_for", "default_weapon_for_action", "initialize_combat", "resolve_exchange"]
