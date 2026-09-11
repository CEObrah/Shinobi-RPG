"""Deterministic physical-response layer for exact personal combat.

This module does not decide damage. It converts perception, body state,
geometry, equipment-derived options and current commitment into one lawful
physical response. Movement responses update real local coordinates; non-
movement responses update directional body/weapon commitment that the resolver
can carry across processing boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import copy
import math
from typing import Any, Mapping, Sequence

from .geometry import (
    DEFAULT_BODY_RADIUS_MM,
    angular_difference_mdeg,
    facing_to_target_mdeg,
    path_clear,
    planar_distance_mm,
    trace_attack_geometry,
)
from .models import ActionProfile, CapabilityProfile, Participant, PositionState

DEFENSE_RESPONSES = (
    "evade", "reposition", "parry", "deflect", "block", "brace", "counter_intercept",
)
_MOVEMENT_LOCKS = frozenset({"immobilized", "restrained", "grappled", "entangled", "confined", "pinned"})
_ACTION_LOCKS = frozenset({"stunned", "unconscious", "incapacitated", "dead"})
_HARD_RESTRAINTS = frozenset({"restrained", "entangled", "pinned"})
_POSTURE_IMPAIRMENTS = frozenset({"knockdown", "knocked_down", "prone"})
_VISION_IMPAIRMENTS = frozenset({"blind", "blinded", "blind_both_eyes"})

_RECOVERABLE_POST_ACTION_POSITIONS = frozenset({
    "extended_attack", "extended_parry", "displaced_guard", "committed_guard", "guarded_brace",
})


def recovered_defense_participant(defender: Any) -> Any:
    """Normalize only completed post-action commitments back to guard."""
    if max(0, int(getattr(defender, "recovery_remaining_ms", 0))) > 0:
        return defender
    weapon_position = str(getattr(defender, "weapon_position", "guard"))
    limb_commitment = max(0, int(getattr(defender, "limb_commitment_milli", 0)))
    if weapon_position not in _RECOVERABLE_POST_ACTION_POSITIONS:
        return defender
    changes = {"weapon_position": "guard", "limb_commitment_milli": 0}
    if hasattr(defender, "balance_milli"):
        changes["balance_milli"] = 1000
    return replace(defender, **changes)


def attack_warning_ms(profile: ActionProfile | None) -> int:
    """Physical warning from attack startup, approach and projectile flight."""
    if profile is None:
        return 150
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    approach = params.get("approach_time_ms", 0)
    if isinstance(approach, bool) or not isinstance(approach, int):
        approach = 0
    flight = 0
    projectile = params.get("projectile")
    if isinstance(projectile, Mapping):
        raw_flight = projectile.get("flight_time_ms", 0)
        if isinstance(raw_flight, int) and not isinstance(raw_flight, bool):
            flight = raw_flight
    return max(40, max(0, int(profile.startup_ms)) + max(0, approach) + max(0, flight))


def resolver_response_lead_ms(*, warning_ms: int, reaction_latency_ms: int) -> int:
    return max(0, max(0, int(warning_ms)) - max(0, int(reaction_latency_ms)))


def causal_response_start_ms(*, attack_start_ms: int, contact_at_ms: int, reaction_latency_ms: int) -> int:
    start = int(attack_start_ms)
    contact = max(start, int(contact_at_ms))
    return min(contact, start + max(0, int(reaction_latency_ms)))


@dataclass(frozen=True)
class PhysicalDefenseDecision:
    detected: bool
    detection_margin: int
    response: str
    before_position: PositionState
    after_position: PositionState
    displacement_mm: int
    reaction_delay_ms: int
    recovery_ms: int
    defense_factor_milli: int
    reaction_availability_milli: int
    balance_after_milli: int
    limb_commitment_after_milli: int
    weapon_position_after: str
    attack_angle_mdeg: int
    tracking_milli: int
    force_transmission_milli: int
    control_disruption: int
    displacement_resistance_milli: int
    interrupts_attacker: bool
    contact_surface: str
    reason: str

    def trace(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "detection_margin": self.detection_margin,
            "response": self.response,
            "before": self.before_position.to_record(),
            "after": self.after_position.to_record(),
            "displacement_mm": self.displacement_mm,
            "reaction_delay_ms": self.reaction_delay_ms,
            "recovery_ms": self.recovery_ms,
            "defense_factor_milli": self.defense_factor_milli,
            "reaction_availability_milli": self.reaction_availability_milli,
            "balance_after_milli": self.balance_after_milli,
            "limb_commitment_after_milli": self.limb_commitment_after_milli,
            "weapon_position_after": self.weapon_position_after,
            "attack_angle_mdeg": self.attack_angle_mdeg,
            "tracking_milli": self.tracking_milli,
            "force_transmission_milli": self.force_transmission_milli,
            "control_disruption": self.control_disruption,
            "displacement_resistance_milli": self.displacement_resistance_milli,
            "interrupts_attacker": self.interrupts_attacker,
            "contact_surface": self.contact_surface,
            "reason": self.reason,
        }


def movement_speed_mmps(capability: CapabilityProfile) -> int:
    """Local combat movement speed; capability is uncapped, hardware is not."""
    # 100 mobility ~= 4 m/s combat footwork; 200 ~= 6.5 m/s. Values above that
    # remain useful without creating teleportation.
    return max(700, 1500 + max(0, int(capability.mobility)) * 25)


def physical_reach_mm(profile: ActionProfile | None) -> int:
    if profile is None or not profile.external_contact:
        return 0
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    raw = params.get("physical_reach_m", params.get("maximum_range_m"))
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return max(100, int(round(float(raw) * 1000)))
    if profile.delivery in {"projectile", "thrown", "ranged"}:
        return 0
    # Ordinary unarmed/body contact. Kicks are handled inside this compact body
    # envelope rather than inheriting the removed abstract short-range band.
    return 1100


def status_action_allowed(status_families: Sequence[str], action: str) -> bool:
    statuses = set(status_families)
    if statuses & _ACTION_LOCKS:
        return action in {"hold"}
    # A fully restrained/entangled body cannot simply execute the same attack
    # or movement vocabulary as an unrestricted fighter. Breaking that state
    # requires a registered release/escape mechanic rather than pretending it
    # is only a defense-number penalty.
    if statuses & _HARD_RESTRAINTS and action in {"attack", "capture", "escape", "extract", "disengage"}:
        return False
    if action in {"escape", "extract", "disengage"} and statuses & _MOVEMENT_LOCKS:
        return False
    return True


def _attack_warning_ms(profile: ActionProfile | None, *, distance_mm: int) -> int:
    del distance_mm
    return attack_warning_ms(profile)


def detect_attack(
    *,
    attacker: Participant,
    defender: Participant,
    attacker_position: PositionState,
    defender_position: PositionState,
    attacker_capability: CapabilityProfile,
    defender_capability: CapabilityProfile,
    profile: ActionProfile | None,
    line_of_sight: bool,
) -> tuple[bool, int, int, int]:
    """Return detected, margin, reaction_delay_ms, incoming_angle_mdeg."""
    bearing = facing_to_target_mdeg(defender_position.to_record(), attacker_position.to_record())
    facing_delta = angular_difference_mdeg(defender_position.facing_mdeg, bearing)
    signed_delta = (bearing - defender_position.facing_mdeg + 540_000) % 360_000 - 180_000
    facing_penalty = 0 if facing_delta <= 60_000 else (20 if facing_delta <= 120_000 else 45)
    statuses = set(defender.status_families)
    blind_both = bool(statuses & _VISION_IMPAIRMENTS)
    sensory_penalty = 120 if blind_both else 0
    # Positive relative bearing is the defender's left hemisphere; negative is
    # the right. A destroyed eye therefore matters directionally instead of
    # becoming a generic global defense debuff.
    if "blind_left_eye" in statuses and signed_delta > 30_000:
        sensory_penalty += 35
    if "blind_right_eye" in statuses and signed_delta < -30_000:
        sensory_penalty += 35
    observed = attacker.participant_ref in defender.information.observed_refs
    # Encounter memory can help interpret the motion of an attacker who is
    # presently visible, but it is not current sensory evidence.  In
    # particular, remembering an exact opponent must not make an otherwise
    # unseen/undetected strike easier to detect than an identical strike from
    # another body.
    observation_bonus = 45 if observed and line_of_sight and not blind_both else 0
    los_bonus = 0 if blind_both else (30 if line_of_sight else -45)
    distance_mm=planar_distance_mm(attacker_position.to_record(), defender_position.to_record())
    warning = _attack_warning_ms(profile, distance_mm=distance_mm)
    speed = max(1, int(profile.speed_score if profile is not None else attacker_capability.mobility))
    speed_pressure = min(120, speed // 4)
    surprise = max(0, int(attacker.information.surprise_milli) // 25)
    projectile_penalty=0
    if profile is not None and profile.delivery in {"projectile","ranged","thrown"}:
        params=profile.effect_parameters if isinstance(profile.effect_parameters,Mapping) else {}
        visibility=max(50,min(1000,int(params.get("projectile_visibility_milli",700))))
        width=max(1,int(params.get("projectile_width_mm",8)))
        release_concealment=max(0,min(1000,int(params.get("release_concealment_milli",0))))
        distance_m=max(0.0,distance_mm/1000.0)
        projectile_penalty=(1000-visibility)//4 + max(0,10-width)*6 + int(distance_m*max(0,700-visibility)/120) + release_concealment//10
    margin = (
        int(defender_capability.perception) * 2
        + int(defender_capability.reaction)
        + observation_bonus + los_bonus
        + min(80, warning // 10)
        - facing_penalty - sensory_penalty - speed_pressure - surprise - projectile_penalty
    )
    detected = margin >= 0 and not (statuses & _ACTION_LOCKS)
    reaction_score = max(1, int(defender_capability.reaction) + int(defender_capability.perception) // 2)
    # Combat latency is anticipatory, not a laboratory simple-reaction test.
    # Fighters already tracking a visible opponent should enter useful defensive
    # motion at realistic trained/elite stat bands, while slow or surprised
    # defenders can still miss a short startup entirely.  The prior hyperbolic
    # curve required ~160+ reaction/perception just to begin answering a normal
    # 330 ms thrust, making ordinary elite melee effectively undefendable.
    delay = max(90, min(650, 490 - 2 * reaction_score))
    return detected, margin, delay, bearing


def _defense_displacement_window_ms(profile: ActionProfile | None) -> int:
    """Return movement time still available after the attacker has closed.

    Melee ``approach_time_ms`` is already consumed by the attacker's tracked
    closing movement. It remains valid warning for detection/orientation, but
    counting it again here would let a defender spend the same seconds twice: once
    while being tracked by the approach and again as a full post-close dodge.
    Projectiles still grant their real post-release flight time.
    """
    if profile is None:
        return 150
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    startup = max(0, int(profile.startup_ms))
    if profile.delivery in {"projectile", "ranged", "thrown"}:
        projectile = params.get("projectile")
        flight = projectile.get("flight_time_ms", 0) if isinstance(projectile, Mapping) else 0
        if isinstance(flight, bool) or not isinstance(flight, int):
            flight = 0
        return max(40, startup + max(0, flight))
    return max(40, startup)


def _preference_bonus(participant: Participant, response: str) -> int:
    prefs = participant.physical_defense_preferences
    if response not in prefs:
        return 0
    return max(0, 80 - prefs.index(response) * 12)


def _candidate_displacements(
    position: PositionState,
    *, incoming_bearing_mdeg: int,
    max_displacement_mm: int,
) -> tuple[tuple[str, int, int, int], ...]:
    # Sidesteps first, then diagonals/backstep, then inside movement. Labels are
    # retained for traceability but all are just coordinate changes.
    offsets = (
        ("sidestep_left", incoming_bearing_mdeg + 90_000),
        ("sidestep_right", incoming_bearing_mdeg - 90_000),
        ("diagonal_left", incoming_bearing_mdeg + 135_000),
        ("diagonal_right", incoming_bearing_mdeg - 135_000),
        ("backstep", incoming_bearing_mdeg + 180_000),
        ("move_inside", incoming_bearing_mdeg),
    )
    rows = []
    for label, angle in offsets:
        normalized = angle % 360_000
        rad = math.radians(normalized / 1000.0)
        rows.append((
            label,
            int(position.x_mm + round(math.cos(rad) * max_displacement_mm)),
            int(position.y_mm + round(math.sin(rad) * max_displacement_mm)),
            normalized,
        ))
    return tuple(rows)




def _turn_toward_mdeg(current: int, target: int, *, max_turn_mdeg: int) -> int:
    current %= 360_000; target %= 360_000
    delta = (target - current + 540_000) % 360_000 - 180_000
    if abs(delta) <= max_turn_mdeg:
        return target
    return (current + (max_turn_mdeg if delta > 0 else -max_turn_mdeg)) % 360_000

def _position_with(position: PositionState, *, x_mm: int, y_mm: int, facing_mdeg: int, vx_mmps: int, vy_mmps: int, stance: str) -> PositionState:
    return PositionState(
        zone_ref=position.zone_ref,
        elevation_mm=position.elevation_mm,
        cover_milli=position.cover_milli,
        x_mm=x_mm,
        y_mm=y_mm,
        facing_mdeg=facing_mdeg % 360_000,
        body_radius_mm=position.body_radius_mm,
        vx_mmps=vx_mmps,
        vy_mmps=vy_mmps,
        stance=stance,
    )


def _movement_response(
    *,
    response: str,
    attacker_ref: str,
    defender_ref: str,
    participant_positions: Mapping[str, Mapping[str, Any]],
    defender_position: PositionState,
    incoming_bearing_mdeg: int,
    defender_capability: CapabilityProfile,
    reaction_delay_ms: int,
    warning_ms: int,
    body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]],
    profile: ActionProfile | None = None,
    attack_trajectory: Mapping[str, Any] | None = None,
    tracking_milli: int = 0,
) -> PositionState | None:
    available_ms = max(0, warning_ms - reaction_delay_ms)
    if available_ms <= 0:
        return None
    speed = movement_speed_mmps(defender_capability)
    # Defensive movement is bounded by actual time available. A tiny positive
    # reaction window may produce only a few millimetres of motion; it must not
    # be inflated into a minimum dodge distance.
    displacement = min(3200, speed * available_ms // 1000)
    if displacement <= 0:
        return None
    current = dict(participant_positions)
    current[defender_ref] = defender_position.to_record()
    candidates = _candidate_displacements(defender_position, incoming_bearing_mdeg=incoming_bearing_mdeg, max_displacement_mm=displacement)
    # Reposition prefers angle/spacing; evade prefers quickest lateral exit.
    if response == "reposition":
        candidates = tuple(sorted(candidates, key=lambda row: (0 if row[0].startswith("diagonal") else 1, row[0])))
    velocity_angle: int | None = None
    if abs(defender_position.vx_mmps) + abs(defender_position.vy_mmps) >= 300:
        velocity_angle = int(round(math.degrees(math.atan2(defender_position.vy_mmps, defender_position.vx_mmps)) * 1000)) % 360000
    for _label, x, y, movement_angle in candidates:
        if velocity_angle is not None:
            turn = angular_difference_mdeg(velocity_angle, movement_angle)
            # Momentum does not forbid redirection, but a near-instant complete
            # reversal is not a free dodge. More warning time can pay the turn.
            if turn >= 135000 and available_ms < 300:
                continue
            if turn >= 90000 and available_ms < 180:
                continue
        if not path_clear(current, actor_ref=defender_ref, end_x_mm=x, end_y_mm=y, body_refs=body_refs, obstacles=obstacles):
            continue
        dx, dy = x - defender_position.x_mm, y - defender_position.y_mm
        # A defensive sidestep/backstep is not a command to turn the torso and
        # attention toward the direction of travel.  Keep the guard oriented on
        # the incoming attacker while the feet move, with only the bounded turn
        # that the available response time can physically support.  The old
        # behavior faced the defender 90-135 degrees away after a successful
        # dodge, making the very next attack from the same opponent function as
        # a blind-side strike.
        facing = _turn_toward_mdeg(
            defender_position.facing_mdeg, incoming_bearing_mdeg,
            max_turn_mdeg=max(20_000, min(120_000, available_ms * 400)),
        )
        candidate = _position_with(
            defender_position, x_mm=x, y_mm=y, facing_mdeg=facing,
            vx_mmps=dx * 1000 // max(1, available_ms), vy_mmps=dy * 1000 // max(1, available_ms),
            stance="moving_defense",
        )
        # Do not score a movement defense as viable merely because the feet can
        # reach the destination.  It also has to move this body out of the
        # incoming physical attack path after lawful tracking.  The old selector
        # repeatedly chose 10-20 cm sidesteps against spear capsules that still
        # intersected the torso, crowding out a viable parry/block and creating
        # a sequence of nominal "repositions" that were guaranteed contacts.
        if profile is not None and profile.external_contact:
            trial = dict(current)
            trial[defender_ref] = candidate.to_record()
            probe = contact_after_defense(
                attacker_ref=attacker_ref, defender_ref=defender_ref, positions=trial,
                profile=profile, obstacles=obstacles, trajectory=attack_trajectory,
                tracking_milli=max(0, min(1000, int(tracking_milli))),
                original_defender_position=defender_position, body_refs=body_refs,
            )
            if bool(probe.get("contact")) and str(probe.get("contacted_ref") or "") == defender_ref:
                continue
        return candidate
    return None


def select_physical_defense(
    *,
    attacker: Participant,
    defender: Participant,
    attacker_position: PositionState,
    defender_position: PositionState,
    attacker_capability: CapabilityProfile,
    defender_capability: CapabilityProfile,
    profile: ActionProfile | None,
    line_of_sight: bool,
    participant_positions: Mapping[str, Mapping[str, Any]],
    body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]] = (),
    at_ms: int = 0,
    attack_trajectory: Mapping[str, Any] | None = None,
) -> PhysicalDefenseDecision:
    detected, detection_margin, reaction_delay, attack_angle = detect_attack(
        attacker=attacker, defender=defender,
        attacker_position=attacker_position, defender_position=defender_position,
        attacker_capability=attacker_capability, defender_capability=defender_capability,
        profile=profile, line_of_sight=line_of_sight,
    )
    warning = _attack_warning_ms(profile, distance_mm=planar_distance_mm(attacker_position.to_record(), defender_position.to_record()))
    movement_warning = _defense_displacement_window_ms(profile)
    active_load=max(0,min(1000,int(defender.active_defense_load_milli)))
    load_availability=max(60,1000-active_load)
    # Shared commitment is not just a trace number: a saturated defender reacts
    # later to the next threat. This is what prevents a fresh full dodge/parry
    # against every attack in a rapid multi-attacker or projectile sequence.
    # Defensive saturation is real occupied attention/body commitment. Express
    # it as added latency, not a weak multiplier that lets a 70%-loaded elite
    # still answer a fresh short-startup attack almost instantly.
    reaction_delay=min(2000,reaction_delay + active_load//2)
    # Beginning a response one instant before contact is not equivalent to an
    # early prepared defense. Scale quality by the real response window as well
    # as accumulated active-defense load. Full timing quality requires roughly
    # half of the incoming warning window after reaction latency.
    available_after_reaction=max(0,warning-reaction_delay)
    timing_availability=max(80,min(1000,available_after_reaction*1000//max(1,warning//2)))
    reaction_availability=max(60,load_availability*timing_availability//1000)
    statuses = set(defender.status_families)
    posture_impaired = bool(statuses & _POSTURE_IMPAIRMENTS)
    recovery_lock = max(0, int(defender.recovery_remaining_ms))
    movement_locked = bool(statuses & _MOVEMENT_LOCKS) or recovery_lock > warning
    options: list[tuple[int, str, PositionState | None]] = []

    # A guard that is already physically committed does not disappear between
    # near-simultaneous attacks. It may cover a follow-up arriving before a new
    # reaction could start, provided the incoming angle remains inside the live
    # guard sector and the prior defensive recovery is still in progress. This
    # preserves one shared defense budget without turning a 20-40 ms follow-up
    # into an undefended fresh body merely because active-load latency exceeds
    # the attack startup.
    prepared_guard_response={
        "committed_guard":"block","extended_parry":"parry",
        "displaced_guard":"deflect","guarded_brace":"brace",
    }.get(str(defender.weapon_position))
    prepared_guard_live=bool(
        detected and prepared_guard_response
        and max(0,int(defender.recovery_remaining_ms))>0
        and angular_difference_mdeg(defender_position.facing_mdeg,attack_angle)<=55_000
        and not (statuses & _ACTION_LOCKS)
    )
    reused_prepared_guard=False
    if reaction_delay >= warning and prepared_guard_live:
        reused_prepared_guard=True
        # The body/weapon is already in motion. Only a bounded orientation/control
        # correction is required, so do not charge a second full simple reaction.
        reaction_delay=min(reaction_delay,max(40,warning//3))
        available_after_reaction=max(0,warning-reaction_delay)
        timing_availability=max(120,min(1000,available_after_reaction*1000//max(1,warning//2)))
        reaction_availability=max(120,load_availability*timing_availability//1000)

    # Detection is not itself an active defense. The pressure-adjusted reaction
    # latency must leave real physical time before contact for a dodge, parry,
    # block, brace, or counter-intercept to begin. Otherwise the old selector
    # could grant a successful stationary defense at contact even though the
    # reaction mathematically completed only at or after that same contact.
    if not detected or reaction_delay >= warning:
        return PhysicalDefenseDecision(
            detected=detected, detection_margin=detection_margin, response="none",
            before_position=defender_position, after_position=defender_position,
            displacement_mm=0, reaction_delay_ms=reaction_delay, recovery_ms=0,
            defense_factor_milli=max(80, 350 * reaction_availability // 1000),
            reaction_availability_milli=reaction_availability,
            balance_after_milli=defender.balance_milli,
            limb_commitment_after_milli=defender.limb_commitment_milli,
            weapon_position_after=defender.weapon_position,
            attack_angle_mdeg=attack_angle, tracking_milli=1000,
            force_transmission_milli=1000, control_disruption=0, displacement_resistance_milli=0,
            interrupts_attacker=False, contact_surface="none",
            reason=("attack_not_detected_in_time" if not detected else "reaction_window_elapsed_before_response"),
        )

    tracking_estimate = max(100, min(1000, (
        int(attacker_capability.reaction) * 3
        + int(attacker_capability.control) * 2
        + max(0, warning - reaction_delay) // 2
    )))

    if not movement_locked:
        for response in ("evade", "reposition"):
            moved = _movement_response(
                response=response, attacker_ref=attacker.participant_ref, defender_ref=defender.participant_ref,
                participant_positions=participant_positions, defender_position=defender_position,
                incoming_bearing_mdeg=attack_angle, defender_capability=defender_capability,
                reaction_delay_ms=reaction_delay, warning_ms=movement_warning, body_refs=body_refs, obstacles=obstacles,
                profile=profile, attack_trajectory=attack_trajectory, tracking_milli=tracking_estimate,
            )
            if moved is not None:
                mobility_axis = int(defender_capability.mobility) * 4 + int(defender_capability.reaction) * 3 + int(defender_capability.perception) * 2
                mobility_axis = mobility_axis * max(250, int(defender.balance_milli)) // 1000
                if response == "reposition":
                    mobility_axis += int(defender_capability.control)
                if posture_impaired:
                    mobility_axis = mobility_axis * 55 // 100
                options.append(((mobility_axis + _preference_bonus(defender, response)) * reaction_availability // 1000, response, moved))

    prefs = set(defender.physical_defense_preferences)
    # Equipment availability is projected as allowed preferences. If no explicit
    # preference projection exists, ordinary weapon users may still parry when
    # their current action profile represents a held direct-contact method.
    current_profile = defender.action_profile
    has_weapon_like = bool(current_profile is not None and current_profile.external_contact and current_profile.method_ref and current_profile.delivery not in {"projectile", "ranged"})
    can_parry = "parry" in prefs or has_weapon_like
    can_block = "block" in prefs or any(d.defense_kind in {"weapon_guard", "physical_guard", "prepared_directional_protection"} for d in defender.reactive_defenses)
    if can_parry:
        score = int(defender_capability.defense) * 4 + int(defender_capability.control) * 3 + int(defender_capability.reaction) * 2
        if defender.weapon_position not in {"guard", "ready", "committed_guard"}:
            score -= 180 + int(defender.limb_commitment_milli) // 4
        score = score * max(300, int(defender.balance_milli)) // 1000
        options.append(((score + _preference_bonus(defender, "parry")) * reaction_availability // 1000, "parry", None))
        options.append(((score - 20 + _preference_bonus(defender, "deflect")) * reaction_availability // 1000, "deflect", None))
    if can_block:
        score = int(defender_capability.defense) * 4 + int(defender_capability.control) * 3 + int(defender_capability.reaction) * 2
        if defender.weapon_position in {"extended_attack", "extended_parry", "displaced_guard"}:
            score -= int(defender.limb_commitment_milli) // 3
        score = score * max(350, int(defender.balance_milli)) // 1000
        options.append(((score + _preference_bonus(defender, "block")) * reaction_availability // 1000, "block", None))
    brace_score = int(defender_capability.defense) * 3 + int(defender_capability.control) * 2 + int(defender.readiness) * 2
    options.append(((brace_score + _preference_bonus(defender, "brace")) * max(400,reaction_availability) // 1000, "brace", None))
    if not movement_locked and reaction_availability>=350 and defender.intent.action in {"attack", "capture"}:
        # Counter-intercept is only a response option when the defender was
        # already prepared to act and the attacker is physically close enough.
        d = planar_distance_mm(attacker_position.to_record(), defender_position.to_record())
        own_reach = physical_reach_mm(defender.action_profile)
        if own_reach > 0 and d <= own_reach:
            score = int(defender_capability.offense) * 3 + int(defender_capability.reaction) * 3 + int(defender_capability.control) * 2
            posture_bias={"rare":-220,"selective":0,"active":220}.get(defender.counterattack_posture,0)
            options.append(((score + posture_bias + _preference_bonus(defender, "counter_intercept")) * reaction_availability // 1000, "counter_intercept", None))

    if not options:
        response, moved = "brace", None
    else:
        _score, response, moved = max(options, key=lambda row: (row[0], -DEFENSE_RESPONSES.index(row[1])))

    after = moved if moved is not None else defender_position
    if moved is None and response in {"parry", "deflect", "block", "brace", "counter_intercept"}:
        # A stationary defense still has to turn the body/guard toward the
        # threat. Reaction determines how much rotation can occur inside the
        # available warning window; rear attacks therefore cannot be answered
        # as though the defender were already square to them.
        available_ms = max(0, warning - reaction_delay)
        turn_rate_mdeg_per_s = 150_000 + max(0, int(defender_capability.reaction)) * 700
        max_turn = min(180_000, turn_rate_mdeg_per_s * available_ms // 1000)
        new_facing = _turn_toward_mdeg(defender_position.facing_mdeg, attack_angle, max_turn_mdeg=max_turn)
        after = _position_with(
            defender_position, x_mm=defender_position.x_mm, y_mm=defender_position.y_mm,
            facing_mdeg=new_facing, vx_mmps=defender_position.vx_mmps, vy_mmps=defender_position.vy_mmps,
            stance="guarding" if response != "brace" else "braced",
        )
    displacement = planar_distance_mm(defender_position.to_record(), after.to_record())
    load = active_load
    base_factor = {
        "evade": 900, "reposition": 820, "parry": 850, "deflect": 800,
        "block": 760, "brace": 520, "counter_intercept": 700,
    }.get(response, 350)
    defense_factor = max(60, base_factor * reaction_availability // 1000)
    if posture_impaired:
        defense_factor = max(60, defense_factor * 70 // 100)
    balance_after = {
        "evade": 690, "reposition": 760, "parry": 820, "deflect": 800,
        "block": 900, "brace": 970, "counter_intercept": 720,
    }.get(response, defender.balance_milli)
    limb_after = {
        "evade": 180, "reposition": 140, "parry": 620, "deflect": 540,
        "block": 500, "brace": 320, "counter_intercept": 760,
    }.get(response, 0)
    recovery = max(90, reaction_delay + {"evade":180,"reposition":140,"parry":220,"deflect":180,"block":260,"brace":220,"counter_intercept":300}.get(response,160))
    weapon_after = {
        "parry":"extended_parry", "deflect":"displaced_guard", "block":"committed_guard",
        "counter_intercept":"extended_attack", "brace":"guarded_brace",
    }.get(response, defender.weapon_position)
    tracking = tracking_estimate
    base_force_transmission = {
        "evade": 900, "reposition": 900, "parry": 280, "deflect": 420,
        "block": 520, "brace": 780, "counter_intercept": 600,
    }.get(response, 1000)
    # As reaction availability collapses, a late parry/block may still contact
    # the attack but cannot magically retain full mitigation quality.
    force_transmission = 1000 - (1000-base_force_transmission) * reaction_availability // 1000
    control_disruption = {
        "parry": 65, "deflect": 85, "block": 35, "brace": 5, "counter_intercept": 90,
        "evade": 20, "reposition": 30,
    }.get(response, 0)
    displacement_resistance = {
        "brace": 900, "block": 720, "parry": 350, "deflect": 250,
        "counter_intercept": 300, "evade": 100, "reposition": 150,
    }.get(response, 0)
    interrupts_attacker = bool(
        response == "counter_intercept"
        and reaction_availability >= 450
        and reaction_delay + max(60, int(defender.recovery_remaining_ms) // 4) < warning
        and defender_capability.reaction + defender_capability.control >= attacker_capability.reaction + attacker_capability.control // 2
    )
    contact_surface = {
        "parry": "weapon", "deflect": "weapon", "block": "weapon_or_body_guard",
        "brace": "body_or_guard", "counter_intercept": "intercepting_weapon_or_limb",
        "evade": "none", "reposition": "none",
    }.get(response, "none")
    return PhysicalDefenseDecision(
        detected=True, detection_margin=detection_margin, response=response,
        before_position=defender_position, after_position=after,
        displacement_mm=displacement, reaction_delay_ms=reaction_delay, recovery_ms=recovery,
        defense_factor_milli=defense_factor, reaction_availability_milli=reaction_availability, balance_after_milli=balance_after,
        limb_commitment_after_milli=limb_after, weapon_position_after=weapon_after,
        attack_angle_mdeg=attack_angle, tracking_milli=tracking,
        force_transmission_milli=force_transmission, control_disruption=control_disruption,
        displacement_resistance_milli=displacement_resistance, interrupts_attacker=interrupts_attacker,
        contact_surface=contact_surface,
        reason=("existing_guard_covers_followup" if reused_prepared_guard else "lawful_physical_response_selected"),
    )



def _candidate_lateral_clearance_mm(
    *, x_mm: int, y_mm: int, positions: Mapping[str, Mapping[str, Any]],
    attacker_ref: str, defender_ref: str, body_refs: Sequence[str],
) -> int:
    probe = {"x_mm": int(x_mm), "y_mm": int(y_mm)}
    distances = []
    for ref in body_refs:
        if ref in {attacker_ref, defender_ref}:
            continue
        row = positions.get(ref)
        if isinstance(row, Mapping):
            distances.append(planar_distance_mm(probe, row))
    return min(distances, default=1_000_000_000)


def _lateral_entry_waypoint(
    *, intent: str, attacker_ref: str, defender_ref: str,
    positions: Mapping[str, Mapping[str, Any]], attacker_position: PositionState,
    defender_position: PositionState, lateral_budget_mm: int, lateral_time_ms: int,
    body_refs: Sequence[str], obstacles: Sequence[Mapping[str, Any]],
) -> tuple[PositionState, str, int] | None:
    if lateral_budget_mm <= 0:
        return None
    dx = defender_position.x_mm - attacker_position.x_mm
    dy = defender_position.y_mm - attacker_position.y_mm
    bearing = math.atan2(dy, dx)
    requested = (("left", 1), ("right", -1))
    if intent == "lateral_left":
        requested = (("left", 1),)
    elif intent == "lateral_right":
        requested = (("right", -1),)
    current = dict(positions)
    current[attacker_ref] = attacker_position.to_record()
    candidates: list[tuple[int, int, str, PositionState]] = []
    for label, sign in requested:
        angle = bearing + sign * math.pi / 2.0
        for fraction_milli in (1000, 750, 500, 250):
            distance = lateral_budget_mm * fraction_milli // 1000
            if distance <= 0:
                continue
            x_mm = attacker_position.x_mm + int(round(math.cos(angle) * distance))
            y_mm = attacker_position.y_mm + int(round(math.sin(angle) * distance))
            if not path_clear(
                current, actor_ref=attacker_ref, end_x_mm=x_mm, end_y_mm=y_mm,
                body_refs=body_refs, obstacles=obstacles,
            ):
                continue
            elapsed = max(1, int(lateral_time_ms) * distance // max(1, lateral_budget_mm))
            waypoint = _position_with(
                attacker_position, x_mm=x_mm, y_mm=y_mm,
                facing_mdeg=int(round(math.degrees(angle) * 1000)) % 360_000,
                vx_mmps=(x_mm-attacker_position.x_mm)*1000//elapsed,
                vy_mmps=(y_mm-attacker_position.y_mm)*1000//elapsed,
                stance=f"lateral_approach_{label}",
            )
            clearance = _candidate_lateral_clearance_mm(
                x_mm=x_mm, y_mm=y_mm, positions=positions, attacker_ref=attacker_ref,
                defender_ref=defender_ref, body_refs=body_refs,
            )
            candidates.append((clearance, distance, label, waypoint))
            break
    if not candidates:
        return None
    _clearance, distance, label, waypoint = max(
        candidates, key=lambda row: (row[0], row[1], 1 if row[2] == "left" else 0)
    )
    return waypoint, label, distance


def close_attacker_into_reach(
    *,
    attacker_ref: str,
    defender_ref: str,
    positions: Mapping[str, Mapping[str, Any]],
    attacker_position: PositionState,
    defender_position: PositionState,
    attacker_capability: CapabilityProfile,
    profile: ActionProfile | None,
    body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]] = (),
) -> tuple[PositionState, dict[str, Any]]:
    """Physically close a melee attacker only as far as the authored action allows.

    The command layer records how much approach travel was available when the
    action began. If the target moved farther during startup, this function may
    fail to reach rather than teleporting the strike.
    """
    if profile is None or not profile.external_contact or profile.delivery in {"projectile", "ranged", "thrown"}:
        return attacker_position, {"moved": False, "reason": "no_melee_approach"}
    reach = physical_reach_mm(profile)
    if reach <= 0:
        return attacker_position, {"moved": False, "reason": "no_physical_reach"}
    start = attacker_position.to_record(); target = defender_position.to_record()
    d = planar_distance_mm(start, target)
    required = max(0, d - reach)
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    intent = str(params.get("tactical_movement_intent") or "")
    # Explicit lateral footwork remains a real movement request even when the
    # attacker is already inside weapon reach.  Returning early here used to
    # silently erase the player's authored side-step and fall back to a frontal
    # strike. Close/chase may remain stationary when no separation exists yet;
    # their reserved pursuit budget is consumed if the target later withdraws.
    if required <= 0 and intent not in {"lateral", "lateral_left", "lateral_right"}:
        return attacker_position, {"moved": False, "reason": "already_in_reach", "required_mm": 0}
    if intent in {"lateral", "lateral_left", "lateral_right"}:
        total_budget = max(0, int(params.get("approach_distance_mm", 0) or 0))
        total_time = max(0, int(params.get("approach_time_ms", 0) or 0))
        if total_budget <= 0 or total_time <= 0:
            raise ValueError("combat tactical movement unavailable")
        lateral_budget = min(1500, total_budget * 400 // 1000)
        lateral_time = max(1, total_time * lateral_budget // max(1, total_budget))
        selected = _lateral_entry_waypoint(
            intent=intent, attacker_ref=attacker_ref, defender_ref=defender_ref,
            positions=positions, attacker_position=attacker_position,
            defender_position=defender_position, lateral_budget_mm=lateral_budget,
            lateral_time_ms=lateral_time, body_refs=body_refs, obstacles=obstacles,
        )
        if selected is None:
            raise ValueError("combat tactical movement path blocked")
        waypoint, side, lateral_distance = selected
        remaining_budget = max(0, total_budget - lateral_distance)
        remaining_time = max(0, total_time - lateral_time * lateral_distance // max(1, lateral_budget))
        staged_positions = dict(positions)
        staged_positions[attacker_ref] = waypoint.to_record()
        follow_params = dict(params)
        follow_params["tactical_movement_intent"] = None
        follow_params["movement_intent"] = None
        follow_params["approach_distance_mm"] = remaining_budget
        follow_params["melee_movement_budget_mm"] = remaining_budget
        follow_params["approach_time_ms"] = remaining_time
        follow_profile = ActionProfile(**{**profile.__dict__, "effect_parameters": follow_params})
        direct_position, direct = close_attacker_into_reach(
            attacker_ref=attacker_ref, defender_ref=defender_ref, positions=staged_positions,
            attacker_position=waypoint, defender_position=defender_position,
            attacker_capability=attacker_capability, profile=follow_profile,
            body_refs=body_refs, obstacles=obstacles,
        )
        direct_distance = max(0, int(direct.get("distance_mm", 0))) if isinstance(direct, Mapping) else 0
        final = direct_position if bool(direct.get("moved")) else waypoint
        final_distance = planar_distance_mm(final.to_record(), defender_position.to_record())
        if final_distance <= reach:
            facing = facing_to_target_mdeg(final.to_record(), defender_position.to_record())
            final = _position_with(
                final, x_mm=final.x_mm, y_mm=final.y_mm, facing_mdeg=facing,
                vx_mmps=final.vx_mmps, vy_mmps=final.vy_mmps, stance=f"lateral_entry_{side}",
            )
            reason = "closed_into_melee_reach"
        else:
            reason = "partial_committed_approach"
        return final, {
            "moved": True, "reason": reason, "movement_intent": intent,
            "lateral_side": side, "lateral_distance_mm": lateral_distance,
            "direct_distance_mm": direct_distance, "distance_mm": lateral_distance + direct_distance,
            "approach_time_ms": total_time, "remaining_mm": max(0, final_distance - reach),
            "waypoint_x_mm": waypoint.x_mm, "waypoint_y_mm": waypoint.y_mm,
            "direct_reason": str(direct.get("reason") or "") if isinstance(direct, Mapping) else "",
        }
    # The action owns one bounded movement envelope across approach plus the
    # physical startup window.  Using only the declaration-time approach
    # distance freezes a fighter who was initially in range if the target moves
    # afterward.  The total envelope remains finite and cannot teleport a strike.
    allowed = params.get("melee_movement_budget_mm", params.get("approach_distance_mm", required))
    if isinstance(allowed, bool) or not isinstance(allowed, int):
        allowed = required
    allowed = max(0, allowed)
    if allowed <= 0:
        return attacker_position, {"moved": False, "reason": "target_moved_beyond_committed_approach", "required_mm": required, "allowed_mm": allowed}
    dx = defender_position.x_mm - attacker_position.x_mm
    dy = defender_position.y_mm - attacker_position.y_mm
    plen = max(1, math.isqrt(dx*dx + dy*dy))
    move = min(required, allowed)
    ex = attacker_position.x_mm + dx * move // plen
    ey = attacker_position.y_mm + dy * move // plen
    current = dict(positions); current[attacker_ref] = attacker_position.to_record(); current[defender_ref] = defender_position.to_record()
    if not path_clear(current, actor_ref=attacker_ref, end_x_mm=ex, end_y_mm=ey, body_refs=body_refs, obstacles=obstacles):
        return attacker_position, {"moved": False, "reason": "approach_lane_blocked", "required_mm": required}
    facing = facing_to_target_mdeg(attacker_position.to_record(), defender_position.to_record())
    approach_ms = params.get("approach_time_ms", 0)
    if isinstance(approach_ms, bool) or not isinstance(approach_ms, int) or approach_ms <= 0:
        approach_ms = max(1, move * 1000 // max(1, movement_speed_mmps(attacker_capability)))
    moved = _position_with(
        attacker_position, x_mm=ex, y_mm=ey, facing_mdeg=facing,
        vx_mmps=(ex-attacker_position.x_mm)*1000//max(1,approach_ms),
        vy_mmps=(ey-attacker_position.y_mm)*1000//max(1,approach_ms),
        stance="approaching",
    )
    reason = "closed_into_melee_reach" if required <= allowed else "partial_committed_approach"
    result = {"moved": True, "reason": reason, "distance_mm": move, "approach_time_ms": approach_ms}
    if required > allowed:
        result["required_mm"] = required
        result["allowed_mm"] = allowed
        result["remaining_mm"] = required - allowed
    return moved, result

def contact_after_defense(
    *,
    attacker_ref: str,
    defender_ref: str,
    positions: Mapping[str, Mapping[str, Any]],
    profile: ActionProfile | None,
    obstacles: Sequence[Mapping[str, Any]],
    trajectory: Mapping[str, Any] | None,
    tracking_milli: int,
    original_defender_position: PositionState,
    body_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Recalculate physical contact after the defender's response."""
    if profile is None or not profile.external_contact:
        return {"contact": True, "reason": "nonphysical_or_unspecified"}
    actor = positions.get(attacker_ref); target = positions.get(defender_ref)
    if not isinstance(actor, Mapping) or not isinstance(target, Mapping):
        return {"contact": False, "reason": "missing_position"}
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    delivery = str(profile.delivery or "direct")
    if delivery in {"projectile", "ranged", "thrown"}:
        geometry = params.get("geometry") if isinstance(params.get("geometry"), Mapping) else {"shape":"direct","width_m":0.15}
        trace = trace_attack_geometry(
            positions, actor_ref=attacker_ref, aim_ref=defender_ref,
            body_refs=tuple(body_refs) or (defender_ref,), geometry=geometry,
            obstacles=obstacles, target_limit=1, maximum_range_m=params.get("maximum_range_m"), channel="projectile", trajectory=trajectory,
        )
        contacts = trace.get("contacts") if isinstance(trace, Mapping) else None
        contacted_ref = contacts[0].get("participant_ref") if isinstance(contacts, list) and contacts and isinstance(contacts[0], Mapping) else None
        return {"contact": bool(contacts), "contacted_ref": contacted_ref, "intended_ref": defender_ref, "reason": "projectile_trajectory_intersection", "trace": trace}

    reach = physical_reach_mm(profile)
    distance = planar_distance_mm(actor, target)
    # Do not reject melee solely because the defender's *center* moved beyond
    # nominal reach.  The shared geometry resolver already tests the finite
    # weapon segment against the defender's occupied body radius, including the
    # end-cap.  A duplicate center-distance cutoff made limbs/body volume vanish
    # the instant the center crossed the reach number.
    committed_trajectory = params.get("committed_melee_trajectory") if isinstance(params.get("committed_melee_trajectory"), Mapping) else None
    committed_displacement = 0
    if isinstance(committed_trajectory, Mapping):
        committed_aim = dict(target)
        try:
            committed_aim["x_mm"] = int(committed_trajectory["aim_x_mm"])
            committed_aim["y_mm"] = int(committed_trajectory["aim_y_mm"])
            committed_aim["elevation_mm"] = int(committed_trajectory.get("aim_elevation_mm", target.get("elevation_mm", 0)))
            committed_displacement = planar_distance_mm(committed_aim, target)
        except (KeyError, TypeError, ValueError):
            committed_trajectory = None
            committed_displacement = 0

    # Tracking is continuous. A committed strike may bend its aim toward the
    # defender during remaining startup, but there is no threshold where 649 is
    # a fixed line and 650 suddenly becomes perfect homing. Preserve the original
    # launch and blend only the committed aim point by tracking quality.
    tracking=max(0,min(1000,int(tracking_milli)))
    tracked_trajectory=copy.deepcopy(dict(committed_trajectory)) if isinstance(committed_trajectory,Mapping) else None
    tracking_shift_mm=0
    if isinstance(tracked_trajectory,dict):
        try:
            old_x=int(tracked_trajectory['aim_x_mm']); old_y=int(tracked_trajectory['aim_y_mm'])
            old_z=int(tracked_trajectory.get('aim_elevation_mm',target.get('elevation_mm',0)))
            new_x=int(target.get('x_mm',old_x)); new_y=int(target.get('y_mm',old_y)); new_z=int(target.get('elevation_mm',old_z))
            aim_x=old_x+(new_x-old_x)*tracking//1000
            aim_y=old_y+(new_y-old_y)*tracking//1000
            aim_z=old_z+(new_z-old_z)*tracking//1000
            tracked_trajectory['aim_x_mm']=aim_x; tracked_trajectory['aim_y_mm']=aim_y; tracked_trajectory['aim_elevation_mm']=aim_z
            tracking_shift_mm=planar_distance_mm({'x_mm':old_x,'y_mm':old_y},{'x_mm':aim_x,'y_mm':aim_y})
        except (KeyError,TypeError,ValueError):
            tracked_trajectory=None
            tracking_shift_mm=0
    intended_ref = params.get("intended_target_ref")
    if not isinstance(intended_ref, str) or intended_ref not in positions:
        intended_ref = defender_ref
    geometry=params.get('geometry') if isinstance(params.get('geometry'),Mapping) else {"shape":"direct","width_m":0.08,"length_m":max(0.2,reach/1000.0)}
    geometry=dict(geometry)
    geometry.setdefault('length_m',max(0.2,reach/1000.0))
    blocker = trace_attack_geometry(
        positions, actor_ref=attacker_ref, aim_ref=intended_ref,
        body_refs=tuple(body_refs) or (defender_ref,),
        geometry=geometry,
        obstacles=obstacles, target_limit=1, maximum_range_m=max(0.2, reach/1000.0), channel="melee",
        trajectory=tracked_trajectory,
    )
    moved = planar_distance_mm(original_defender_position.to_record(), target)
    contacts = blocker.get("contacts") if isinstance(blocker, Mapping) else None
    contacted_ref = contacts[0].get("participant_ref") if isinstance(contacts, list) and contacts and isinstance(contacts[0], Mapping) else None
    return {
        "contact": bool(contacts), "contacted_ref": contacted_ref, "intended_ref": intended_ref,
        "reason": "tracked_melee_continuous_intersection" if tracked_trajectory is not None and tracking_shift_mm>0 else "committed_melee_lane_intersection",
        "distance_mm": distance, "reach_mm": reach, "trace": blocker,
        "tracking_milli": tracking, "tracking_aim_shift_mm": tracking_shift_mm,
        "defense_displacement_mm": moved,
        "committed_target_displacement_mm": committed_displacement,
    }


__all__ = [
    "DEFENSE_RESPONSES", "PhysicalDefenseDecision", "close_attacker_into_reach", "contact_after_defense",
    "detect_attack", "movement_speed_mmps", "physical_reach_mm",
    "select_physical_defense", "status_action_allowed",
]
