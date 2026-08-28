"""Deterministic physical equipment calculations for the Jianghu game.

The registry stores physical properties. Final movement, fatigue, stealth,
thermal, sensory and weapon-contact effects are derived here from item, wearer
and environment. Clothing never provides hidden combat damage reduction. No random quality rolls or faction bonuses.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


def _num(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _clamp(low: int, high: int, value: float) -> int:
    return max(low, min(high, int(round(value))))


def load_support_kg(*, strength: int, endurance: int) -> float:
    """Return the wearer's strength-relative practical load support.

    Strength is deliberately the dominant term. Endurance helps sustain a load
    over time, but does not substitute for the force required to carry it.
    The result is a physical denominator for burden, not an inventory-slot cap.
    """
    strength_v = max(0, int(strength))
    endurance_v = max(0, int(endurance))
    return max(5.0, 5.0 + 0.55 * strength_v + 0.15 * endurance_v)


def encumbrance_effects(
    *,
    total_mass_kg: float,
    strength: int,
    endurance: int,
    distribution_milli: int = 1000,
) -> dict[str, int | float]:
    """Derive physical load penalties from conserved carried mass.

    There are no inventory slots or clothing-articulation bonuses.  The only
    load inputs are actual carried mass and the wearer's physical capability.
    """
    support = load_support_kg(strength=strength, endurance=endurance)
    effective_mass = max(0.0, total_mass_kg) * max(700, min(1400, distribution_milli)) / 1000.0
    burden = effective_mass / support
    movement_factor = 1000 / (1.0 + 0.60 * burden * burden)
    reaction_factor = 1000 / (1.0 + 0.40 * burden * burden)
    fatigue_milli = 1000 * (1.0 + 0.85 * burden * burden)
    return {
        "load_support_kg": round(support, 4),
        "effective_mass_kg": round(effective_mass, 4),
        "burden_ratio": round(burden, 6),
        "movement_factor_milli": _clamp(300, 1000, movement_factor),
        "reaction_factor_milli": _clamp(400, 1000, reaction_factor),
        "fatigue_cost_milli": _clamp(1000, 5000, fatigue_milli),
    }


def resolve_equipment_item(catalog: Mapping[str, Any], item_ref: str) -> dict[str, Any] | None:
    """Resolve a physical item, including static faction-clothing variants."""
    for key in ("weapon_catalog", "ammunition_catalog", "clothing_catalog", "tool_catalog", "consumable_catalog"):
        table = catalog.get(key, {})
        row = table.get(item_ref) if isinstance(table, Mapping) else None
        if isinstance(row, Mapping):
            out = dict(row); out.setdefault("item_ref", item_ref); return out
    variants = catalog.get("faction_clothing_variants", {})
    variant = variants.get(item_ref) if isinstance(variants, Mapping) else None
    if isinstance(variant, Mapping):
        base_ref = variant.get("base_ref")
        base = catalog.get("clothing_catalog", {}).get(base_ref) if isinstance(catalog.get("clothing_catalog"), Mapping) else None
        if isinstance(base, Mapping):
            out = dict(base); out.update(dict(variant)); out["item_ref"] = item_ref; return out
    return None


def carried_mass_kg(items: Mapping[str, Any], catalog: Mapping[str, Any]) -> float:
    """Return exact aggregate carried mass for simple quantity stacks."""
    total = 0.0
    for ref, raw_qty in items.items():
        try: qty = max(0, int(raw_qty))
        except (TypeError, ValueError): continue
        if qty <= 0: continue
        row = resolve_equipment_item(catalog, str(ref))
        if isinstance(row, Mapping): total += max(0.0, _num(row.get("mass_kg"))) * qty
    return round(total, 6)

def stealth_noise_milli(
    item: Mapping[str, Any],
    *,
    movement_milli: int,
    weather_sound_masking_milli: int = 0,
) -> int:
    noise = int(_num(item.get("noise_milli"), _num(item.get("stealth_noise")) * 100))
    mass = _num(item.get("mass_kg"))
    movement = max(0, min(1800, movement_milli))
    raw = noise + int(round(mass * 12))
    raw = raw * max(250, movement) // 1000
    return max(0, raw - max(0, weather_sound_masking_milli))


def sensory_obstruction(item: Mapping[str, Any]) -> dict[str, int]:
    return {
        "vision_factor_milli": max(500, 1000 - int(_num(item.get("vision_obstruction_milli")))),
        "hearing_factor_milli": max(500, 1000 - int(_num(item.get("hearing_obstruction_milli")))),
    }


def weapon_contact_profile(
    weapon: Mapping[str, Any], *, skill: int, strength: int, dexterity: int, range_m: float
) -> dict[str, int | float | bool]:
    """Resolve the user's ability through the weapon's fixed physical envelope."""
    lo, hi = 0.0, float(weapon.get("reach_m", 0) or weapon.get("maximum_range_m", 0) or 0)
    ideal = weapon.get("ideal_range_m")
    if isinstance(ideal, list) and len(ideal) == 2:
        lo, hi = float(ideal[0]), float(ideal[1])
    in_reach = range_m <= float(weapon.get("reach_m", hi) or hi) if weapon.get("discipline") != "bow" else range_m <= float(weapon.get("maximum_range_m", hi) or hi)
    if lo <= range_m <= hi:
        range_factor = 1000
    elif not in_reach:
        range_factor = 0
    else:
        delta = min(abs(range_m - lo), abs(range_m - hi))
        span = max(0.25, hi - lo)
        range_factor = _clamp(300, 950, 1000 - 600 * delta / span)
    requirement = int(_num(weapon.get("strength_requirement")))
    understrength = max(0, requirement - max(0, strength))
    precision = int(_num(weapon.get("precision"), 50)); control = int(_num(weapon.get("control"), 50))
    handling = max(0, (45 * max(0, skill) + 20 * max(0, dexterity) + 15 * max(0, strength) + 10 * precision + 10 * control) // 100 - understrength * 2)
    return {
        "in_reach": bool(in_reach), "range_factor_milli": range_factor, "handling_score": handling,
        "cut_score": max(0, int(_num(weapon.get("cut"))) * handling * range_factor // 100000),
        "pierce_score": max(0, int(_num(weapon.get("pierce"))) * handling * range_factor // 100000),
        "blunt_score": max(0, int(_num(weapon.get("impact"))) * handling * range_factor // 100000),
        "penetration_score": max(0, int(_num(weapon.get("penetration"))) * handling * range_factor // 100000),
        "guard_score": max(0, int(_num(weapon.get("guard"))) + handling // 3),
        "recovery_ms": max(1, int(_num(weapon.get("recovery_ms"), _num(weapon.get("reload_ms"), 1000)))) + understrength * 10,
    }


def bow_shot_profile(
    bow: Mapping[str, Any], *, bow_skill: int, strength: int, dexterity: int, perception: int,
    distance_m: float, crosswind_mps_tenths: int = 0
) -> dict[str, int | float | bool]:
    max_range = float(_num(bow.get("maximum_range_m")))
    speed = max(1.0, _num(bow.get("projectile_speed_mps"), 1.0))
    requirement = int(_num(bow.get("strength_requirement")))
    understrength = max(0, requirement - max(0, strength))
    hardware_draw = _num(bow.get("draw_weight_kgf"))
    can_draw = understrength == 0
    flight = max(0.0, distance_m) / speed
    wind_sensitivity = int(_num(bow.get("wind_sensitivity_milli"), 1000))
    crosswind = abs(crosswind_mps_tenths) / 10.0
    wind_drift_m = crosswind * flight * 0.12 * wind_sensitivity / 1000.0
    handling = (55 * max(0, bow_skill) + 20 * max(0, dexterity) + 25 * max(0, perception)) // 100
    range_factor = 0 if max_range <= 0 or distance_m > max_range else _clamp(250, 1000, 1000 - 650 * (distance_m / max_range) ** 2)
    wind_penalty = int(round(wind_drift_m * 18))
    accuracy = max(0, handling * range_factor // 1000 - wind_penalty - understrength * 3)
    return {
        "can_draw": can_draw, "hardware_draw_kgf": round(hardware_draw, 2), "launch_speed_mps": round(speed, 2),
        "flight_time_seconds": round(flight, 4), "wind_drift_m": round(wind_drift_m, 4),
        "accuracy_score": accuracy, "range_factor_milli": range_factor,
        "reload_ms": max(1, int(_num(bow.get("reload_ms"), 1000))) + understrength * 20,
    }



def projectile_contact_profile(
    projectile: Mapping[str, Any],
    *,
    launch_speed_mps: float,
    impact_speed_mps: float | None = None,
    accuracy_margin: int = 0,
) -> dict[str, int | float]:
    """Resolve wound channels from the projectile, never from the launcher.

    The bow controls launch and accuracy. The arrow, needle or throwing
    knife controls mass, piercing geometry and penetration at contact.
    """
    launch = max(1.0, float(launch_speed_mps))
    impact = max(0.0, float(impact_speed_mps if impact_speed_mps is not None else launch))
    retained_milli = _clamp(100, 1100, impact * 1000.0 / launch)
    margin_milli = _clamp(450, 1600, 900 + max(-100, min(100, int(accuracy_margin))) * 4)
    pierce = max(0, int(_num(projectile.get("pierce"))) * retained_milli * margin_milli // 1_000_000)
    penetration = max(0, int(_num(projectile.get("penetration"))) * retained_milli * margin_milli // 1_000_000)
    mass_grams = max(1, int(round(_num(projectile.get("mass_kg"), 0.01) * 1000)))
    blunt = max(0, mass_grams * int(round(impact)) // 180)
    cut = max(0, int(_num(projectile.get("cut"))) * retained_milli // 1000)
    return {
        "launch_speed_mps": round(launch, 3),
        "impact_speed_mps": round(impact, 3),
        "retained_energy_milli": retained_milli,
        "cut_score": cut,
        "pierce_score": pierce,
        "blunt_score": blunt,
        "penetration_score": penetration,
    }

def transition_seconds(item: Mapping[str, Any], *, action: str) -> float:
    if action == "ready": return max(0.0, _num(item.get("ready_seconds")))
    if action == "stow": return max(0.0, _num(item.get("stow_seconds")))
    raise ValueError("action must be ready or stow")


FIELD_CONSUMERS = {
    "mass_kg": ("encumbrance_effects", "stealth_noise_milli"),
    "noise_milli": ("stealth_noise_milli",),
    "vision_obstruction_milli": ("sensory_obstruction",),
    "hearing_obstruction_milli": ("sensory_obstruction",),
    "strength_requirement": ("weapon_contact_profile", "bow_shot_profile"),
    "integrity_max": ("degrade_integrity", "repair_quote"),
    "reach_m": ("weapon_contact_profile",),
    "ideal_range_m": ("weapon_contact_profile",),
    "impact": ("weapon_contact_profile",),
    "cut": ("weapon_contact_profile",),
    "pierce": ("weapon_contact_profile", "projectile_contact_profile"),
    "penetration": ("weapon_contact_profile", "projectile_contact_profile"),
    "precision": ("weapon_contact_profile", "bow_shot_profile"),
    "control": ("weapon_contact_profile",),
    "guard": ("weapon_contact_profile",),
    "recovery_ms": ("weapon_contact_profile",),
    "draw_weight_kgf": ("bow_shot_profile",),
    "projectile_speed_mps": ("bow_shot_profile",),
    "maximum_range_m": ("bow_shot_profile", "weapon_contact_profile"),
    "reload_ms": ("bow_shot_profile",),
    "wind_sensitivity_milli": ("bow_shot_profile",),
    "ready_seconds": ("transition_seconds",),
    "stow_seconds": ("transition_seconds",),
    "hands_required": ("exact_combat.weapon_readiness",),
    "visibility_milli": ("exact_combat.projectile_detection",),
    "interception_difficulty_milli": ("exact_combat.projectile_interception",),
    "interception_area_milli": ("exact_combat.projectile_interception",),
}


def degrade_integrity(*,current_integrity:int,impact_score:int,penetration_score:int,material_resistance:int)->dict[str,int]:
    """Deterministic equipment condition loss from one physical contact."""
    current=max(0,int(current_integrity)); stress=max(0,int(impact_score))+max(0,int(penetration_score))*2
    resistance=max(1,int(material_resistance)); loss=max(0,(stress-resistance)//10)
    return {'integrity_before':current,'integrity_loss':min(current,loss),'integrity_after':max(0,current-loss)}


def repair_quote(*,item_mass_kg:float,current_integrity:int,maximum_integrity:int,crafting_skill:int)->dict[str,int]:
    if maximum_integrity<=0 or not 0<=current_integrity<=maximum_integrity: raise ValueError('integrity invalid')
    missing=maximum_integrity-current_integrity
    material_kg_milli=int(round(max(0.0,item_mass_kg)*1000))*missing//maximum_integrity//4
    labor_hours_milli=max(0,missing*2000//max(20,crafting_skill))
    return {'missing_integrity':missing,'replacement_material_kg_milli':material_kg_milli,'labor_hours_milli':labor_hours_milli}
