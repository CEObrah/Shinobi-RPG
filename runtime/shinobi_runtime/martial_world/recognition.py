"""Deterministic personal recognition and faction-identification evidence."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def faction_clothing_evidence(items: Mapping[str, int], equipment_catalog: Mapping[str, Any]) -> str | None:
    variants = equipment_catalog.get("faction_clothing_variants", {}) if isinstance(equipment_catalog, Mapping) else {}
    if not isinstance(variants, Mapping):
        return None
    for ref, quantity in sorted(items.items()):
        if int(quantity) <= 0:
            continue
        row = variants.get(ref)
        if isinstance(row, Mapping) and isinstance(row.get("faction_ref"), str):
            return str(row["faction_ref"])
    return None


def _concealment(items: Mapping[str, int], equipment_catalog: Mapping[str, Any]) -> tuple[int, int]:
    clothing = equipment_catalog.get("clothing_catalog", {}) if isinstance(equipment_catalog, Mapping) else {}
    face = 1000
    hair = 1000
    if not isinstance(clothing, Mapping):
        return face, hair
    for ref, quantity in items.items():
        if int(quantity) <= 0:
            continue
        row = clothing.get(ref)
        if not isinstance(row, Mapping):
            # Faction clothing variants inherit the shared base mechanically.
            variant = equipment_catalog.get("faction_clothing_variants", {}).get(ref, {}) if isinstance(equipment_catalog.get("faction_clothing_variants"), Mapping) else {}
            base_ref = variant.get("base_ref") if isinstance(variant, Mapping) else None
            row = clothing.get(base_ref) if isinstance(base_ref, str) else None
        if not isinstance(row, Mapping):
            continue
        face = min(face, max(0, int(row.get("face_evidence_milli", 1000))))
        hair = min(hair, max(0, int(row.get("hair_evidence_milli", 1000))))
    return face, hair


def _visible_weapon_refs(items: Mapping[str, int], equipment_catalog: Mapping[str, Any]) -> list[str]:
    weapons = equipment_catalog.get("weapon_catalog", {}) if isinstance(equipment_catalog, Mapping) else {}
    if not isinstance(weapons, Mapping):
        return []
    return [str(ref) for ref, qty in sorted(items.items()) if int(qty) > 0 and ref in weapons]


def _visible_injury_marks(target: Mapping[str, Any]) -> list[str]:
    health = target.get("health", {}) if isinstance(target.get("health"), Mapping) else {}
    injuries = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
    marks = []
    for row in injuries:
        if not isinstance(row, Mapping):
            continue
        if int(row.get("function_loss_pct", 0)) >= 35 or int(row.get("severity", 0)) >= 60:
            zone = str(row.get("zone") or row.get("structure_ref") or "")
            if zone:
                marks.append(zone)
    return sorted(set(marks))[:6]


def recognition_assessment(
    *, observer: Mapping[str, Any], target: Mapping[str, Any], target_items: Mapping[str, int],
    equipment_catalog: Mapping[str, Any], familiarity: int = 0, distance_m: float = 3.0,
    light_milli: int = 1000, viewing_seconds: int = 5,
    weather_visibility_milli: int = 1000, smoke_visibility_milli: int = 1000,
    viewing_angle_milli: int = 1000, obstruction_milli: int = 1000,
    known_weapon_refs: Sequence[str] = (), known_injury_marks: Sequence[str] = (),
    known_companion_refs: Sequence[str] = (), visible_companion_refs: Sequence[str] = (),
    known_qi_profile_milli: int = 0, qi_perceptible: bool = False,
) -> dict[str, Any]:
    """Return evidence channels separately from identity conclusions.

    Only channels backed by current data are used. Voice/gait are deliberately
    absent until the person model owns stable voice/gait facts. Appearance
    affects salience/memorability, never trust or persuasion. Faction clothing
    identifies garment provenance, not membership.
    """
    oattrs = observer.get("attributes", {}) if isinstance(observer.get("attributes"), Mapping) else {}
    perception = max(0, int(oattrs.get("perception", 0)))
    intelligence = max(0, int(oattrs.get("intelligence", 0)))
    appearance = max(0, int(target.get("appearance", 0)))
    face_milli, hair_milli = _concealment(target_items, equipment_catalog)
    distance_milli = max(120, min(1000, int(1000 / max(1.0, float(distance_m) / 3.0))))
    duration_milli = max(160, min(1200, 400 + max(0, int(viewing_seconds)) * 80))
    visual = (face_milli * 7 + hair_milli * 3) // 10
    environment = visual * max(50, min(1000, int(light_milli))) // 1000
    for factor in (weather_visibility_milli, smoke_visibility_milli, viewing_angle_milli, obstruction_milli):
        environment = environment * max(50, min(1000, int(factor))) // 1000
    environment = environment * distance_milli // 1000
    environment = environment * duration_milli // 1000

    familiarity = max(0, min(100, int(familiarity)))
    observer_score = perception * 6 + intelligence * 2 + familiarity * 10

    # Explicit known-comparison channels. These never establish what the
    # observer knew; the caller must supply player/NPC knowledge lawfully.
    visible_weapons = _visible_weapon_refs(target_items, equipment_catalog)
    weapon_overlap = sorted(set(str(x) for x in known_weapon_refs) & set(visible_weapons))
    injury_marks = _visible_injury_marks(target)
    injury_overlap = sorted(set(str(x) for x in known_injury_marks) & set(injury_marks))
    companion_overlap = sorted(set(str(x) for x in known_companion_refs) & set(str(x) for x in visible_companion_refs))
    body_profile_milli = 0
    if familiarity >= 20:
        # Height is not currently authoritative. Body mass plus physical build is.
        attrs = target.get("attributes", {}) if isinstance(target.get("attributes"), Mapping) else {}
        body_profile_milli = min(220, 60 + max(0, int(target.get("body_mass_kg", 0))) + (int(attrs.get("strength", 0)) + int(attrs.get("endurance", 0))) // 4)
    contextual_bonus = min(500, len(weapon_overlap) * 90 + len(injury_overlap) * 110 + len(companion_overlap) * 80 + body_profile_milli)
    qi_evidence = 0
    if qi_perceptible and int(known_qi_profile_milli) > 0:
        qi = max(0, int(target.get("qi", 0)))
        qic = max(0, int(target.get("qi_control", 0)))
        actual = min(1000, qi * 4 + qic * 3)
        qi_evidence = max(0, 180 - abs(actual - min(1000, int(known_qi_profile_milli))) // 4)
        contextual_bonus += qi_evidence

    identity_evidence = observer_score * environment // 1000 + contextual_bonus
    threshold = max(250, 1000 - appearance * 3)
    # High contextual evidence may permit recognition at slightly lower direct
    # familiarity, but a total stranger still cannot identify a face from model truth.
    recognized = familiarity >= 20 and identity_evidence >= threshold
    salience_milli = min(1800, 700 + appearance * 7)
    faction_ref = faction_clothing_evidence(target_items, equipment_catalog)
    return {
        "personal_recognized": bool(recognized),
        "personal_recognition_confidence_milli": min(1000, max(0, identity_evidence * 1000 // max(1, threshold * 2))),
        "faction_clothing_evidence_ref": faction_ref,
        "faction_membership_proven_by_clothing": False,
        "face_evidence_milli": face_milli,
        "hair_evidence_milli": hair_milli,
        "body_build_evidence_milli": body_profile_milli,
        "visible_weapon_refs": visible_weapons,
        "matching_known_weapon_refs": weapon_overlap,
        "visible_injury_marks": injury_marks,
        "matching_known_injury_marks": injury_overlap,
        "matching_companion_refs": companion_overlap,
        "qi_behavior_evidence_milli": qi_evidence,
        "environment_visibility_milli": environment,
        "visual_salience_milli": salience_milli,
    }


__all__ = ["faction_clothing_evidence", "recognition_assessment"]
