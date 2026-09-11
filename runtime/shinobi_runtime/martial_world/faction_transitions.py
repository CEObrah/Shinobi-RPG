"""Conserved institutional transition helpers for current Jianghu factions."""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping, Sequence


def transfer_inventory(
    source: Mapping[str, Any], destination: Mapping[str, Any], *,
    food_ration_days: int = 0, requested: Mapping[str, Any] | None = None,
    transfer_all: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Move conserved portable inventory between faction owners."""
    src = copy.deepcopy(dict(source)); dst = copy.deepcopy(dict(destination)); moved: dict[str, Any] = {}
    food = max(0, int(src.get("food_ration_days", 0))) if transfer_all else max(0, int(food_ration_days))
    if food > max(0, int(src.get("food_ration_days", 0))):
        raise ValueError("insufficient faction food")
    if food:
        src["food_ration_days"] = max(0, int(src.get("food_ration_days", 0))) - food
        dst["food_ration_days"] = max(0, int(dst.get("food_ration_days", 0))) + food
        moved["food_ration_days"] = food
    buckets = ("equipment", "raw_materials", "herbs", "medicines", "transport_capacity")
    request = requested if isinstance(requested, Mapping) else {}
    unknown = set(request) - set(buckets)
    if unknown:
        raise ValueError("unknown inventory transfer bucket")
    for bucket in buckets:
        source_bucket = src.get(bucket, {}) if isinstance(src.get(bucket), Mapping) else {}
        destination_bucket = dst.setdefault(bucket, {})
        if not isinstance(destination_bucket, dict):
            raise ValueError("invalid destination inventory")
        if transfer_all:
            wants = {str(key): max(0, int(value)) for key, value in source_bucket.items()}
        else:
            raw = request.get(bucket, {})
            if raw is None:
                raw = {}
            if not isinstance(raw, Mapping):
                raise ValueError("invalid inventory transfer bucket")
            wants = {str(key): max(0, int(value)) for key, value in raw.items()}
        bucket_moved: dict[str, int] = {}
        mutable_source = dict(source_bucket)
        for key, qty in wants.items():
            available = max(0, int(mutable_source.get(key, 0)))
            if qty > available:
                raise ValueError("insufficient faction inventory")
            if qty <= 0:
                continue
            remaining = available - qty
            if remaining:
                mutable_source[key] = remaining
            else:
                mutable_source.pop(key, None)
            destination_bucket[key] = max(0, int(destination_bucket.get(key, 0))) + qty
            bucket_moved[key] = qty
        src[bucket] = mutable_source
        if bucket_moved:
            moved[bucket] = bucket_moved
    return src, dst, moved



def transfer_holdings(
    source: Mapping[str, Any], destination: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Move conserved institutional holdings into one surviving owner.

    ``holdings`` are physical/current property facts, unlike
    ``enterprise_scale`` which describes the living institution's operating
    organization. Rural land therefore adds exactly and held record identities
    union exactly, while the source is cleared.
    """
    src = copy.deepcopy(dict(source)); dst = copy.deepcopy(dict(destination))
    source_holdings = src.get("holdings", {}) if isinstance(src.get("holdings"), Mapping) else {}
    destination_holdings = dst.get("holdings", {}) if isinstance(dst.get("holdings"), Mapping) else {}
    moved: dict[str, Any] = {}

    rural_land = max(0, int(source_holdings.get("rural_land_mu", 0)))
    source_records = [str(x) for x in source_holdings.get("record_refs", []) if isinstance(x, str) and x] if isinstance(source_holdings.get("record_refs"), list) else []
    destination_records = [str(x) for x in destination_holdings.get("record_refs", []) if isinstance(x, str) and x] if isinstance(destination_holdings.get("record_refs"), list) else []

    merged_holdings = copy.deepcopy(dict(destination_holdings))
    if rural_land:
        merged_holdings["rural_land_mu"] = max(0, int(merged_holdings.get("rural_land_mu", 0))) + rural_land
        moved["rural_land_mu"] = rural_land
    merged_records = sorted(set(destination_records) | set(source_records))
    if merged_records:
        merged_holdings["record_refs"] = merged_records
    elif "record_refs" in merged_holdings:
        merged_holdings.pop("record_refs", None)
    if source_records:
        moved["record_refs"] = sorted(set(source_records))

    dst["holdings"] = merged_holdings
    src["holdings"] = {}
    return src, dst, moved


def retire_organizational_scale(faction: Mapping[str, Any]) -> dict[str, Any]:
    """Clear living-institution operating scale when that institution ends.

    Enterprise levels/facilities may be captured as physical property. Operating
    scale is staffing/capital organization and is not transferable loot.
    """
    out = copy.deepcopy(dict(faction))
    out["enterprise_scale"] = {}
    return out

def reconcile_family_transition(
    family: Mapping[str, Any], *, moved_refs: Sequence[str], source_faction_ref: str,
    target_faction_ref: str | None,
) -> dict[str, Any]:
    """Move current family institutional refs without changing durable kinship."""
    out = copy.deepcopy(dict(family)); moved = {str(x) for x in moved_refs if isinstance(x, str)}
    target = str(target_faction_ref or "")
    marriages = out.get("marriages", {})
    if isinstance(marriages, dict):
        for key, raw in list(marriages.items()):
            if not isinstance(raw, Mapping):
                continue
            row = copy.deepcopy(dict(raw)); spouses = {str(x) for x in row.get("spouse_refs", []) if isinstance(x, str)}
            if row.get("faction_ref") != source_faction_ref and source_faction_ref not in (row.get("faction_refs", []) if isinstance(row.get("faction_refs"), list) else []):
                continue
            moved_spouses = spouses & moved
            if not moved_spouses:
                continue
            if target and moved_spouses == spouses:
                row["faction_ref"] = target; row.pop("faction_refs", None)
            elif target:
                row.pop("faction_ref", None); row["faction_refs"] = sorted({source_faction_ref, target})
            elif moved_spouses == spouses:
                row.pop("faction_ref", None); row.pop("faction_refs", None)
            else:
                # One spouse became independent/civic while the other remains
                # in the source faction. Keep only the surviving institutional
                # affiliation and let exact-person routing distinguish owners.
                row.pop("faction_ref", None); row["faction_refs"] = [source_faction_ref]
            marriages[key] = row
    claims = out.get("succession_claims", {})
    if isinstance(claims, dict):
        for key, raw in list(claims.items()):
            if not isinstance(raw, Mapping) or raw.get("faction_ref") != source_faction_ref or str(raw.get("person_ref") or "") not in moved:
                continue
            if target:
                row = copy.deepcopy(dict(raw)); row["faction_ref"] = target; claims[key] = row
            else:
                claims.pop(key, None)
    households = out.get("households", {})
    if isinstance(households, dict):
        for hid, raw in list(households.items()):
            if not isinstance(raw, Mapping) or raw.get("faction_ref") != source_faction_ref:
                continue
            row = copy.deepcopy(dict(raw)); members = [str(x) for x in row.get("member_refs", []) if isinstance(x, str)]
            moved_members = [ref for ref in members if ref in moved]; stayed = [ref for ref in members if ref not in moved]
            if not moved_members:
                continue
            if not stayed:
                if target:
                    row["faction_ref"] = target; households[hid] = row
                else:
                    households.pop(hid, None)
                continue
            row["member_refs"] = stayed
            if str(row.get("head_ref") or "") not in stayed:
                row["head_ref"] = sorted(stayed)[0]
            households[hid] = row
            if target:
                digest = hashlib.sha256((hid + "|" + target + "|" + ",".join(sorted(moved_members))).encode("utf-8")).hexdigest()[:16]
                new_hid = f"household:transition:{digest}"
                households[new_hid] = {
                    "faction_ref": target,
                    "head_ref": str(raw.get("head_ref")) if str(raw.get("head_ref")) in moved_members else sorted(moved_members)[0],
                    "member_refs": sorted(moved_members),
                    "residence_ref": str(raw.get("residence_ref") or ""),
                    "status": str(raw.get("status") or "active"),
                }
    return out


def retire_faction_relations(relations: Mapping[str, Any], faction_ref: str) -> dict[str, Any]:
    out = copy.deepcopy(dict(relations)); edges = out.get("edges", [])
    if isinstance(edges, list):
        out["edges"] = [
            copy.deepcopy(dict(row)) for row in edges
            if isinstance(row, Mapping) and row.get("from_faction") != faction_ref and row.get("to_faction") != faction_ref
        ]
    coalitions = out.get("coalitions", {})
    if isinstance(coalitions, dict):
        for ref, raw in list(coalitions.items()):
            if not isinstance(raw, Mapping):
                coalitions.pop(ref, None); continue
            members = {str(x) for x in raw.get("member_faction_refs", []) if isinstance(x, str)}
            if faction_ref in members or str(raw.get("target_faction_ref") or "") == faction_ref:
                coalitions.pop(ref, None)
        if not coalitions:
            out.pop("coalitions", None)
    return out


def primary_estate_projection(faction: Mapping[str, Any], *, acquired_at: str) -> tuple[str, dict[str, Any]] | None:
    site = str(faction.get("local_site_ref") or "")
    if not site:
        return None
    return site, {
        "source_faction_ref": str(faction.get("faction_id") or ""),
        "acquired_at": acquired_at,
        "status": "occupied",
        "headquarters_place_ref": str(faction.get("headquarters") or ""),
        "buildings": copy.deepcopy(dict(faction.get("buildings", {}))) if isinstance(faction.get("buildings"), Mapping) else {},
        "infrastructure": copy.deepcopy(dict(faction.get("infrastructure", {}))) if isinstance(faction.get("infrastructure"), Mapping) else {},
        "enterprises": copy.deepcopy(dict(faction.get("enterprises", {}))) if isinstance(faction.get("enterprises"), Mapping) else {},
    }


__all__ = ["primary_estate_projection", "reconcile_family_transition", "retire_faction_relations", "retire_organizational_scale", "transfer_holdings", "transfer_inventory"]
