"""Compact deterministic appearance derivation for persistent Jianghu people.

Immutable visual identity is derived from stable person facts.  Long prose is
never persisted per person; current injuries remain health authority.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence


def _pick(stable: str, salt: str, values: Sequence[str]) -> str:
    if not values:
        raise ValueError("appearance choice set empty")
    raw = hashlib.sha256(f"{stable}|{salt}".encode("utf-8")).digest()
    return values[int.from_bytes(raw[:4], "big") % len(values)]


def _age(person: Mapping[str, Any], current_year: int | None) -> int | None:
    if current_year is None or not isinstance(person.get("birth_year"), int):
        return None
    return max(0, int(current_year) - int(person["birth_year"]))


def _build(person: Mapping[str, Any], current_year: int | None) -> str:
    if str(person.get("person_id")) == "pc_wei_tang":
        return "lean athletic"
    attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
    strength = max(0, int(attrs.get("strength", 0)))
    endurance = max(0, int(attrs.get("endurance", 0)))
    mass = max(1, int(person.get("body_mass_kg", 60)))
    age = _age(person, current_year)
    if age is not None and age < 13:
        return "childlike" if mass < 45 else "sturdy childlike"
    physical = (strength + endurance) // 2
    if physical >= 85 and mass >= 78:
        return "powerfully built"
    if physical >= 75:
        return "athletic"
    if mass <= 58:
        return "slender"
    if mass >= 85:
        return "heavyset"
    return "average"


def _health_marks(health: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(health, Mapping):
        return []
    marks: list[str] = []
    for raw in health.get("injuries", []):
        if not isinstance(raw, Mapping):
            continue
        loss = int(raw.get("function_loss_pct", 0))
        zone = str(raw.get("zone", ""))
        if zone and loss >= 50:
            marks.append(f"visible impairment: {zone}")
    penalties = health.get("functional_penalties", {})
    if isinstance(penalties, Mapping):
        if int(penalties.get("vision_milli", 0)) >= 500:
            marks.append("visible vision impairment")
    return sorted(set(marks))[:6]


def appearance_profile(
    person: Mapping[str, Any], *, current_year: int | None = None,
    health: Mapping[str, Any] | None = None,
    parent_profiles: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    pid = str(person.get("person_id") or "unknown")
    if pid == "pc_wei_tang":
        hair_color = "black"; eye_color = "black"; hair_texture = "curly"; hair_presentation = "short curly"
    else:
        parent_hair = [str(p.get("hair_color")) for p in parent_profiles if p.get("hair_color")]
        parent_eyes = [str(p.get("eye_color")) for p in parent_profiles if p.get("eye_color")]
        hair_color = _pick(pid, "hair-color", parent_hair or ("black", "black", "black", "dark brown"))
        eye_color = _pick(pid, "eye-color", parent_eyes or ("black", "dark brown", "dark brown", "brown"))
        hair_texture = _pick(pid, "hair-texture", ("straight", "straight", "wavy", "curly"))
        sex = str(person.get("sex") or "")
        presentation_pool = ("short", "tied back", "shoulder length", "cropped") if sex == "male" else ("tied back", "braided", "shoulder length", "long")
        hair_presentation = _pick(pid, "hair-presentation", presentation_pool)
    age = _age(person, current_year)
    return {
        "hair_color": hair_color,
        "eye_color": eye_color,
        "hair_texture": hair_texture,
        "hair_presentation": hair_presentation,
        "build": _build(person, current_year),
        "appearance": int(person.get("appearance", 0)),
        "age": age,
        "visible_health_marks": _health_marks(health if health is not None else person.get("health")),
    }


__all__ = ["appearance_profile"]
