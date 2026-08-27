"""Sparse deterministic strategic choices for autonomous Jianghu factions.

This module only chooses *which lawful pressure to examine*.  It never mutates
campaign state and never manufactures resources.  Callers still prove travel,
people, treasury, project capacity, target availability and exact combat before
committing an outcome.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Mapping, Sequence

from .autonomy_rules import autonomy_mechanics, hostile_affordance, outlaw_intent
from .calendar_modifiers import formal_challenge_pressure_milli
from .faction_politics import conflict_stage, cross_camp_pressure, faction_camp, war_operation_active_front_limit


def stable_permille(*parts: object) -> int:
    key = "|".join(str(x) for x in parts)
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big") % 1000


def choose_hostile_action(
    edges: Sequence[Mapping[str, Any]], *, faction_ref: str, year: int, month: int,
    risk_tolerance: int, active_strategic_operations: int = 0,
    faction_type: str = "", outlaw_subtype: str = "",
    eligible_raid_target_refs: set[str] | None = None,
    coalition_target_refs: set[str] | None = None,
) -> dict[str, Any] | None:
    """Choose one bounded hostile action from current grievance and faction nature.

    Political camp never creates hostility. It only changes willingness to act
    on a rivalry/grievance already present on the directed relationship edge.
    Conflict stage controls seriousness; faction type controls the lawful action
    affordance, so road bands do not behave like honor-bound martial schools.
    """
    active = max(0, int(active_strategic_operations))
    if active >= war_operation_active_front_limit():
        return None
    risk = max(0, min(100, int(risk_tolerance)))
    source_camp = faction_camp(faction_ref)
    candidates: list[tuple[int, int, str, str, Mapping[str, Any]]] = []
    for edge in edges:
        if not isinstance(edge, Mapping) or str(edge.get("from_faction") or "") != faction_ref:
            continue
        target = str(edge.get("to_faction") or "")
        if not target or target == faction_ref:
            continue
        hostility = max(0, min(100, int(edge.get("hostility", 0))))
        stage = conflict_stage(edge)
        if stage == "peace":
            continue
        kind = hostile_affordance(str(faction_type), stage)
        if not kind:
            continue
        # Ordinary outlaw raids are local route pressure. Apply that locality
        # before deterministic candidate ranking so the faction does not spend
        # its monthly hostile decision selecting a target the execution boundary
        # must reject. War strikes remain strategically unrestricted.
        if (
            kind == "faction_raid" and eligible_raid_target_refs is not None
            and target not in eligible_raid_target_refs
        ):
            continue
        # Two concurrent fronts are already a strong brake.  Only an existing
        # war may justify a third finite operation.
        if active >= 2 and kind != "faction_war_strike":
            continue
        political_pressure = cross_camp_pressure(source_camp, faction_camp(target))
        coalition_pressure = bool(coalition_target_refs and target in coalition_target_refs and kind == "faction_war_strike")
        if kind == "faction_war_strike":
            threshold = min(700, max(70, 90 + (hostility - 65) * 10 + risk * 2 + political_pressure * 2 + (120 if coalition_pressure else 0)))
        elif kind == "faction_raid":
            threshold = min(360, max(35, (hostility - 45) * 14 + risk * 2 + political_pressure))
        else:
            threshold = min(240, max(20, (hostility - 25) * 8 + risk + political_pressure // 2))
            threshold=min(400,threshold * formal_challenge_pressure_milli(datetime(year,month,1)) // 1000)
        roll = stable_permille("hostile-action", faction_ref, target, year, month, kind)
        if roll >= threshold:
            continue
        candidates.append((-hostility, roll, target, kind, edge))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    _neg_hostility, roll, target, kind, edge = candidates[0]
    return {
        "action": kind,
        "target_faction_ref": target,
        "hostility": max(0, int(edge.get("hostility", 0))),
        "conflict_stage": conflict_stage(edge),
        "source_camp": source_camp,
        "target_camp": faction_camp(target),
        "operation_intent": (
            outlaw_intent(str(outlaw_subtype), stage=conflict_stage(edge))
            if str(faction_type) == "outlaw_faction" else
            ("honor_challenge" if kind == "formal_challenge" else "punitive_expedition")
        ),
        "roll": int(roll),
        "coalition_pressure": bool(coalition_target_refs and target in coalition_target_refs and kind == "faction_war_strike"),
    }


def choose_friendly_aid_target(
    edges: Sequence[Mapping[str, Any]], *, faction_ref: str, year: int, month: int,
    cash_reserve_months: int,
) -> str | None:
    """Return one friendly target worth examining for conserved silver aid."""
    if int(cash_reserve_months) < max(1, int(autonomy_mechanics().get("growth_cash_reserve_months", 8))):
        return None
    rows: list[tuple[int, int, str]] = []
    for edge in edges:
        if not isinstance(edge, Mapping) or str(edge.get("from_faction") or "") != faction_ref:
            continue
        target = str(edge.get("to_faction") or "")
        if not target or target == faction_ref:
            continue
        trust = int(edge.get("trust", 0)); respect = int(edge.get("respect", 0)); hostility = int(edge.get("hostility", 0))
        obligation = max(0, int(edge.get("obligation", 0)))
        if hostility > 12 or (trust < 18 and obligation <= 0):
            continue
        # A current debt is a real Jianghu reason to help even when ordinary
        # friendship alone would not clear the aid threshold.  It remains
        # bounded by hostility, reserves and the destination's actual need.
        strength = trust * 3 + max(0, respect) + obligation * 5
        roll = stable_permille("friendly-aid", faction_ref, target, year, month)
        threshold = min(160, max(8, strength // 3))
        if roll < threshold:
            rows.append((-strength, roll, target))
    rows.sort()
    return rows[0][2] if rows else None



def tournament_travel_interested(
    *, faction_ref: str, tournament_ref: str, tournament_kind: str,
    training_priority: int, risk_tolerance: int, entry_fee_cash: int = 0,
    current_prize_cash: int = 0, prestige_weight: int = 50,
    faction_type: str = "", living_members: int = 0,
    faction_interest_floor_permille: int = 0,
    major_sect_population_threshold: int = 100,
    travel_days_hint: int = 0,
) -> bool:
    """Return whether one NPC faction is willing to consider tournament travel.

    There is no field-size slot gate.  Interest only determines whether a faction
    evaluates entrants; the caller must still prove real routes, time, provisions,
    tolls, health, faction-funded entry fees and operating reserves.  The growing
    entry-funded purse and event prestige make large tournaments more attractive
    without giving travelers free appearance money.
    """
    if tournament_kind not in {"regional_martial_tournament", "great_jianghu_tournament", "midyear_junior_tournament"}:
        return False
    training = max(0, min(100, int(training_priority)))
    risk = max(0, min(100, int(risk_tolerance)))
    prestige = max(0, min(100, int(prestige_weight)))
    fee = max(0, int(entry_fee_cash))
    purse = max(0, int(current_prize_cash))
    cash_bonus = min(160, purse // 5000)
    fee_penalty = min(180, fee // 250)
    if tournament_kind == "great_jianghu_tournament":
        # This is the four-year convergence of the martial world.  Every faction
        # evaluates attendance; stochastic "interest" is not allowed to make a
        # major institution casually ignore it.  Actual participation is still
        # constrained by route reachability, timing, health, availability, food,
        # tolls, entry sponsorship and operating reserves in the caller.
        return True
    else:
        # Regional championships are important but are not a second world
        # congress.  Distant attendance remains possible with no geographic or
        # field-size cap, yet the marginal willingness to spend months on the
        # road falls with real route burden.  Nearby factions therefore make up
        # most of the field while exceptional distant institutions can still
        # choose to travel.
        travel_days = max(0, int(travel_days_hint))
        distance_penalty = min(300, travel_days * 5)
        threshold = min(820, max(20, 200 + training + risk // 2 + prestige * 2 + cash_bonus - fee_penalty - distance_penalty))
        salt = "regional-tournament-travel"
    return stable_permille(salt, tournament_ref, faction_ref) < threshold


def tournament_entrant_interested(
    *, faction_ref: str, person_ref: str, tournament_ref: str, tournament_kind: str,
    entrant_order: int, training_priority: int, risk_tolerance: int,
    prestige_weight: int, faction_type: str = "", living_members: int = 0,
    major_sect_population_threshold: int = 100,
    major_sect_competitor_floor: int = 0,
    major_institution_population_threshold: int = 100,
    major_institution_competitor_floor: int = 0,
    ordinary_competitor_floor: int = 0,
    candidate_combat_index: float = 0.0,
    best_combat_index: float = 0.0,
    additional_competitor_interest_permille: int = 0,
    additional_competitor_decay_permille: int = 0,
    additional_competitor_relative_strength_permille: int = 0,
) -> bool:
    """Soft marginal nomination gate for one faction-sponsored fighter.

    This is deliberately not a per-faction entrant cap.  Strong, wealthy factions
    may sponsor several people, but the marginal institutional value falls as more
    of the same faction's members are already committed.  After a faction's
    core representatives, extra entrants must also remain close to that
    faction's best available combat strength.  This is a qualification rule,
    not a field-size or per-faction cap: sufficiently deep elite rosters can
    still sponsor many fighters.  Cash, provisions and travel remain hard
    conserved constraints in the caller.
    """
    if tournament_kind not in {"regional_martial_tournament", "great_jianghu_tournament", "midyear_junior_tournament"}:
        return False
    order = max(0, int(entrant_order))
    training = max(0, min(100, int(training_priority)))
    risk = max(0, min(100, int(risk_tolerance)))
    prestige = max(0, min(100, int(prestige_weight)))
    if tournament_kind == "great_jianghu_tournament":
        members = max(0, int(living_members))
        if str(faction_type) == "sect" and members >= max(1, int(major_sect_population_threshold)):
            floor = max(0, int(major_sect_competitor_floor))
        elif members >= max(1, int(major_institution_population_threshold)):
            floor = max(0, int(major_institution_competitor_floor))
        else:
            floor = max(0, int(ordinary_competitor_floor))
        if order < floor:
            return True
        relative_floor = max(0, min(1000, int(additional_competitor_relative_strength_permille)))
        best = max(0.0, float(best_combat_index))
        candidate = max(0.0, float(candidate_combat_index))
        if relative_floor > 0 and best > 0.0 and candidate * 1000.0 < best * relative_floor:
            return False
        extra_order = max(0, order - floor)
        base = max(0, min(1000, int(additional_competitor_interest_permille)))
        decay = max(0, int(additional_competitor_decay_permille))
        threshold = base + training + risk + prestige - extra_order * decay
        salt = "great-tournament-entrant"
    else:
        # Regional factions normally sponsor one or two serious representatives.
        # There is still no per-faction cap: an unusually deep roster can pass
        # the declining marginal gate, but the annual regional field no longer
        # approaches the four-year Great Tournament by default.
        threshold = 180 + training + risk // 2 + prestige * 2 - order * 180
        salt = "regional-tournament-entrant"
    threshold = max(20, min(980, threshold))
    return stable_permille(salt, tournament_ref, faction_ref, person_ref, order) < threshold


def tournament_spectator_interested(
    *, faction_ref: str, person_ref: str, tournament_ref: str, tournament_kind: str,
    spectator_order: int, is_leader: bool, faction_type: str, living_members: int,
    spectator_delegation_floor: int, major_spectator_population_threshold: int,
    major_spectator_delegation_floor: int, major_sect_spectator_delegation_floor: int = 0,
    leader_attendance_permille: int = 0, spectator_marginal_interest_permille: int = 0,
    spectator_marginal_decay_permille: int = 0,
) -> bool:
    """Return whether one real member is nominated for a spectator delegation.

    There is intentionally no headcount cap.  Major-event representation creates
    a minimum institutional delegation preference, leaders receive a separate
    attendance preference, and every further nomination faces a declining stable
    marginal gate.  The caller still enforces money, food, time, location,
    health, commitments and player-agency boundaries.
    """
    if tournament_kind not in {"regional_martial_tournament", "great_jianghu_tournament", "midyear_junior_tournament"}:
        return False
    order = max(0, int(spectator_order))
    members = max(0, int(living_members))
    floor = max(0, int(spectator_delegation_floor))
    if members >= max(1, int(major_spectator_population_threshold)):
        floor = max(floor, max(0, int(major_spectator_delegation_floor)))
    if (
        tournament_kind == "great_jianghu_tournament"
        and str(faction_type) == "sect"
        and members >= max(1, int(major_spectator_population_threshold))
    ):
        floor = max(floor, max(0, int(major_sect_spectator_delegation_floor)))
    if order < floor:
        return True
    leader_gate = max(0, min(1000, int(leader_attendance_permille)))
    if is_leader and stable_permille("tournament-leader-attendance", tournament_ref, faction_ref, person_ref) < leader_gate:
        return True
    base = max(0, min(1000, int(spectator_marginal_interest_permille)))
    decay = max(0, int(spectator_marginal_decay_permille))
    threshold = max(5, min(980, base - max(0, order - floor) * decay))
    return stable_permille("tournament-spectator", tournament_ref, faction_ref, person_ref, order) < threshold


def tournament_match_relation_event(
    *, faction_a: str, faction_b: str, tournament_ref: str,
    person_a: str, person_b: str, hostility: int,
) -> str:
    """Classify one real public match as sportsmanship or martial rivalry.

    Camp identity alone never creates a relation edge in the abstract world.
    Once two factions actually meet in a public tournament, however, existing
    hostility and political-cultural pressure may color the witnessed result.
    The rivalry outcome is intentionally small: it adds respect and only a few
    hostility points, so one match cannot manufacture a feud or war.
    """
    hostility=max(0,min(100,int(hostility)))
    pressure=cross_camp_pressure(faction_camp(faction_a),faction_camp(faction_b))
    if hostility < 20 and pressure <= 0:
        return "tournament_sportsmanship"
    threshold=min(520,max(0,hostility*4+pressure*6))
    roll=stable_permille(
        "tournament-match-relation",tournament_ref,
        min(faction_a,faction_b),max(faction_a,faction_b),
        min(person_a,person_b),max(person_a,person_b),
    )
    return "tournament_rivalry" if roll < threshold else "tournament_sportsmanship"

def choose_investment_priority(
    faction: Mapping[str, Any], *, living_population: int, residential_capacity: int,
    training_capacity: int, cash_reserve_months: int, active_projects: int,
    stress_milli: int = 0,
) -> dict[str, Any] | None:
    """Choose one strategic growth pressure; execution still checks real quotes.

    Factions invest only from meaningful reserves.  Housing and training
    bottlenecks take priority over organizational enterprise scale.  The helper
    returns compact intent, never a free building/enterprise change.
    """
    if active_projects > 0 or int(cash_reserve_months) < max(1, int(autonomy_mechanics().get("growth_cash_reserve_months", 8))) or int(stress_milli) > 0:
        return None
    pop = max(0, int(living_population)); housing = max(0, int(residential_capacity)); training = max(0, int(training_capacity))
    buildings = faction.get("buildings", {}) if isinstance(faction.get("buildings"), Mapping) else {}
    enterprises = faction.get("enterprises", {}) if isinstance(faction.get("enterprises"), Mapping) else {}
    infrastructure = faction.get("infrastructure", {}) if isinstance(faction.get("infrastructure"), Mapping) else {}
    facilities = infrastructure.get("facilities", {}) if isinstance(infrastructure.get("facilities"), Mapping) else {}

    if housing > 0 and pop * 100 >= housing * 78 and int(buildings.get("residential_compound", 0)) > 0:
        current = facilities.get("residential_compound", {}) if isinstance(facilities, Mapping) else {}
        footprint = max(0, int(current.get("footprint_m2", 0))) if isinstance(current, Mapping) else 0
        return {"kind": "expand_building", "building_type": "residential_compound", "additional_footprint_m2": max(200, footprint // 10)}

    if training > 0 and pop * 2 > training and int(buildings.get("training_grounds", 0)) > 0:
        current = facilities.get("training_grounds", {}) if isinstance(facilities, Mapping) else {}
        footprint = max(0, int(current.get("footprint_m2", 0))) if isinstance(current, Mapping) else 0
        return {"kind": "expand_building", "building_type": "training_grounds", "additional_footprint_m2": max(250, footprint // 12)}

    # Quality improvements remain meaningful when a retained facility is below
    # its ceiling.  Only one level is attempted per project.
    quality_order = ("main_hall", "training_hall", "training_grounds", "qi_hall", "armory_workshop", "infirmary_apothecary", "storehouse", "library_records", "transport_yard", "walls_gate")
    for building_type in quality_order:
        level = max(0, int(buildings.get(building_type, 0)))
        if 0 < level < 5:
            return {"kind": "upgrade_building", "building_type": building_type, "target_level": level + 1}

    # If physical institutions are healthy, widen an existing enterprise's
    # organizational scale.  The command/runtime executor still refuses scale
    # beyond owned land, workstations, transport or available people.
    for enterprise_type in ("escort_service", "trade_merchant_business", "crafting_workshop", "medicine_apothecary", "agriculture_landholding"):
        level = max(0, int(enterprises.get(enterprise_type, 0)))
        if level > 0:
            return {"kind": "expand_enterprise", "enterprise_type": enterprise_type, "additional_scale": 1}
    return None


__all__ = [
    "choose_friendly_aid_target", "tournament_travel_interested", "tournament_entrant_interested", "tournament_spectator_interested", "choose_hostile_action", "choose_investment_priority", "stable_permille",
]
