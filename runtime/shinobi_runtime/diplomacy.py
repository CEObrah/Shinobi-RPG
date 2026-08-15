"""Typed, read-only diplomatic policy queries.

The diplomacy registry owns persisted agreements.  Domain reducers import these
helpers to enforce those agreements at the actual downstream decision boundary
rather than copying treaty logic into prose or UI projections.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence


DIPLOMACY_PATH = "state/reg/diplomacy.json"

# These treaty kinds make a state-authorized initiation of hostilities against
# the bound counterparty unlawful while the agreement remains active.  A party
# can terminate an agreement first; the runtime does not silently breach it.
_PAIR_HOSTILITY_BARRIERS = {
    "nonaggression",
    "alliance",
    "ceasefire_framework",
}


def active_agreements(registry: Mapping[str, Any], agreement_type: Optional[str] = None) -> list[tuple[str, Mapping[str, Any]]]:
    agreements = registry.get("agreements") if isinstance(registry, Mapping) else None
    if not isinstance(agreements, Mapping):
        return []
    result: list[tuple[str, Mapping[str, Any]]] = []
    for agreement_ref, row in sorted(agreements.items()):
        if not isinstance(agreement_ref, str) or not isinstance(row, Mapping) or row.get("status") != "active":
            continue
        if agreement_type is not None and row.get("agreement_type") != agreement_type:
            continue
        result.append((agreement_ref, row))
    return result


def _party_set(row: Mapping[str, Any]) -> set[str]:
    raw = row.get("party_refs")
    return {ref for ref in raw if isinstance(ref, str) and ref} if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)) else set()


def _provisions(row: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = row.get("provisions")
    return raw if isinstance(raw, Mapping) else {}


def hostility_barrier(
    registry: Mapping[str, Any], *, initiator_refs: Iterable[str], target_refs: Iterable[str]
) -> Optional[tuple[str, str, str, str]]:
    """Return the first active treaty that forbids a state-level hostile start."""
    initiators = {ref for ref in initiator_refs if isinstance(ref, str) and ref}
    targets = {ref for ref in target_refs if isinstance(ref, str) and ref}
    if not initiators or not targets:
        return None
    for agreement_ref, row in active_agreements(registry):
        kind = row.get("agreement_type")
        parties = _party_set(row)
        if kind in _PAIR_HOSTILITY_BARRIERS:
            for initiator in sorted(initiators & parties):
                for target in sorted(targets & parties):
                    if initiator != target:
                        return agreement_ref, str(kind), initiator, target
        provisions = _provisions(row)
        if kind == "client_state":
            patron = provisions.get("patron_ref")
            client = provisions.get("client_ref")
            pair = {patron, client}
            for initiator in sorted(initiators):
                for target in sorted(targets):
                    if initiator != target and {initiator, target} == pair:
                        return agreement_ref, str(kind), initiator, target
        elif kind == "guarantee":
            guarantor = provisions.get("guarantor_ref")
            protected = provisions.get("protected_ref")
            if guarantor in initiators and protected in targets:
                return agreement_ref, str(kind), str(guarantor), str(protected)
    return None


def defense_obligation_specs(
    registry: Mapping[str, Any], *, initiator_refs: Iterable[str], target_refs: Iterable[str], conflict_ref: str
) -> list[dict[str, str]]:
    """Derive exact mutual-defense/guarantee obligations from a new attack.

    The returned records are deterministic specifications.  The conflict reducer
    persists them as ordinary obligation commitments so they participate in the
    same due/overdue lifecycle as other campaign obligations.
    """
    initiators = {ref for ref in initiator_refs if isinstance(ref, str) and ref}
    targets = {ref for ref in target_refs if isinstance(ref, str) and ref}
    all_conflict = initiators | targets
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for agreement_ref, row in active_agreements(registry):
        kind = row.get("agreement_type")
        if kind == "alliance":
            parties = _party_set(row)
            attacked = sorted(targets & parties)
            for beneficiary in attacked:
                for obligor in sorted(parties - {beneficiary}):
                    if obligor in initiators or obligor in all_conflict:
                        continue
                    key = (agreement_ref, obligor, beneficiary)
                    if key in seen:
                        continue
                    seen.add(key)
                    result.append({
                        "agreement_ref": agreement_ref,
                        "agreement_type": "alliance",
                        "obligor_ref": obligor,
                        "beneficiary_ref": beneficiary,
                        "conflict_ref": conflict_ref,
                    })
        elif kind == "guarantee":
            provisions = _provisions(row)
            guarantor = provisions.get("guarantor_ref")
            protected = provisions.get("protected_ref")
            if (
                isinstance(guarantor, str) and guarantor
                and isinstance(protected, str) and protected in targets
                and guarantor not in initiators and guarantor not in all_conflict
            ):
                key = (agreement_ref, guarantor, protected)
                if key not in seen:
                    seen.add(key)
                    result.append({
                        "agreement_ref": agreement_ref,
                        "agreement_type": "guarantee",
                        "obligor_ref": guarantor,
                        "beneficiary_ref": protected,
                        "conflict_ref": conflict_ref,
                    })
        elif kind == "client_state":
            provisions = _provisions(row)
            patron = provisions.get("patron_ref")
            client = provisions.get("client_ref")
            pairs = []
            if isinstance(client, str) and client in targets and isinstance(patron, str):
                pairs.append((patron, client))
            if isinstance(patron, str) and patron in targets and isinstance(client, str):
                pairs.append((client, patron))
            for obligor, beneficiary in pairs:
                if obligor in initiators or obligor in all_conflict:
                    continue
                key = (agreement_ref, obligor, beneficiary)
                if key in seen:
                    continue
                seen.add(key)
                result.append({
                    "agreement_ref": agreement_ref,
                    "agreement_type": "client_state",
                    "obligor_ref": obligor,
                    "beneficiary_ref": beneficiary,
                    "conflict_ref": conflict_ref,
                })
    return result


def client_state_access_basis(
    registry: Mapping[str, Any], *, force_owner_ref: str, sovereign_ref: Optional[str], administration_ref: Optional[str]
) -> Optional[str]:
    """A patron has strategic access to its active client state's jurisdiction."""
    for agreement_ref, row in active_agreements(registry, "client_state"):
        provisions = _provisions(row)
        patron = provisions.get("patron_ref")
        client = provisions.get("client_ref")
        if force_owner_ref == patron and client in (sovereign_ref, administration_ref):
            return f"agreement:{agreement_ref}:client_state_access"
    return None


def border_route_restriction(
    registry: Mapping[str, Any], *, force_owner_ref: str, sovereign_ref: Optional[str], administration_ref: Optional[str], destination_ref: str, route_ref: str
) -> Optional[tuple[str, bool]]:
    """Return (agreement_ref, allowed) for an applicable active border treaty."""
    destination_parties = {ref for ref in (sovereign_ref, administration_ref) if isinstance(ref, str) and ref}
    if not destination_parties:
        return None
    for agreement_ref, row in active_agreements(registry, "border"):
        parties = _party_set(row)
        if force_owner_ref not in parties or not (parties & destination_parties):
            continue
        provisions = _provisions(row)
        place_refs = provisions.get("place_refs")
        route_refs = provisions.get("route_refs")
        if isinstance(place_refs, Sequence) and not isinstance(place_refs, (str, bytes, bytearray)) and place_refs and destination_ref not in place_refs:
            continue
        allowed_routes = {ref for ref in route_refs if isinstance(ref, str)} if isinstance(route_refs, Sequence) and not isinstance(route_refs, (str, bytes, bytearray)) else set()
        return agreement_ref, (not allowed_routes or route_ref in allowed_routes)
    return None


def trade_tariff_multiplier_milli(
    registry: Mapping[str, Any], *, taxing_party_refs: Iterable[str], commerce_party_refs: Iterable[str], place_ref: str, route_ref: str
) -> tuple[int, list[str]]:
    """Return the strongest applicable treaty tariff multiplier and its bases.

    1000 means no treaty adjustment.  Trade agreements default to 750 (a 25%
    route-tax reduction) unless unanimously negotiated provisions specify a
    different 0..1000 multiplier.
    """
    taxing = {ref for ref in taxing_party_refs if isinstance(ref, str) and ref}
    commerce = {ref for ref in commerce_party_refs if isinstance(ref, str) and ref}
    if not taxing or not commerce:
        return 1000, []
    best = 1000
    refs: list[str] = []
    for agreement_ref, row in active_agreements(registry, "trade"):
        parties = _party_set(row)
        if not (parties & taxing) or not (parties & commerce):
            continue
        provisions = _provisions(row)
        places = provisions.get("place_refs")
        routes = provisions.get("route_refs")
        if isinstance(places, Sequence) and not isinstance(places, (str, bytes, bytearray)) and places and place_ref not in places:
            continue
        if isinstance(routes, Sequence) and not isinstance(routes, (str, bytes, bytearray)) and routes and route_ref not in routes:
            continue
        raw = provisions.get("tariff_multiplier_milli", 750)
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 1000:
            continue
        if raw < best:
            best = raw
            refs = [agreement_ref]
        elif raw == best and raw < 1000:
            refs.append(agreement_ref)
    return best, sorted(set(refs))


def treaty_obligation_policy(
    registry: Mapping[str, Any],
    conflict_registry: Mapping[str, Any],
    *,
    agreement_ref: str,
    conflict_ref: str,
    obligor_ref: str,
    beneficiary_ref: str,
    owner_view: Optional[Mapping[str, Any]] = None,
    policy_view: Optional[Mapping[str, Any]] = None,
    force_view: Optional[Mapping[str, Any]] = None,
    review_count: int = 0,
) -> Mapping[str, Any]:
    """Deterministically choose comply/negotiate/refuse for a defense obligation.

    This is a bounded political policy, not hidden omniscience. It uses only the
    persisted treaty, the exact conflict, current treaty conflicts, the obligor's
    represented relationship cue when available, and current conflict burden.
    """
    agreements = registry.get("agreements") if isinstance(registry, Mapping) else None
    agreement = agreements.get(agreement_ref) if isinstance(agreements, Mapping) else None
    if not isinstance(agreement, Mapping) or agreement.get("status") != "active":
        return {"decision": "cancel", "score_milli": 0, "reason": "treaty no longer active", "conflicting_agreement_refs": []}
    records = conflict_registry.get("records") if isinstance(conflict_registry, Mapping) else None
    conflict = records.get(conflict_ref) if isinstance(records, Mapping) else None
    if not isinstance(conflict, Mapping) or conflict.get("status") == "ended":
        return {"decision": "cancel", "score_milli": 0, "reason": "conflict no longer active", "conflicting_agreement_refs": []}
    sides_raw = conflict.get("side_refs")
    sides = {ref for ref in sides_raw if isinstance(ref, str)} if isinstance(sides_raw, Sequence) and not isinstance(sides_raw, (str, bytes, bytearray)) else set()
    if beneficiary_ref not in sides:
        return {"decision": "cancel", "score_milli": 0, "reason": "beneficiary is no longer a conflict participant", "conflicting_agreement_refs": []}
    if obligor_ref in sides:
        return {"decision": "comply", "score_milli": 1000, "reason": "obligor already participates in the conflict", "conflicting_agreement_refs": []}

    kind = str(agreement.get("agreement_type") or "")
    if isinstance(policy_view, Mapping):
        reliability = policy_view.get("treaty_reliability_milli", 500)
        risk_tolerance = policy_view.get("risk_tolerance_milli", 500)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (reliability, risk_tolerance)):
            reliability, risk_tolerance = 500, 500
        score = max(0, min(1000, int(reliability)))
        score += ({"alliance": 100, "guarantee": 20, "client_state": 120}.get(kind, 0))
        # A power that accepted more military risk when the treaty was signed is
        # more willing to honor it, but this cannot by itself overcome no capacity.
        score += (max(0, min(1000, int(risk_tolerance))) - 500) // 4
        if isinstance(force_view, Mapping):
            total = force_view.get("total", 0)
            availability = force_view.get("availability")
            if isinstance(total, int) and not isinstance(total, bool) and total > 0 and isinstance(availability, Mapping):
                ready = sum(
                    value for key in ("ready_24h", "mobilizable_7d", "mobilizable_30d")
                    for value in (availability.get(key, 0),)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                )
                readiness_milli = max(0, min(1000, (ready * 1000) // total))
                weight = policy_view.get("readiness_weight_milli", 250)
                if isinstance(weight, int) and not isinstance(weight, bool):
                    score += (readiness_milli * max(0, min(1000, weight))) // 1000
        score = max(0, min(1200, score))
    else:
        score = {"alliance": 780, "guarantee": 640, "client_state": 840}.get(kind, 500)
    attackers = sorted(sides - {beneficiary_ref})
    conflicting: list[str] = []
    for counterparty in attackers:
        barrier = hostility_barrier(registry, initiator_refs=(obligor_ref,), target_refs=(counterparty,))
        if barrier is not None and barrier[0] != agreement_ref:
            conflicting.append(barrier[0])
    conflicting = sorted(set(conflicting))
    score -= min(350, 220 * len(conflicting))

    active_conflict_count = 0
    if isinstance(records, Mapping):
        for row in records.values():
            if not isinstance(row, Mapping) or row.get("status") not in ("active", "ceasefire"):
                continue
            row_sides = row.get("side_refs")
            if isinstance(row_sides, Sequence) and not isinstance(row_sides, (str, bytes, bytearray)) and obligor_ref in row_sides:
                active_conflict_count += 1
    conflict_penalty = 120
    if isinstance(policy_view, Mapping):
        raw_penalty = policy_view.get("active_conflict_penalty_milli", conflict_penalty)
        if isinstance(raw_penalty, int) and not isinstance(raw_penalty, bool):
            conflict_penalty = max(0, min(1000, raw_penalty))
    score -= min(400, active_conflict_count * conflict_penalty)

    relation_stance = None
    relation_intensity = 0
    faction = owner_view.get("faction") if isinstance(owner_view, Mapping) else None
    relations = faction.get("relationships") if isinstance(faction, Mapping) else None
    if isinstance(relations, Sequence) and not isinstance(relations, (str, bytes, bytearray)):
        for row in relations:
            if not isinstance(row, Mapping) or row.get("target_id") != beneficiary_ref:
                continue
            relation_stance = row.get("stance") if isinstance(row.get("stance"), str) else None
            intensity = row.get("intensity", 0)
            if isinstance(intensity, int) and not isinstance(intensity, bool):
                relation_intensity = max(0, min(100, intensity))
            break
    if relation_stance in ("cooperative", "friendly", "allied"):
        score += relation_intensity
    elif relation_stance in ("hostile", "adversarial"):
        score -= min(180, relation_intensity * 2)
    elif relation_stance == "transactional":
        score += relation_intensity // 4

    score = max(0, min(1000, score))
    honor_threshold = 600
    negotiate_threshold = 350
    if isinstance(policy_view, Mapping):
        raw_honor = policy_view.get("honor_threshold_milli", honor_threshold)
        raw_negotiate = policy_view.get("negotiate_threshold_milli", negotiate_threshold)
        if isinstance(raw_honor, int) and not isinstance(raw_honor, bool):
            honor_threshold = max(0, min(1000, raw_honor))
        if isinstance(raw_negotiate, int) and not isinstance(raw_negotiate, bool):
            negotiate_threshold = max(0, min(honor_threshold, raw_negotiate))
    if score >= honor_threshold:
        decision = "comply"
    elif score >= negotiate_threshold and review_count <= 0:
        decision = "negotiate"
    else:
        decision = "refuse"
    return {
        "decision": decision,
        "score_milli": score,
        "reason": "bounded treaty-risk policy",
        "conflicting_agreement_refs": conflicting,
        "active_conflict_count": active_conflict_count,
        "relationship_stance": relation_stance,
        "relationship_intensity": relation_intensity,
    }
