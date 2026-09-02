"""Derived decisions around live kidnapping/custody state.

Custody itself is persistent because the prisoner can be moved, rescued, released,
or ransomed independently.  Family concern, rescue force sizing, and response
priority are derived at the causal boundary and are never stored as a second
"kidnapping system".
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

_TERMINAL = {"released", "escaped", "rescued", "executed"}


def custody_is_active(record: Mapping[str, Any]) -> bool:
    return str(record.get("status") or "restrained") not in _TERMINAL


def validate_active_custody_uniqueness(state: Mapping[str, Any]) -> None:
    """Require one physical custody authority per exact person."""
    rows = state.get("records", []) if isinstance(state, Mapping) else []
    if not isinstance(rows, list):
        raise ValueError("custody records must be a list")
    active_by_person: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not custody_is_active(row):
            continue
        person_ref = str(row.get("person_ref") or "")
        if not person_ref:
            continue
        custody_id = str(row.get("custody_id") or "")
        prior = active_by_person.get(person_ref)
        if prior is not None:
            raise ValueError(
                f"duplicate active custody for {person_ref}: {prior} and {custody_id}"
            )
        active_by_person[person_ref] = custody_id


def active_custody_record(state: Mapping[str, Any], person_ref: str) -> Mapping[str, Any] | None:
    rows = state.get("records", []) if isinstance(state, Mapping) else []
    if not isinstance(rows, list):
        return None
    found = None
    for row in rows:
        if isinstance(row, Mapping) and row.get("person_ref") == person_ref and custody_is_active(row):
            found = row
    return found


def close_kin_refs(family_state: Mapping[str, Any], person_ref: str) -> list[str]:
    """Return exact close-family refs in deterministic priority order.

    Parents and spouse are strongest responders; other current household members
    are still family/social stakeholders but do not require duplicate relationship
    flags on the captive.
    """
    refs: list[str] = []
    parentage = family_state.get("parentage", {}) if isinstance(family_state, Mapping) else {}
    if isinstance(parentage, Mapping):
        row = parentage.get(person_ref)
        if isinstance(row, Mapping):
            refs.extend(str(x) for x in row.get("parent_refs", []) if isinstance(x, str))
    marriages = family_state.get("marriages", {}) if isinstance(family_state, Mapping) else {}
    if isinstance(marriages, Mapping):
        for row in marriages.values():
            if not isinstance(row, Mapping) or str(row.get("status") or "married") not in {"married", "active"}:
                continue
            spouses = [str(x) for x in row.get("spouse_refs", []) if isinstance(x, str)]
            if person_ref in spouses:
                refs.extend(x for x in spouses if x != person_ref)
    households = family_state.get("households", {}) if isinstance(family_state, Mapping) else {}
    if isinstance(households, Mapping):
        for row in households.values():
            if not isinstance(row, Mapping) or str(row.get("status") or "active") != "active":
                continue
            members = [str(x) for x in row.get("member_refs", []) if isinstance(x, str)]
            if person_ref in members:
                refs.extend(x for x in members if x != person_ref)
    out: list[str] = []
    for ref in refs:
        if ref and ref not in out:
            out.append(ref)
    return out


def family_household_faction(family_state: Mapping[str, Any], person_ref: str) -> str | None:
    households = family_state.get("households", {}) if isinstance(family_state, Mapping) else {}
    if not isinstance(households, Mapping):
        return None
    for row in households.values():
        if not isinstance(row, Mapping) or str(row.get("status") or "active") != "active":
            continue
        members = row.get("member_refs", [])
        if isinstance(members, list) and person_ref in members:
            faction_ref = row.get("faction_ref")
            return str(faction_ref) if isinstance(faction_ref, str) and faction_ref else None
    return None



def kidnapping_report_delay_hours(
    *, route_hours: int, surviving_reporters: int, public_witness_milli: int,
) -> int | None:
    """Derive when credible kidnapping news can reach an interested institution.

    A surviving escort is a physical information carrier and therefore reports
    faster than ambient public rumor.  With neither a survivor nor a sufficiently
    credible public witness there is no automatic institutional knowledge at all.
    This helper derives timing only; it does not persist a rumor owner.
    """
    travel = max(1, int(route_hours))
    reporters = max(0, int(surviving_reporters))
    witness = max(0, min(1000, int(public_witness_milli)))
    if reporters > 0:
        # A survivor turns around from the incident rather than waiting for the
        # original trip to finish.  More survivors improve redundancy, not speed.
        return max(1, (travel + 1) // 2)
    if witness < 500:
        return None
    # Public news is slower and less reliable than a direct survivor report.
    # Better witnessed incidents diffuse sooner, but never faster than one day.
    rumor_lag = max(24, travel + max(6, (1000 - witness + 19) // 20))
    return rumor_lag

def rescue_force_size(*, available_count: int, captive_value_cash: int, close_kin_count: int, risk_tolerance: int) -> int:
    """Size a rescue from real available manpower rather than a hidden cap.

    Kinship, captive value, and institutional risk tolerance raise both a small
    mission-need floor and the share of locally available fighters the faction
    is willing to commit.  Large institutions can therefore send genuinely
    large rescues when the stakes justify it; exact route/combat frontage still
    determines who can physically engage at once.
    """
    available = max(0, int(available_count))
    if available <= 0:
        return 0
    value = max(0, int(captive_value_cash))
    kin = max(0, int(close_kin_count))
    risk = max(0, min(100, int(risk_tolerance)))
    need_floor = 3 + risk // 25 + min(3, kin)
    if value >= 50_000: need_floor += 2
    if value >= 150_000: need_floor += 2
    value_scale = min(250, int((value // 1_000) ** 0.5) * 15) if value else 0
    commitment_milli = min(900, 120 + risk * 4 + min(180, kin * 60) + value_scale)
    scalable = max(1, (available * commitment_milli + 999) // 1000)
    return min(available, max(need_floor, scalable))


def should_launch_rescue(
    *, rescue_power: int, estimated_defender_power: int, captive_value_cash: int,
    close_kin_count: int, ransom_cash: int, treasury_cash: int, risk_tolerance: int,
) -> bool:
    """Choose a physical rescue only when motive and plausible force support it.

    This is a strategic decision gate, not combat resolution.  Exact combat still
    decides whether the rescue succeeds and what injuries/deaths occur.
    """
    own = max(0, int(rescue_power))
    enemy = max(1, int(estimated_defender_power))
    if own <= 0:
        return False
    kin = max(0, int(close_kin_count))
    risk = max(0, min(100, int(risk_tolerance)))
    value = max(0, int(captive_value_cash))
    ransom = max(0, int(ransom_cash))
    treasury = max(0, int(treasury_cash))
    # Close family will accept a worse projected matchup. Institutions protecting
    # a valuable member also accept more risk than they would for ordinary loot.
    required_permille = 1250 - min(250, kin * 90) - min(180, value // 1_000) - risk * 2
    required_permille = max(700, required_permille)
    if own * 1000 >= enemy * required_permille:
        return True
    # If ransom would consume a major share of liquid reserves, a marginal rescue
    # can still be preferred instead of magically creating payment capacity.
    if ransom > 0 and treasury > 0 and ransom * 1000 >= treasury * 350:
        return own * 1000 >= enemy * max(650, required_permille - 180)
    return False


def should_pay_ransom(
    *, captive_value_cash: int, ransom_cash: int, treasury_cash: int,
    close_kin_count: int, risk_tolerance: int,
) -> bool:
    """Choose whether an NPC institution accepts a live ransom demand.

    This is evaluated only after a physical rescue was not dispatched. Payment
    is bounded by actual treasury cash and by the captive's derived importance,
    so kidnapping never becomes guaranteed money creation for the captor.
    """
    ransom = max(0, int(ransom_cash))
    treasury = max(0, int(treasury_cash))
    value = max(0, int(captive_value_cash))
    kin = max(0, int(close_kin_count))
    risk = max(0, min(100, int(risk_tolerance)))
    if ransom <= 0 or treasury < ransom:
        return False
    kin_multiplier_milli = min(2200, 1000 + kin * 300)
    risk_adjust_milli = max(700, 1250 - risk * 5)
    willingness = max(1500, value) * kin_multiplier_milli // 1000
    willingness = willingness * risk_adjust_milli // 1000
    liquidity_cap = treasury * (850 if kin > 0 else 600) // 1000
    return ransom <= min(willingness, liquidity_cap)


__all__ = [
    "active_custody_record", "close_kin_refs", "custody_is_active",
    "validate_active_custody_uniqueness",
    "family_household_faction", "kidnapping_report_delay_hours",
    "rescue_force_size", "should_launch_rescue", "should_pay_ransom",
]
