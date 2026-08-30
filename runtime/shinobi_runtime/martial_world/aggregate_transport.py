"""Aggregate finite transport capacity for ordinary Jianghu logistics.

Routine horses, pack trains, carts and handlers are not persistent identities and
are not counted as individual world objects. Factions hold only pooled transport
capacity. Civilian settlements derive pooled commercial capacity from population.
Route movements reserve the capacity they actually occupy, preventing double use
without creating one object per animal, wagon, or driver.
"""
from __future__ import annotations
from typing import Any, Mapping

FREIGHT_SERVICE_UNIT_KG = 120
CIVILIAN_FREIGHT_KG_PER_100_PEOPLE = 120
CIVILIAN_CREW_PER_200_PEOPLE = 1
CREW_FREIGHT_KG_PER_PERSON = 720

def faction_transport_capacity(inventory: Mapping[str, Any]) -> dict[str, int]:
    capacity = inventory.get("transport_capacity", {}) if isinstance(inventory.get("transport_capacity"), Mapping) else {}
    return {"rider_slots": max(0, int(capacity.get("rider_slots", 0))), "freight_capacity_kg": max(0, int(capacity.get("freight_capacity_kg", 0)))}

def freight_service_units(freight_capacity_kg: int) -> int:
    kg=max(0,int(freight_capacity_kg)); return 0 if kg<=0 else (kg+FREIGHT_SERVICE_UNIT_KG-1)//FREIGHT_SERVICE_UNIT_KG

def civilian_transport_capacity(place_population: int) -> dict[str, int]:
    pop=max(0,int(place_population))
    return {"freight_capacity_kg": max(0,(pop//100)*CIVILIAN_FREIGHT_KG_PER_100_PEOPLE), "crew_capacity": max(0,pop//200)}

def freight_crew_required(freight_kg: int) -> int:
    kg=max(0,int(freight_kg)); return 0 if kg<=0 else (kg+CREW_FREIGHT_KG_PER_PERSON-1)//CREW_FREIGHT_KG_PER_PERSON

def active_reserved_capacity(route_operations: Mapping[str, Any], *, provider_kind: str, provider_ref: str) -> dict[str, int]:
    movements=route_operations.get("movements",{}) if isinstance(route_operations,Mapping) else {}
    freight=riders=crew=0
    if not isinstance(movements,Mapping): return {"freight_capacity_kg":0,"rider_slots":0,"crew_capacity":0}
    for row in movements.values():
        if not isinstance(row,Mapping) or str(row.get("status") or "active") in {"completed","cancelled","failed"}: continue
        reservation=row.get("transport_reservation",{}) if isinstance(row.get("transport_reservation"),Mapping) else {}
        if str(reservation.get("provider_kind") or "")!=provider_kind or str(reservation.get("provider_ref") or "")!=provider_ref: continue
        freight+=max(0,int(reservation.get("freight_capacity_kg",0))); riders+=max(0,int(reservation.get("rider_slots",0))); crew+=max(0,int(reservation.get("crew_capacity",0)))
    return {"freight_capacity_kg":freight,"rider_slots":riders,"crew_capacity":crew}

def faction_available_capacity(inventory: Mapping[str, Any], route_operations: Mapping[str, Any], *, faction_ref: str) -> dict[str, int]:
    cap=faction_transport_capacity(inventory); used=active_reserved_capacity(route_operations,provider_kind="faction_pool",provider_ref=faction_ref)
    return {"freight_capacity_kg":max(0,cap["freight_capacity_kg"]-used["freight_capacity_kg"]),"rider_slots":max(0,cap["rider_slots"]-used["rider_slots"])}

def civilian_available_capacity(*, place_ref: str, place_population: int, route_operations: Mapping[str, Any]) -> dict[str, int]:
    cap=civilian_transport_capacity(place_population); used=active_reserved_capacity(route_operations,provider_kind="civilian_logistics",provider_ref=place_ref)
    return {"freight_capacity_kg":max(0,cap["freight_capacity_kg"]-used["freight_capacity_kg"]),"crew_capacity":max(0,cap["crew_capacity"]-used["crew_capacity"])}

def make_transport_reservation(*, provider_kind: str, provider_ref: str, freight_capacity_kg: int=0, rider_slots: int=0, crew_capacity: int=0) -> dict[str, Any]:
    if provider_kind not in {"faction_pool","civilian_logistics"}: raise ValueError("unsupported aggregate transport provider")
    out={"provider_kind":provider_kind,"provider_ref":str(provider_ref)}
    if freight_capacity_kg>0: out["freight_capacity_kg"]=int(freight_capacity_kg)
    if rider_slots>0: out["rider_slots"]=int(rider_slots)
    if crew_capacity>0: out["crew_capacity"]=int(crew_capacity)
    return out

__all__=["FREIGHT_SERVICE_UNIT_KG","active_reserved_capacity","civilian_available_capacity","civilian_transport_capacity","faction_available_capacity","faction_transport_capacity","freight_crew_required","freight_service_units","make_transport_reservation"]
