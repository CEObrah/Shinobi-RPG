"""Public martial tournament frontier.

Tournament planning, sponsored travel, public/faction attendance, exact combat,
prize escrow, reputation evidence and return travel are one deterministic domain.
The tournament owner holds current competition state only; completed events are
settled into durable people/economy/reputation consequences and then removed.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .commitments import release_resources, reserve_resources
from .faction_politics import cross_camp_pressure, faction_camp
from .frontier_support import (
    arrival_site as _arrival_site, reputation_after_points as _reputation_after_points,
    social_event as _social_event, tournament_organizer_ref as _tournament_organizer_ref,
    tournament_venue_site as _tournament_venue_site,
)
from .handoffs import classify_handoff
from .rankings import (
    apply_faction_awareness_evidence, apply_faction_reputation_evidence,
    apply_personal_fame_evidence, public_score,
)
from .strategic_autonomy import (
    stable_permille, tournament_entrant_interested, tournament_match_relation_event,
    tournament_spectator_interested, tournament_travel_interested,
)
from .tournaments import (
    add_attendance_prize_cash as tournament_add_attendance_prize_cash,
    advance_individual_competition, close_registration,
    convergence_day_theme as tournament_convergence_day_theme,
    estimated_host_days as tournament_estimated_host_days,
    event_profile as tournament_event_profile, faction_performance_standings,
    merge_delegation_presence as tournament_merge_delegation_presence, open_tournament,
    placement_payouts as tournament_placement_payouts, register as tournament_register,
    tournament_person_eligible,
    themed_convergence_pairs as tournament_themed_convergence_pairs,
)
from .travel import latest_safe_departure, shortest_route, travel_plan
from .upkeep import monthly_upkeep_quote

_TOURNAMENTS_PATH = "state/martial-world/tournaments.json"
_DEPLOYMENTS_PATH = "state/martial-world/deployments.json"
_REPUTATION_PATH = "state/martial-world/reputation.json"
_SOCIAL_PATH = "state/martial-world/social.json"
_EQUIPMENT_LEDGER_PATH = "state/martial-world/equipment-ledger.json"
_COMBATS_PATH = "state/martial-world/combats.json"


def tournament_person_physically_present(
    person: Mapping[str, Any], *, host_place_ref: str, local_sites: Mapping[str, Any],
) -> bool:
    """Whether one exact living person is physically inside the host settlement.

    Registration is a paid/bracket fact, not a location authority. Attendance
    and social convergence therefore derive presence from exact current health
    and location rather than from paperwork alone.
    """
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    if str(health.get("status") or "") == "dead":
        return False
    host = str(host_place_ref or "")
    location = str(person.get("location_ref") or "")
    if not host or not location:
        return False
    if location == host:
        return True
    site = local_sites.get(location) if isinstance(local_sites, Mapping) else None
    return isinstance(site, Mapping) and str(site.get("parent_place_ref") or "") == host


def settle_tournament_frontier(
    *,
    sorted_events: Sequence[Mapping[str, Any]],
    at: datetime,
    at_iso: str,
    world_seed: str,
    player_ref: str,
    all_faction_ids: Sequence[str],
    tournament_state: dict[str, Any],
    deployments_state: dict[str, Any],
    civilian_state: dict[str, Any],
    reputation_state: dict[str, Any],
    social_state: dict[str, Any],
    equipment_ledger: dict[str, Any],
    combats_state: dict[str, Any],
    commitments_state: Mapping[str, Any],
    writes: dict[str, Any],
    reviews: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
    pending_one_off_events: list[dict[str, Any]],
    faction_cache: dict[str, tuple[str, dict[str, Any]]],
    inventory_cache: dict[str, tuple[str, dict[str, Any]]],
    market_cache: dict[str, tuple[str, dict[str, Any]]],
    roster_cache: dict[str, tuple[str, dict[str, Any]]],
    local_sites: Mapping[str, Any],
    site_rows: Mapping[str, Any],
    place_region: Mapping[str, str],
    relation_index: Mapping[str, Any],
    load_faction: Callable[[str], tuple[str, dict[str, Any]]],
    load_inventory: Callable[[str], tuple[str, dict[str, Any]]],
    load_market: Callable[[str], tuple[str, dict[str, Any]]],
    load_roster: Callable[[str], tuple[str, dict[str, Any]]],
    load_person_ref: Callable[[str], tuple[str, str, dict[str, Any], int, dict[str, Any]]],
    current_faction_type: Callable[[str], str],
    person_place: Callable[..., str],
    person_combat_index: Callable[[Mapping[str, Any]], int],
    unavailable_person_refs: Callable[[], set[str]],
    usable_martial_people: Callable[..., list[dict[str, Any]]],
    pause_people_for_commitment: Callable[[str, Sequence[str]], None],
    settle_and_resume_people: Callable[..., Mapping[str, Any]],
    apply_directed_relation_event: Callable[[str, str, str], None],
) -> dict[str, Any]:
    combats = combats_state.setdefault("combats", {})
    if not isinstance(combats, dict):
        raise ValueError("jianghu combat state invalid")
    newly_dead_refs: set[str] = set()

    def plan_tournament_trip(
        fid: str, *, person_ref: str, tournament_ref: str, host_place: str,
        registration_closes_on: str, competition_date: str, entry_fee_cash: int,
        arrival_lead_hours_min: int, arrival_lead_hours_max: int,
        host_cash_per_person_day: int, minimum_host_days: int,
    ) -> dict[str, Any]:
        """Schedule a future departure instead of parking entrants at the host for months.

        Registration opening is notice/planning time.  A distant faction does
        not reserve the fighter or fee until the real departure frontier, where
        current health, commitments, food, tolls and treasury are rechecked.
        """
        try:
            _fpath, faction = load_faction(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "owner_unresolved"}
        source_place = str(faction.get("headquarters") or "")
        if not source_place or not host_place or source_place == host_place:
            return {"result": "travel_not_required"}
        try:
            close_at = datetime.fromisoformat(
                str(registration_closes_on) + ("T18:00:00" if len(str(registration_closes_on)) == 10 else "")
            )
        except ValueError:
            return {"result": "registration_close_invalid"}
        lo = max(0, int(arrival_lead_hours_min)); hi = max(lo, int(arrival_lead_hours_max))
        span = hi - lo
        lead = lo + (stable_permille("tournament-arrival-lead", tournament_ref, fid, person_ref) * span // 999 if span > 0 else 0)
        target_arrival = close_at - timedelta(hours=lead)
        try:
            safe = latest_safe_departure(
                world_seed=world_seed, not_before=at + timedelta(minutes=1), target_arrival=target_arrival,
                start=source_place, end=host_place, mode="foot",
            )
        except (KeyError, ValueError):
            return {"result": "no_registered_route"}
        if not bool(safe.get("reachable", False)):
            return {
                "result": "cannot_arrive_before_registration_close",
                "earliest_arrival_at": str(safe.get("earliest_arrival_at") or ""),
                "registration_closes_at": close_at.isoformat(),
            }
        departure_at = datetime.fromisoformat(str(safe.get("departure_at")))
        event_id = f"tournament_trip_departure:{tournament_ref}:{person_ref}"
        pending_one_off_events.append({
            "event_id": event_id, "kind": "tournament_trip_departure",
            "due_at": departure_at.isoformat(), "owner_ref": person_ref,
            "faction_ref": fid, "person_ref": person_ref,
            "tournament_ref": tournament_ref, "host_place": host_place,
            "registration_closes_on": registration_closes_on,
            "competition_date": competition_date, "entry_fee_cash": max(0, int(entry_fee_cash)),
            "host_cash_per_person_day": max(0, int(host_cash_per_person_day)),
            "minimum_host_days": max(1, int(minimum_host_days)),
            "requires_player_decision": False,
        })
        return {
            "result": "departure_planned", "person_ref": person_ref,
            "departure_at": departure_at.isoformat(), "target_arrival_at": target_arrival.isoformat(),
        }

    def start_tournament_trip(
        fid: str, *, person_ref: str, tournament_ref: str, host_place: str,
        registration_closes_on: str, entry_fee_cash: int,
        host_cash_per_person_day: int, minimum_host_days: int,
    ) -> dict[str, Any]:
        """Start one real nonteleporting faction-sponsored tournament journey.

        The sponsoring faction reserves the entrant fee at departure so many
        simultaneous travelers cannot oversubscribe the same treasury.  The
        reserved fee enters the tournament only after lawful physical arrival
        and registration; otherwise it is refunded to the sponsor.
        """
        nonlocal commitments_state
        try:
            fpath, faction = load_faction(fid); rpath, roster = load_roster(fid); ipath, inventory = load_inventory(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "owner_unresolved"}
        source_place = str(faction.get("headquarters") or "")
        if not source_place or not host_place or source_place == host_place:
            return {"result": "travel_not_required"}
        rows = roster.get("people", []) if isinstance(roster, Mapping) else []
        person = next((p for p in rows if isinstance(p, Mapping) and p.get("person_id") == person_ref), None) if isinstance(rows, list) else None
        if not isinstance(person, Mapping) or person_ref == player_ref:
            return {"result": "entrant_unavailable"}
        if person_ref in unavailable_person_refs() or person_place(person, home_place=source_place, home_site_ref=str(faction.get("local_site_ref") or "")) != source_place:
            return {"result": "entrant_unavailable"}
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") in {"dead", "incapacitated"} or bool(person.get("retired_from_field", False)):
            return {"result": "entrant_ineligible"}
        try:
            plan = travel_plan(world_seed=world_seed, start_at=at, start=source_place, end=host_place, mode="foot")
        except (KeyError, ValueError):
            return {"result": "no_registered_route"}
        try:
            close_at = datetime.fromisoformat(
                str(registration_closes_on) + ("T18:00:00" if len(str(registration_closes_on)) == 10 else "")
            )
        except ValueError:
            close_at = at
        arrival_at = datetime.fromisoformat(str(plan.get("arrival_at")))
        if arrival_at > close_at:
            return {"result": "cannot_arrive_before_registration_close"}
        travel_days = max(1, (int(float(plan.get("travel_hours", 0)) * 1000) + 23999) // 24000)
        food_need = travel_days * 2
        if max(0, int(inventory.get("food_ration_days", 0))) < food_need:
            return {"result": "insufficient_travel_provisions"}
        toll = max(0, int(plan.get("toll_cash", 0))) * 2
        fee = max(0, int(entry_fee_cash))
        host_reserve = max(0, int(host_cash_per_person_day)) * max(1, int(minimum_host_days))
        transport = inventory.get("transport_capacity", {}) if isinstance(inventory.get("transport_capacity"), Mapping) else {}
        quote = monthly_upkeep_quote(
            faction,
            rider_capacity_slots=max(0, int(transport.get("rider_slots", 0))),
            freight_capacity_kg=max(0, int(transport.get("freight_capacity_kg", 0))),
        )
        policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
        reserve_months = max(2, int(policy.get("reserve_cash_months", 6)))
        reserve_floor = max(0, int(quote.get("total_cash", 0))) * reserve_months
        treasury = max(0, int(faction.get("treasury_cash", 0)))
        if treasury - toll - fee - host_reserve < reserve_floor:
            return {"result": "entry_and_travel_cash_reserved"}
        toll_market_path = ""
        toll_market: dict[str, Any] | None = None
        source_region = place_region.get(source_place)
        if toll > 0:
            if not isinstance(source_region, str) or not source_region:
                return {"result": "travel_toll_destination_unresolved"}
            try:
                toll_market_path, toll_market = load_market(source_region)
            except FileNotFoundError:
                return {"result": "travel_toll_destination_unresolved"}
            if not isinstance(toll_market, dict) or toll_market.get("region_id") not in (None, source_region):
                return {"result": "travel_toll_destination_unresolved"}
        op_ref = f"tournament_trip:{tournament_ref}:{person_ref}"
        deployments = deployments_state.setdefault("deployments", {})
        if not isinstance(deployments, dict) or op_ref in deployments:
            return {"result": "trip_already_active"}
        try:
            commitments_state = reserve_resources(
                commitments_state, resources=[("person", person_ref, fid)], actor_ref=person_ref,
                owner_ref=fid, activity_ref=op_ref, activity_kind="tournament_trip",
                started_at=at_iso, location_ref=source_place,
            )
        except ValueError:
            return {"result": "entrant_unavailable"}
        # Food remains in the faction inventory until the physical departure
        # frontier atomically reserves this exact leg.  Tournament planning may
        # verify affordability here, but must never pre-consume the same rations.
        faction["treasury_cash"] = treasury - toll - fee - host_reserve
        if toll > 0 and isinstance(toll_market, dict) and isinstance(source_region, str):
            toll_market["cash_pool"] = max(0, int(toll_market.get("cash_pool", 0))) + toll
            writes[toll_market_path] = toll_market
            market_cache[source_region] = (toll_market_path, toll_market)
        deployments[op_ref] = {
            "faction_ref": fid, "operation_kind": "tournament_travel",
            "tournament_ref": tournament_ref, "participant_refs": [person_ref],
            "source_place_ref": source_place, "source_site_ref": str(faction.get("local_site_ref") or ""), "target_place_ref": host_place,
            "started_at": at_iso, "departure_at": (at + timedelta(seconds=1)).isoformat(),
            "arrival_at": arrival_at.isoformat(), "travel_hours": float(plan.get("travel_hours", 0)),
            "route_refs": list(plan.get("edges", [])), "status": "mobilizing",
            "arrival_event_kind": "tournament_travel_arrival",
            "entry_fee_reserved_cash": fee,
            "host_spend_reserved_cash": host_reserve,
        }
        pause_people_for_commitment(fid, [person_ref])
        writes[_DEPLOYMENTS_PATH] = deployments_state;writes[fpath] = faction; writes[ipath] = inventory; faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory)
        pending_one_off_events.append({
            "event_id": f"operation_departure:{op_ref}", "kind": "faction_operation_departure",
            "due_at": (at + timedelta(seconds=1)).isoformat(), "owner_ref": op_ref, "direction": "outbound",
            "arrival_event_kind": "tournament_travel_arrival", "requires_player_decision": False,
        })
        return {
            "result": "travel_started", "trip_ref": op_ref, "person_ref": person_ref,
            "arrival_at": arrival_at.isoformat(), "entry_fee_reserved_cash": fee,
            "host_spend_reserved_cash": host_reserve,
        }

    def _tournament_delegate_roles(person: Mapping[str, Any]) -> tuple[bool, bool]:
        offices = {str(x) for x in person.get("standing_offices", []) if isinstance(x, str)} if isinstance(person.get("standing_offices"), list) else set()
        leader = "leader" in offices
        senior_offices = {"leader", "deputy_leader", "chief_martial_instructor", "chief_physician", "chief_steward", "treasurer", "quartermaster"}
        grade = str(person.get("membership_grade") or "")
        senior = bool(offices & senior_offices) or grade in {"elder", "elite"}
        return leader, senior

    def _add_tournament_delegation_presence(
        tournament_ref: str, faction_ref: str, *, entrant_refs: Sequence[str] = (),
        spectator_refs: Sequence[str] = (), leader_refs: Sequence[str] = (),
        senior_refs: Sequence[str] = (),
    ) -> None:
        registry = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = registry.get(tournament_ref) if isinstance(registry, Mapping) else None
        if not isinstance(tournament, dict) or not faction_ref:
            return
        merged = tournament_merge_delegation_presence(
            tournament, faction_ref=faction_ref, camp=faction_camp(faction_ref),
            entrant_refs=entrant_refs, spectator_refs=spectator_refs,
            leader_refs=leader_refs, senior_refs=senior_refs,
        )
        registry[tournament_ref] = merged
        writes[_TOURNAMENTS_PATH] = tournament_state

    def fund_public_tournament_attendance(
        tournament_ref: str, *, tournament_kind: str, attendance_date: str,
        delegate_count: int,
    ) -> dict[str, int]:
        """Charge aggregate public spectators once per event day into the purse.

        Civilians remain aggregate. Their paid ticket cash is conserved out of
        the host regional market cash pool and into tournament prize escrow.
        Venue capacity limits official spectators; inability to pay reduces paid
        attendance instead of minting ticket revenue. Repeated competition
        sessions on the same date reuse the stored daily attendance receipt.
        """
        registry=tournament_state.get("tournaments",{}) if isinstance(tournament_state,Mapping) else {}
        tournament=registry.get(tournament_ref) if isinstance(registry,Mapping) else None
        if not isinstance(tournament,Mapping):
            return {"public_spectator_count":0,"public_spectator_overflow":0,"public_ticket_cash":0,"venue_capacity":0}
        receipts=tournament.get("public_attendance_by_date",{}) if isinstance(tournament.get("public_attendance_by_date"),Mapping) else {}
        existing=receipts.get(attendance_date) if isinstance(receipts,Mapping) else None
        if isinstance(existing,Mapping):
            return {
                "public_spectator_count":max(0,int(existing.get("public_spectator_count",0))),
                "public_spectator_overflow":max(0,int(existing.get("public_spectator_overflow",0))),
                "public_ticket_cash":max(0,int(existing.get("public_ticket_cash",0))),
                "venue_capacity":max(0,int(existing.get("venue_capacity",0))),
            }
        profile=tournament_event_profile(tournament_kind)
        venue_ref=str(tournament.get("venue_site_ref") or "")
        venue_row=site_rows.get(venue_ref) if isinstance(site_rows,Mapping) else None
        venue_capacity=max(0,int(venue_row.get("capacity",0))) if isinstance(venue_row,Mapping) else 0
        host_place=str(tournament.get("host_place_ref") or "")
        civilian_places=civilian_state.get("places",{}) if isinstance(civilian_state,Mapping) else {}
        host_civilians=civilian_places.get(host_place,{}) if isinstance(civilian_places,Mapping) else {}
        host_population=max(0,int(host_civilians.get("current_population",0))) if isinstance(host_civilians,Mapping) else 0
        demand_permille=25 if tournament_kind=="great_jianghu_tournament" else 10
        demand=host_population*demand_permille//1000
        seat_limit=max(0,venue_capacity-max(0,int(delegate_count))) if venue_capacity>0 else 0
        possible=min(seat_limit,demand)
        ticket=max(0,int(profile.get("public_spectator_ticket_cash_per_day",0)))
        paid=possible; ticket_cash=0
        host_region=str(tournament.get("host_region") or "")
        if ticket>0 and possible>0 and host_region:
            try:
                mpath,market=load_market(host_region)
            except FileNotFoundError:
                market=None; mpath=""
            if isinstance(market,dict):
                paid=min(possible,max(0,int(market.get("cash_pool",0)))//ticket)
                ticket_cash=paid*ticket
                market["cash_pool"]=max(0,int(market.get("cash_pool",0)))-ticket_cash
                writes[mpath]=market; market_cache[host_region]=(mpath,market)
            else:
                paid=0
        elif ticket>0:
            paid=0
        overflow=max(0,demand-paid)
        updated=dict(tournament)
        if ticket_cash>0:
            updated=tournament_add_attendance_prize_cash(
                updated,amount_cash=ticket_cash,source_kind="public_spectator_ticket",
            )
        receipt={
            "public_spectator_count":paid,"public_spectator_overflow":overflow,
            "public_ticket_cash":ticket_cash,"venue_capacity":venue_capacity,
        }
        updated_receipts=dict(receipts) if isinstance(receipts,Mapping) else {}
        updated_receipts[attendance_date]=receipt
        updated["public_attendance_by_date"]=updated_receipts
        updated["peak_public_spectator_count"]=max(max(0,int(updated.get("peak_public_spectator_count",0))),paid)
        updated["peak_delegate_count"]=max(max(0,int(updated.get("peak_delegate_count",0))),max(0,int(delegate_count)))
        registry[tournament_ref]=updated
        writes[_TOURNAMENTS_PATH]=tournament_state
        return dict(receipt)


    def plan_tournament_delegation_trip(
        fid: str, *, candidate_refs: Sequence[str], tournament_ref: str, host_place: str,
        competition_date: str, convergence_days_before: int, host_cash_per_person_day: int,
        delegate_ticket_cash_per_day: int, minimum_host_days: int,
    ) -> dict[str, Any]:
        """Plan one real spectator/representative delegation per faction.

        The plan does not reserve people or money.  It schedules a departure
        frontier close to the official convergence window; that frontier
        rechecks the exact roster, commitments, food, tolls and treasury.
        """
        try:
            _fpath, faction = load_faction(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "owner_unresolved"}
        refs = [str(x) for x in candidate_refs if isinstance(x, str) and x and str(x) != player_ref]
        if not refs:
            return {"result": "no_delegates_nominated"}
        source_place = str(faction.get("headquarters") or "")
        if not source_place or not host_place:
            return {"result": "travel_endpoint_unresolved"}
        try:
            competition_at = datetime.fromisoformat(str(competition_date) + ("T09:00:00" if len(str(competition_date)) == 10 else ""))
        except ValueError:
            return {"result": "competition_date_invalid"}
        convergence_start = competition_at - timedelta(days=max(0, int(convergence_days_before)))
        lead_hours = 8 + stable_permille("tournament-delegation-arrival-lead", tournament_ref, fid) * 28 // 999
        target_arrival = convergence_start - timedelta(hours=lead_hours)
        if source_place == host_place:
            departure_at = max(at + timedelta(minutes=1), target_arrival)
        else:
            try:
                safe = latest_safe_departure(
                    world_seed=world_seed, not_before=at + timedelta(minutes=1), target_arrival=target_arrival,
                    start=source_place, end=host_place, mode="foot",
                )
            except (KeyError, ValueError):
                return {"result": "no_registered_route"}
            if not bool(safe.get("reachable", False)):
                return {
                    "result": "cannot_arrive_before_tournament",
                    "faction_ref": fid,
                    "earliest_arrival_at": str(safe.get("earliest_arrival_at") or ""),
                    "competition_at": competition_at.isoformat(),
                }
            departure_at = datetime.fromisoformat(str(safe.get("departure_at")))
        event_id = f"tournament_delegation_departure:{tournament_ref}:{fid}"
        pending_one_off_events.append({
            "event_id": event_id, "kind": "tournament_delegation_departure",
            "due_at": departure_at.isoformat(), "owner_ref": fid, "faction_ref": fid,
            "candidate_refs": refs, "tournament_ref": tournament_ref,
            "host_place": host_place, "competition_date": competition_date,
            "latest_arrival_at": competition_at.isoformat(),
            "host_cash_per_person_day": max(0, int(host_cash_per_person_day)),
            "delegate_ticket_cash_per_day": max(0, int(delegate_ticket_cash_per_day)),
            "minimum_host_days": max(1, int(minimum_host_days)),
            "requires_player_decision": False,
        })
        return {
            "result": "delegation_departure_planned", "faction_ref": fid,
            "candidate_count": len(refs), "departure_at": departure_at.isoformat(),
            "target_arrival_at": target_arrival.isoformat(),
        }

    def start_tournament_delegation_trip(
        fid: str, *, candidate_refs: Sequence[str], tournament_ref: str, host_place: str,
        host_cash_per_person_day: int, delegate_ticket_cash_per_day: int, minimum_host_days: int,
        latest_arrival_at: str = "",
    ) -> dict[str, Any]:
        """Commit the largest currently affordable named spectator delegation."""
        nonlocal commitments_state
        try:
            fpath, faction = load_faction(fid); rpath, roster = load_roster(fid); ipath, inventory = load_inventory(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "owner_unresolved"}
        source_place = str(faction.get("headquarters") or "")
        source_site = str(faction.get("local_site_ref") or "")
        if not source_place or not host_place:
            return {"result": "travel_endpoint_unresolved"}
        rows = roster.get("people", []) if isinstance(roster, Mapping) else []
        by_ref = {str(p.get("person_id")): p for p in rows if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)} if isinstance(rows, list) else {}
        blocked = unavailable_person_refs()
        refs: list[str] = []
        leader_refs: list[str] = []
        senior_refs: list[str] = []
        for ref in [str(x) for x in candidate_refs if isinstance(x, str)]:
            person = by_ref.get(ref)
            if not isinstance(person, Mapping) or ref == player_ref or ref in blocked:
                continue
            if person_place(person, home_place=source_place, home_site_ref=source_site) != source_place:
                continue
            health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
            if health.get("status") in {"dead", "incapacitated"}:
                continue
            if at.year - int(person.get("birth_year", at.year)) < 14:
                continue
            refs.append(ref)
            is_leader, is_senior = _tournament_delegate_roles(person)
            if is_leader:
                leader_refs.append(ref)
            if is_senior:
                senior_refs.append(ref)
        if not refs:
            return {"result": "delegation_unavailable"}
        local = source_place == host_place
        if local:
            plan = {"arrival_at": at.isoformat(), "travel_hours": 1.0, "toll_cash": 0, "edges": []}
        else:
            try:
                plan = travel_plan(world_seed=world_seed, start_at=at, start=source_place, end=host_place, mode="foot")
            except (KeyError, ValueError):
                return {"result": "no_registered_route"}
        if latest_arrival_at:
            try:
                planned_arrival = datetime.fromisoformat(str(plan.get("arrival_at")))
                latest_arrival = datetime.fromisoformat(str(latest_arrival_at))
            except (TypeError, ValueError):
                return {"result": "tournament_arrival_deadline_invalid"}
            if planned_arrival >= latest_arrival:
                return {
                    "result": "cannot_arrive_before_tournament",
                    "arrival_at": planned_arrival.isoformat(),
                    "latest_arrival_at": latest_arrival.isoformat(),
                }
        travel_days = 0 if local else max(1, (int(float(plan.get("travel_hours", 0)) * 1000) + 23999) // 24000)
        transport = inventory.get("transport_capacity", {}) if isinstance(inventory.get("transport_capacity"), Mapping) else {}
        quote = monthly_upkeep_quote(
            faction, rider_capacity_slots=max(0, int(transport.get("rider_slots", 0))),
            freight_capacity_kg=max(0, int(transport.get("freight_capacity_kg", 0))),
        )
        policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
        reserve_floor = max(0, int(quote.get("total_cash", 0))) * max(2, int(policy.get("reserve_cash_months", 6)))
        treasury = max(0, int(faction.get("treasury_cash", 0)))
        food_before = max(0, int(inventory.get("food_ration_days", 0)))
        toll_each = 0 if local else max(0, int(plan.get("toll_cash", 0))) * 2
        # Event/lodging spending is real for local and traveling delegations.
        # Travel tolls are zero for locals, but attending a major gathering is
        # not free just because the faction already lives in the host city.
        host_each = max(0, int(host_cash_per_person_day)) * max(1, int(minimum_host_days))
        ticket_each = max(0, int(delegate_ticket_cash_per_day)) * max(1, int(minimum_host_days))
        # There is no delegation cap.  If the originally nominated party is too
        # expensive today, shrink only from the lowest-priority tail until the
        # remaining real party is supportable.
        while refs:
            count = len(refs)
            food_need = count * travel_days * 2
            cash_need = count * (toll_each + host_each + ticket_each)
            if food_before >= food_need and treasury - cash_need >= reserve_floor:
                break
            removed = refs.pop()
            if removed in leader_refs:
                leader_refs.remove(removed)
            if removed in senior_refs:
                senior_refs.remove(removed)
        if not refs:
            return {"result": "delegation_not_affordable"}
        food_need = len(refs) * travel_days * 2
        toll = len(refs) * toll_each
        host_reserve = len(refs) * host_each
        delegate_ticket_reserve = len(refs) * ticket_each
        toll_market_path = ""
        toll_market: dict[str, Any] | None = None
        source_region = place_region.get(source_place)
        if toll > 0:
            if not isinstance(source_region, str) or not source_region:
                return {"result": "travel_toll_destination_unresolved"}
            try:
                toll_market_path, toll_market = load_market(source_region)
            except FileNotFoundError:
                return {"result": "travel_toll_destination_unresolved"}
            if not isinstance(toll_market, dict) or toll_market.get("region_id") not in (None, source_region):
                return {"result": "travel_toll_destination_unresolved"}
        op_ref = f"tournament_delegation:{tournament_ref}:{fid}"
        deployments = deployments_state.setdefault("deployments", {})
        if not isinstance(deployments, dict) or op_ref in deployments:
            return {"result": "delegation_already_active"}
        try:
            commitments_state = reserve_resources(
                commitments_state, resources=[("person", ref, fid) for ref in refs],
                actor_ref=leader_refs[0] if leader_refs else refs[0], owner_ref=fid,
                activity_ref=op_ref, activity_kind="tournament_delegation",
                started_at=at_iso, location_ref=source_place,
            )
        except ValueError:
            return {"result": "delegation_unavailable"}
        # The shared faction-operation departure reducer owns travel ration
        # reservation.  Keep this check as a current affordability screen only.
        faction["treasury_cash"] = treasury - toll - host_reserve - delegate_ticket_reserve
        if toll > 0 and isinstance(toll_market, dict) and isinstance(source_region, str):
            toll_market["cash_pool"] = max(0, int(toll_market.get("cash_pool", 0))) + toll
            writes[toll_market_path] = toll_market
            market_cache[source_region] = (toll_market_path, toll_market)
        local_host_spend = 0
        local_delegate_ticket = 0
        if local and host_reserve > 0:
            host_region = place_region.get(host_place)
            if isinstance(host_region, str):
                try:
                    mpath, market = load_market(host_region)
                    market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + host_reserve
                    writes[mpath] = market; market_cache[host_region] = (mpath, market)
                    local_host_spend = host_reserve
                except FileNotFoundError:
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + host_reserve
                    host_reserve = 0
        if local and delegate_ticket_reserve > 0:
            registry = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
            tournament = registry.get(tournament_ref) if isinstance(registry, Mapping) else None
            if isinstance(tournament, Mapping):
                funded = tournament_add_attendance_prize_cash(
                    tournament, amount_cash=delegate_ticket_reserve,
                    source_kind="faction_delegate_ticket",
                )
                registry[tournament_ref] = funded
                writes[_TOURNAMENTS_PATH] = tournament_state
                local_delegate_ticket = delegate_ticket_reserve
            else:
                faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + delegate_ticket_reserve
                delegate_ticket_reserve = 0
        deployment = {
            "faction_ref": fid, "operation_kind": "tournament_delegation",
            "tournament_ref": tournament_ref, "participant_refs": refs, "leader_refs": leader_refs, "senior_refs": senior_refs,
            "source_place_ref": source_place, "source_site_ref": source_site,
            "target_place_ref": host_place, "started_at": at_iso,
            "departure_at": (at + timedelta(seconds=1)).isoformat() if not local else at_iso,
            "arrival_at": str(plan.get("arrival_at")), "travel_hours": float(plan.get("travel_hours", 1.0)),
            "route_refs": list(plan.get("edges", [])), "status": "mobilizing" if not local else "at_tournament",
            "arrival_event_kind": "tournament_delegation_arrival",
            "host_spend_reserved_cash": 0 if local else host_reserve,
            "host_spend_per_person_cash": host_each,
            "host_spend_cash": local_host_spend,
            "delegate_ticket_reserved_cash": 0 if local else delegate_ticket_reserve,
            "delegate_ticket_per_person_cash": ticket_each,
            "delegate_ticket_cash": local_delegate_ticket,
        }
        deployments[op_ref] = deployment
        pause_people_for_commitment(fid, refs)
        writes[_DEPLOYMENTS_PATH] = deployments_state;writes[fpath] = faction; writes[ipath] = inventory
        faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory)
        if local:
            venue = str((tournament_state.get("tournaments", {}) or {}).get(tournament_ref, {}).get("venue_site_ref") or _tournament_venue_site(local_sites, host_place) or host_place)
            if isinstance(rows, list):
                for i, raw in enumerate(rows):
                    if isinstance(raw, Mapping) and str(raw.get("person_id")) in refs:
                        person = copy.deepcopy(dict(raw)); person["location_ref"] = venue; rows[i] = person
                roster["people"] = rows; writes[rpath] = roster; roster_cache[fid] = (rpath, roster)
            _add_tournament_delegation_presence(
                tournament_ref, fid, spectator_refs=refs, leader_refs=leader_refs, senior_refs=senior_refs,
            )
        else:
            pending_one_off_events.append({
                "event_id": f"operation_departure:{op_ref}", "kind": "faction_operation_departure",
                "due_at": (at + timedelta(seconds=1)).isoformat(), "owner_ref": op_ref, "direction": "outbound",
                "arrival_event_kind": "tournament_delegation_arrival", "requires_player_decision": False,
            })
        return {
            "result": "delegation_present" if local else "delegation_departed", "delegation_ref": op_ref,
            "participant_count": len(refs), "leader_count": len(leader_refs), "senior_count": len(senior_refs), "toll_cash": toll,
            "host_spend_reserved_cash": 0 if local else host_reserve, "host_spend_cash": local_host_spend,
            "delegate_ticket_reserved_cash": 0 if local else delegate_ticket_reserve,
            "delegate_ticket_cash": local_delegate_ticket,
        }

    def schedule_tournament_returns(tournament_ref: str) -> int:
        """Turn all surviving tournament-trip deployments into return journeys."""
        nonlocal commitments_state
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        if not isinstance(deployments, dict):
            return 0
        count = 0
        for op_ref, raw in list(deployments.items()):
            if not isinstance(raw, Mapping) or raw.get("operation_kind") not in {"tournament_travel", "tournament_delegation"} or raw.get("tournament_ref") != tournament_ref:
                continue
            op = copy.deepcopy(dict(raw)); refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
            alive_refs: list[str] = []
            for ref in refs:
                try:
                    _ofid, _rp, _ros, _ord, person = load_person_ref(ref)
                except (KeyError, FileNotFoundError, ValueError):
                    continue
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if health.get("status") != "dead": alive_refs.append(ref)
            if not alive_refs:
                commitments_state = release_resources(commitments_state, activity_ref=str(op_ref)); deployments.pop(op_ref, None); continue
            op["participant_refs"] = alive_refs
            if str(op.get("source_place_ref") or "") != str(op.get("target_place_ref") or "") and op.get("route_refs"):
                op["status"] = "return_preparing"; op["pending_travel_direction"] = "return"; deployments[op_ref] = op
                pending_one_off_events.append({
                    "event_id": f"operation_departure:return:{op_ref}", "kind": "faction_operation_departure",
                    "due_at": (at + timedelta(seconds=1)).isoformat(), "owner_ref": op_ref, "direction": "return",
                    "arrival_event_kind": "tournament_return_arrival", "requires_player_decision": False,
                })
            else:
                return_at = at + timedelta(hours=1)
                op["status"] = "traveling_return"; op["return_arrival_at"] = return_at.isoformat(); deployments[op_ref] = op
                pending_one_off_events.append({"event_id": f"tournament_return_arrival:{op_ref}", "kind": "tournament_return_arrival", "due_at": return_at.isoformat(), "owner_ref": op_ref, "requires_player_decision": False})
            count += len(alive_refs)
        writes[_DEPLOYMENTS_PATH] = deployments_state;return count

    # Spectator/representative delegations are one bounded group movement per
    # faction.  Planning happened at registration opening; departure rechecks
    # the current named people and current conserved resources.
    for event in sorted_events:
        if event.get("kind") != "tournament_delegation_departure":
            continue
        fid = str(event.get("faction_ref") or event.get("owner_ref") or "")
        tref = str(event.get("tournament_ref") or "")
        registry = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = registry.get(tref) if isinstance(registry, Mapping) else None
        if not fid or not isinstance(tournament, Mapping) or tournament.get("status") == "completed":
            reviews.append({
                "kind": "tournament_delegation_departure", "event_id": event.get("event_id"),
                "tournament_ref": tref, "faction_ref": fid, "result": "tournament_unavailable",
            })
            continue
        outcome = start_tournament_delegation_trip(
            fid, candidate_refs=[str(x) for x in event.get("candidate_refs", []) if isinstance(x, str)],
            tournament_ref=tref, host_place=str(event.get("host_place") or tournament.get("host_place_ref") or ""),
            host_cash_per_person_day=max(0, int(event.get("host_cash_per_person_day", 0))),
            delegate_ticket_cash_per_day=max(0, int(event.get("delegate_ticket_cash_per_day", 0))),
            minimum_host_days=max(1, int(event.get("minimum_host_days", 1))),
            latest_arrival_at=str(event.get("latest_arrival_at") or ""),
        )
        reviews.append({
            "kind": "tournament_delegation_departure", "event_id": event.get("event_id"),
            "tournament_ref": tref, "faction_ref": fid, **dict(outcome),
        })

    # Registration opening creates future travel plans; the actual departure
    # frontier rechecks current faction/person resources so a months-old plan
    # cannot reserve a fighter or spend money prematurely.
    for event in sorted_events:
        if event.get("kind") != "tournament_trip_departure":
            continue
        fid = str(event.get("faction_ref") or "")
        person_ref = str(event.get("person_ref") or event.get("owner_ref") or "")
        tref = str(event.get("tournament_ref") or "")
        registry = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = registry.get(tref) if isinstance(registry, Mapping) else None
        if not fid or not person_ref or not isinstance(tournament, Mapping) or tournament.get("status") != "registration_open":
            reviews.append({
                "kind": "tournament_trip_departure", "event_id": event.get("event_id"),
                "tournament_ref": tref, "faction_ref": fid, "person_ref": person_ref,
                "result": "registration_not_open",
            })
            continue
        outcome = start_tournament_trip(
            fid, person_ref=person_ref, tournament_ref=tref,
            host_place=str(event.get("host_place") or tournament.get("host_place_ref") or ""),
            registration_closes_on=str(event.get("registration_closes_on") or tournament.get("registration_closes_on") or ""),
            entry_fee_cash=max(0, int(event.get("entry_fee_cash", tournament.get("entry_fee_cash", 0)))),
            host_cash_per_person_day=max(0, int(event.get("host_cash_per_person_day", 0))),
            minimum_host_days=max(1, int(event.get("minimum_host_days", 1))),
        )
        reviews.append({
            "kind": "tournament_trip_departure", "event_id": event.get("event_id"),
            "tournament_ref": tref, "faction_ref": fid, "person_ref": person_ref,
            **dict(outcome),
        })

    # Distant tournament entrants travel under real commitments. They register
    # only after physically reaching the host before the registration deadline.
    # Entry sponsorship is faction-funded: the fee was reserved from treasury
    # at departure and is transferred only on successful physical registration.
    def refund_reserved_tournament_fee(fid: str, op: Mapping[str, Any]) -> int:
        amount = max(0, int(op.get("entry_fee_reserved_cash", 0)))
        if amount <= 0 or not fid:
            return 0
        try:
            fpath, faction = load_faction(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return 0
        faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + amount
        writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
        return amount

    def send_tournament_trip_home(op_ref: str, op: Mapping[str, Any], *, refund_fee: bool) -> tuple[dict[str, Any], int]:
        fid = str(op.get("faction_ref") or "")
        current = copy.deepcopy(dict(op))
        refunded = refund_reserved_tournament_fee(fid, current) if refund_fee else 0
        host_refunded = refund_reserved_host_spend(fid, op_ref, current) if refund_fee else 0
        if refunded > 0:
            current["entry_fee_reserved_cash"] = 0
        if host_refunded > 0:
            current["host_spend_reserved_cash"] = 0
            current["host_spend_refunded_cash"] = host_refunded
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        source_place = str(current.get("source_place_ref") or "")
        target_place = str(current.get("target_place_ref") or "")
        if source_place and target_place and source_place != target_place:
            # A failed/late registration still produces a real journey home.
            # Weather, terrain and food are recalculated at the actual return
            # departure; no outbound duration or prepaid food is reused.
            current["status"] = "return_preparing"
            current["pending_travel_direction"] = "return"
            if isinstance(deployments, dict):
                deployments[op_ref] = current
            pending_one_off_events.append({
                "event_id": f"operation_departure:return:{op_ref}",
                "kind": "faction_operation_departure",
                "due_at": (at + timedelta(seconds=1)).isoformat(),
                "owner_ref": op_ref, "direction": "return",
                "arrival_event_kind": "tournament_return_arrival",
                "requires_player_decision": False,
            })
        else:
            current["status"] = "traveling_return"
            return_at = at + timedelta(hours=1)
            current["return_arrival_at"] = return_at.isoformat()
            if isinstance(deployments, dict):
                deployments[op_ref] = current
            pending_one_off_events.append({
                "event_id": f"tournament_return_arrival:{op_ref}", "kind": "tournament_return_arrival",
                "due_at": return_at.isoformat(), "owner_ref": op_ref, "requires_player_decision": False,
            })
        writes[_DEPLOYMENTS_PATH] = deployments_state
        return current, refunded

    def _refund_faction_cash(fid: str, amount_cash: int) -> int:
        amount=max(0,int(amount_cash))
        if amount<=0 or not fid:
            return 0
        try:
            fpath,faction=load_faction(fid)
        except (KeyError,FileNotFoundError,ValueError):
            return 0
        faction["treasury_cash"]=max(0,int(faction.get("treasury_cash",0)))+amount
        writes[fpath]=faction; faction_cache[fid]=(fpath,faction)
        return amount

    def refund_reserved_host_spend(fid: str, op_ref: str, op: Mapping[str, Any], *, amount_cash: int | None = None) -> int:
        reserved=max(0,int(op.get("host_spend_reserved_cash",0)))
        amount=reserved if amount_cash is None else min(reserved,max(0,int(amount_cash)))
        refunded=_refund_faction_cash(fid,amount)
        deployments=deployments_state.get("deployments",{}) if isinstance(deployments_state,Mapping) else {}
        current=deployments.get(op_ref) if isinstance(deployments,Mapping) else None
        if isinstance(current,dict) and refunded>0:
            current["host_spend_reserved_cash"]=max(0,int(current.get("host_spend_reserved_cash",0))-refunded)
        return refunded

    def refund_reserved_delegate_ticket(fid: str, op_ref: str, op: Mapping[str, Any], *, amount_cash: int | None = None) -> int:
        reserved=max(0,int(op.get("delegate_ticket_reserved_cash",0)))
        amount=reserved if amount_cash is None else min(reserved,max(0,int(amount_cash)))
        refunded=_refund_faction_cash(fid,amount)
        deployments=deployments_state.get("deployments",{}) if isinstance(deployments_state,Mapping) else {}
        current=deployments.get(op_ref) if isinstance(deployments,Mapping) else None
        if isinstance(current,dict) and refunded>0:
            current["delegate_ticket_reserved_cash"]=max(0,int(current.get("delegate_ticket_reserved_cash",0))-refunded)
        return refunded

    for event in sorted_events:
        if event.get("kind") != "tournament_delegation_arrival":
            continue
        op_ref = str(event.get("owner_ref") or "")
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
        if not isinstance(op, Mapping) or op.get("operation_kind") != "tournament_delegation" or op.get("status") not in {"traveling_outbound", "arrived_pending"}:
            reviews.append({"kind": "tournament_delegation_arrival", "event_id": event.get("event_id"), "result": "delegation_not_active"})
            continue
        fid = str(op.get("faction_ref") or ""); tref = str(op.get("tournament_ref") or "")
        refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
        leader_refs = [str(x) for x in op.get("leader_refs", []) if isinstance(x, str)]
        senior_refs = [str(x) for x in op.get("senior_refs", []) if isinstance(x, str)]
        tournaments = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = tournaments.get(tref) if isinstance(tournaments, Mapping) else None
        if not fid or not refs or not isinstance(tournament, Mapping) or tournament.get("status") == "completed":
            refunded = refund_reserved_host_spend(fid, op_ref, op)
            ticket_refunded = refund_reserved_delegate_ticket(fid, op_ref, op)
            _current, _fee_refund = send_tournament_trip_home(op_ref, op, refund_fee=False)
            reviews.append({
                "kind": "tournament_delegation_arrival", "event_id": event.get("event_id"),
                "delegation_ref": op_ref, "result": "tournament_unavailable_returning",
                "host_spend_refunded_cash": refunded, "delegate_ticket_refunded_cash": ticket_refunded,
            })
            continue
        host_place = str(tournament.get("host_place_ref") or op.get("target_place_ref") or "")
        venue = str(tournament.get("venue_site_ref") or _tournament_venue_site(local_sites, host_place) or host_place)
        rpath, roster = load_roster(fid); rows = roster.get("people", []) if isinstance(roster, Mapping) else []
        present_refs: list[str] = []
        if isinstance(rows, list):
            for i, raw in enumerate(rows):
                if not isinstance(raw, Mapping) or str(raw.get("person_id")) not in refs:
                    continue
                health = raw.get("health", {}) if isinstance(raw.get("health"), Mapping) else {}
                if health.get("status") == "dead":
                    continue
                person = copy.deepcopy(dict(raw)); person["location_ref"] = venue; rows[i] = person
                present_refs.append(str(person.get("person_id")))
            roster["people"] = rows; writes[rpath] = roster; roster_cache[fid] = (rpath, roster)
        if not present_refs:
            refunded = refund_reserved_host_spend(fid, op_ref, op)
            ticket_refunded = refund_reserved_delegate_ticket(fid, op_ref, op)
            _current, _fee_refund = send_tournament_trip_home(op_ref, op, refund_fee=False)
            reviews.append({
                "kind": "tournament_delegation_arrival", "event_id": event.get("event_id"),
                "delegation_ref": op_ref, "result": "delegation_missing_returning",
                "host_spend_refunded_cash": refunded, "delegate_ticket_refunded_cash": ticket_refunded,
            })
            continue
        planned_count=max(1,len(refs)); present_count=len(present_refs)
        host_reserved=max(0,int(op.get("host_spend_reserved_cash",0)))
        host_per=max(0,int(op.get("host_spend_per_person_cash",0)))
        host_spend=min(host_reserved,host_per*present_count if host_per>0 else host_reserved*present_count//planned_count)
        host_refund=max(0,host_reserved-host_spend)
        if host_refund>0:
            _refund_faction_cash(fid,host_refund)
        ticket_reserved=max(0,int(op.get("delegate_ticket_reserved_cash",0)))
        ticket_per=max(0,int(op.get("delegate_ticket_per_person_cash",0)))
        delegate_ticket_cash=min(ticket_reserved,ticket_per*present_count if ticket_per>0 else ticket_reserved*present_count//planned_count)
        delegate_ticket_refund=max(0,ticket_reserved-delegate_ticket_cash)
        if delegate_ticket_refund>0:
            _refund_faction_cash(fid,delegate_ticket_refund)
        host_region = str(tournament.get("host_region") or "")
        if host_spend > 0 and host_region:
            try:
                mpath, market = load_market(host_region)
                market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + host_spend
                writes[mpath] = market; market_cache[host_region] = (mpath, market)
            except FileNotFoundError:
                _refund_faction_cash(fid,host_spend)
                host_spend = 0
        if delegate_ticket_cash>0:
            tournament=tournament_add_attendance_prize_cash(
                tournament,amount_cash=delegate_ticket_cash,source_kind="faction_delegate_ticket",
            )
            tournaments[tref]=tournament
            writes[_TOURNAMENTS_PATH]=tournament_state
        current = copy.deepcopy(dict(op)); current["status"] = "at_tournament"; current["arrived_at"] = at_iso
        current["participant_refs"] = present_refs; current["host_spend_reserved_cash"] = 0
        current["delegate_ticket_reserved_cash"] = 0; current["host_spend_cash"] = host_spend
        current["delegate_ticket_cash"] = delegate_ticket_cash
        deployments[op_ref] = current; writes[_DEPLOYMENTS_PATH] = deployments_state
        present_set = set(present_refs)
        _add_tournament_delegation_presence(
            tref, fid, spectator_refs=present_refs,
            leader_refs=[ref for ref in leader_refs if ref in present_set],
            senior_refs=[ref for ref in senior_refs if ref in present_set],
        )
        reviews.append({
            "kind": "tournament_delegation_arrival", "event_id": event.get("event_id"),
            "delegation_ref": op_ref, "tournament_ref": tref, "faction_ref": fid,
            "participant_count": len(present_refs), "host_spend_cash": host_spend,
            "delegate_ticket_cash": delegate_ticket_cash,
            "host_spend_refunded_cash": host_refund, "delegate_ticket_refunded_cash": delegate_ticket_refund,
            "prize_cash": max(0,int(tournament.get("prize_escrow_cash",0))),
            "result": "arrived",
        })

    for event in sorted_events:
        if event.get("kind") != "tournament_travel_arrival":
            continue
        op_ref = str(event.get("owner_ref") or "")
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
        if not isinstance(op, Mapping) or op.get("operation_kind") != "tournament_travel" or op.get("status") not in {"traveling_outbound", "arrived_pending"}:
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "result": "trip_not_active"}); continue
        fid = str(op.get("faction_ref") or ""); tref = str(op.get("tournament_ref") or ""); refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
        tournaments = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = tournaments.get(tref) if isinstance(tournaments, Mapping) else None
        if not fid or len(refs) != 1 or not isinstance(tournament, Mapping) or tournament.get("status") != "registration_open":
            _current, refunded = send_tournament_trip_home(op_ref, op, refund_fee=True)
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "result": "registration_closed_returning", "entry_fee_refunded_cash": refunded}); continue
        person_ref = refs[0]; host_place = str(tournament.get("host_place_ref") or op.get("target_place_ref") or ""); venue = str(tournament.get("venue_site_ref") or _tournament_venue_site(local_sites, host_place) or host_place)
        rpath, roster = load_roster(fid); rows = roster.get("people", []) if isinstance(roster, Mapping) else []
        idx = next((i for i, raw in enumerate(rows) if isinstance(raw, Mapping) and raw.get("person_id") == person_ref), None) if isinstance(rows, list) else None
        if idx is None:
            _current, refunded = send_tournament_trip_home(op_ref, op, refund_fee=True)
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "result": "entrant_missing_returning", "entry_fee_refunded_cash": refunded}); continue
        person = copy.deepcopy(dict(rows[idx])); person["location_ref"] = venue
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        alive = health.get("status") != "dead"; medically_eligible = health.get("status") not in {"dead", "incapacitated"}
        host_region = str(tournament.get("host_region") or "")
        try:
            mpath, organizer_market = load_market(host_region) if host_region else ("", None)
        except FileNotFoundError:
            organizer_market = None; mpath = ""
        if not isinstance(organizer_market, dict):
            _current, refunded = send_tournament_trip_home(op_ref, op, refund_fee=True)
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "result": "organizer_market_missing_returning", "entry_fee_refunded_cash": refunded}); continue
        reserved_fee = max(0, int(op.get("entry_fee_reserved_cash", 0)))
        try:
            audience = (reputation_state.get("audiences", {}) or {}).get(person_ref, {}) if isinstance(reputation_state.get("audiences", {}), Mapping) else {}
            qualifying = int(audience.get("public_score", public_score(audience))) if isinstance(audience, Mapping) else 0
            reg = tournament_register(
                tournament, entrant_ref=person_ref, qualifying_score=qualifying,
                payer_cash=reserved_fee, alive=alive, medically_eligible=medically_eligible,
            )
        except (TypeError, ValueError):
            _current, refunded = send_tournament_trip_home(op_ref, op, refund_fee=True)
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "result": "registration_failed_returning", "entry_fee_refunded_cash": refunded}); continue
        tournament = dict(reg["tournament_after"]); tournament["registrations"][-1]["faction_ref"] = fid
        tournaments[tref] = tournament
        entrant_is_leader, entrant_is_senior = _tournament_delegate_roles(person)
        _add_tournament_delegation_presence(
            tref, fid, entrant_refs=[person_ref],
            leader_refs=[person_ref] if entrant_is_leader else (),
            senior_refs=[person_ref] if entrant_is_senior else (),
        )
        tournament = dict(tournaments[tref])
        rows[idx] = person; roster["people"] = rows
        host_spend = max(0, int(op.get("host_spend_reserved_cash", 0)))
        organizer_market["cash_pool"] = (
            max(0, int(organizer_market.get("cash_pool", 0)))
            + host_spend
        )
        writes[mpath] = organizer_market; market_cache[host_region] = (mpath, organizer_market)
        current = copy.deepcopy(dict(op)); current["status"] = "at_tournament"; current["arrived_at"] = at_iso
        current["entry_fee_reserved_cash"] = 0; current["host_spend_reserved_cash"] = 0; current["host_spend_cash"] = host_spend
        deployments[op_ref] = current
        writes[rpath] = roster; roster_cache[fid] = (rpath, roster); writes[_TOURNAMENTS_PATH] = tournament_state; writes[_DEPLOYMENTS_PATH] = deployments_state
        reviews.append({
            "kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref,
            "tournament_ref": tref, "faction_ref": fid, "person_ref": person_ref,
            "entry_fee_cash": int(tournament.get("entry_fee_cash", 0)),
            "host_spend_cash": host_spend,
            "prize_cash": int(tournament.get("prize_escrow_cash", 0)), "result": "registered",
        })

    for event in sorted_events:
        if event.get("kind") != "tournament_return_arrival":
            continue
        op_ref = str(event.get("owner_ref") or ""); deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}; op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
        if not isinstance(op, Mapping) or op.get("operation_kind") not in {"tournament_travel", "tournament_delegation"} or op.get("status") != "traveling_return":
            reviews.append({"kind": "tournament_return_arrival", "event_id": event.get("event_id"), "result": "trip_not_returning"}); continue
        fid = str(op.get("faction_ref") or ""); refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]; source_place = str(op.get("source_place_ref") or ""); source_site = str(op.get("source_site_ref") or "") or _arrival_site(local_sites, source_place)
        if fid and source_site:
            rpath, roster = load_roster(fid); rows = roster.get("people", []) if isinstance(roster, Mapping) else []
            if isinstance(rows, list):
                out_rows: list[Any] = []
                for raw in rows:
                    if isinstance(raw, Mapping) and str(raw.get("person_id")) in refs:
                        person = copy.deepcopy(dict(raw)); person["location_ref"] = source_site; out_rows.append(person)
                    else: out_rows.append(raw)
                roster["people"] = out_rows; writes[rpath] = roster; roster_cache[fid] = (rpath, roster)
        commitments_state = settle_and_resume_people(refs, activity_ref=op_ref, commitments_state=commitments_state); deployments.pop(op_ref, None)
        writes[_DEPLOYMENTS_PATH] = deployments_state;
        reviews.append({"kind": "tournament_return_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "faction_ref": fid, "returned_count": len(refs), "result": "completed"})

    # Public martial championships have one fully mechanical production format:
    # individual exact combat. Registrations are sponsored by real faction
    # treasuries, entry fees build the prize escrow, injuries persist, and the
    # live tournament owner is deleted after payout instead of becoming history.
    tournament_events = [
        e for e in sorted_events
        if e.get("kind") in {
            "tournament_advance_notice",
            "tournament_registration_open", "tournament_registration_close",
            "tournament_convergence_day",
            "regional_martial_tournament", "great_jianghu_tournament", "midyear_junior_tournament",
            "tournament_competition_continue",
        }
    ]
    if tournament_events:
        tournaments = tournament_state.setdefault("tournaments", {})
        if not isinstance(tournaments, dict):
            raise ValueError("jianghu tournament registry invalid")

        def _tref(kind: str, competition_date: str) -> str:
            return f"tournament:{kind}:{competition_date}:individual"

        def _registration_owner_map(tournament: Mapping[str, Any]) -> dict[str, str]:
            result: dict[str, str] = {}
            rows = tournament.get("registrations", []) if isinstance(tournament, Mapping) else []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    ref = row.get("entrant_ref"); fid = row.get("faction_ref")
                    if isinstance(ref, str) and isinstance(fid, str):
                        result[ref] = fid
            return result

        def _write_tournament_people(owner_map: Mapping[str, str], people_after: Mapping[str, Mapping[str, Any]]) -> None:
            by_faction: dict[str, dict[str, Mapping[str, Any]]] = {}
            for ref, person in people_after.items():
                fid = owner_map.get(str(ref))
                if isinstance(fid, str) and isinstance(person, Mapping):
                    by_faction.setdefault(fid, {})[str(ref)] = person
            for fid, replacements in by_faction.items():
                rpath, roster = load_roster(fid)
                rows = roster.get("people", []) if isinstance(roster, Mapping) else []
                if not isinstance(rows, list):
                    continue
                changed = False; after_rows: list[Any] = []
                for raw in rows:
                    ref = raw.get("person_id") if isinstance(raw, Mapping) else None
                    if isinstance(ref, str) and ref in replacements:
                        replacement = copy.deepcopy(dict(replacements[ref]))
                        before_health = raw.get("health", {}) if isinstance(raw.get("health"), Mapping) else {}
                        after_health = replacement.get("health", {}) if isinstance(replacement.get("health"), Mapping) else {}
                        if before_health.get("status") != "dead" and after_health.get("status") == "dead":
                            newly_dead_refs.add(ref)
                        after_rows.append(replacement); changed = True
                    else:
                        after_rows.append(raw)
                if changed:
                    roster["people"] = after_rows
                    writes[rpath] = roster
                    roster_cache[fid] = (rpath, roster)

        for event in tournament_events:
            kind = str(event.get("kind", ""))
            if kind == "tournament_advance_notice":
                tournament_kind = str(event.get("tournament_kind") or "")
                competition_date = str(event.get("competition_date") or "")
                host = str(event.get("host_place_id") or "")
                if tournament_kind == "great_jianghu_tournament" and competition_date and host:
                    notice = {
                        "kind": "tournament_registration",
                        "phase": "advance_notice",
                        "tournament_kind": tournament_kind,
                        "competition_date": competition_date,
                        "registration_opens_on": str(event.get("registration_opens_on") or ""),
                        "registration_closes_on": str(event.get("registration_closes_on") or ""),
                        "host_place_id": host,
                        "delivered_to_player": True,
                        "requires_player_decision": False,
                    }
                    handoff = classify_handoff(notice)
                    reviews.append({
                        "kind": "tournament_advance_notice", "event_id": event.get("event_id"),
                        "tournament_kind": tournament_kind, "competition_date": competition_date,
                        "host_place_id": host, "handoff": handoff,
                    })
                    if handoff["class"] != "internal":
                        handoffs.append({**notice, "handoff": handoff})
                continue
            if kind == "tournament_registration_open":
                tournament_kind = str(event.get("tournament_kind") or "")
                competition_date = str(event.get("competition_date") or "")
                if tournament_kind not in {"regional_martial_tournament", "great_jianghu_tournament", "midyear_junior_tournament"} or not competition_date:
                    continue
                tref = _tref(tournament_kind, competition_date)
                if tref in tournaments:
                    continue
                host = str(event.get("host_place_id") or "")
                host_region = place_region.get(host)
                if not host or not isinstance(host_region, str):
                    reviews.append({"kind": "tournament_registration_open", "event_id": event.get("event_id"), "result": "host_unresolved"})
                    continue
                try:
                    profile = tournament_event_profile(tournament_kind)
                    mpath, organizer_market = load_market(host_region)
                    is_great = tournament_kind == "great_jianghu_tournament"
                    opened = open_tournament(
                        event_id=tref, format_ref="individual",
                        organizer_ref=_tournament_organizer_ref(host, great=is_great),
                        great=is_great, tournament_kind=tournament_kind,
                    )
                except (FileNotFoundError, KeyError, ValueError):
                    reviews.append({"kind": "tournament_registration_open", "event_id": event.get("event_id"), "result": "host_services_unavailable"})
                    continue
                tournament = dict(opened)
                tournament.update({
                    "tournament_ref": tref, "tournament_kind": tournament_kind,
                    "competition_date": competition_date,
                    "registration_closes_on": str(event.get("registration_closes_on") or ""),
                    "host_place_ref": host, "host_region": host_region,
                    "venue_site_ref": _tournament_venue_site(local_sites, host) or host,
                })
                fee = max(0, int(tournament.get("entry_fee_cash", 0)))
                prestige = int(profile.get("prestige_weight", 50))
                allows_outlaws = bool(profile.get("allows_outlaw_factions", False))
                entrants = 0

                # Local host-city factions may sponsor any number of eligible
                # entrants.  There is no field cap or local slot allocation;
                # marginal nominations simply become less attractive while
                # treasury reserve and medical/physical eligibility remain hard.
                for fid in all_faction_ids:
                    fpath, faction = load_faction(fid)
                    if faction.get("headquarters") != host:
                        continue
                    if current_faction_type(fid) == "outlaw_faction" and not allows_outlaws:
                        continue
                    rpath, roster = load_roster(fid); ipath, inventory = load_inventory(fid)
                    people = usable_martial_people(roster, exclude_committed=unavailable_person_refs())
                    people = [
                        p for p in people
                        if tournament_person_eligible(tournament_kind, p, year=at.year)
                        and person_place(p, home_place=str(faction.get("headquarters") or ""), home_site_ref=str(faction.get("local_site_ref") or "")) == host
                        and str(p.get("person_id")) != player_ref
                    ]
                    people.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
                    if not people:
                        continue
                    policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                    transport = inventory.get("transport_capacity", {}) if isinstance(inventory.get("transport_capacity"), Mapping) else {}
                    quote = monthly_upkeep_quote(
                        faction,
                        rider_capacity_slots=max(0, int(transport.get("rider_slots", 0))),
                        freight_capacity_kg=max(0, int(transport.get("freight_capacity_kg", 0))),
                    )
                    reserve_months = max(2, int(policy.get("reserve_cash_months", 6)))
                    reserve_floor = max(0, int(quote.get("total_cash", 0))) * reserve_months
                    local_registered = 0
                    best_combat = person_combat_index(people[0]) if people else 0.0
                    for candidate_index, candidate in enumerate(people):
                        ref = str(candidate.get("person_id") or "")
                        if not ref:
                            continue
                        if not tournament_entrant_interested(
                            faction_ref=fid, person_ref=ref, tournament_ref=tref,
                            tournament_kind=tournament_kind, entrant_order=candidate_index,
                            training_priority=int(policy.get("training_priority", 50)),
                            risk_tolerance=int(policy.get("risk_tolerance", 50)),
                            prestige_weight=prestige,
                            faction_type=current_faction_type(fid),
                            living_members=max(0, int(faction.get("population", 0))),
                            major_sect_population_threshold=int(profile.get("major_sect_population_threshold", 100)),
                            major_sect_competitor_floor=int(profile.get("major_sect_competitor_floor", 0)),
                            major_institution_population_threshold=int(profile.get("major_institution_population_threshold", 100)),
                            major_institution_competitor_floor=int(profile.get("major_institution_competitor_floor", 0)),
                            ordinary_competitor_floor=int(profile.get("ordinary_competitor_floor", 0)),
                            candidate_combat_index=person_combat_index(candidate),
                            best_combat_index=best_combat,
                            additional_competitor_interest_permille=int(profile.get("additional_competitor_interest_permille", 0)),
                            additional_competitor_decay_permille=int(profile.get("additional_competitor_decay_permille", 0)),
                            additional_competitor_relative_strength_permille=int(profile.get("additional_competitor_relative_strength_permille", 0)),
                        ):
                            continue
                        treasury = max(0, int(faction.get("treasury_cash", 0)))
                        if treasury - fee < reserve_floor:
                            break
                        audience = reputation_state.get("audiences", {}).get(ref, {}) if isinstance(reputation_state.get("audiences"), Mapping) else {}
                        qualifying = max(0, int(audience.get("public_score", 0))) if isinstance(audience, Mapping) else 0
                        try:
                            reg = tournament_register(
                                tournament, entrant_ref=ref, qualifying_score=qualifying,
                                payer_cash=treasury, alive=True, medically_eligible=True,
                            )
                        except ValueError:
                            continue
                        tournament = dict(reg["tournament_after"])
                        tournament["registrations"][-1]["faction_ref"] = fid
                        tournaments[tref] = tournament
                        entrant_is_leader, entrant_is_senior = _tournament_delegate_roles(candidate)
                        _add_tournament_delegation_presence(
                            tref, fid, entrant_refs=[ref],
                            leader_refs=[ref] if entrant_is_leader else (),
                            senior_refs=[ref] if entrant_is_senior else (),
                        )
                        tournament = dict(tournaments[tref])
                        faction["treasury_cash"] = int(reg["payer_cash_after"])
                        entrants += 1; local_registered += 1
                    if local_registered:
                        writes[fpath] = faction; faction_cache[fid] = (fpath, faction)

                # Distant factions may send multiple sponsored entrants.  No
                # reserved travel slots exist.  Interest creates candidates;
                # every actual trip still proves route time, provisions, tolls,
                # health, commitment availability and faction operating reserve.
                travel_planned = 0
                planned_competitors_by_faction: dict[str, set[str]] = {}
                for registration in tournament.get("registrations", []):
                    if isinstance(registration, Mapping) and isinstance(registration.get("faction_ref"), str) and isinstance(registration.get("entrant_ref"), str):
                        planned_competitors_by_faction.setdefault(str(registration["faction_ref"]), set()).add(str(registration["entrant_ref"]))
                travel_candidates: list[tuple[float, str, str, int]] = []
                for fid in all_faction_ids:
                    _fpath, faction = load_faction(fid)
                    if faction.get("headquarters") == host:
                        continue
                    if current_faction_type(fid) == "outlaw_faction" and not allows_outlaws:
                        continue
                    policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                    source_place = str(faction.get("headquarters") or "")
                    try:
                        route_hint = shortest_route(start=source_place, end=host, mode="foot") if source_place and source_place != host else {"baseline_hours": 0}
                        travel_days_hint = max(0, (int(float(route_hint.get("baseline_hours", 0)) * 1000) + 23999) // 24000)
                    except (KeyError, ValueError):
                        travel_days_hint = 999
                    if not tournament_travel_interested(
                        faction_ref=fid, tournament_ref=tref, tournament_kind=tournament_kind,
                        training_priority=int(policy.get("training_priority", 50)),
                        risk_tolerance=int(policy.get("risk_tolerance", 50)),
                        entry_fee_cash=fee,
                        current_prize_cash=int(tournament.get("prize_escrow_cash", 0)),
                        prestige_weight=prestige,
                        faction_type=current_faction_type(fid),
                        living_members=max(0, int(faction.get("population", 0))),
                        faction_interest_floor_permille=int(profile.get("faction_interest_floor_permille", 0)),
                        major_sect_population_threshold=int(profile.get("major_sect_population_threshold", 100)),
                        travel_days_hint=travel_days_hint,
                    ):
                        continue
                    _rpath, roster = load_roster(fid)
                    people = [
                        p for p in usable_martial_people(roster, exclude_committed=unavailable_person_refs())
                        if person_place(p, home_place=str(faction.get("headquarters") or ""), home_site_ref=str(faction.get("local_site_ref") or "")) == str(faction.get("headquarters") or "")
                        and tournament_person_eligible(tournament_kind, p, year=at.year)
                        and str(p.get("person_id")) != player_ref
                    ]
                    people.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
                    best_combat = person_combat_index(people[0]) if people else 0.0
                    for candidate_index, person in enumerate(people):
                        person_ref = str(person.get("person_id") or "")
                        if not person_ref:
                            continue
                        if tournament_entrant_interested(
                            faction_ref=fid, person_ref=person_ref, tournament_ref=tref,
                            tournament_kind=tournament_kind, entrant_order=candidate_index,
                            training_priority=int(policy.get("training_priority", 50)),
                            risk_tolerance=int(policy.get("risk_tolerance", 50)),
                            prestige_weight=prestige,
                            faction_type=current_faction_type(fid),
                            living_members=max(0, int(faction.get("population", 0))),
                            major_sect_population_threshold=int(profile.get("major_sect_population_threshold", 100)),
                            major_sect_competitor_floor=int(profile.get("major_sect_competitor_floor", 0)),
                            major_institution_population_threshold=int(profile.get("major_institution_population_threshold", 100)),
                            major_institution_competitor_floor=int(profile.get("major_institution_competitor_floor", 0)),
                            ordinary_competitor_floor=int(profile.get("ordinary_competitor_floor", 0)),
                            candidate_combat_index=person_combat_index(person),
                            best_combat_index=best_combat,
                            additional_competitor_interest_permille=int(profile.get("additional_competitor_interest_permille", 0)),
                            additional_competitor_decay_permille=int(profile.get("additional_competitor_decay_permille", 0)),
                            additional_competitor_relative_strength_permille=int(profile.get("additional_competitor_relative_strength_permille", 0)),
                        ):
                            travel_candidates.append((-person_combat_index(person), fid, person_ref, candidate_index))
                travel_candidates.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
                # Attendance spending is derived from the size of the uncapped
                # planned field rather than a fixed fictional stay.  A larger
                # bracket really occupies more competition days and therefore
                # costs every visiting delegation more lodging/event cash.
                estimated_field_size = entrants + len(travel_candidates)
                budgeted_host_days = tournament_estimated_host_days(tournament_kind, estimated_field_size)
                for _neg_score, fid, person_ref, _candidate_index in travel_candidates:
                    outcome = plan_tournament_trip(
                        fid, person_ref=person_ref, tournament_ref=tref,
                        host_place=host, registration_closes_on=str(event.get("registration_closes_on") or ""),
                        competition_date=competition_date, entry_fee_cash=fee,
                        arrival_lead_hours_min=int(profile.get("arrival_lead_hours_min", 12)),
                        arrival_lead_hours_max=int(profile.get("arrival_lead_hours_max", 36)),
                        host_cash_per_person_day=int(profile.get("attendee_host_cash_per_person_day", 0)),
                        minimum_host_days=budgeted_host_days,
                    )
                    if outcome.get("result") == "departure_planned":
                        travel_planned += 1
                        planned_competitors_by_faction.setdefault(fid, set()).add(person_ref)

                # Tournament attendance includes real faction delegations, not
                # only bracket entrants.  The Great event makes every faction
                # evaluate a delegation; regional events use ordinary interest.
                delegation_attempts = 0
                delegation_planned = 0
                spectator_people_nominated = 0
                delegation_failures: dict[str, int] = {}
                office_priority = {
                    "leader": 1000, "deputy_leader": 900, "chief_martial_instructor": 850,
                    "chief_physician": 800, "chief_steward": 760, "treasurer": 720,
                    "quartermaster": 680,
                }
                grade_priority = {"elder": 600, "elite": 520, "senior": 440, "full": 300, "junior": 180, "probationary": 80}
                for fid in all_faction_ids:
                    fpath, faction = load_faction(fid)
                    ftype = current_faction_type(fid)
                    if ftype == "outlaw_faction" and not allows_outlaws:
                        continue
                    policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                    if tournament_kind != "great_jianghu_tournament" and faction.get("headquarters") != host:
                        source_place = str(faction.get("headquarters") or "")
                        try:
                            route_hint = shortest_route(start=source_place, end=host, mode="foot") if source_place and source_place != host else {"baseline_hours": 0}
                            travel_days_hint = max(0, (int(float(route_hint.get("baseline_hours", 0)) * 1000) + 23999) // 24000)
                        except (KeyError, ValueError):
                            travel_days_hint = 999
                        if not tournament_travel_interested(
                            faction_ref=fid, tournament_ref=tref, tournament_kind=tournament_kind,
                            training_priority=int(policy.get("training_priority", 50)),
                            risk_tolerance=int(policy.get("risk_tolerance", 50)), entry_fee_cash=fee,
                            current_prize_cash=int(tournament.get("prize_escrow_cash", 0)), prestige_weight=prestige,
                            faction_type=ftype, living_members=max(0, int(faction.get("population", 0))),
                            faction_interest_floor_permille=int(profile.get("faction_interest_floor_permille", 0)),
                            major_sect_population_threshold=int(profile.get("major_sect_population_threshold", 100)),
                            travel_days_hint=travel_days_hint,
                        ):
                            continue
                    delegation_attempts += 1
                    rpath, roster = load_roster(fid)
                    competitors = planned_competitors_by_faction.get(fid, set())
                    home_place = str(faction.get("headquarters") or "")
                    home_site = str(faction.get("local_site_ref") or "")
                    candidates = [
                        p for p in usable_martial_people(roster, exclude_committed=unavailable_person_refs())
                        if person_place(p, home_place=home_place, home_site_ref=home_site) == home_place
                        and at.year - int(p.get("birth_year", at.year)) >= 14
                        and str(p.get("person_id") or "") != player_ref
                        and str(p.get("person_id") or "") not in competitors
                    ]
                    def _spectator_priority(person: Mapping[str, Any]) -> tuple[int, int, int, str]:
                        offices = person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else []
                        office = max((office_priority.get(str(ref), 0) for ref in offices), default=0)
                        grade = grade_priority.get(str(person.get("membership_grade") or ""), 0)
                        professional = person.get("professional_skills", {}) if isinstance(person.get("professional_skills"), Mapping) else {}
                        useful = max(int(professional.get("medicine", 0)), int(professional.get("administration", 0)), int(professional.get("commerce", 0)), int(professional.get("instruction", 0)))
                        return (-office, -grade, -useful, str(person.get("person_id", "")))
                    candidates.sort(key=_spectator_priority)
                    selected: list[str] = []
                    for spectator_order, person in enumerate(candidates):
                        ref = str(person.get("person_id") or "")
                        offices = person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else []
                        if not ref or not tournament_spectator_interested(
                            faction_ref=fid, person_ref=ref, tournament_ref=tref, tournament_kind=tournament_kind,
                            spectator_order=spectator_order, is_leader="leader" in offices, faction_type=ftype,
                            living_members=max(0, int(faction.get("population", 0))),
                            spectator_delegation_floor=int(profile.get("spectator_delegation_floor", 0)),
                            major_spectator_population_threshold=int(profile.get("major_spectator_population_threshold", 100)),
                            major_spectator_delegation_floor=int(profile.get("major_spectator_delegation_floor", 0)),
                            major_sect_spectator_delegation_floor=int(profile.get("major_sect_spectator_delegation_floor", 0)),
                            leader_attendance_permille=int(profile.get("leader_attendance_permille", 0)),
                            spectator_marginal_interest_permille=int(profile.get("spectator_marginal_interest_permille", 0)),
                            spectator_marginal_decay_permille=int(profile.get("spectator_marginal_decay_permille", 0)),
                        ):
                            continue
                        selected.append(ref)
                    if not selected:
                        delegation_failures["no_available_delegates"] = delegation_failures.get("no_available_delegates", 0) + 1
                        continue
                    outcome = plan_tournament_delegation_trip(
                        fid, candidate_refs=selected, tournament_ref=tref, host_place=host,
                        competition_date=competition_date, convergence_days_before=int(profile.get("convergence_days_before", 0)),
                        host_cash_per_person_day=int(profile.get("attendee_host_cash_per_person_day", 0)),
                        delegate_ticket_cash_per_day=int(profile.get("faction_delegate_ticket_cash_per_day", 0)),
                        minimum_host_days=budgeted_host_days,
                    )
                    if outcome.get("result") == "delegation_departure_planned":
                        delegation_planned += 1
                        spectator_people_nominated += len(selected)
                    else:
                        reason = str(outcome.get("result") or "planning_failed")
                        delegation_failures[reason] = delegation_failures.get(reason, 0) + 1

                tournaments[tref] = tournament
                writes[_TOURNAMENTS_PATH] = tournament_state
                writes[mpath] = organizer_market; market_cache[host_region] = (mpath, organizer_market)
                notice = {
                    "kind": "tournament_registration", "tournament_ref": tref,
                    "tournament_kind": tournament_kind, "host_place_id": host,
                    "organizer_ref": tournament.get("organizer_ref"),
                    "competition_date": competition_date, "entry_fee_cash": fee,
                    "prize_cash": int(tournament.get("prize_escrow_cash", 0)),
                    "local_paid_registrations": entrants, "traveling_entrants_planned": travel_planned,
                    "faction_attendance_attempts": delegation_attempts,
                    "spectator_delegations_planned": delegation_planned,
                    "spectator_people_nominated": spectator_people_nominated,
                    "delegation_failure_counts": dict(sorted(delegation_failures.items())),
                    "estimated_uncapped_field_size": estimated_field_size,
                    "budgeted_host_days": budgeted_host_days,
                    "field_size_cap": None,
                    "delivered_to_player": True, "requires_player_decision": False,
                }
                handoff = classify_handoff(notice); reviews.append({
                    "kind": "tournament_registration_open", "event_id": event.get("event_id"),
                    "tournament_ref": tref, "local_paid_registrations": entrants,
                    "traveling_entrants_planned": travel_planned,
                    "faction_attendance_attempts": delegation_attempts,
                    "spectator_delegations_planned": delegation_planned,
                    "spectator_people_nominated": spectator_people_nominated,
                    "delegation_failure_counts": dict(sorted(delegation_failures.items())),
                    "prize_cash": int(tournament.get("prize_escrow_cash", 0)), "handoff": handoff,
                }); handoffs.append({**notice, "handoff": handoff})
                continue

            if kind == "tournament_registration_close":
                tournament_kind = str(event.get("tournament_kind") or ""); competition_date = str(event.get("competition_date") or "")
                tref = _tref(tournament_kind, competition_date)
                tournament = tournaments.get(tref)
                if isinstance(tournament, Mapping) and tournament.get("status") == "registration_open":
                    tournaments[tref] = close_registration(tournament)
                    writes[_TOURNAMENTS_PATH] = tournament_state
                    registrations = tournaments[tref].get("registrations", []) if isinstance(tournaments[tref], Mapping) else []
                    entrant_factions = {
                        str(row.get("faction_ref")) for row in registrations
                        if isinstance(row, Mapping) and isinstance(row.get("faction_ref"), str)
                    } if isinstance(registrations, list) else set()
                    reviews.append({"kind": "tournament_registration_close", "event_id": event.get("event_id"), "tournament_ref": tref, "entrant_count": len(registrations) if isinstance(registrations, list) else 0, "entrant_faction_count": len(entrant_factions)})
                continue

            if kind == "tournament_convergence_day":
                tournament_kind = str(event.get("tournament_kind") or "")
                competition_date = str(event.get("competition_date") or "")
                tref = _tref(tournament_kind, competition_date)
                tournament = tournaments.get(tref)
                if not isinstance(tournament, Mapping) or tournament.get("status") not in {"bracket_ready", "in_progress"}:
                    reviews.append({
                        "kind": kind, "event_id": event.get("event_id"),
                        "tournament_ref": tref, "result": "tournament_not_ready_for_convergence",
                    })
                    continue
                profile = tournament_event_profile(tournament_kind)
                registrations = [
                    dict(row) for row in tournament.get("registrations", [])
                    if isinstance(row, Mapping) and isinstance(row.get("faction_ref"), str)
                    and isinstance(row.get("entrant_ref"), str)
                ]
                host_place = str(tournament.get("host_place_ref") or event.get("host_place_id") or host or "")
                presence_cache: dict[tuple[str, str], bool] = {}

                def physically_present(fid: str, person_ref: str) -> bool:
                    key = (str(fid), str(person_ref))
                    if key in presence_cache:
                        return presence_cache[key]
                    try:
                        _rpath, current_roster = load_roster(str(fid))
                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                        presence_cache[key] = False
                        return False
                    people = current_roster.get("people", []) if isinstance(current_roster, Mapping) else []
                    person = next((
                        row for row in people
                        if isinstance(row, Mapping) and str(row.get("person_id") or "") == str(person_ref)
                    ), None) if isinstance(people, list) else None
                    present = (
                        isinstance(person, Mapping)
                        and str(person_ref) not in unavailable_person_refs()
                        and tournament_person_physically_present(
                            person, host_place_ref=host_place, local_sites=local_sites,
                        )
                    )
                    presence_cache[key] = bool(present)
                    return bool(present)

                registration_by_faction: dict[str, list[dict[str, Any]]] = {}
                for row in registrations:
                    fid = str(row["faction_ref"]); ref = str(row["entrant_ref"])
                    if physically_present(fid, ref):
                        registration_by_faction.setdefault(fid, []).append(row)
                for rows in registration_by_faction.values():
                    rows.sort(key=lambda row: (-int(row.get("public_qualifying_score", 0)), str(row.get("entrant_ref", ""))))

                # Registration and delegation roles are institutional facts.
                # Presence is delegation-wide only for people physically at the host.
                # A faction may attend the Great
                # Tournament for diplomacy/observation even if it could not
                # sponsor a fighter, and leaders/elders are legitimate meeting
                # representatives rather than forcing every conversation through
                # the faction's highest-seeded competitor.
                presence_by_faction: dict[str, dict[str, Any]] = {}
                raw_delegations = tournament.get("delegations", {}) if isinstance(tournament.get("delegations"), Mapping) else {}
                for fid, raw in raw_delegations.items():
                    if not isinstance(fid, str) or not isinstance(raw, Mapping):
                        continue
                    entrants = [str(x) for x in raw.get("entrant_refs", []) if isinstance(x, str) and physically_present(fid, str(x))]
                    spectators = [str(x) for x in raw.get("spectator_refs", []) if isinstance(x, str) and physically_present(fid, str(x))]
                    leaders = [str(x) for x in raw.get("leader_refs", []) if isinstance(x, str) and physically_present(fid, str(x))]
                    seniors = [str(x) for x in raw.get("senior_refs", []) if isinstance(x, str) and physically_present(fid, str(x))]
                    if not entrants and not spectators:
                        continue
                    presence_by_faction[fid] = {
                        "entrant_refs": entrants, "spectator_refs": spectators,
                        "leader_refs": leaders, "senior_refs": seniors,
                        "camp": str(raw.get("camp") or faction_camp(fid)),
                    }
                for fid, rows in registration_by_faction.items():
                    row = presence_by_faction.setdefault(fid, {
                        "entrant_refs": [], "spectator_refs": [], "leader_refs": [], "senior_refs": [], "camp": faction_camp(fid),
                    })
                    row["entrant_refs"] = sorted(set(row.get("entrant_refs", [])) | {str(r["entrant_ref"]) for r in rows})

                day_index = max(1, int(event.get("convergence_day_index", 1)))
                day_count = max(day_index, int(event.get("convergence_day_count", day_index)))
                day_theme = tournament_convergence_day_theme(tournament_kind, day_index)
                contacts_per_faction = max(0, int(profile.get("convergence_contacts_per_faction_per_day", 1)))

                def current_edge(source: str, target: str) -> Mapping[str, Any] | None:
                    return next((
                        edge for edge in relation_index.get(source, [])
                        if isinstance(edge, Mapping) and edge.get("to_faction") == target
                    ), None)

                senior_factions = {
                    fid for fid, row in presence_by_faction.items()
                    if isinstance(row, Mapping) and (row.get("leader_refs") or row.get("senior_refs"))
                }
                camp_by_faction = {fid: faction_camp(fid) or "unclassified" for fid in presence_by_faction}
                hostility_by_pair: dict[tuple[str, str], int] = {}
                for fa in sorted(presence_by_faction):
                    for edge in relation_index.get(fa, []):
                        if not isinstance(edge, Mapping):
                            continue
                        fb = str(edge.get("to_faction") or "")
                        if fb not in presence_by_faction or fa == fb:
                            continue
                        pair = (fa, fb) if fa < fb else (fb, fa)
                        reverse = current_edge(fb, fa)
                        hostility_by_pair[pair] = max(
                            hostility_by_pair.get(pair, 0),
                            max(0, int(edge.get("hostility", 0))),
                            max(0, int(reverse.get("hostility", 0))) if isinstance(reverse, Mapping) else 0,
                        )
                pairs = tournament_themed_convergence_pairs(
                    sorted(presence_by_faction), tournament_ref=tref, day_index=day_index,
                    tournament_kind=tournament_kind, theme=day_theme,
                    contacts_per_faction=contacts_per_faction,
                    senior_faction_refs=sorted(senior_factions),
                    camp_by_faction=camp_by_faction,
                    hostility_by_pair=hostility_by_pair,
                )

                def delegation_representative(fid: str) -> str:
                    row = presence_by_faction.get(fid, {})
                    for key in ("leader_refs", "senior_refs", "entrant_refs", "spectator_refs"):
                        refs = [str(x) for x in row.get(key, []) if isinstance(x, str)] if isinstance(row, Mapping) else []
                        if refs:
                            return sorted(refs)[0]
                    return ""

                meaningful_contacts = 0
                tense_contacts = 0
                mediated_contacts = 0
                senior_contacts = 0
                new_person_contacts = 0
                for fa, fb in pairs:
                    edge_ab = current_edge(fa, fb)
                    edge_ba = current_edge(fb, fa)
                    hostility = max(
                        int(edge_ab.get("hostility", 0)) if isinstance(edge_ab, Mapping) else 0,
                        int(edge_ba.get("hostility", 0)) if isinstance(edge_ba, Mapping) else 0,
                    )
                    has_prior = isinstance(edge_ab, Mapping) or isinstance(edge_ba, Mapping)
                    camp_a = faction_camp(fa)
                    camp_b = faction_camp(fb)
                    same_camp = camp_a == camp_b and bool(camp_a)
                    camp_pressure = cross_camp_pressure(camp_a, camp_b)
                    roll = stable_permille("tournament-convergence-contact", tref, day_index, fa, fb)
                    rep_a = delegation_representative(fa)
                    rep_b = delegation_representative(fb)
                    leaders_a = set(presence_by_faction.get(fa, {}).get("leader_refs", []))
                    leaders_b = set(presence_by_faction.get(fb, {}).get("leader_refs", []))
                    seniors_a = set(presence_by_faction.get(fa, {}).get("senior_refs", [])) | leaders_a
                    seniors_b = set(presence_by_faction.get(fb, {}).get("senior_refs", [])) | leaders_b
                    senior_pair = rep_a in seniors_a and rep_b in seniors_b
                    # The Great Tournament's private-negotiation day is one of
                    # the few lawful neutral spaces where an existing feud or
                    # war can cool without pretending camp identity made the
                    # factions friends.  It requires real senior delegations, a
                    # preexisting relationship edge, and a bounded successful
                    # mediation roll.
                    respect = max(
                        int(edge_ab.get("respect", 0)) if isinstance(edge_ab, Mapping) else 0,
                        int(edge_ba.get("respect", 0)) if isinstance(edge_ba, Mapping) else 0,
                    )
                    trust = max(
                        int(edge_ab.get("trust", 0)) if isinstance(edge_ab, Mapping) else 0,
                        int(edge_ba.get("trust", 0)) if isinstance(edge_ba, Mapping) else 0,
                    )
                    mediation_threshold = min(360, max(40, 80 + max(0, respect) * 3 + max(0, trust)))
                    can_mediate = (
                        tournament_kind == "great_jianghu_tournament"
                        and day_theme == "private_negotiations_and_rivalry_mediation"
                        and has_prior and senior_pair and hostility >= 30
                    )
                    # Camp identity never creates a grievance merely because
                    # two factions coexist in the world. This *is* a real
                    # face-to-face encounter, however, so political-cultural
                    # pressure may make the meeting itself become a small
                    # recorded grievance. One bad exchange can seed rivalry;
                    # it cannot jump directly to feud or war.
                    tension_threshold = min(
                        900,
                        hostility * 8 + camp_pressure * 6
                        + (120 if hostility >= 20 else 0),
                    )
                    contact_threshold = (760 if tournament_kind == "great_jianghu_tournament" else 430) + (80 if same_camp else 0)
                    if can_mediate and stable_permille("tournament-mediation", tref, fa, fb) < mediation_threshold:
                        relation_event = "tournament_mediation"
                        mediated_contacts += 1
                    elif (hostility > 0 or camp_pressure > 0) and roll < tension_threshold:
                        relation_event = "tournament_tension"
                        tense_contacts += 1
                    elif has_prior or roll < min(920, contact_threshold):
                        relation_event = "tournament_contact"
                    else:
                        continue
                    apply_directed_relation_event(fa, fb, relation_event)
                    apply_directed_relation_event(fb, fa, relation_event)
                    meaningful_contacts += 1
                    if senior_pair:
                        senior_contacts += 1
                    if rep_a and rep_b and rep_a != rep_b:
                        severity = 500 if rep_a in leaders_a or rep_b in leaders_b else 350
                        social_state = _social_event(
                            social_state, observer_ref=rep_a, subject_ref=rep_b,
                            event_kind="conversation", severity_milli=severity, player_ref=player_ref,
                        )
                        social_state = _social_event(
                            social_state, observer_ref=rep_b, subject_ref=rep_a,
                            event_kind="conversation", severity_milli=severity, player_ref=player_ref,
                        )
                        new_person_contacts += 1
                if new_person_contacts:
                    writes[_SOCIAL_PATH] = social_state
                spectator_count = sum(len(set(row.get("spectator_refs", []))) for row in presence_by_faction.values())
                delegate_count = sum(len(set(row.get("entrant_refs", [])) | set(row.get("spectator_refs", []))) for row in presence_by_faction.values())
                leader_delegate_count = sum(len(set(row.get("leader_refs", []))) for row in presence_by_faction.values())
                senior_delegate_count = sum(len(set(row.get("senior_refs", [])) | set(row.get("leader_refs", []))) for row in presence_by_faction.values())
                attendance=fund_public_tournament_attendance(
                    tref,tournament_kind=tournament_kind,attendance_date=at_iso[:10],
                    delegate_count=delegate_count,
                )
                public_spectator_count=int(attendance["public_spectator_count"])
                public_spectator_overflow=int(attendance["public_spectator_overflow"])
                public_ticket_cash=int(attendance["public_ticket_cash"])
                venue_capacity=int(attendance["venue_capacity"])
                tournament=tournaments.get(tref,tournament)
                camp_counts: dict[str, int] = {}
                for fid in presence_by_faction:
                    camp = faction_camp(fid) or "unclassified"
                    camp_counts[camp] = camp_counts.get(camp, 0) + 1
                if isinstance(tournament, dict):
                    tournament["peak_delegate_count"] = max(max(0, int(tournament.get("peak_delegate_count", 0))), delegate_count)
                    tournament["peak_faction_count"] = max(max(0, int(tournament.get("peak_faction_count", 0))), len(presence_by_faction))
                    tournament["peak_public_spectator_count"] = max(max(0, int(tournament.get("peak_public_spectator_count", 0))), public_spectator_count)
                    tournaments[tref] = tournament
                    writes[_TOURNAMENTS_PATH] = tournament_state
                notice = {
                    "kind": "great_tournament_convergence" if tournament_kind == "great_jianghu_tournament" else "regional_tournament_convergence",
                    "tournament_ref": tref, "host_place_id": tournament.get("host_place_ref"),
                    "convergence_day": day_index, "convergence_days": day_count,
                    "convergence_theme": day_theme,
                    "entrant_count": len(registrations), "spectator_count": spectator_count,
                    "delegate_count": delegate_count, "leader_delegate_count": leader_delegate_count,
                    "senior_delegate_count": senior_delegate_count,
                    "public_spectator_count": public_spectator_count,
                    "public_spectator_overflow": public_spectator_overflow,
                    "public_ticket_cash": public_ticket_cash,
                    "prize_cash": max(0,int(tournament.get("prize_escrow_cash",0))),
                    "venue_capacity": venue_capacity,
                    "faction_count": len(presence_by_faction),
                    "camp_counts": dict(sorted(camp_counts.items())),
                    "meaningful_contacts": meaningful_contacts, "tense_contacts": tense_contacts,
                    "mediated_contacts": mediated_contacts, "senior_contacts": senior_contacts,
                    "opening_assembly": day_index == day_count,
                    "delivered_to_player": tournament_kind == "great_jianghu_tournament",
                    "requires_player_decision": False,
                }
                handoff = classify_handoff(notice)
                reviews.append({
                    "kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref,
                    "entrant_count": len(registrations), "spectator_count": spectator_count,
                    "delegate_count": delegate_count, "leader_delegate_count": leader_delegate_count,
                    "senior_delegate_count": senior_delegate_count,
                    "public_spectator_count": public_spectator_count,
                    "public_spectator_overflow": public_spectator_overflow,
                    "public_ticket_cash": public_ticket_cash,
                    "prize_cash": max(0,int(tournament.get("prize_escrow_cash",0))),
                    "faction_count": len(presence_by_faction),
                    "meaningful_contacts": meaningful_contacts, "tense_contacts": tense_contacts,
                    "mediated_contacts": mediated_contacts, "senior_contacts": senior_contacts, "handoff": handoff,
                })
                if handoff["class"] != "internal":
                    handoffs.append({**notice, "handoff": handoff})
                continue

            # Competition day. Large paid fields have no entrant cap. They
            # advance through finite venue throughput across as many real days
            # as needed, resuming the same bracket frontier each day.
            competition_date = str(event.get("due_at") or at_iso)[:10]
            if kind == "tournament_competition_continue":
                tref = str(event.get("tournament_ref") or event.get("owner_ref") or "")
            else:
                tref = _tref(kind, competition_date)
            tournament = tournaments.get(tref)
            if not isinstance(tournament, Mapping):
                reviews.append({"kind": kind, "event_id": event.get("event_id"), "result": "tournament_not_opened"})
                continue
            if tournament.get("status") == "registration_open":
                tournament = close_registration(tournament)
            tournament_kind = str(tournament.get("tournament_kind") or (kind if kind != "tournament_competition_continue" else ""))
            if tournament_kind not in {"regional_martial_tournament", "great_jianghu_tournament", "midyear_junior_tournament"}:
                reviews.append({"kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref, "result": "tournament_kind_invalid"})
                continue
            profile = tournament_event_profile(tournament_kind)
            delegation_rows = tournament.get("delegations", {}) if isinstance(tournament.get("delegations"), Mapping) else {}
            competition_delegate_count = sum(
                len(set(row.get("entrant_refs", [])) | set(row.get("spectator_refs", [])))
                for row in delegation_rows.values() if isinstance(row, Mapping)
            ) if isinstance(delegation_rows, Mapping) else 0
            competition_attendance = fund_public_tournament_attendance(
                tref, tournament_kind=tournament_kind, attendance_date=competition_date,
                delegate_count=competition_delegate_count,
            )
            tournament = tournaments.get(tref, tournament)
            public_spectator_count = max(0, int(competition_attendance.get("public_spectator_count", 0)))
            public_spectator_overflow = max(0, int(competition_attendance.get("public_spectator_overflow", 0)))
            public_ticket_cash = max(0, int(competition_attendance.get("public_ticket_cash", 0)))
            matches_this_session = max(1, int(profile.get("matches_per_competition_session", 16)))
            sessions_per_day = max(1, int(profile.get("competition_sessions_per_day", 1)))
            session_index = max(1, int(event.get("competition_session_index", 1)))
            max_match_exchanges = max(1, int(profile.get("max_exchanges_per_match", 96)))
            owner_map = _registration_owner_map(tournament)
            people: dict[str, Mapping[str, Any]] = {}
            doctrines: dict[str, Mapping[str, Any]] = {}
            blocked_at_competition = unavailable_person_refs()
            trip_rows = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
            tournament_trip_refs = {
                str(ref) for row in trip_rows.values() if isinstance(row, Mapping)
                and row.get("operation_kind") == "tournament_travel" and row.get("tournament_ref") == tref and row.get("status") == "at_tournament"
                for ref in row.get("participant_refs", []) if isinstance(ref, str)
            } if isinstance(trip_rows, Mapping) else set()
            blocked_at_competition -= tournament_trip_refs
            host_place = str(tournament.get("host_place_ref") or "")
            for ref, fid in sorted(owner_map.items()):
                _rpath, roster = load_roster(fid)
                match = next((p for p in roster.get("people", []) if isinstance(p, Mapping) and p.get("person_id") == ref), None)
                # A registration is not a month-long reservation.  If an entrant
                # is committed elsewhere or has physically left the host by
                # competition day, they are unavailable and forfeit normally.
                if isinstance(match, Mapping) and ref not in blocked_at_competition:
                    site = site_rows.get(str(match.get("location_ref")))
                    if not host_place or (isinstance(site, Mapping) and site.get("parent_place_ref") == host_place):
                        people[ref] = copy.deepcopy(dict(match))
                _fpath, faction = load_faction(fid)
                doctrines[fid] = faction.get("doctrine", {}) if isinstance(faction.get("doctrine"), Mapping) else {}
            advanced = advance_individual_competition(
                tournament, people=people, equipment_ledger=equipment_ledger,
                doctrines=doctrines, combats_state=combats_state,
                zone_ref=str(tournament.get("venue_site_ref") or tournament.get("host_place_ref") or "tournament_venue"),
                at_iso=at_iso, player_ref=player_ref or None,
                max_matches=matches_this_session, max_exchanges=max_match_exchanges, social_state=social_state,
            )
            # Keep one compact current-event accumulator for faction-wide
            # performance.  Every actual match win contributes one point to
            # the sponsoring institution.  This lets the final public result
            # show which factions demonstrated roster depth, not only which
            # single person won the championship, without persisting a
            # per-match historical ledger.
            live_after = dict(advanced["tournament_after"])
            existing_performance = tournament.get("faction_performance_points", {})
            faction_performance: dict[str, int] = {
                str(fid): max(0, int(points))
                for fid, points in existing_performance.items()
                if isinstance(fid, str)
            } if isinstance(existing_performance, Mapping) else {}
            for winner_ref, points in advanced.get("winner_points", {}).items():
                winner_faction = str(owner_map.get(str(winner_ref)) or "")
                if winner_faction and int(points) > 0:
                    faction_performance[winner_faction] = faction_performance.get(winner_faction, 0) + int(points)
            live_after["faction_performance_points"] = dict(sorted(faction_performance.items()))
            advanced["tournament_after"] = live_after
            # Only matches that actually occurred create sportsmanship evidence.
            # Forfeits and merely sharing a bracket do not manufacture trust.
            # A real witnessed match between existing/cross-camp rivals may
            # instead sharpen a bounded martial rivalry while still increasing
            # respect; one tournament fight cannot manufacture a feud or war.
            for pair in advanced.get("resolved_pairs", []):
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    continue
                fa = str(owner_map.get(str(pair[0])) or "")
                fb = str(owner_map.get(str(pair[1])) or "")
                if fa and fb and fa != fb:
                    edge_ab = next((
                        edge for edge in relation_index.get(fa, [])
                        if isinstance(edge, Mapping) and edge.get("to_faction") == fb
                    ), None)
                    edge_ba = next((
                        edge for edge in relation_index.get(fb, [])
                        if isinstance(edge, Mapping) and edge.get("to_faction") == fa
                    ), None)
                    hostility = max(
                        max(0, int(edge_ab.get("hostility", 0))) if isinstance(edge_ab, Mapping) else 0,
                        max(0, int(edge_ba.get("hostility", 0))) if isinstance(edge_ba, Mapping) else 0,
                    )
                    relation_event = tournament_match_relation_event(
                        faction_a=fa, faction_b=fb, tournament_ref=tref,
                        person_a=str(pair[0]), person_b=str(pair[1]), hostility=hostility,
                    )
                    apply_directed_relation_event(fa, fb, relation_event)
                    apply_directed_relation_event(fb, fa, relation_event)
            _write_tournament_people(owner_map, advanced["people_after"])
            social_state = copy.deepcopy(dict(advanced.get("social_state_after", social_state)))
            writes[_SOCIAL_PATH] = social_state
            equipment_ledger = copy.deepcopy(dict(advanced["equipment_ledger_after"]))
            combats_state = copy.deepcopy(dict(advanced["combats_state_after"]))
            combats = combats_state.setdefault("combats", {})
            if not isinstance(combats, dict):
                raise ValueError("jianghu combat state invalid")
            writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
            writes[_COMBATS_PATH] = combats_state
            public_audience = f"public:{host_place}" if host_place else "public:tournament"
            for ref, points in advanced.get("winner_points", {}).items():
                ref = str(ref); points = int(points)
                reputation_state = _reputation_after_points(reputation_state, ref, tournament_points=points)
                if points > 0:
                    reputation_state = apply_personal_fame_evidence(
                        reputation_state, audience_ref=public_audience, person_ref=ref,
                        evidence_kind="tournament_placement", delivered=True,
                    )
                    owner_faction = str(owner_map.get(ref) or "")
                    if owner_faction:
                        reputation_state = apply_faction_awareness_evidence(
                            reputation_state, audience_ref=public_audience, faction_ref=owner_faction,
                            evidence_kind="public_tournament", delivered=True,
                        )
                        reputation_state = apply_faction_reputation_evidence(
                            reputation_state, audience_ref=public_audience, faction_ref=owner_faction,
                            axis_deltas={"martial_respect": min(5, max(1, points))}, delivered=True,
                        )
            if advanced["waiting_for_player"]:
                live = dict(advanced["tournament_after"]); tournaments[tref] = live
                combat_ref = str(advanced.get("combat_ref") or "")
                pair = [str(x) for x in live.get("active_pair", []) if isinstance(x, str)]
                if combat_ref and len(pair) == 2:
                    resources = [("person", ref, str(owner_map.get(ref) or "")) for ref in pair]
                    commitments_state = reserve_resources(
                        commitments_state, resources=resources,
                        actor_ref=player_ref or pair[0], owner_ref=tref,
                        activity_ref=combat_ref, activity_kind="tournament_match",
                        started_at=at_iso, location_ref=str(live.get("venue_site_ref") or live.get("host_place_ref") or ""),
                    )
                    for fid in sorted(set(str(owner_map.get(ref) or "") for ref in pair if owner_map.get(ref))):
                        pause_people_for_commitment(fid, [ref for ref in pair if owner_map.get(ref) == fid]); writes[_TOURNAMENTS_PATH] = tournament_state; writes[_REPUTATION_PATH] = reputation_state
                notice = {"kind": "tournament_match_due", "tournament_ref": tref, "combat_ref": advanced.get("combat_ref"), "requires_player_decision": True, "delivered_to_player": True}
                handoff = classify_handoff(notice); reviews.append({"kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref, "result": "awaiting_player_match", "handoff": handoff}); handoffs.append({**notice, "handoff": handoff})
                continue
            if advanced.get("continuation_required"):
                live = dict(advanced["tournament_after"])
                completed_days = max(0, int(tournament.get("competition_days_completed", 0)))
                if session_index < sessions_per_day:
                    next_session = session_index + 1
                    next_due = at + timedelta(hours=2)
                    result_kind = "competition_session_complete"
                else:
                    live["competition_days_completed"] = completed_days + 1
                    next_session = 1
                    next_due = (at + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
                    result_kind = "competition_day_complete"
                tournaments[tref] = live
                pending_one_off_events.append({
                    "event_id": f"tournament_competition_continue:{tref}:{next_due.isoformat()}",
                    "kind": "tournament_competition_continue",
                    "due_at": next_due.isoformat(),
                    "owner_ref": tref,
                    "tournament_ref": tref,
                    "competition_session_index": next_session,
                    "requires_player_decision": False,
                })
                writes[_TOURNAMENTS_PATH] = tournament_state
                writes[_REPUTATION_PATH] = reputation_state
                reviews.append({
                    "kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref,
                    "result": result_kind,
                    "competition_day": completed_days + 1,
                    "competition_session": session_index,
                    "sessions_per_day": sessions_per_day,
                    "matches_resolved": int(advanced.get("matches_resolved_count", 0)),
                    "public_spectator_count": public_spectator_count,
                    "public_spectator_overflow": public_spectator_overflow,
                    "public_ticket_cash": public_ticket_cash,
                    "prize_cash": max(0,int(live.get("prize_escrow_cash",0))),
                    "next_competition_at": next_due.isoformat(),
                })
                continue
            champion = advanced.get("champion_ref")
            live = dict(advanced["tournament_after"])
            competition_days = max(0, int(tournament.get("competition_days_completed", 0))) + 1
            prize = max(0, int(live.get("prize_escrow_cash", 0)))
            champion_faction_prize = 0
            champion_personal_prize = 0
            performance_points = live.get("faction_performance_points", {}) if isinstance(live.get("faction_performance_points"), Mapping) else {}
            standings_limit = max(0, int(profile.get("public_faction_standings_count", 0)))
            top_faction_performance = faction_performance_standings(
                performance_points, owner_map, limit=standings_limit,
            )
            delegation_rows = live.get("delegations", {}) if isinstance(live.get("delegations"), Mapping) else {}
            attending_factions = {
                str(fid) for fid, row in delegation_rows.items()
                if isinstance(fid, str) and isinstance(row, Mapping)
                and max(0, int(row.get("present_count", 0))) > 0
            }
            attending_factions.update(str(fid) for fid in owner_map.values() if isinstance(fid, str) and fid)

            # The Great Tournament exists to establish institutional martial
            # standing in front of the Jianghu, not merely an individual
            # champion.  Deliver the compact top-faction table only to public
            # audiences and institutions that actually had a delegation there.
            # This changes current reputation/awareness; it does not create a
            # giant per-match witness history or make absent factions omniscient.
            max_performance_respect = max(0, int(profile.get("faction_performance_max_martial_respect", 0)))
            recognized_performance: list[dict[str, Any]] = []
            for rank_index, standing in enumerate(top_faction_performance):
                faction_ref = str(standing.get("faction_ref") or "")
                if not faction_ref:
                    continue
                rank = rank_index + 1
                if max_performance_respect > 0 and standings_limit > 0:
                    delta = max(1, max_performance_respect - ((rank - 1) * max_performance_respect // max(1, standings_limit)))
                else:
                    delta = 0
                enriched = dict(standing)
                enriched["rank"] = rank
                enriched["camp"] = faction_camp(faction_ref) or "unclassified"
                enriched["martial_respect_delta"] = delta
                recognized_performance.append(enriched)
                if delta <= 0:
                    continue
                reputation_state = apply_faction_awareness_evidence(
                    reputation_state, audience_ref=public_audience, faction_ref=faction_ref,
                    evidence_kind="public_tournament", delivered=True,
                )
                reputation_state = apply_faction_reputation_evidence(
                    reputation_state, audience_ref=public_audience, faction_ref=faction_ref,
                    axis_deltas={"martial_respect": delta}, delivered=True,
                )
                for audience_fid in sorted(attending_factions):
                    faction_audience = f"faction:{audience_fid}"
                    reputation_state = apply_faction_awareness_evidence(
                        reputation_state, audience_ref=faction_audience, faction_ref=faction_ref,
                        evidence_kind="public_tournament", delivered=True,
                    )
                    reputation_state = apply_faction_reputation_evidence(
                        reputation_state, audience_ref=faction_audience, faction_ref=faction_ref,
                        axis_deltas={"martial_respect": delta}, delivered=True,
                    )
            top_faction_performance = recognized_performance
            placement_awards: list[dict[str, Any]] = []
            payout_rows = tournament_placement_payouts(live)
            if prize > 0 and not payout_rows:
                raise ValueError("funded tournament completed without earned placements")
            faction_share = max(0, min(1000, int(live.get("placement_faction_share_permille", 700))))
            personal_share = max(0, min(1000, int(live.get("placement_personal_share_permille", 300))))
            if faction_share + personal_share != 1000:
                raise ValueError("tournament placement payout shares invalid")
            total_prize_paid = 0
            placement_reputation_points = {"first": 4, "second": 3, "third": 3, "fourth": 2}
            for award in payout_rows:
                place = str(award.get("place") or "")
                ref = str(award.get("entrant_ref") or "")
                gross = max(0, int(award.get("cash", 0)))
                if not place or not ref or gross <= 0:
                    continue
                fid = str(owner_map.get(ref) or "")
                faction_cash = 0
                personal_cash = gross
                if fid:
                    faction_cash = gross * faction_share // 1000
                    personal_cash = gross - faction_cash
                    fpath, faction = load_faction(fid)
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + faction_cash
                    writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
                    rpath, roster = load_roster(fid)
                    rows = roster.get("people", []) if isinstance(roster, Mapping) else []
                    if isinstance(rows, list):
                        for i, raw in enumerate(rows):
                            if isinstance(raw, Mapping) and raw.get("person_id") == ref:
                                person = copy.deepcopy(dict(raw))
                                person["personal_cash"] = max(0, int(person.get("personal_cash", 0))) + personal_cash
                                rows[i] = person
                                break
                        writes[rpath] = roster; roster_cache[fid] = (rpath, roster)
                else:
                    # An unaffiliated entrant has no faction treasury share;
                    # preserve the full earned placement prize as personal cash.
                    personal_cash = gross
                    owner_fid = next((
                        owner for owner, (_path, roster) in roster_cache.items()
                        if isinstance(roster, Mapping) and any(
                            isinstance(row, Mapping) and row.get("person_id") == ref
                            for row in roster.get("people", [])
                        )
                    ), None)
                    if owner_fid:
                        rpath, roster = load_roster(owner_fid)
                        rows = roster.get("people", []) if isinstance(roster, Mapping) else []
                        for i, raw in enumerate(rows if isinstance(rows, list) else []):
                            if isinstance(raw, Mapping) and raw.get("person_id") == ref:
                                person = copy.deepcopy(dict(raw))
                                person["personal_cash"] = max(0, int(person.get("personal_cash", 0))) + personal_cash
                                rows[i] = person
                                break
                        writes[rpath] = roster; roster_cache[owner_fid] = (rpath, roster)
                total_prize_paid += gross
                evidence_kind = "tournament_win" if place == "first" else "tournament_placement"
                reputation_state = _reputation_after_points(
                    reputation_state, ref,
                    tournament_points=max(1, int(placement_reputation_points.get(place, 1))),
                )
                reputation_state = apply_personal_fame_evidence(
                    reputation_state, audience_ref=public_audience, person_ref=ref,
                    evidence_kind=evidence_kind, delivered=True,
                )
                placement_awards.append({
                    "place": place, "entrant_ref": ref, "faction_ref": fid or None,
                    "gross_prize_cash": gross, "faction_prize_cash": faction_cash,
                    "personal_prize_cash": personal_cash,
                })
            if total_prize_paid != prize:
                raise ValueError("tournament prize escrow not fully paid to placements")
            live["prize_escrow_cash"] = 0
            champion_award = next((row for row in placement_awards if row.get("place") == "first"), None)
            if isinstance(champion_award, Mapping):
                champion_faction_prize = max(0, int(champion_award.get("faction_prize_cash", 0)))
                champion_personal_prize = max(0, int(champion_award.get("personal_prize_cash", 0)))
            if isinstance(champion, str) and champion:
                champion_faction = str(owner_map.get(champion) or "")
                if champion_faction:
                    reputation_state = apply_faction_awareness_evidence(
                        reputation_state, audience_ref=public_audience, faction_ref=champion_faction,
                        evidence_kind="public_tournament", delivered=True,
                    )
                    reputation_state = apply_faction_reputation_evidence(
                        reputation_state, audience_ref=public_audience, faction_ref=champion_faction,
                        axis_deltas={"martial_respect": 6}, delivered=True,
                    )
                    for audience_fid in sorted(attending_factions):
                        faction_audience = f"faction:{audience_fid}"
                        reputation_state = apply_personal_fame_evidence(
                            reputation_state, audience_ref=faction_audience, person_ref=champion,
                            evidence_kind="tournament_win", delivered=True,
                        )
                        reputation_state = apply_faction_awareness_evidence(
                            reputation_state, audience_ref=faction_audience, faction_ref=champion_faction,
                            evidence_kind="public_tournament", delivered=True,
                        )
                        reputation_state = apply_faction_reputation_evidence(
                            reputation_state, audience_ref=faction_audience, faction_ref=champion_faction,
                            axis_deltas={"martial_respect": 6}, delivered=True,
                        )
            returning_travelers = schedule_tournament_returns(tref)
            tournament_kind = str(live.get("tournament_kind") or kind)
            tournaments.pop(tref, None)
            writes[_TOURNAMENTS_PATH] = tournament_state; writes[_REPUTATION_PATH] = reputation_state
            notice = {
                "kind": "tournament_result", "tournament_ref": tref,
                "champion_ref": champion, "prize_cash": prize,
                "placements": dict(live.get("placements", {})) if isinstance(live.get("placements"), Mapping) else {},
                "placement_awards": placement_awards,
                "entry_fees_collected_cash": max(0, int(live.get("entry_fees_collected_cash", 0))),
                "faction_delegate_ticket_cash_collected": max(0, int(live.get("delegate_ticket_cash_collected", 0))),
                "public_ticket_cash_collected": max(0, int(live.get("public_ticket_cash_collected", 0))),
                "champion_faction_prize_cash": champion_faction_prize,
                "champion_personal_prize_cash": champion_personal_prize,
                "attending_faction_count": max(0, int(live.get("peak_faction_count", 0))),
                "peak_delegate_count": max(0, int(live.get("peak_delegate_count", 0))),
                "peak_public_spectator_count": max(0, int(live.get("peak_public_spectator_count", 0))),
                "top_faction_performance": top_faction_performance,
                "performance_witness_faction_count": len(attending_factions),
                "returning_travelers": returning_travelers,
                "delivered_to_player": True, "requires_player_decision": False,
            }
            handoff = classify_handoff(notice); reviews.append({"kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref, "champion_ref": champion, "handoff": handoff}); handoffs.append({**notice, "handoff": handoff})

    return {
        "commitments_state": commitments_state,
        "reputation_state": reputation_state,
        "social_state": social_state,
        "equipment_ledger": equipment_ledger,
        "combats_state": combats_state,
        "tournament_state": tournament_state,
        "deployments_state": deployments_state,
        "newly_dead_refs": sorted(newly_dead_refs),
    }


__all__ = ["settle_tournament_frontier"]
