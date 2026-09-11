"""Sparse current Jianghu obligations, beliefs, vows and martial familiarity.

The social owner stores only unresolved/current facts.  Event chronology stays
outside hot state.  Helpers in this module are deterministic and side-effect
free so commands/frontiers can stage one authoritative after-image atomically.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

_OBLIGATION_KINDS = {
    "life_debt",
    "promise_aid",
    "promise_protect",
    "promise_nonaggression",
    "vengeance",
}
_VOW_KINDS = {
    "nonlethal",
    "no_poison",
    "protect_person",
    "loyal_to_faction",
    "repay_debts",
}
_BELIEF_STANCES = {"uncertain", "supports", "refutes", "confirmed", "disproved"}
_MAX_OBLIGATIONS_PER_ACTOR = 32
_MAX_VOWS_PER_PERSON = 12
_MAX_BELIEFS_PER_OBSERVER = 64
_MAX_FAMILIAR_OPPONENTS = 8
_MIN_PERSISTED_MARTIAL_EXPOSURE = 10


def _bounded(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def obligation_ref(actor_ref: str, counterparty_ref: str, kind: str) -> str:
    return f"obligation:{actor_ref}|{counterparty_ref}|{kind}"


def add_personal_obligation(
    state: Mapping[str, Any], *, actor_ref: str, counterparty_ref: str,
    kind: str, strength: int, created_at: str,
) -> dict[str, Any]:
    """Create/reinforce one unresolved personal obligation.

    ``actor_ref`` is the person bound by the obligation.  ``counterparty_ref``
    is the beneficiary for debts/promises and the target for vengeance.
    Repeated events strengthen the same current row instead of appending history.
    """
    actor_ref = str(actor_ref or ""); counterparty_ref = str(counterparty_ref or "")
    kind = str(kind or "")
    if not actor_ref or not counterparty_ref or actor_ref == counterparty_ref:
        raise ValueError("personal obligation requires two distinct people")
    if kind not in _OBLIGATION_KINDS:
        raise KeyError(kind)
    amount = _bounded(strength, 1, 100)
    out = copy.deepcopy(dict(state)); out.setdefault("schema", "jianghu-social-state-1.0")
    rows = out.setdefault("obligations", {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu personal obligations invalid")
    ref = obligation_ref(actor_ref, counterparty_ref, kind)
    prior = rows.get(ref)
    if not isinstance(prior, Mapping):
        active_for_actor = sum(
            1 for row in rows.values()
            if isinstance(row, Mapping) and str(row.get("actor_ref") or "") == actor_ref
        )
        if active_for_actor >= _MAX_OBLIGATIONS_PER_ACTOR:
            raise ValueError("jianghu active personal obligation limit reached")
    prior_strength = _bounded(int(prior.get("strength", 0)), 0, 100) if isinstance(prior, Mapping) else 0
    # Reinforcement is deliberately sublinear so repeated rescues/promises do
    # not become an append-only history encoded as unbounded magnitude.
    after_strength = amount if prior_strength <= 0 else min(100, prior_strength + max(1, amount // 3))
    rows[ref] = {
        "actor_ref": actor_ref,
        "counterparty_ref": counterparty_ref,
        "kind": kind,
        "strength": after_strength,
        "created_at": str(prior.get("created_at") or created_at) if isinstance(prior, Mapping) else str(created_at),
    }
    return {"state_after": out, "obligation_ref": ref, "strength": after_strength}


def resolve_personal_obligation(state: Mapping[str, Any], *, obligation_ref_value: str) -> dict[str, Any]:
    out = copy.deepcopy(dict(state)); rows = out.get("obligations", {})
    if rows not in (None, {}) and not isinstance(rows, dict):
        raise ValueError("jianghu personal obligations invalid")
    removed = None
    if isinstance(rows, dict):
        removed = rows.pop(str(obligation_ref_value), None)
        if not rows:
            out.pop("obligations", None)
    return {"state_after": out, "resolved": isinstance(removed, Mapping), "obligation": copy.deepcopy(removed) if isinstance(removed, Mapping) else None}


def obligations_for_actor(state: Mapping[str, Any], actor_ref: str) -> list[dict[str, Any]]:
    rows = state.get("obligations", {}) if isinstance(state, Mapping) else {}
    if not isinstance(rows, Mapping):
        return []
    out = [dict(row) for row in rows.values() if isinstance(row, Mapping) and row.get("actor_ref") == actor_ref]
    out.sort(key=lambda row: (str(row.get("kind") or ""), str(row.get("counterparty_ref") or "")))
    return out


def obligation_action_pressure(
    state: Mapping[str, Any], *, actor_ref: str, target_person_refs: Sequence[str], action_kind: str,
) -> int:
    """Signed personal pressure toward one contemplated action.

    Positive favors the action, negative resists it.  This is decision pressure,
    never an automatic outcome or mind-control override.
    """
    targets = {str(x) for x in target_person_refs if isinstance(x, str) and x}
    if not targets:
        return 0
    total = 0
    for row in obligations_for_actor(state, actor_ref):
        if str(row.get("counterparty_ref") or "") not in targets:
            continue
        strength = _bounded(int(row.get("strength", 0)), 0, 100)
        kind = str(row.get("kind") or "")
        if action_kind in {"aid", "protect"}:
            if kind in {"life_debt", "promise_aid", "promise_protect"}: total += strength
            elif kind == "vengeance": total -= strength
        elif action_kind in {"attack", "hostile"}:
            if kind == "vengeance": total += strength
            elif kind in {"life_debt", "promise_aid", "promise_protect", "promise_nonaggression"}: total -= strength
        elif action_kind == "forgive":
            if kind == "vengeance": total -= strength
    return _bounded(total, -100, 100)


def personal_aid_duty_target(
    state: Mapping[str, Any], *, decision_person_refs: Sequence[str],
    counterparty_faction_by_ref: Mapping[str, str], relation_edges: Sequence[Mapping[str, Any]],
    own_faction_ref: str, minimum_pressure: int = 60,
) -> dict[str, str] | None:
    """Choose one faction aid target from unresolved exact personal duties.

    This is pure current-state decision pressure. It returns the strongest exact
    obligation that supplied the winning target so a successful aid transfer can
    close that duty instead of creating a permanent obligation fossil.
    """
    edge_by_target = {
        str(row.get("to_faction") or ""): row for row in relation_edges
        if isinstance(row, Mapping) and str(row.get("to_faction") or "")
    }
    scores: dict[str, int] = {}
    candidates: dict[str, list[tuple[int, str]]] = {}
    for decision_ref in decision_person_refs:
        for row in obligations_for_actor(state, str(decision_ref)):
            kind = str(row.get("kind") or "")
            if kind not in {"life_debt", "promise_aid", "promise_protect"}:
                continue
            counterparty = str(row.get("counterparty_ref") or "")
            target_faction = str(counterparty_faction_by_ref.get(counterparty) or "")
            if not target_faction or target_faction in {own_faction_ref, "independent"}:
                continue
            edge = edge_by_target.get(target_faction)
            if isinstance(edge, Mapping) and int(edge.get("hostility", 0)) > 12:
                continue
            strength = _bounded(int(row.get("strength", 0)), 0, 100)
            scores[target_faction] = scores.get(target_faction, 0) + strength
            candidates.setdefault(target_faction, []).append((
                strength, obligation_ref(str(decision_ref), counterparty, kind),
            ))
    viable = sorted((-score, target) for target, score in scores.items() if score >= max(1, int(minimum_pressure)))
    if not viable:
        return None
    target = viable[0][1]
    exact_rows = sorted(candidates.get(target, []), key=lambda item: (-item[0], item[1]))
    return {
        "target_faction_ref": target,
        "obligation_ref": exact_rows[0][1] if exact_rows else "",
    }


def hostile_target_pressure(
    state: Mapping[str, Any], *, actor_ref: str, target_ref: str, target_faction_ref: str = "",
) -> int:
    """Signed willingness to direct hostility at one exact person.

    Positive pressure can come from unresolved vengeance. Negative pressure comes
    from debts/promises and explicit vows. The value is an AI input only; it
    never overrides a player's declared action or the physical combat resolver.
    """
    pressure = obligation_action_pressure(
        state, actor_ref=actor_ref, target_person_refs=[target_ref], action_kind="attack",
    )
    resistance = 0
    for row in vow_conflicts(
        state, person_ref=actor_ref, action_kind="hostile",
        target_ref=target_ref, target_faction_ref=target_faction_ref,
    ):
        resistance += _bounded(int(row.get("strength", 0)), 0, 100)
    return _bounded(pressure - resistance, -100, 100)


def breach_hostile_commitments(
    state: Mapping[str, Any], *, actor_ref: str, target_ref: str, target_faction_ref: str = "",
    targeting_intent: str = "disable", poison_ref: str = "",
) -> dict[str, Any]:
    """Close explicit promises/vows broken by one chosen hostile act.

    Life debts and vengeance remain current even after a hostile act because the
    duty/grievance itself has not been resolved. A promise not to attack/protect
    the target and a personal vow directly contradicted by the act are closed.
    """
    out = copy.deepcopy(dict(state))
    broken_obligations: list[str] = []
    rows = out.get("obligations", {})
    if isinstance(rows, dict):
        for ref, raw in list(rows.items()):
            if not isinstance(raw, Mapping):
                continue
            if str(raw.get("actor_ref") or "") != actor_ref or str(raw.get("counterparty_ref") or "") != target_ref:
                continue
            if str(raw.get("kind") or "") in {"promise_nonaggression", "promise_protect", "promise_aid"}:
                rows.pop(ref, None); broken_obligations.append(str(ref))
        if not rows:
            out.pop("obligations", None)
    conflicts = vow_conflicts(
        out, person_ref=actor_ref, action_kind="combat", target_ref=target_ref,
        target_faction_ref=target_faction_ref, targeting_intent=targeting_intent,
        poison_ref=poison_ref,
    )
    broken_vows: list[str] = []
    vow_rows = out.get("vows", {})
    if isinstance(vow_rows, dict):
        for raw in conflicts:
            if not isinstance(raw, Mapping):
                continue
            ref = vow_ref(
                actor_ref, str(raw.get("kind") or ""),
                str(raw.get("subject_ref") or ""), str(raw.get("faction_ref") or ""),
            )
            if ref in vow_rows:
                vow_rows.pop(ref, None); broken_vows.append(ref)
        if not vow_rows:
            out.pop("vows", None)
    return {
        "state_after": out,
        "broken_obligation_refs": sorted(broken_obligations),
        "broken_vow_refs": sorted(broken_vows),
    }


def vow_ref(person_ref: str, kind: str, subject_ref: str = "", faction_ref: str = "") -> str:
    scope = subject_ref or faction_ref or "self"
    return f"vow:{person_ref}|{kind}|{scope}"


def add_vow(
    state: Mapping[str, Any], *, person_ref: str, kind: str, strength: int,
    declared_at: str, subject_ref: str = "", faction_ref: str = "",
) -> dict[str, Any]:
    person_ref = str(person_ref or ""); kind = str(kind or "")
    subject_ref = str(subject_ref or ""); faction_ref = str(faction_ref or "")
    if not person_ref or kind not in _VOW_KINDS:
        raise ValueError("jianghu vow invalid")
    if kind == "protect_person" and not subject_ref:
        raise ValueError("protect_person vow requires subject")
    if kind == "loyal_to_faction" and not faction_ref:
        raise ValueError("loyal_to_faction vow requires faction")
    out = copy.deepcopy(dict(state)); out.setdefault("schema", "jianghu-social-state-1.0")
    rows = out.setdefault("vows", {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu vows invalid")
    ref = vow_ref(person_ref, kind, subject_ref, faction_ref)
    if ref not in rows:
        active_for_person = sum(
            1 for row in rows.values()
            if isinstance(row, Mapping) and str(row.get("person_ref") or "") == person_ref
        )
        if active_for_person >= _MAX_VOWS_PER_PERSON:
            raise ValueError("jianghu active vow limit reached")
    row: dict[str, Any] = {
        "person_ref": person_ref,
        "kind": kind,
        "strength": _bounded(strength, 1, 100),
        "declared_at": str(declared_at),
    }
    if subject_ref: row["subject_ref"] = subject_ref
    if faction_ref: row["faction_ref"] = faction_ref
    rows[ref] = row
    return {"state_after": out, "vow_ref": ref}


def release_vow(state: Mapping[str, Any], *, vow_ref_value: str, person_ref: str) -> dict[str, Any]:
    out = copy.deepcopy(dict(state)); rows = out.get("vows", {})
    if rows not in (None, {}) and not isinstance(rows, dict):
        raise ValueError("jianghu vows invalid")
    removed = None
    if isinstance(rows, dict):
        current = rows.get(str(vow_ref_value))
        if isinstance(current, Mapping) and str(current.get("person_ref") or "") == person_ref:
            removed = rows.pop(str(vow_ref_value), None)
        if not rows:
            out.pop("vows", None)
    return {"state_after": out, "released": isinstance(removed, Mapping), "vow": copy.deepcopy(removed) if isinstance(removed, Mapping) else None}


def vows_for_person(state: Mapping[str, Any], person_ref: str) -> list[dict[str, Any]]:
    rows = state.get("vows", {}) if isinstance(state, Mapping) else {}
    if not isinstance(rows, Mapping):
        return []
    out = [dict(row) for row in rows.values() if isinstance(row, Mapping) and row.get("person_ref") == person_ref]
    out.sort(key=lambda row: (str(row.get("kind") or ""), str(row.get("subject_ref") or row.get("faction_ref") or "")))
    return out


def vow_conflicts(
    state: Mapping[str, Any], *, person_ref: str, action_kind: str,
    target_ref: str = "", target_faction_ref: str = "", targeting_intent: str = "", poison_ref: str = "",
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for row in vows_for_person(state, person_ref):
        kind = str(row.get("kind") or ""); hit = False
        if kind == "nonlethal" and action_kind in {"attack", "combat"} and targeting_intent == "lethal": hit = True
        elif kind == "no_poison" and action_kind in {"attack", "combat"} and bool(poison_ref): hit = True
        elif kind == "protect_person" and action_kind in {"attack", "hostile", "combat"} and str(row.get("subject_ref") or "") == target_ref: hit = True
        elif kind == "loyal_to_faction" and action_kind in {"attack", "hostile", "combat"} and str(row.get("faction_ref") or "") == target_faction_ref: hit = True
        elif kind == "repay_debts" and action_kind in {"attack", "hostile", "combat"}:
            if obligation_action_pressure(state, actor_ref=person_ref, target_person_refs=[target_ref], action_kind="attack") < 0: hit = True
        if hit:
            conflicts.append(row)
    return conflicts


def belief_ref(observer_ref: str, claim_ref: str) -> str:
    return f"belief:{observer_ref}|{claim_ref}"


def record_belief(
    state: Mapping[str, Any], *, observer_ref: str, claim_ref: str, subject_ref: str,
    claim_kind: str, confidence_milli: int, stance: str = "supports",
    source_ref: str = "", value_cash: int | None = None, evidence_ref: str = "",
) -> dict[str, Any]:
    observer_ref = str(observer_ref or ""); claim_ref = str(claim_ref or "")
    if not observer_ref or not claim_ref or not subject_ref or not claim_kind:
        raise ValueError("jianghu belief invalid")
    if stance not in _BELIEF_STANCES:
        raise ValueError("jianghu belief stance invalid")
    out = copy.deepcopy(dict(state)); out.setdefault("schema", "jianghu-social-state-1.0")
    rows = out.setdefault("beliefs", {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu beliefs invalid")
    ref = belief_ref(observer_ref, claim_ref)
    row: dict[str, Any] = {
        "observer_ref": observer_ref,
        "claim_ref": claim_ref,
        "subject_ref": str(subject_ref),
        "claim_kind": str(claim_kind),
        "confidence_milli": _bounded(confidence_milli, 0, 1000),
        "stance": stance,
    }
    if source_ref: row["source_ref"] = str(source_ref)
    if value_cash is not None: row["value_cash"] = max(0, int(value_cash))
    if evidence_ref: row["evidence_ref"] = str(evidence_ref)
    prior = rows.get(ref)
    if isinstance(prior, Mapping):
        prior_stance = str(prior.get("stance") or "uncertain")
        prior_confidence = _bounded(int(prior.get("confidence_milli", 0)), 0, 1000)
        incoming_confidence = _bounded(int(row.get("confidence_milli", 0)), 0, 1000)
        prior_terminal = prior_stance in {"confirmed", "disproved"}
        incoming_terminal = stance in {"confirmed", "disproved"}
        # Verified evidence is not erased by later hearsay. Conflicting verified
        # evidence can replace it only when at least as strong; ordinary rumor
        # replaces an ordinary current belief only when it is no weaker.
        if prior_terminal and not incoming_terminal:
            row = copy.deepcopy(dict(prior))
        elif prior_terminal and incoming_terminal and incoming_confidence < prior_confidence:
            row = copy.deepcopy(dict(prior))
        elif not incoming_terminal and incoming_confidence < prior_confidence:
            row = copy.deepcopy(dict(prior))
    rows[ref] = row
    same_observer = [
        (key, raw) for key, raw in rows.items()
        if isinstance(key, str) and key != ref and isinstance(raw, Mapping)
        and str(raw.get("observer_ref") or "") == observer_ref
    ]
    overflow = max(0, len(same_observer) + 1 - _MAX_BELIEFS_PER_OBSERVER)
    if overflow:
        stance_weight = {"uncertain": 0, "supports": 1, "refutes": 1, "confirmed": 3, "disproved": 3}
        same_observer.sort(key=lambda item: (
            stance_weight.get(str(item[1].get("stance") or "uncertain"), 0),
            _bounded(int(item[1].get("confidence_milli", 0)), 0, 1000),
            item[0],
        ))
        for key, _raw in same_observer[:overflow]:
            rows.pop(key, None)
    return {"state_after": out, "belief_ref": ref, "belief": copy.deepcopy(row)}


def revise_belief(
    state: Mapping[str, Any], *, belief_ref_value: str, observer_ref: str,
    evidence_stance: str, evidence_confidence_milli: int,
) -> dict[str, Any]:
    if evidence_stance not in {"supports", "refutes", "confirmed", "disproved"}:
        raise ValueError("belief evidence stance invalid")
    out = copy.deepcopy(dict(state)); rows = out.get("beliefs", {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu beliefs invalid")
    row = rows.get(str(belief_ref_value))
    if not isinstance(row, dict) or str(row.get("observer_ref") or "") != observer_ref:
        raise KeyError(belief_ref_value)
    evidence = _bounded(evidence_confidence_milli, 0, 1000)
    current = _bounded(int(row.get("confidence_milli", 0)), 0, 1000)
    if evidence_stance in {"confirmed", "disproved"}:
        row["stance"] = evidence_stance
        row["confidence_milli"] = max(current, evidence)
    elif evidence_stance == str(row.get("stance")):
        row["confidence_milli"] = min(1000, current + max(1, evidence // 4))
    else:
        if evidence > current:
            row["stance"] = evidence_stance
            row["confidence_milli"] = evidence - current // 3
        else:
            row["confidence_milli"] = max(0, current - evidence // 2)
            if row["confidence_milli"] < 200:
                row["stance"] = "uncertain"
    return {"state_after": out, "belief_ref": str(belief_ref_value), "belief": copy.deepcopy(row)}


def belief_for(state: Mapping[str, Any], *, observer_ref: str, claim_ref: str) -> Mapping[str, Any] | None:
    rows = state.get("beliefs", {}) if isinstance(state, Mapping) else {}
    row = rows.get(belief_ref(observer_ref, claim_ref)) if isinstance(rows, Mapping) else None
    return row if isinstance(row, Mapping) else None


def belief_action_pressure(
    state: Mapping[str, Any], *, actor_ref: str, target_person_refs: Sequence[str], action_kind: str,
) -> int:
    """Signed decision pressure from one person's current beliefs.

    Only registered claim kinds with a real institutional consequence contribute.
    A belief is not treated as truth: confidence and stance determine how much it
    changes the actor's willingness to aid or oppose the believed subject.
    """
    targets = {str(x) for x in target_person_refs if isinstance(x, str) and x}
    rows = state.get("beliefs", {}) if isinstance(state, Mapping) else {}
    if not targets or not isinstance(rows, Mapping):
        return 0
    total = 0
    for raw in rows.values():
        if not isinstance(raw, Mapping) or str(raw.get("observer_ref") or "") != actor_ref:
            continue
        if str(raw.get("subject_ref") or "") not in targets:
            continue
        if str(raw.get("claim_kind") or "") != "property_crime_responsibility":
            continue
        confidence = _bounded(int(raw.get("confidence_milli", 0)), 0, 1000)
        stance = str(raw.get("stance") or "uncertain")
        sign = 0
        if stance in {"supports", "confirmed"}:
            sign = 1
        elif stance in {"refutes", "disproved"}:
            sign = -1
        if not sign:
            continue
        magnitude = max(0, confidence * 80 // 1000)
        if action_kind in {"attack", "hostile"}:
            total += sign * magnitude
        elif action_kind in {"aid", "protect"}:
            total -= sign * (magnitude * 3 // 4)
    return _bounded(total, -100, 100)


def prune_beliefs_for_subject_refs(state: Mapping[str, Any], subject_refs: Sequence[str]) -> dict[str, Any]:
    subjects = {str(x) for x in subject_refs if isinstance(x, str) and x}
    if not subjects:
        return copy.deepcopy(dict(state))
    out = copy.deepcopy(dict(state)); rows = out.get("beliefs", {})
    if not isinstance(rows, dict):
        return out
    for ref in list(rows):
        row = rows.get(ref)
        if isinstance(row, Mapping) and str(row.get("subject_ref") or "") in subjects:
            rows.pop(ref, None)
    if not rows: out.pop("beliefs", None)
    return out


def _martial_ref(observer_ref: str, opponent_ref: str) -> str:
    return f"martial:{observer_ref}|{opponent_ref}"


def record_martial_contact(
    state: Mapping[str, Any], *, observer_ref: str, opponent_ref: str,
    opponent_action_kind: str = "", exposure_gain: int = 4,
) -> dict[str, Any]:
    if not observer_ref or not opponent_ref or observer_ref == opponent_ref:
        raise ValueError("martial familiarity requires two distinct people")
    out = copy.deepcopy(dict(state)); out.setdefault("schema", "jianghu-social-state-1.0")
    rows = out.setdefault("martial_familiarity", {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu martial familiarity invalid")
    ref = _martial_ref(observer_ref, opponent_ref); prior = rows.get(ref, {})
    if prior not in (None, {}) and not isinstance(prior, Mapping):
        raise ValueError("jianghu martial familiarity row invalid")
    exposure = min(100, max(0, int(prior.get("exposure", 0))) + max(1, int(exposure_gain))) if isinstance(prior, Mapping) else max(1, int(exposure_gain))
    melee = max(0, int(prior.get("melee_pressure", 0))) if isinstance(prior, Mapping) else 0
    ranged = max(0, int(prior.get("ranged_pressure", 0))) if isinstance(prior, Mapping) else 0
    control = max(0, int(prior.get("control_pressure", 0))) if isinstance(prior, Mapping) else 0
    kind = str(opponent_action_kind or "")
    if kind in {"bow_shot", "hidden_weapon_throw"}: ranged = min(100, ranged + 5)
    elif kind in {"staff_sweep", "staff_strike", "staff_thrust", "staff_butt_strike"}: control = min(100, control + 4); melee = min(100, melee + 3)
    elif kind in {"cut", "thrust", "unarmed_strike"}: melee = min(100, melee + 5)
    rows[ref] = {
        "observer_ref": str(observer_ref), "opponent_ref": str(opponent_ref),
        "exposure": min(100, exposure), "melee_pressure": melee,
        "ranged_pressure": ranged, "control_pressure": control,
    }
    same_observer = [
        (key, raw) for key, raw in rows.items()
        if isinstance(key, str) and key != ref and isinstance(raw, Mapping)
        and str(raw.get("observer_ref") or "") == str(observer_ref)
    ]
    overflow = max(0, len(same_observer) + 1 - _MAX_FAMILIAR_OPPONENTS)
    if overflow:
        same_observer.sort(key=lambda item: (
            _bounded(int(item[1].get("exposure", 0)), 0, 100),
            _bounded(int(item[1].get("melee_pressure", 0)), 0, 100)
            + _bounded(int(item[1].get("ranged_pressure", 0)), 0, 100)
            + _bounded(int(item[1].get("control_pressure", 0)), 0, 100),
            item[0],
        ))
        for key, _raw in same_observer[:overflow]:
            rows.pop(key, None)
    return {"state_after": out, "martial_ref": ref, "profile": copy.deepcopy(rows[ref])}


def apply_martial_events(
    state: Mapping[str, Any], events: Sequence[Mapping[str, Any]], *,
    side_by_ref: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    out = copy.deepcopy(dict(state))
    for event in events:
        if not isinstance(event, Mapping): continue
        actor = str(event.get("actor_ref") or "")
        opponent = str(event.get("actual_ref") or event.get("intended_ref") or "")
        if not actor or not opponent or actor == opponent: continue
        if side_by_ref is not None:
            actor_side = str(side_by_ref.get(actor) or "")
            opponent_side = str(side_by_ref.get(opponent) or "")
            # Physical screening can make an attack contact a friend. That is
            # a real combat event, but it must not create an "opponent"
            # familiarity profile between allies on the same side.
            if not actor_side or not opponent_side or actor_side == opponent_side:
                continue
        kind = str(event.get("action_kind") or "")
        # The defender sees the attacker's style; the attacker also learns the
        # opponent through a real exchange, but without inventing a defense type.
        out = record_martial_contact(out, observer_ref=opponent, opponent_ref=actor, opponent_action_kind=kind, exposure_gain=5)["state_after"]
        out = record_martial_contact(out, observer_ref=actor, opponent_ref=opponent, exposure_gain=3)["state_after"]
    return out


def prune_incidental_martial_familiarity(
    state: Mapping[str, Any], *, minimum_exposure: int = _MIN_PERSISTED_MARTIAL_EXPOSURE,
    keep_refs: set[str] | None = None,
) -> dict[str, Any]:
    """Drop weak/new pairwise combat impressions before they become hot history.

    Active combat may carry sub-threshold rows across exchanges so adaptation
    can emerge during the fight. Once the encounter closes, only a meaningful
    accumulated profile survives. ``keep_refs`` can additionally restrict new
    persistence, which is used by mass combat to update already-established
    rivals without materializing a pairwise matrix for one battlefield.
    """
    out = copy.deepcopy(dict(state)); rows = out.get("martial_familiarity", {})
    if not isinstance(rows, dict):
        return out
    threshold = max(1, int(minimum_exposure))
    allowed = None if keep_refs is None else {str(ref) for ref in keep_refs}
    for ref in list(rows):
        raw = rows.get(ref)
        if not isinstance(raw, Mapping):
            rows.pop(ref, None); continue
        if int(raw.get("exposure", 0)) < threshold or (allowed is not None and str(ref) not in allowed):
            rows.pop(ref, None)
    if not rows:
        out.pop("martial_familiarity", None)
    return out


def martial_profile(state: Mapping[str, Any], *, observer_ref: str, opponent_ref: str) -> Mapping[str, Any] | None:
    rows = state.get("martial_familiarity", {}) if isinstance(state, Mapping) else {}
    row = rows.get(_martial_ref(observer_ref, opponent_ref)) if isinstance(rows, Mapping) else None
    return row if isinstance(row, Mapping) else None


def close_family_refs(family_state: Mapping[str, Any], person_ref: str) -> set[str]:
    result: set[str] = set()
    marriages = family_state.get("marriages", {}) if isinstance(family_state, Mapping) else {}
    if isinstance(marriages, Mapping):
        for row in marriages.values():
            if not isinstance(row, Mapping) or row.get("status") != "married": continue
            refs = [str(x) for x in row.get("spouse_refs", []) if isinstance(x, str)]
            if person_ref in refs: result.update(x for x in refs if x != person_ref)
    parentage = family_state.get("parentage", {}) if isinstance(family_state, Mapping) else {}
    if isinstance(parentage, Mapping):
        row = parentage.get(person_ref)
        if isinstance(row, Mapping): result.update(str(x) for x in row.get("parent_refs", []) if isinstance(x, str))
        # Children and siblings are derived only at the moment of a decision.
        my_parents = set(str(x) for x in (row.get("parent_refs", []) if isinstance(row, Mapping) else []) if isinstance(x, str))
        for child_ref, child_row in parentage.items():
            if not isinstance(child_row, Mapping): continue
            parents = {str(x) for x in child_row.get("parent_refs", []) if isinstance(x, str)}
            if person_ref in parents: result.add(str(child_ref))
            if my_parents and str(child_ref) != person_ref and my_parents & parents: result.add(str(child_ref))
    result.discard(person_ref)
    return result


def add_witnessed_family_vengeance(
    state: Mapping[str, Any], family_state: Mapping[str, Any], *,
    dead_ref: str, killer_ref: str, witness_refs: Sequence[str], created_at: str,
) -> dict[str, Any]:
    """Create vengeance only for close family who actually witnessed the killing.

    This deliberately avoids omniscient revenge. Remote relatives receive no
    private knowledge merely because the runtime knows who caused a death.
    """
    witnesses = {str(x) for x in witness_refs if isinstance(x, str) and x}
    relatives = close_family_refs(family_state, dead_ref) & witnesses
    relatives.discard(killer_ref); relatives.discard(dead_ref)
    out = copy.deepcopy(dict(state)); created: list[str] = []
    for person_ref in sorted(relatives):
        result = add_personal_obligation(
            out, actor_ref=person_ref, counterparty_ref=killer_ref, kind="vengeance",
            strength=80, created_at=created_at,
        )
        out = result["state_after"]; created.append(str(result["obligation_ref"]))
    return {"state_after": out, "created_refs": created}


def decision_refs(people: Sequence[Mapping[str, Any]], *, limit: int = 12) -> list[str]:
    """Bounded senior institutional voices for a temporary decision camp."""
    rows: list[tuple[int, int, str]] = []
    for person in people:
        if not isinstance(person, Mapping): continue
        ref = str(person.get("person_id") or "")
        if not ref: continue
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") in {"dead", "incapacitated"}: continue
        offices = {str(x) for x in person.get("standing_offices", []) if isinstance(x, str)} if isinstance(person.get("standing_offices"), list) else set()
        office_weight = 3 if "leader" in offices else 2 if offices & {"elder", "deputy", "commander", "chief"} else 0
        attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
        influence = max(0, int(attrs.get("intelligence", 0))) + max(0, int(attrs.get("willpower", 0)))
        rows.append((-office_weight, -influence, ref))
    rows.sort()
    chosen = [ref for office, _influence, ref in rows if office < 0][:max(1, int(limit))]
    if chosen: return chosen
    return [ref for _office, _influence, ref in rows[:max(1, min(int(limit), 3))]]


def internal_action_consensus(
    social_state: Mapping[str, Any], family_state: Mapping[str, Any], *,
    decision_person_refs: Sequence[str], person_faction_by_ref: Mapping[str, str],
    target_faction_ref: str, target_member_refs: Sequence[str], action_kind: str,
) -> dict[str, Any]:
    target_members = {str(x) for x in target_member_refs if isinstance(x, str) and x}
    support: list[str] = []; oppose: list[str] = []; neutral: list[str] = []; scores: dict[str, int] = {}
    for person_ref in decision_person_refs:
        score = obligation_action_pressure(social_state, actor_ref=person_ref, target_person_refs=sorted(target_members), action_kind=action_kind)
        score += belief_action_pressure(
            social_state, actor_ref=person_ref, target_person_refs=sorted(target_members), action_kind=action_kind,
        )
        family = close_family_refs(family_state, person_ref)
        if any(person_faction_by_ref.get(ref) == target_faction_ref for ref in family):
            score += -70 if action_kind in {"attack", "hostile"} else 40
        for vow in vows_for_person(social_state, person_ref):
            kind = str(vow.get("kind") or ""); strength = _bounded(int(vow.get("strength", 0)), 0, 100)
            if kind == "loyal_to_faction" and str(vow.get("faction_ref") or "") == target_faction_ref:
                if action_kind in {"attack", "hostile"}:
                    score -= strength
                elif action_kind in {"aid", "protect"}:
                    score += strength // 2
            if kind == "protect_person" and str(vow.get("subject_ref") or "") in target_members and action_kind in {"attack", "hostile"}:
                score -= strength
            if kind == "nonlethal" and action_kind in {"attack", "hostile"}:
                score -= strength // 3
            if kind == "repay_debts" and action_kind in {"aid", "protect"}:
                if obligation_action_pressure(social_state, actor_ref=person_ref, target_person_refs=sorted(target_members), action_kind="aid") > 0:
                    score += strength // 2
        score = _bounded(score, -100, 100); scores[person_ref] = score
        if score >= 20: support.append(person_ref)
        elif score <= -20: oppose.append(person_ref)
        else: neutral.append(person_ref)
    count = max(1, len(scores)); aggregate = sum(scores.values()) // count if scores else 0
    return {
        "support_refs": support, "oppose_refs": oppose, "neutral_refs": neutral,
        "pressure": _bounded(aggregate, -100, 100), "scores": scores,
    }


__all__ = [
    "add_personal_obligation", "add_vow", "add_witnessed_family_vengeance", "apply_martial_events",
    "belief_action_pressure", "belief_for", "belief_ref", "breach_hostile_commitments", "close_family_refs", "decision_refs",
    "hostile_target_pressure", "internal_action_consensus", "martial_profile", "obligation_action_pressure",
    "personal_aid_duty_target",
    "obligation_ref", "obligations_for_actor", "prune_beliefs_for_subject_refs", "record_belief",
    "prune_incidental_martial_familiarity", "record_martial_contact", "release_vow", "resolve_personal_obligation", "revise_belief",
    "vow_conflicts", "vow_ref", "vows_for_person",
]
