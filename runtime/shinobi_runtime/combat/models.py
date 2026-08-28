"""Minimal deterministic exact-combat data contracts for the Jianghu game.

These objects deliberately model only the bounded local combat state consumed by
``martial_world.exact_combat``.  They contain only the physical and tactical fields needed by exact combat
and are not a second character authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _int(value: object, label: str, lo: int = 0, hi: int = 1_000_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{label} must be an integer in {lo}..{hi}")
    return value


@dataclass(frozen=True)
class PersonnelState:
    total: int
    active: int
    wounded: int = 0
    incapacitated: int = 0
    killed: int = 0
    captured: int = 0
    escaped: int = 0

    def __post_init__(self) -> None:
        _int(self.total, "personnel total", 1)
        values = (self.active, self.wounded, self.incapacitated, self.killed, self.captured, self.escaped)
        for name, value in zip(("active", "wounded", "incapacitated", "killed", "captured", "escaped"), values):
            _int(value, f"personnel {name}", 0, self.total)
        if sum(values) != self.total:
            raise ValueError("personnel categories must conserve total")


@dataclass(frozen=True)
class CapabilityProfile:
    offense: int
    defense: int
    control: int
    mobility: int
    perception: int
    stealth: int
    capture: int
    escape: int
    reaction: int = 0

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _int(getattr(self, name), f"capability {name}")


@dataclass(frozen=True)
class CombatIntent:
    action: str
    target_refs: Tuple[str, ...] = ()
    objective_ref: Optional[str] = None

    def __post_init__(self) -> None:
        _nonempty(self.action, "combat intent action")
        if self.objective_ref is not None:
            _nonempty(self.objective_ref, "combat objective ref")
        if any(not isinstance(x, str) or not x for x in self.target_refs):
            raise ValueError("combat intent target refs must be strings")


@dataclass(frozen=True)
class InformationState:
    observed_refs: Tuple[str, ...]
    confidence_milli: int = 1000
    concealment_milli: int = 0
    surprise_milli: int = 0

    def __post_init__(self) -> None:
        if any(not isinstance(x, str) or not x for x in self.observed_refs):
            raise ValueError("observed refs must be strings")
        _int(self.confidence_milli, "confidence", 0, 1000)
        _int(self.concealment_milli, "concealment", 0, 1000)
        _int(self.surprise_milli, "surprise", 0, 1000)


@dataclass(frozen=True)
class PositionState:
    zone_ref: str
    elevation_mm: int = 0
    cover_milli: int = 0
    x_mm: int = 0
    y_mm: int = 0
    facing_mdeg: int = 0
    body_radius_mm: int = 300
    vx_mmps: int = 0
    vy_mmps: int = 0
    stance: str = "ready"

    def __post_init__(self) -> None:
        _nonempty(self.zone_ref, "position zone")
        _int(self.elevation_mm + 100_000_000, "position elevation_mm offset", 0, 200_000_000)
        _int(self.cover_milli, "cover", 0, 1000)
        if isinstance(self.x_mm, bool) or not isinstance(self.x_mm, int): raise ValueError("x_mm invalid")
        if isinstance(self.y_mm, bool) or not isinstance(self.y_mm, int): raise ValueError("y_mm invalid")
        _int(self.facing_mdeg, "facing", 0, 359_999)
        _int(self.body_radius_mm, "body radius", 50, 5_000)
        if isinstance(self.vx_mmps, bool) or not isinstance(self.vx_mmps, int): raise ValueError("vx_mmps invalid")
        if isinstance(self.vy_mmps, bool) or not isinstance(self.vy_mmps, int): raise ValueError("vy_mmps invalid")
        _nonempty(self.stance, "stance")

    def to_record(self) -> dict[str, Any]:
        return {
            "zone_ref": self.zone_ref,
            "elevation_mm": self.elevation_mm,
            "cover_milli": self.cover_milli,
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
            "facing_mdeg": self.facing_mdeg,
            "body_radius_mm": self.body_radius_mm,
            "vx_mmps": self.vx_mmps,
            "vy_mmps": self.vy_mmps,
            "stance": self.stance,
        }


@dataclass(frozen=True)
class ActionProfile:
    method_ref: Optional[str] = None
    effect_kind: str = "physical"
    delivery: str = "direct"
    startup_ms: int = 0
    external_contact: bool = True
    speed_score: int = 0
    damage_channels: Tuple[str, ...] = ()
    effect_parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.method_ref is not None: _nonempty(self.method_ref, "method ref")
        _nonempty(self.effect_kind, "effect kind")
        _nonempty(self.delivery, "delivery")
        _int(self.startup_ms, "startup ms", 0, 60_000)
        if not isinstance(self.external_contact, bool): raise ValueError("external_contact must be boolean")
        _int(self.speed_score, "speed score")
        if any(not isinstance(x, str) or not x for x in self.damage_channels):
            raise ValueError("damage channels invalid")
        if not isinstance(self.effect_parameters, Mapping):
            raise ValueError("effect_parameters must be a mapping")


@dataclass(frozen=True)
class ReactiveDefense:
    defense_ref: str
    defense_kind: str

    def __post_init__(self) -> None:
        _nonempty(self.defense_ref, "defense ref")
        _nonempty(self.defense_kind, "defense kind")


@dataclass(frozen=True)
class Participant:
    participant_ref: str
    authoritative_owner_ref: str
    side_ref: str
    sequence: int
    representation: str
    capability: CapabilityProfile
    personnel: PersonnelState
    position: PositionState
    information: InformationState
    intent: CombatIntent
    initiative: int
    readiness: int
    morale: int
    cohesion: int
    action_profile: Optional[ActionProfile] = None
    reactive_defenses: Tuple[ReactiveDefense, ...] = ()
    active_defense_load_milli: int = 0
    balance_milli: int = 1000
    limb_commitment_milli: int = 0
    recovery_remaining_ms: int = 0
    weapon_position: str = "guard"
    status_families: Tuple[str, ...] = ()
    physical_defense_preferences: Tuple[str, ...] = ()
    counterattack_posture: str = "selective"
    health_model: str = "anatomy"
    body_mass_grams: int = 70_000
    physiology_endurance: int = 100
    physiology_willpower: int = 100
    blood_lost_ml: int = 0
    wounds: Tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        for value, label in ((self.participant_ref,"participant ref"),(self.authoritative_owner_ref,"owner ref"),(self.side_ref,"side ref"),(self.representation,"representation"),(self.weapon_position,"weapon position"),(self.health_model,"health model")):
            _nonempty(value, label)
        _int(self.sequence, "sequence")
        _int(self.initiative, "initiative")
        _int(self.readiness, "readiness", 0, 200)
        _int(self.morale, "morale", 0, 200)
        _int(self.cohesion, "cohesion", 0, 200)
        _int(self.active_defense_load_milli, "active defense load", 0, 1000)
        _int(self.balance_milli, "balance", 0, 1000)
        _int(self.limb_commitment_milli, "limb commitment", 0, 1000)
        _int(self.recovery_remaining_ms, "recovery remaining")
        _int(self.body_mass_grams, "body mass", 1_000, 1_000_000)
        _int(self.physiology_endurance, "physiology endurance")
        _int(self.physiology_willpower, "physiology willpower")
        _int(self.blood_lost_ml, "blood lost", 0, 20_000)
        if any(not isinstance(x, str) or not x for x in self.status_families): raise ValueError("status families invalid")
        if any(not isinstance(x, str) or not x for x in self.physical_defense_preferences): raise ValueError("defense preferences invalid")
        if self.counterattack_posture not in {"rare", "selective", "active"}: raise ValueError("counterattack posture invalid")


__all__ = [
    "ActionProfile", "CapabilityProfile", "CombatIntent", "InformationState",
    "Participant", "PersonnelState", "PositionState", "ReactiveDefense",
]
