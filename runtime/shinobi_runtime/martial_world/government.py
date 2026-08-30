"""Deterministic government attention and aggregate mobilization."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Mapping,Sequence,Any
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'government-response.json').read_text(encoding='utf-8'))
def attention_from_evidence(events:Sequence[Mapping[str,Any]],*,prior_offenses:int=0)->int:
    cfg=_data()['attention']; score=0
    for e in events:
        if not e.get('publicly_delivered'): continue
        kind=e.get('kind'); mapped=_data().get('offense_attention_kind_map',{}).get(kind,kind); base=int(cfg.get(mapped,0)); confidence=max(0,min(100,int(e.get('confidence',100)))); score+=base*confidence//100
    mult=1000+max(0,prior_offenses)*int(cfg['repeat_offense_multiplier_milli_per_prior'])
    return min(300,score*mult//1000)


def compact_attention_row(*, attention:int, bounty_cash:int=0, prior_offenses:int=0)->dict[str,int]:
    """Persist only current government response state, never evidence-history metadata."""
    return {
        'attention': max(0, min(300, int(attention))),
        'bounty_cash': max(0, int(bounty_cash)),
        'prior_offenses': max(0, int(prior_offenses)),
    }


def decay_attention_rows(rows: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Apply the registered 30-day attention decay to compact current rows.

    Prior offenses are a durable repeat-offender accumulator and funded bounty
    authority is settled by warrants/death/custody. Only current attention
    decays here. A row disappears only when it carries no remaining mechanical
    fact at all.
    """
    cfg = _data().get('attention', {})
    decay = max(0, int(cfg.get('decay_per_30_days', 0))) if isinstance(cfg, Mapping) else 0
    out: dict[str, dict[str, int]] = {}
    for ref, raw in rows.items() if isinstance(rows, Mapping) else []:
        if not isinstance(ref, str) or not isinstance(raw, Mapping):
            continue
        row = compact_attention_row(
            attention=max(0, int(raw.get('attention', 0))) - decay,
            bounty_cash=max(0, int(raw.get('bounty_cash', 0))),
            prior_offenses=max(0, int(raw.get('prior_offenses', 0))),
        )
        if row['attention'] or row['bounty_cash'] or row['prior_offenses']:
            out[ref] = row
    return out

def mobilization(attention:int,*,distance_hours:int=0)->dict[str,int]:
    c=_data()['mobilization']; a=max(0,attention)
    militia=a*int(c['militia_headcount_per_attention'])
    standard=max(0,a-40)*int(c['standard_headcount_per_attention_over_40'])
    elite=max(0,a-80)*int(c['elite_headcount_per_attention_over_80'])
    hours=int(c['base_mobilization_hours'])+max(0,distance_hours)*int(c['distance_hours_multiplier_milli'])//1000
    return {'militia':militia,'standard':standard,'elite':elite,'mobilization_hours':hours,'exact_headcount':militia+standard+elite}


def allocate_response(attention:int,capacity:Mapping[str,int])->dict[str,Any]:
    """Allocate only real finite regional response capacity."""
    available={k:max(0,int(capacity.get(k,0))) for k in ("militia","standard","elite")}
    a=max(0,int(attention))
    requested={"militia":max(0,min(available["militia"],1+a//25)),"standard":max(0,min(available["standard"],a//50)),"elite":max(0,min(available["elite"],a//100))}
    after={k:available[k]-requested[k] for k in available}
    return {'allocated':{**requested,'exact_headcount':sum(requested.values())},'capacity_after':after}
