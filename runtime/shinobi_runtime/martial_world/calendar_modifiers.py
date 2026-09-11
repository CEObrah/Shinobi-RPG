"""Deterministic effects for mechanically meaningful recurring Jianghu seasons."""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT=Path(__file__).resolve().parents[3]
_PATH=_ROOT/'game'/'data'/'martial-world'/'world-events.json'

@lru_cache(maxsize=1)
def _events()->dict[str,Any]:
    raw=json.loads(_PATH.read_text(encoding='utf-8'))
    rows=raw.get('calendar_events',{}) if isinstance(raw,Mapping) else {}
    return dict(rows) if isinstance(rows,Mapping) else {}

def event_active(event_id:str, when:date|datetime)->bool:
    row=_events().get(event_id)
    if not isinstance(row,Mapping) or str(row.get('cadence'))!='YEARLY': return False
    day=when.date() if isinstance(when,datetime) else when
    try:
        month,dom=(int(x) for x in str(row.get('month_day')).split('-',1))
        start=date(day.year,month,dom)
    except (TypeError,ValueError): return False
    duration=max(1,int(row.get('duration_days',1)))
    return start <= day < start+timedelta(days=duration)

def event_overlaps(event_id:str, when:date|datetime, *, lookback_days:int)->bool:
    """Whether a yearly event overlapped a bounded aggregate review window."""
    days=max(0,int(lookback_days))
    if days<=0:
        return event_active(event_id,when)
    day=when.date() if isinstance(when,datetime) else when
    row=_events().get(event_id)
    if not isinstance(row,Mapping) or str(row.get('cadence'))!='YEARLY': return False
    for year in {day.year, (day-timedelta(days=days)).year}:
        try:
            month,dom=(int(x) for x in str(row.get('month_day')).split('-',1))
            start=date(year,month,dom)
        except (TypeError,ValueError):
            continue
        duration=max(1,int(row.get('duration_days',1)))
        end=start+timedelta(days=duration-1)
        window_start=day-timedelta(days=days)
        if start<=day and end>=window_start:
            return True
    return False

def recruitment_capacity_milli(when:date|datetime)->int:
    return 1500 if event_active('spring_recruitment_season',when) or event_active('autumn_recruitment_season',when) else 1000

def trade_capital_milli(when:date|datetime, *, river_route:bool=False, review_window_days:int=0)->int:
    value=1000
    active=lambda event_id: event_overlaps(event_id,when,lookback_days=review_window_days)
    if active('spring_trade_fair') or active('autumn_trade_fair'): value=value*1500//1000
    if river_route and active('summer_river_trade_convoy_season'): value=value*1250//1000
    return value

def government_attention_milli(when:date|datetime, *, review_window_days:int=0)->int:
    return 1250 if event_overlaps('ghost_month_security_surge',when,lookback_days=review_window_days) else 1000

def formal_challenge_pressure_milli(when:date|datetime)->int:
    return 1400 if event_active('winter_school_challenge_meets',when) else 1000

def escort_demand_milli(when:date|datetime, *, review_window_days:int=0)->int:
    active=lambda event_id: event_overlaps(event_id,when,lookback_days=review_window_days)
    return 1400 if active('spring_escort_exchange') or active('autumn_escort_exchange') else 1000

__all__=['escort_demand_milli','event_active','event_overlaps','formal_challenge_pressure_milli','government_attention_milli','recruitment_capacity_milli','trade_capital_milli']
