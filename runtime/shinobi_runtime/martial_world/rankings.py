"""Public-evidence-only Jianghu rankings."""
from __future__ import annotations
import json
from pathlib import Path
import copy
from typing import Mapping,Sequence,Any
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'rankings.json').read_text(encoding='utf-8'))
def public_score(record:Mapping[str,Any])->int:
    w=_data()['public_score_weights']
    return int(record.get('tournament_points',0))*w['tournament_points']+int(record.get('documented_duel_points',0))*w['documented_duel_points']+int(record.get('documented_contract_points',0))*w['documented_contract_points']
def publish_rankings(records:Sequence[Mapping[str,Any]])->list[dict[str,Any]]:
    rows=[{'person_id':r['person_id'],'public_score':public_score(r)} for r in records]
    rows.sort(key=lambda r:(-r['public_score'],r['person_id']))
    for i,r in enumerate(rows,1): r['rank']=i
    return rows

def add_public_points(state:Mapping[str,Any],person_ref:str,*,tournament_points:int=0,contract_points:int=0,duel_points:int=0)->dict[str,Any]:
    """Update one compact public-evidence aggregate and its derived score."""
    out=copy.deepcopy(dict(state)); audiences=out.setdefault('audiences',{})
    if not isinstance(audiences,dict): raise ValueError('jianghu reputation audiences invalid')
    record=copy.deepcopy(dict(audiences.get(person_ref,{}))) if isinstance(audiences.get(person_ref),Mapping) else {}
    record['tournament_points']=max(0,int(record.get('tournament_points',0)))+max(0,int(tournament_points))
    record['documented_contract_points']=max(0,int(record.get('documented_contract_points',0)))+max(0,int(contract_points))
    record['documented_duel_points']=max(0,int(record.get('documented_duel_points',0)))+max(0,int(duel_points))
    rep=record.get('reputation',{}) if isinstance(record.get('reputation'),Mapping) else {}
    record['reputation']={'martial_respect':max(0,int(rep.get('martial_respect',0))),'confidence':max(0,int(rep.get('confidence',0)))}
    record['public_score']=public_score(record); audiences[person_ref]=record
    return out


_REPUTATION_AXES = ("martial_respect", "trustworthiness", "honor", "fear", "reliability", "criminal_notoriety")


def _awareness_cfg() -> Mapping[str, Any]:
    data = _data()
    row = data.get("awareness_and_fame", {}) if isinstance(data, Mapping) else {}
    return row if isinstance(row, Mapping) else {}


def _bounded_milli(value: int) -> int:
    return max(0, min(1000, int(value)))


def _diminishing_gain(current: int, nominal: int, reliability_milli: int = 1000) -> int:
    current = _bounded_milli(current)
    nominal = max(0, int(nominal))
    reliability = _bounded_milli(reliability_milli)
    effective = nominal * reliability // 1000
    if effective <= 0 or current >= 1000:
        return 0
    return min(1000 - current, max(1, effective * (1000 - current) // 1000))


def baseline_faction_awareness(
    *, faction_ref: str, audience_kind: str, audience_place_ref: str | None,
    faction_headquarters: str | None, faction_type: str | None = None,
) -> int:
    """Derive baseline awareness without materializing audience matrices."""
    cfg = _awareness_cfg()
    baselines = cfg.get("baseline_by_audience_kind", {}) if isinstance(cfg.get("baseline_by_audience_kind"), Mapping) else {}
    base = int(baselines.get(str(audience_kind), baselines.get("distant_public", 0)))
    same_place = bool(audience_place_ref and faction_headquarters and str(audience_place_ref) == str(faction_headquarters))
    if same_place:
        base += int(cfg.get("same_settlement_bonus_milli", 0))
    # House Tang's major Luoyang Sword Manor is deliberately prominent, but
    # this is awareness only. It says nothing about opinion or recognition.
    if str(faction_ref) == "house_tang":
        base += int(cfg.get("house_tang_prominence_bonus_milli", 0))
    if str(faction_headquarters) == "luoyang":
        base += int(cfg.get("imperial_capital_prominence_bonus_milli", 0))
    if str(faction_type or "") in {"sect", "martial_house", "escort_agency"}:
        base += 30
    return _bounded_milli(base)


def faction_awareness_score(
    state: Mapping[str, Any], *, audience_ref: str, faction_ref: str, baseline_milli: int = 0,
) -> int:
    groups = state.get("faction_awareness", {}) if isinstance(state.get("faction_awareness"), Mapping) else {}
    audience = groups.get(audience_ref, {}) if isinstance(groups, Mapping) else {}
    saved = int(audience.get(faction_ref, 0)) if isinstance(audience, Mapping) else 0
    return max(_bounded_milli(baseline_milli), _bounded_milli(saved))


def personal_fame_score(state: Mapping[str, Any], *, audience_ref: str, person_ref: str) -> int:
    groups = state.get("personal_fame", {}) if isinstance(state.get("personal_fame"), Mapping) else {}
    audience = groups.get(audience_ref, {}) if isinstance(groups, Mapping) else {}
    return _bounded_milli(int(audience.get(person_ref, 0))) if isinstance(audience, Mapping) else 0


def audience_knows_faction(state: Mapping[str, Any], *, audience_ref: str, faction_ref: str, baseline_milli: int = 0) -> bool:
    threshold = int(_awareness_cfg().get("knowledge_threshold_milli", 250))
    return faction_awareness_score(state, audience_ref=audience_ref, faction_ref=faction_ref, baseline_milli=baseline_milli) >= threshold


def audience_knows_person_by_fame(state: Mapping[str, Any], *, audience_ref: str, person_ref: str) -> bool:
    threshold = int(_awareness_cfg().get("knowledge_threshold_milli", 250))
    return personal_fame_score(state, audience_ref=audience_ref, person_ref=person_ref) >= threshold


def apply_faction_awareness_evidence(
    state: Mapping[str, Any], *, audience_ref: str, faction_ref: str,
    evidence_kind: str, delivered: bool, reliability_milli: int = 1000,
) -> dict[str, Any]:
    out = copy.deepcopy(dict(state))
    if not delivered:
        return out
    cfg = _awareness_cfg(); gains = cfg.get("faction_awareness_gains", {}) if isinstance(cfg.get("faction_awareness_gains"), Mapping) else {}
    nominal = int(gains.get(evidence_kind, 0))
    if nominal <= 0:
        raise KeyError(evidence_kind)
    groups = out.setdefault("faction_awareness", {})
    if not isinstance(groups, dict): raise ValueError("jianghu faction awareness invalid")
    audience = groups.setdefault(str(audience_ref), {})
    if not isinstance(audience, dict): raise ValueError("jianghu faction awareness audience invalid")
    current = _bounded_milli(int(audience.get(str(faction_ref), 0)))
    audience[str(faction_ref)] = current + _diminishing_gain(current, nominal, reliability_milli)
    return out


def apply_personal_fame_evidence(
    state: Mapping[str, Any], *, audience_ref: str, person_ref: str,
    evidence_kind: str, delivered: bool, reliability_milli: int = 1000,
) -> dict[str, Any]:
    out = copy.deepcopy(dict(state))
    if not delivered:
        return out
    cfg = _awareness_cfg(); gains = cfg.get("personal_fame_gains", {}) if isinstance(cfg.get("personal_fame_gains"), Mapping) else {}
    nominal = int(gains.get(evidence_kind, 0))
    if nominal <= 0:
        raise KeyError(evidence_kind)
    groups = out.setdefault("personal_fame", {})
    if not isinstance(groups, dict): raise ValueError("jianghu personal fame invalid")
    audience = groups.setdefault(str(audience_ref), {})
    if not isinstance(audience, dict): raise ValueError("jianghu personal fame audience invalid")
    current = _bounded_milli(int(audience.get(str(person_ref), 0)))
    audience[str(person_ref)] = current + _diminishing_gain(current, nominal, reliability_milli)
    return out


def apply_faction_reputation_evidence(
    state: Mapping[str, Any], *, audience_ref: str, faction_ref: str,
    axis_deltas: Mapping[str, int], delivered: bool, reliability_milli: int = 1000,
) -> dict[str, Any]:
    """Apply opinion only to an audience that actually received the evidence."""
    out = copy.deepcopy(dict(state))
    if not delivered:
        return out
    reliability = _bounded_milli(reliability_milli)
    groups = out.setdefault("faction_reputation", {})
    if not isinstance(groups, dict): raise ValueError("jianghu faction reputation invalid")
    audience = groups.setdefault(str(audience_ref), {})
    if not isinstance(audience, dict): raise ValueError("jianghu faction reputation audience invalid")
    row = audience.setdefault(str(faction_ref), {})
    if not isinstance(row, dict): raise ValueError("jianghu faction reputation row invalid")
    for axis, raw in axis_deltas.items():
        if axis not in _REPUTATION_AXES:
            raise KeyError(axis)
        delta = int(raw) * reliability // 1000
        row[axis] = max(-100, min(100, int(row.get(axis, 0)) + delta))
    return out
