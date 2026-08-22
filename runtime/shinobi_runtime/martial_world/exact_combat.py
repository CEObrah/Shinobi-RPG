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
from shinobi_runtime.combat.team_tactics import plan_team_exchange, replan_reasons

from .combat import active_defense_available, allocate_qi, commit_active_defense, control_efficiency_milli
from .equipment import (
    bow_shot_profile, carried_mass_kg, encumbrance_effects, projectile_contact_profile,
    resolve_equipment_item, transition_seconds, weapon_contact_profile,
)
from .equipment_state import compact_equipment_ledger, effective_person_loadout, hydrate_equipment_ledger
from .health import (
    combat_status_families,
    functional_capacity_factors,
    functional_penalties,
    settle_physiology,
    structure_definition,
    target_zone,
    vision_state,
    wound_from_contact,
)
from .poison import apply_poison
from .targeting import doctrine_target
from .mounts import mount_contact_result, mounted_motion_profile

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game" / "data" / "martial-world"


def _load(name: str) -> Mapping[str, Any]:
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
    fatigue = max(0, min(3000, int(person.get("fatigue_milli", 0))))
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
    fatigue_factor = max(100, 1000 - fatigue // 3)
    base_mobility = (speed * 60 + dexterity * 40) // 100
    mobility = base_mobility * movement_factor // 1000 * fatigue_factor // 1000
    reaction_base = (speed * 35 + dexterity * 30 + perception * 25 + intelligence * 10) // 100
    reaction_posture = 500 + standing_factor // 2
    reaction = reaction_base * reaction_posture // 1000 * fatigue_factor // 1000
    best_guard_skill = max([0] + [max(0, int(skills.get(key, 0))) for key in ("sword", "spear", "unarmed")])
    response = (best_guard_skill * 40 + dexterity * 25 + perception * 20 + intelligence * 5 + endurance * 10) // 100
    response = response * (500 + standing_factor // 2) // 1000
    control = (skill * 35 + dexterity * 25 + perception * 15 + intelligence * 10 + qi_control * 15) // 100
    control = control * (750 + standing_factor // 4) // 1000
    offense = (skill * 50 + strength * 18 + dexterity * 22 + perception * 10) // 100
    if skill_ref in {"sword", "spear", "unarmed"}:
        offense = offense * (650 + movement_factor * 350 // 1000) // 1000
    elif skill_ref == "hidden_weapons":
        offense = offense * (800 + standing_factor // 5) // 1000
    capture = (max(0, int(skills.get("unarmed", 0))) * 50 + strength * 35 + willpower * 15) // 100
    capture = capture * (500 + standing_factor // 2) // 1000
    escape_base = (speed * 45 + dexterity * 35 + perception * 10 + intelligence * 10) // 100
    escape = escape_base * running_factor // 1000 * fatigue_factor // 1000
    return CapabilityProfile(
        offense=offense, defense=response, control=control, mobility=mobility, perception=perception,
        stealth=max(0, int(skills.get("stealth_scouting", 0))) * max(250, movement_factor) // 1000,
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
    return mounted_motion_profile(person, carried_mass_kg=mass, mount_state=mount)


def _combat_capability_for_state(
    person_ref: str,
    person: Mapping[str, Any],
    equipment_ledger: Mapping[str, Any],
    combatant_state: Mapping[str, Any] | None,
    *,
    action_skill: str | None = None,
) -> CapabilityProfile:
    base = _combat_capability(person_ref, person, equipment_ledger, action_skill=action_skill)
    motion = _mount_motion_for_state(person_ref, person, equipment_ledger, combatant_state)
    if not bool(motion.get("mounted")):
        return base
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

def _weapon_owned(equipment_ledger: Mapping[str, Any], person_ref: str, weapon_ref: str) -> bool:
    return weapon_ref == "body_unarmed" or int(_loadout_items(equipment_ledger, person_ref).get(weapon_ref, 0)) > 0


def _weapon(weapon_ref: str | None) -> Mapping[str, Any] | None:
    if not weapon_ref or weapon_ref == "body_unarmed":
        return None
    row = _equipment_catalog().get("weapon_catalog", {}).get(weapon_ref)
    return row if isinstance(row, Mapping) else None


def _usable_hand_count(person: Mapping[str, Any]) -> int:
    impair = functional_penalties(_wounds(person))
    left = max(int(impair.get("grip_left", 0)), int(impair.get("weapon_control_left", 0))) < 90
    right = max(int(impair.get("grip_right", 0)), int(impair.get("weapon_control_right", 0))) < 90
    return int(left) + int(right)


def _weapon_ready_delay_ms(person: Mapping[str, Any], current_ref: str | None, requested_ref: str) -> int:
    if requested_ref == "body_unarmed" or requested_ref == current_ref:
        return 0
    dexterity = max(0, int(_attrs(person).get("dexterity", 0)))
    speed_factor_milli = max(600, min(1600, 800 + dexterity * 4))
    delay_ms = 0
    current = _weapon(current_ref)
    if isinstance(current, Mapping):
        delay_ms += int(round(transition_seconds(current, action="stow") * 1000))
    requested = _weapon(requested_ref)
    if not isinstance(requested, Mapping):
        return delay_ms
    delay_ms += int(round(transition_seconds(requested, action="ready") * 1000))
    return delay_ms * 1000 // speed_factor_milli


def _ready_melee_weapon_ref(person_ref: str, person: Mapping[str, Any], equipment_ledger: Mapping[str, Any], combatant_state: Mapping[str, Any]) -> str | None:
    explicit = combatant_state.get("ready_weapon_ref")
    if isinstance(explicit, str) and _weapon_owned(equipment_ledger, person_ref, explicit):
        row = _weapon(explicit)
        if isinstance(row, Mapping) and int(row.get("interception_area_milli",0)) > 0:
            return explicit
    return None


def _guard_profile(person_ref: str, person: Mapping[str, Any], equipment_ledger: Mapping[str, Any], combatant_state: Mapping[str, Any]) -> ActionProfile | None:
    ref = _ready_melee_weapon_ref(person_ref, person, equipment_ledger, combatant_state)
    row = _weapon(ref)
    if not isinstance(row, Mapping): return None
    reach = max(0.2, float(row.get("reach_m", 1.0)))
    return ActionProfile(method_ref="guard", effect_kind="physical", delivery="direct", startup_ms=0, external_contact=True,
                         speed_score=_combat_capability(person_ref, person, equipment_ledger, action_skill=str(row.get("discipline", "sword"))).reaction,
                         effect_parameters={"physical_reach_m": reach, "geometry": {"shape":"direct","width_m":0.35,"length_m":reach}})


def _action_rule(action_kind: str, weapon: Mapping[str, Any] | None) -> Mapping[str, Any]:
    rules = _combat_rules().get("actions", {})
    row = rules.get(action_kind) if isinstance(rules, Mapping) else None
    if isinstance(row, Mapping): return row
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


def _action_profile(action_kind: str, actor: Mapping[str, Any], weapon_ref: str | None, target_position: Mapping[str, Any], actor_position: Mapping[str, Any]) -> tuple[ActionProfile, Mapping[str, Any] | None]:
    weapon = _weapon(weapon_ref)
    if weapon_ref not in (None, "", "body_unarmed") and not isinstance(weapon, Mapping): raise ValueError("weapon unresolved")
    rule = _action_rule(action_kind, weapon); discipline = str(rule.get("skill", _discipline_for_action(action_kind, weapon)))
    startup = max(1, int(rule.get("startup_ms", 300))); speed_score = capability_from_person(actor, action_skill=discipline).reaction
    params: dict[str, Any] = {"commitment_milli": max(0, min(1000, int(rule.get("commitment_milli", 400))))}; delivery = "direct"
    if action_kind == "unarmed_strike":
        params.update(physical_reach_m=0.65, geometry={"shape":"direct","width_m":0.35,"length_m":0.85})
    elif action_kind in {"cut","thrust","staff_strike","staff_thrust","staff_butt_strike"}:
        if not isinstance(weapon, Mapping): raise ValueError("physical weapon required")
        if action_kind.startswith("staff") and weapon_ref != "weapon_staff": raise ValueError("staff action requires staff")
        reach=float(weapon.get("reach_m",1.0)); reach=min(reach,0.85) if action_kind=="staff_butt_strike" else reach
        shape="arc" if action_kind in {"cut","staff_strike"} else "direct"; geom={"shape":shape,"length_m":reach,"width_m":0.35}
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
    saved_status=[str(x) for x in combatant_state.get("status_families",[]) if isinstance(x,str)]; wounds=_wounds(person); status=tuple(dict.fromkeys(saved_status+list(combat_status_families(wounds))))
    health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
    return Participant(participant_ref=ref,authoritative_owner_ref=ref,side_ref=side_ref,sequence=0,representation="exact",capability=cap,personnel=PersonnelState(total=1,active=1),position=_pos(position),
        information=InformationState(observed_refs=tuple(dict.fromkeys(str(x) for x in known_refs if isinstance(x,str))),confidence_milli=max(0,min(1000,int(combatant_state.get("awareness_confidence_milli",1000)))),concealment_milli=max(0,min(1000,int(combatant_state.get("concealment_milli",0)))),surprise_milli=max(0,min(1000,int(combatant_state.get("surprise_milli",0))))),
        intent=CombatIntent(action=intent),initiative=max(0,cap.reaction+cap.mobility),readiness=100,morale=100,cohesion=100,action_profile=action_profile,reactive_defenses=reactive,
        active_defense_load_milli=max(0,min(1000,int(combatant_state.get("defense_state",{}).get("load_milli",0)))),balance_milli=max(0,min(1000,int(combatant_state.get("balance_milli",1000)))),limb_commitment_milli=max(0,min(1000,int(combatant_state.get("limb_commitment_milli",0)))),
        recovery_remaining_ms=max(0,int(combatant_state.get("recovery_until_ms",0))-max(0,int(at_ms))),weapon_position=str(combatant_state.get("weapon_position","guard")),status_families=status,physical_defense_preferences=tuple(dict.fromkeys(prefs)),
        health_model="anatomy",body_mass_grams=max(1000,int(float(person.get("body_mass_kg",70))*1000)),physiology_endurance=max(0,int(_attrs(person).get("endurance",0))),physiology_willpower=max(0,int(_attrs(person).get("willpower",0))),blood_lost_ml=max(0,int(health.get("blood_lost_ml",0))),wounds=tuple(wounds))


def _default_weapon_for(person_ref: str, person: Mapping[str, Any], equipment_ledger: Mapping[str, Any], *, target_distance_mm: int, role: str | None = None) -> tuple[str, str]:
    items=_loadout_items(equipment_ledger,person_ref); weapons=_equipment_catalog().get("weapon_catalog",{})
    bows=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline")=="bow"]
    if target_distance_mm>3500 and bows and int(items.get("item_arrow",0))>0:
        return "bow_shot", max(bows,key=lambda ref:(int(_skills(person).get("bow",0)),int(weapons[ref].get("precision",0)),ref))
    thrown=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline")=="hidden_weapons"]
    if target_distance_mm>2500 and thrown:
        return "hidden_weapon_throw", max(thrown,key=lambda ref:(int(_skills(person).get("hidden_weapons",0)),int(weapons[ref].get("precision",0)),ref))
    melee=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline") in {"sword","spear"}]
    if melee:
        ref=max(melee,key=lambda item_ref:(int(_skills(person).get(str(weapons[item_ref].get("discipline")),0)),int(weapons[item_ref].get("control",0)),item_ref))
        if ref=="weapon_staff": return ("staff_sweep" if role in {"control","shape"} else "staff_strike"),ref
        return ("thrust" if int(weapons[ref].get("pierce",0))>=int(weapons[ref].get("cut",0)) else "cut"),ref
    return "unarmed_strike","body_unarmed"


def initialize_combat(*, combat_ref: str, side_a_refs: Sequence[str], side_b_refs: Sequence[str], people: Mapping[str, Mapping[str, Any]], zone_ref: str, started_at: str, objective: Mapping[str, Any], awareness_mode: str = "mutual", initial_range_band: int = 1, obstacles: Sequence[Mapping[str, Any]] = (), awareness_evidence: Mapping[str, Any] | None = None, equipment_ledger: Mapping[str, Any] | None = None, initial_ready_weapons: Mapping[str, str] | None = None, mount_assignments: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    a=tuple(dict.fromkeys(str(x) for x in side_a_refs)); b=tuple(dict.fromkeys(str(x) for x in side_b_refs))
    if not a or not b or set(a)&set(b): raise ValueError("combat sides invalid")
    if any(ref not in people for ref in a+b): raise ValueError("combat participant unresolved")
    if awareness_mode not in {"mutual","side_a_ambush","side_b_ambush"}: raise ValueError("awareness mode invalid")
    if awareness_mode!="mutual" and not isinstance(awareness_evidence,Mapping): raise ValueError("ambush requires derived awareness evidence")
    side_map={ref:"side_a" for ref in a}; side_map.update({ref:"side_b" for ref in b}); positions=initial_positions(side_by_participant=side_map,zone_ref=zone_ref,initial_range_band=initial_range_band); state={}
    explicit_ready=initial_ready_weapons if isinstance(initial_ready_weapons,Mapping) else {}
    mounts=mount_assignments if isinstance(mount_assignments,Mapping) else {}
    if any(str(ref) not in set(a+b) for ref in mounts): raise ValueError("mount assignment participant unresolved")
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
        state[ref]={"defense_state":{"load_milli":0,"last_at_ms":-1_000_000_000,"recent_attackers":{}},"balance_milli":1000,"limb_commitment_milli":0,"recovery_until_ms":0,"weapon_position":"guard","ready_weapon_ref":ready_ref,"status_families":[],"surprise_milli":700 if surprised else 0,"observed_refs":observed,"awareness_confidence_milli":1000 if observed else 0,"qi_allocation_milli":{}}
        mount=mounts.get(ref)
        if isinstance(mount,Mapping):
            owner=str(mount.get("owner_faction_ref") or people[ref].get("faction_ref") or "")
            if not owner: raise ValueError("mount owner faction unresolved")
            state[ref]["mount"]={
                "kind":"riding_horse","owner_faction_ref":owner,"condition_milli":max(1,min(1000,int(mount.get("condition_milli",1000)))),
                "status":"active","active":True,"inventory_debited":bool(mount.get("inventory_debited",False)),"service_loss_pending":False,
            }
    return {"combat_id":combat_ref,"status":"active","started_at":started_at,"elapsed_ms":0,"zone_ref":zone_ref,"sides":{"side_a":list(a),"side_b":list(b)},"objective":copy.deepcopy(dict(objective)),"positions":positions,"obstacles":[copy.deepcopy(dict(row)) for row in obstacles],"combatants":state,"team_plans":{},"awareness_mode":awareness_mode}


def _active(person: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}; statuses=set(state.get("status_families",[]))
    return health.get("status") not in {"dead","incapacitated"} and int(health.get("consciousness",100))>0 and not ({"dead","unconscious","incapacitated","escaped"}&statuses)


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
        concealment=max(0,int(combat["combatants"][enemy_ref].get("concealment_milli",0))+enemy_cap.stealth*3); detection=actor_cap.perception*5+actor_cap.reaction*2-int(distance_m*5)
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


def _apply_physiology(person: dict[str, Any], *, elapsed_seconds: int) -> dict[str, Any]:
    health=copy.deepcopy(person.get("health",{})); wounds=health.get("injuries",[]) if isinstance(health.get("injuries"),list) else []
    physiology=settle_physiology(body_mass_kg=float(person.get("body_mass_kg",70)),wounds=wounds,blood_lost_ml=int(health.get("blood_lost_ml",0)),elapsed_seconds=max(0,elapsed_seconds),endurance=int(_attrs(person).get("endurance",0)),willpower=int(_attrs(person).get("willpower",0)))
    health["blood_lost_ml"]=physiology["blood_lost_ml"]; health["shock"]=physiology["shock"]; health["consciousness"]=max(0,min(100,physiology["consciousness"])); lethal=physiology["lethal_state"]
    if lethal=="dead": health["status"]="dead"
    elif lethal in {"dying","critical","unconscious"}: health["status"]="incapacitated"
    elif wounds: health["status"]="injured"
    else: health["status"]="ready"
    person["health"]=health; return physiology


def _target_difficulty(*, structure_ref: str | None, hit_zone: str, distance_m: float, target_speed: int, visibility_milli: int, action_kind: str) -> int:
    structure_penalty=0
    if structure_ref:
        row=structure_definition(structure_ref); zone=str(row.get("zone","")) if isinstance(row,Mapping) else ""
        structure_penalty={"eyes":95,"throat":80,"wrist":65,"hand":62,"elbow":55,"knee":50,"ankle":58,"heart":75}.get(structure_ref,{"eyes":95,"neck":75,"wrist":65,"hands":62,"elbow":55,"knee":50,"ankle":58,"chest":45}.get(zone,40))
    zone_penalty={"eyes":65,"neck":45,"wrist":40,"hands":38,"knee":32,"ankle":35,"mount":-18}.get(hit_zone,0); distance_penalty=int(max(0.0,distance_m-1.0)*(2.0 if action_kind=="bow_shot" else 8.0)); movement_penalty=max(0,target_speed)//5; visibility_penalty=max(0,1000-max(0,min(1000,visibility_milli)))//10
    return structure_penalty+zone_penalty+distance_penalty+movement_penalty+visibility_penalty


def _precision_margin(*, actor: Mapping[str, Any], weapon: Mapping[str, Any] | None, action_kind: str, structure_ref: str | None, hit_zone: str, distance_m: float, target: Mapping[str, Any], visibility_milli: int, bow_accuracy_score: int | None = None, target_speed: int | None = None, mounted_control_milli: int | None = None) -> int:
    attrs=_attrs(actor); skills=_skills(actor); discipline=_discipline_for_action(action_kind,weapon); base=(int(skills.get(discipline,0))*45+int(attrs.get("dexterity",0))*25+int(attrs.get("perception",0))*25+int(attrs.get("intelligence",0))*5)//100
    if isinstance(weapon,Mapping): base += int(weapon.get("precision",0))//3
    if bow_accuracy_score is not None: base=(base+max(0,int(bow_accuracy_score)))//2
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
    ready=_weapon(ready_ref)
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



def _qi_effect(*, person: dict[str, Any], combatant_state: dict[str, Any], duration_ms: int) -> dict[str, Any]:
    allocations=combatant_state.get("qi_allocation_milli",{})
    if not isinstance(allocations,Mapping) or not allocations:
        current=max(0,int(person.get("current_qi",person.get("qi",0)))); return {"allocations_milli":{},"current_qi_before":current,"current_qi_after":current,"strain_milli_added":0,"control_efficiency_milli":control_efficiency_milli(int(person.get("qi_control",0)))}
    current=max(0,int(person.get("current_qi",person.get("qi",0)))); result=allocate_qi(qi=max(0,int(person.get("qi",0))),qi_control=max(0,int(person.get("qi_control",0))),current_qi_milli=current*1000,allocations_milli={str(k):max(0,int(v)) for k,v in allocations.items()},duration_ms=max(1,duration_ms)); after=max(0,int(result["current_qi_milli_after"])//1000); person["current_qi"]=after; strain=max(0,int(result.get("strain_milli_added",0)))
    if strain: person["fatigue_milli"]=max(0,int(person.get("fatigue_milli",0)))+strain
    return {**result,"current_qi_before":current,"current_qi_after":after,"control_efficiency_milli":control_efficiency_milli(int(person.get("qi_control",0)))}


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
    attrs=_attrs(actor); skills=_skills(actor); profile: Mapping[str, Any]
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
    if isinstance(qi_result,Mapping):
        allocations=qi_result.get("allocations_milli",{}); weapon_flow=int(allocations.get("weapon",0)) if isinstance(allocations,Mapping) else 0; efficiency=max(0,int(qi_result.get("control_efficiency_milli",0))); reinforcement=min(500,weapon_flow*efficiency//1_000_000)
        if reinforcement:
            cut=cut*(1000+reinforcement//2)//1000; pierce=pierce*(1000+reinforcement//2)//1000; blunt=blunt*(1000+reinforcement//3)//1000; penetration=penetration*(1000+reinforcement)//1000
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
    profile, weapon = _action_profile(action_kind, people[actor_ref], weapon_ref, target_position, actor_position)
    cap = _combat_capability_for_state(actor_ref, people[actor_ref], equipment_ledger, actor_state, action_skill=_discipline_for_action(action_kind, weapon))
    mount_at_declaration = _mount_motion_for_state(actor_ref, people[actor_ref], equipment_ledger, actor_state)
    params = dict(profile.effect_parameters)
    params["mounted_at_declaration"] = bool(mount_at_declaration.get("mounted"))
    profile = ActionProfile(**{**profile.__dict__, "effect_parameters": params})
    ready_at = max(declared, int(actor_state.get("recovery_until_ms", 0)))
    decision_ms = max(20, 95_000 // max(90, cap.reaction + cap.perception // 2 + 80))
    current_ready = actor_state.get("ready_weapon_ref") if isinstance(actor_state.get("ready_weapon_ref"), str) else None
    ready_delay_ms = _weapon_ready_delay_ms(people[actor_ref], current_ready, weapon_ref)
    approach_ms = 0
    approach_distance_mm = 0
    if profile.delivery not in {"projectile", "ranged", "thrown"}:
        distance = planar_distance_mm(actor_position, target_position)
        body_allowance = int(actor_position.get("body_radius_mm", 300)) + int(target_position.get("body_radius_mm", 300))
        required = max(0, distance - physical_reach_mm(profile) - body_allowance)
        approach_distance_mm = required
        approach_ms = required * 1000 // max(1, _movement_speed_for_state(actor_ref, people[actor_ref], equipment_ledger, actor_state, cap))
        params = dict(profile.effect_parameters)
        params["approach_distance_mm"] = approach_distance_mm
        params["approach_time_ms"] = approach_ms
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
    defender_state=combat["combatants"][defender_ref]; weapon_ref=_ready_melee_weapon_ref(defender_ref,people[defender_ref],equipment_ledger,defender_state); weapon=_weapon(weapon_ref)
    if weapon_ref is None: action_kind="unarmed_strike"; weapon_ref="body_unarmed"
    else: action_kind="thrust" if int(weapon.get("pierce",0))>=int(weapon.get("cut",0)) else "cut"
    distance_m=planar_distance_mm(combat["positions"][defender_ref],combat["positions"][attacker_ref])/1000.0
    if weapon is not None and distance_m>float(weapon.get("reach_m",0))+0.6: return None
    result=_contact_damage(actor=people[defender_ref],defender=people[attacker_ref],weapon=weapon,weapon_ref=weapon_ref,action_kind=action_kind,range_m=distance_m,defense_force_milli=620,hit_zone="forearms_hands",target_structure_ref=None,created_at=str(at_ms),precision_margin=0); wound=result.get("wound")
    if isinstance(wound,Mapping):
        health=copy.deepcopy(people[attacker_ref].get("health",{})); injuries=list(health.get("injuries",[])); injuries.append(copy.deepcopy(dict(wound))); health["injuries"]=injuries; people[attacker_ref]["health"]=health; _apply_physiology(people[attacker_ref],elapsed_seconds=1)
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
    actor_ref=action.actor_ref; target_ref=action.target_ref; event_base={"actor_ref":actor_ref,"intended_ref":target_ref,"action_kind":action.action_kind,"weapon_ref":action.weapon_ref,"poison_ref":action.poison_ref,"decision_origin":action.decision_origin,"declared_at_ms":action.declared_at_ms,"start_at_ms":action.start_at_ms,"ready_delay_ms":action.ready_delay_ms,"previous_ready_weapon_ref":action.previous_ready_weapon_ref,"commit_at_ms":action.commit_at_ms,"release_at_ms":action.release_at_ms,"contact_at_ms":action.contact_at_ms,"recovery_end_ms":action.recovery_end_ms}
    if actor_ref not in people or target_ref not in people: return {**event_base,"result":"invalid_target"}
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
    if not status_action_allowed(actor_state.get("status_families",[]),"attack") and not (isinstance(disabled_at,int) and disabled_at>action.commit_at_ms): return {**event_base,"result":"status_blocks_action"}
    if _side_of(combat,actor_ref)==_side_of(combat,target_ref): return {**event_base,"result":"friendly_target_rejected"}
    if not _weapon_owned(equipment_ledger,actor_ref,action.weapon_ref): return {**event_base,"result":"weapon_not_owned"}
    wounds=_wounds(people[actor_ref])
    if action.action_kind in {"bow_shot","hidden_weapon_throw"} and vision_state(wounds).get("state")=="blind": return {**event_base,"result":"visual_targeting_unavailable"}
    if isinstance(action.weapon,Mapping):
        hands_required=max(1,int(action.weapon.get("hands_required",1)))
        usable_hands=_usable_hand_count(people[actor_ref])
        if usable_hands<hands_required:
            return {**event_base,"result":"weapon_hand_control_unavailable","hands_required":hands_required,"usable_hands":usable_hands}
    at_ms=action.contact_at_ms; combat["elapsed_ms"]=max(int(combat.get("elapsed_ms",0)),at_ms); actor_state["weapon_position"]="extended_attack"; actor_state["limb_commitment_milli"]=int(action.profile.effect_parameters.get("commitment_milli",400)); actor_state["balance_milli"]=max(350,int(actor_state.get("balance_milli",1000))-int(actor_state["limb_commitment_milli"])//5); actor_state["recovery_until_ms"]=max(int(actor_state.get("recovery_until_ms",0)),action.recovery_end_ms)
    if action.weapon_ref!="body_unarmed":
        actor_state["ready_weapon_ref"]=action.weapon_ref
        actor_state["ready_hands_required"]=max(1,int((action.weapon or {}).get("hands_required",1)))
    positions=combat["positions"]; start_actor=copy.deepcopy(positions[actor_ref]); start_target=copy.deepcopy(positions[target_ref]); profile=action.profile; body_refs=[ref for refs in combat["sides"].values() for ref in refs]; actor_cap=_combat_capability_for_state(actor_ref,people[actor_ref],equipment_ledger,actor_state,action_skill=_discipline_for_action(action.action_kind,action.weapon))
    if profile.delivery not in {"projectile","ranged","thrown"}:
        params=dict(profile.effect_parameters); params["committed_melee_trajectory"]=copy.deepcopy(dict(action.trajectory)); params["intended_target_ref"]=target_ref; profile=ActionProfile(**{**profile.__dict__,"effect_parameters":params}); moved,approach=close_attacker_into_reach(attacker_ref=actor_ref,defender_ref=target_ref,positions=positions,attacker_position=_pos(start_actor),defender_position=_pos(start_target),attacker_capability=actor_cap,profile=profile,body_refs=body_refs,obstacles=combat.get("obstacles",[]))
        if approach.get("moved"): positions[actor_ref]=moved.to_record(); positions[actor_ref]["elevation_mm"]=int(start_actor.get("elevation_mm",0)); params=dict(profile.effect_parameters); params["approach_time_ms"]=int(approach.get("approach_time_ms",0)); params["approach_distance_mm"]=int(approach.get("distance_mm",0)); profile=ActionProfile(**{**profile.__dict__,"effect_parameters":params})
    distance_m=planar_distance_mm(positions[actor_ref],positions[target_ref])/1000.0; visibility=1000-cover_milli_between(positions,actor_ref=actor_ref,target_ref=target_ref,obstacles=combat.get("obstacles",[])); bow_profile=None; trajectory=copy.deepcopy(dict(action.trajectory))
    if action.action_kind=="bow_shot":
        bow_profile=bow_shot_profile(action.weapon or {},bow_skill=int(_skills(people[actor_ref]).get("bow",0)),strength=int(_attrs(people[actor_ref]).get("strength",0)),dexterity=int(_attrs(people[actor_ref]).get("dexterity",0)),perception=int(_attrs(people[actor_ref]).get("perception",0)),distance_m=distance_m,crosswind_mps_tenths=int(combat.get("crosswind_mps_tenths",0)))
        if not bow_profile.get("can_draw"): return {**event_base,"result":"strength_draw_requirement_not_met","bow_profile":bow_profile}
    target_cap_for_precision=_combat_capability_for_state(target_ref,people[target_ref],equipment_ledger,target_state)
    actor_mount_motion=_mount_motion_for_state(actor_ref,people[actor_ref],equipment_ledger,actor_state)
    precision=_precision_margin(
        actor=people[actor_ref],weapon=action.weapon,action_kind=action.action_kind,structure_ref=action.target_structure_ref,
        hit_zone=action.hit_zone,distance_m=distance_m,target=people[target_ref],visibility_milli=visibility,
        bow_accuracy_score=int(bow_profile.get("accuracy_score",0)) if isinstance(bow_profile,Mapping) else None,
        target_speed=target_cap_for_precision.mobility,
        mounted_control_milli=(int(actor_mount_motion.get("control_milli",0)) if bool(actor_mount_motion.get("mounted")) else None),
    )
    if profile.delivery in {"projectile","ranged","thrown"}:
        wind_error=int(round(float(bow_profile.get("wind_drift_m",0))*1000)) if isinstance(bow_profile,Mapping) else 0; aim_error=max(0,-precision)*18+wind_error; trajectory=_trajectory_with_error(trajectory,error_mm=aim_error,seed_parts=(combat.get("combat_id"),actor_ref,target_ref,action.contact_at_ms)); resource_commit=_commit_projectile_resources(equipment_ledger,actor_ref=actor_ref,action_kind=action.action_kind,weapon_ref=action.weapon_ref,poison_ref=action.poison_ref)
        if not resource_commit.get("ok"): return {**event_base,"result":str(resource_commit.get("reason") or "projectile_resource_unavailable"),"resource_commit":resource_commit}
        if action.action_kind=="hidden_weapon_throw":
            actor_state["ready_weapon_ref"]=None
            actor_state.pop("ready_hands_required",None)
            actor_state["weapon_position"]="released_projectile"
    geometry=profile.effect_parameters.get("geometry"); channel="projectile" if profile.delivery in {"projectile","ranged","thrown"} else "melee"; trace=trace_attack_geometry(positions,actor_ref=actor_ref,aim_ref=target_ref,body_refs=body_refs,geometry=geometry,obstacles=combat.get("obstacles",[]),target_limit=1,maximum_range_m=(profile.effect_parameters.get("maximum_range_m") if channel=="projectile" else profile.effect_parameters.get("physical_reach_m")),channel=channel,trajectory=trajectory if channel=="projectile" else action.trajectory); contacts=trace.get("contacts",[]) if isinstance(trace,Mapping) else []; actual_ref=contacts[0].get("participant_ref") if contacts and isinstance(contacts[0],Mapping) else None
    if actual_ref is None and channel=="projectile": return {**event_base,"result":"miss_no_spatial_intersection","trace":trace,"precision_margin":precision,"bow_profile":bow_profile,"resource_commit":resource_commit}
    if actual_ref is None: actual_ref=target_ref
    if actual_ref not in people: return {**event_base,"result":"no_contact","trace":trace}
    defender=people[actual_ref]; defender_state=combat["combatants"][actual_ref]; defender_cap=_combat_capability_for_state(actual_ref,defender,equipment_ledger,defender_state)
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
    if decision.detected and decision.response != "none":
        response_start_ms=max(int(combat.get("_exchange_declared_at_ms",0)),at_ms-max(0,int(decision.reaction_delay_ms)))
        _record_defensive_interruption(
            combat, defender_ref=actual_ref, attacker_ref=actor_ref, response=decision.response,
            response_start_ms=response_start_ms, response_contact_ms=at_ms,
        )
    defense_commit=commit_active_defense(defender_state.get("defense_state",{}),attacker_ref=actor_ref,at_ms=at_ms,threat_speed=max(1,int(profile.speed_score)),reaction_score=max(1,int(defender_cap.reaction)),body_commitment_milli=int(decision.limb_commitment_after_milli))
    defender_state["defense_state"]=copy.deepcopy(dict(defense_commit["state_after"]))
    if decision.interrupts_attacker and action.commit_at_ms>=at_ms-decision.reaction_delay_ms:
        counter=_interception_damage(defender_ref=actual_ref,attacker_ref=actor_ref,people=people,equipment_ledger=equipment_ledger,combat=combat,at_ms=at_ms); return {**event_base,"actual_ref":actual_ref,"result":"counter_intercepted","defense":decision.trace(),"defense_pressure":defense_pressure,"counter_contact":counter,"trace":trace,"resource_commit":resource_commit if channel=="projectile" else None}
    interception={"outcome":"not_applicable"}
    if channel=="projectile":
        interception=_projectile_interception(defender_ref=actual_ref,defender=defender,defender_state=defender_state,defender_capability=defender_cap,equipment_ledger=equipment_ledger,decision=decision,profile=profile,trajectory=trajectory,combat_id=str(combat.get("combat_id","")),attacker_ref=actor_ref,at_ms=at_ms)
        if interception.get("outcome")=="clean":
            return {**event_base,"actual_ref":actual_ref,"result":"projectile_intercepted_clean","defense":decision.trace(),"defense_pressure":defense_pressure,"interception":interception,"trace":trace,"resource_commit":resource_commit}
        if interception.get("outcome")=="partial":
            trajectory=copy.deepcopy(interception.get("trajectory") or trajectory)
    contact=contact_after_defense(attacker_ref=actor_ref,defender_ref=actual_ref,positions=positions,profile=profile,obstacles=combat.get("obstacles",[]),trajectory=trajectory if channel=="projectile" else action.trajectory,tracking_milli=max(100,decision.tracking_milli+min(0,precision)*4),original_defender_position=original_defender_position,body_refs=body_refs)
    if not contact.get("contact"):
        return {**event_base,"actual_ref":actual_ref,"result":"defended_or_missed","defense":decision.trace(),"defense_pressure":defense_pressure,"interception":interception,"contact":contact,"precision_margin":precision,"trace":trace,"resource_commit":resource_commit if channel=="projectile" else None}
    redirected_ref=contact.get("contacted_ref")
    if channel=="projectile" and isinstance(redirected_ref,str) and redirected_ref in people and redirected_ref!=actual_ref:
        # A physically redirected projectile may strike a different body.  That
        # person did not receive the original defender's reaction for free.
        actual_ref=redirected_ref; defender=people[actual_ref]; defender_state=combat["combatants"][actual_ref]
        decision_force_milli=1000
    elif redirected_ref not in {None,actual_ref}:
        return {**event_base,"actual_ref":actual_ref,"result":"defended_or_missed","defense":decision.trace(),"defense_pressure":defense_pressure,"interception":interception,"contact":contact,"precision_margin":precision,"trace":trace,"resource_commit":resource_commit if channel=="projectile" else None}
    else:
        # Projectile mitigation is physical interception, not an invisible
        # generic defense multiplier. Clean interception already ended above;
        # partial interception changes trajectory/speed. If the projectile still
        # contacts this body, it carries its remaining physical force.
        decision_force_milli=1000 if channel=="projectile" else decision.force_transmission_milli
    range_m=planar_distance_mm(positions[actor_ref],positions[actual_ref])/1000.0; projectile_profile=None
    if action.action_kind=="bow_shot":
        arrow=_equipment_catalog().get("ammunition_catalog",{}).get("item_arrow")
        if not isinstance(arrow,Mapping): raise ValueError("arrow definition missing")
        launch=float(bow_profile.get("launch_speed_mps",1)) if isinstance(bow_profile,Mapping) else 1.0; projectile_profile=projectile_contact_profile(arrow,launch_speed_mps=launch,impact_speed_mps=launch*max(0,int(interception.get("speed_factor_milli",1000)))/1000.0,accuracy_margin=precision)
    elif action.action_kind=="hidden_weapon_throw":
        launch=float((action.weapon or {}).get("projectile_speed_mps",1)); projectile_profile=projectile_contact_profile(action.weapon or {},launch_speed_mps=launch,impact_speed_mps=launch*max(0,int(interception.get("speed_factor_milli",1000)))/1000.0,accuracy_margin=precision)
    qi_result=_qi_effect(person=people[actor_ref],combatant_state=actor_state,duration_ms=max(1,action.release_at_ms-action.start_at_ms))
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
        health=copy.deepcopy(defender.get("health",{})); injuries=list(health.get("injuries",[])); injuries.append(copy.deepcopy(dict(wound))); health["injuries"]=injuries; defender["health"]=health
        if action.poison_ref and any(int(wound.get(key,0))>0 for key in ("cut","pierce","penetration")):
            attrs=_attrs(defender); burdens=defender.setdefault("poison_burdens",{}); current=int(burdens.get(action.poison_ref,0)) if isinstance(burdens,Mapping) else 0
            poison=apply_poison(poison_ref=action.poison_ref,current_burden=current,doses=1,endurance=int(attrs.get("endurance",0)),qi=int(defender.get("qi",0)),qi_control=int(defender.get("qi_control",0)))
            defender.setdefault("poison_burdens",{})[action.poison_ref]=int(poison["burden_after"])
        physiology=_apply_physiology(defender,elapsed_seconds=1)
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
    return {**event_base,"actual_ref":actual_ref,"result":result_kind,"defense":decision.trace(),"defense_pressure":defense_pressure,"contact":contact,"damage":damage,"physiology":physiology,"poison":poison,"interception":interception,"resource_commit":resource_commit if channel=="projectile" else None,"precision_margin":precision,"bow_profile":bow_profile,"qi":qi_result}


def _npc_target_structure(actor: Mapping[str, Any], target: Mapping[str, Any], *, intent: str) -> str | None:
    attrs=_attrs(actor); skills=_skills(actor); tactical=(int(attrs.get("intelligence",0))+int(attrs.get("perception",0))+max(int(skills.get("sword",0)),int(skills.get("spear",0)),int(skills.get("unarmed",0)),int(skills.get("bow",0))))//3
    if tactical<55: return None
    chosen=doctrine_target(actor,intent=intent,target=target)
    if chosen: return chosen
    damaged=[row for row in _wounds(target) if isinstance(row.get("structure_ref"),str) and int(row.get("structure_damage",row.get("severity",0)))>0]
    return str(max(damaged,key=lambda row:(int(row.get("structure_damage",row.get("severity",0))),str(row.get("structure_ref"))))["structure_ref"]) if damaged else None


def resolve_exchange(*, combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], equipment_ledger: Mapping[str, Any], doctrines: Mapping[str, Mapping[str, Any]], player_ref: str, player_action_kind: str, player_target_ref: str, player_weapon_ref: str, player_hit_zone: str = "chest", player_target_structure_ref: str | None = None, player_targeting_intent: str = "disable", player_poison_ref: str | None = None, npc_targeting_intent: str = "lethal") -> dict[str, Any]:
    out=copy.deepcopy(dict(combat)); persons={ref:copy.deepcopy(dict(person)) for ref,person in people.items()}; ledger=hydrate_equipment_ledger(equipment_ledger)
    if out.get("status")!="active": raise ValueError("combat not active")
    if player_ref not in persons or player_ref not in out.get("combatants",{}): raise ValueError("player not in combat")
    if player_targeting_intent not in {"disable","lethal"} or npc_targeting_intent not in {"disable","lethal"}: raise ValueError("targeting intent invalid")
    for side in ("side_a","side_b"):
        members=out.get("sides",{}).get(side,[]); faction_ref=persons[members[0]].get("faction_ref") if members else None; _refresh_team_plan(out,side=side,people=persons,doctrine=doctrines.get(str(faction_ref),{}))
    active_at_declaration=[ref for refs in out["sides"].values() for ref in refs if _active(persons[ref],out["combatants"][ref])]; scheduled=[]; declaration_events=[]
    for actor_ref in active_at_declaration:
        side=_side_of(out,actor_ref); enemy_side="side_b" if side=="side_a" else "side_a"; enemies=[ref for ref in out["sides"][enemy_side] if _active(persons[ref],out["combatants"][ref])]; known=_observe_visible_enemies(out,actor_ref=actor_ref,enemy_refs=enemies,people=persons,at_ms=int(out.get("elapsed_ms",0)))
        if not known: declaration_events.append({"actor_ref":actor_ref,"result":"no_lawfully_known_target","decision_origin":"awareness"}); continue
        if actor_ref==player_ref:
            target=player_target_ref; kind=player_action_kind; weapon_ref=player_weapon_ref; poison_ref=player_poison_ref; provenance="player"; target_structure=player_target_structure_ref
            if target not in known: declaration_events.append({"actor_ref":actor_ref,"result":"target_not_observed","decision_origin":provenance}); continue
            if target_structure in {None,"","auto"} and player_hit_zone=="auto": target_structure=doctrine_target(persons[actor_ref],intent=player_targeting_intent,target=persons[target])
            hit_zone=player_hit_zone if player_hit_zone!="auto" else target_zone(structure_ref=target_structure) if target_structure else "chest"
        else:
            plan=out.get("team_plans",{}).get(side,{}); assignment=plan.get("assignments",{}).get(actor_ref,{}) if isinstance(plan,Mapping) else {}; target=assignment.get("target_ref") if assignment.get("target_ref") in known else min(known,key=lambda ref:(planar_distance_mm(out["positions"][actor_ref],out["positions"][ref]),ref)); role=str(assignment.get("role") or "pressure"); kind,weapon_ref=_default_weapon_for(actor_ref,persons[actor_ref],ledger,target_distance_mm=planar_distance_mm(out["positions"][actor_ref],out["positions"][target]),role=role); poison_ref=None; provenance="team_ai" if assignment else "actor_ai"; target_structure=_npc_target_structure(persons[actor_ref],persons[target],intent=npc_targeting_intent); hit_zone=target_zone(structure_ref=target_structure) if target_structure else "chest"
        if target not in enemies: declaration_events.append({"actor_ref":actor_ref,"result":"target_unavailable","decision_origin":provenance}); continue
        try: scheduled.append(_schedule_action(combat=out,actor_ref=actor_ref,target_ref=target,action_kind=kind,weapon_ref=weapon_ref,poison_ref=poison_ref,hit_zone=hit_zone,target_structure_ref=target_structure,decision_origin=provenance,people=persons,equipment_ledger=ledger))
        except ValueError as exc: declaration_events.append({"actor_ref":actor_ref,"result":"action_rejected","reason":str(exc),"decision_origin":provenance})
    scheduled.sort(key=lambda row:(row.contact_at_ms,row.commit_at_ms,-_combat_capability_for_state(row.actor_ref,persons[row.actor_ref],ledger,out["combatants"].get(row.actor_ref,{})).reaction,row.actor_ref)); events=list(declaration_events); exchange_end=int(out.get("elapsed_ms",0))
    out["_exchange_declared_at_ms"] = int(out.get("elapsed_ms", 0))
    out["_pending_actions"] = {action.actor_ref: _pending_action_record(action) for action in scheduled}
    out["_defense_interruptions"] = {}
    for action in scheduled:
        event=_resolve_scheduled_action(combat=out,action=action,people=persons,equipment_ledger=ledger); events.append(event); exchange_end=max(exchange_end,action.contact_at_ms)
        pending = out.get("_pending_actions", {})
        if isinstance(pending, dict):
            pending.pop(action.actor_ref, None)
    declared_exchange_ms=int(combat.get("elapsed_ms",0))
    if exchange_end<=declared_exchange_ms:
        exchange_end=declared_exchange_ms+max(1,int(_combat_rules().get("minimum_exchange_advance_ms",250)))
    out["elapsed_ms"]=max(int(out.get("elapsed_ms",0)),exchange_end)
    out.pop("_pending_actions", None)
    out.pop("_defense_interruptions", None)
    out.pop("_exchange_declared_at_ms", None)
    # Exchange events are returned to the caller for narration/effects but are
    # deliberately not persisted. Current combat geometry, injuries, readiness,
    # recovery and objective state are sufficient authority for the next exchange.
    active_a=[ref for ref in out["sides"]["side_a"] if _active(persons[ref],out["combatants"][ref])]; active_b=[ref for ref in out["sides"]["side_b"] if _active(persons[ref],out["combatants"][ref])]
    if not active_a or not active_b: out["status"]="resolved"; out["winner_side"]="side_a" if active_a else "side_b" if active_b else "none"
    return {"combat_after":out,"people_after":persons,"equipment_ledger_after":compact_equipment_ledger(ledger),"events":events,"active_side_a":active_a,"active_side_b":active_b}



def default_action_for(*, combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], equipment_ledger: Mapping[str, Any], actor_ref: str, target_ref: str, role: str | None = None) -> tuple[str, str]:
    """Return the same deterministic default physical action used by combat AI."""
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    if actor_ref not in people or target_ref not in people or actor_ref not in positions or target_ref not in positions:
        raise ValueError("combat default action participant unresolved")
    distance = planar_distance_mm(positions[actor_ref], positions[target_ref])
    return _default_weapon_for(actor_ref, people[actor_ref], equipment_ledger, target_distance_mm=distance, role=role)

def attempt_disengage(*, combat: Mapping[str, Any], actor_ref: str, people: Mapping[str, Mapping[str, Any]] | None = None, equipment_ledger: Mapping[str, Any] | None = None) -> dict[str, Any]:
    out=copy.deepcopy(dict(combat))
    if actor_ref not in out.get("combatants",{}) or actor_ref not in out.get("positions",{}): raise ValueError("combat actor unresolved")
    state=out["combatants"][actor_ref]
    if not status_action_allowed(state.get("status_families",[]),"disengage"): return {"combat_after":out,"escaped":False,"reason":"status_blocks_disengagement"}
    body=[ref for refs in out.get("sides",{}).values() for ref in refs]; corridors=list(open_retreat_corridors(out["positions"],actor_ref=actor_ref,body_refs=body,obstacles=out.get("obstacles",[])))
    if not corridors: return {"combat_after":out,"escaped":False,"reason":"no_open_retreat_corridor"}
    chosen=sorted(corridors,key=lambda row:int(row.get("angle_mdeg",0)))[0]; row=out["positions"][actor_ref]; start_x,start_y=int(row["x_mm"]),int(row["y_mm"]); end_x,end_y=int(chosen["end_x_mm"]),int(chosen["end_y_mm"]); duration_ms=1000
    if people and actor_ref in people:
        cap=capability_from_person(people[actor_ref])
        speed=movement_speed_mmps(cap)
        if isinstance(equipment_ledger,Mapping):
            speed=max(speed,_movement_speed_for_state(actor_ref,people[actor_ref],equipment_ledger,state,cap))
        maximum=speed*duration_ms//1000; dx,dy=end_x-start_x,end_y-start_y; distance=max(1,math.isqrt(dx*dx+dy*dy))
        if distance>maximum: end_x=start_x+dx*maximum//distance; end_y=start_y+dy*maximum//distance
    if not path_clear(out["positions"],actor_ref=actor_ref,end_x_mm=end_x,end_y_mm=end_y,body_refs=body,obstacles=out.get("obstacles",[])): return {"combat_after":out,"escaped":False,"reason":"retreat_path_became_blocked"}
    row["x_mm"]=end_x; row["y_mm"]=end_y; row["facing_mdeg"]=int(chosen["angle_mdeg"])%360000; row["stance"]="disengaging"; out["elapsed_ms"]=int(out.get("elapsed_ms",0))+duration_ms; state["recovery_until_ms"]=int(out["elapsed_ms"])+250; side=_side_of(out,actor_ref); enemy_side="side_b" if side=="side_a" else "side_a"; enemies=[ref for ref in out["sides"][enemy_side] if ref in out["positions"]]; nearest=min([planar_distance_mm(row,out["positions"][ref]) for ref in enemies],default=999_999); escaped=nearest>=6000
    if escaped: statuses=set(state.get("status_families",[])); statuses.add("escaped"); state["status_families"]=sorted(statuses)
    return {"combat_after":out,"escaped":escaped,"reason":"cleared_opponent_reach" if escaped else "retreat_in_progress","corridor":chosen,"movement":{"start_x_mm":start_x,"start_y_mm":start_y,"end_x_mm":end_x,"end_y_mm":end_y,"duration_ms":duration_ms,"nearest_enemy_mm":nearest}}


__all__ = ["attempt_disengage", "capability_from_person", "default_action_for", "initialize_combat", "resolve_exchange"]
