"""Finite deterministic Jianghu calendar events and tournament seeding."""
from __future__ import annotations
import json
import math
from datetime import date,timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any,Mapping,Sequence
from .travel import shortest_route
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'

def _data(): return json.loads((_MW/'world-events.json').read_text(encoding='utf-8'))
def _event_date(year:int,month_day:str)->date:
    m,d=(int(x) for x in month_day.split('-')); return date(year,m,d)


@lru_cache(maxsize=None)
def _connected_route_preparation_days(host_place_id: str, mode: str = "foot") -> int:
    """Conservative travel horizon from every connected registered place.

    Great-tournament notice must never be so late that a lawful established
    faction is excluded merely by map distance.  Static geography is the right
    authority here: a future faction founded at any registered connected place
    inherits the same guarantee without adding faction-specific calendar data.

    ``shortest_route`` already includes terrain/road geometry.  We multiply the
    longest baseline route by the worst registered weather and ground factors so
    even an unlucky seasonal journey still fits inside the formal preparation
    period.  Actual travel continues to use exact date-specific weather.
    """
    geo=json.loads((_MW/'geography.json').read_text(encoding='utf-8'))
    travel=json.loads((_MW/'travel.json').read_text(encoding='utf-8'))
    places=geo.get('places',{}) if isinstance(geo,Mapping) else {}
    longest_hours=0.0
    for place_id in places:
        try:
            route=shortest_route(start=str(place_id),end=host_place_id,mode=mode)
        except (KeyError,ValueError):
            continue
        longest_hours=max(longest_hours,float(route.get('baseline_hours',0.0)))
    weather=max((int(x) for x in travel.get('weather_time_milli',{}).values()),default=1000)
    ground=max((int(x) for x in travel.get('ground_time_milli',{}).values()),default=1000)
    worst_hours=longest_hours*(weather/1000.0)*(ground/1000.0)
    return max(1,int(math.ceil(worst_hours/24.0)))


def tournament_preparation_days(event_id: str, *, host_place_id: str | None = None) -> int:
    """Return the formal registration/preparation horizon before close.

    Ordinary events may keep an authored fixed window.  The Great Jianghu
    Tournament derives its minimum from the entire connected map plus an
    explicit institutional preparation buffer, because late notice is not a
    lawful reason for an established faction to miss the world's four-year
    premier gathering.
    """
    data=_data(); spec=data.get('calendar_events',{}).get(event_id,{})
    if not isinstance(spec,Mapping):
        raise KeyError(event_id)
    fixed=max(1,int(spec.get('registration_opens_days_before_close',30)))
    if not bool(spec.get('derive_open_from_connected_route',False)):
        return fixed
    host=str(host_place_id or data.get('host_cycles',{}).get('great_jianghu_tournament_host') or '')
    if not host:
        return fixed
    mode=str(spec.get('preparation_route_mode') or 'foot')
    route_days=_connected_route_preparation_days(host,mode)
    buffer_days=max(0,int(spec.get('preparation_buffer_days',0)))
    return max(fixed,route_days+buffer_days)


def calendar_events_between(start:date,end:date)->list[dict[str,Any]]:
    if end<start: raise ValueError('end before start')
    d=_data(); out=[]
    for year in range(start.year,end.year+1):
        for event_id,spec in d['calendar_events'].items():
            cadence=spec['cadence']
            if cadence=='EVERY_4_YEARS' and year%4!=0: continue
            when=_event_date(year,spec['month_day'])
            if not start<=when<=end: continue
            row={'event_id':event_id,'date':when.isoformat(),'year':year}
            if isinstance(spec.get('formats'),list): row['formats']=[str(x) for x in spec['formats']]
            cycle=d.get('host_cycles',{}).get(event_id)
            if isinstance(cycle,list) and cycle: row['host_place_id']=cycle[year%len(cycle)]
            elif event_id=='great_jianghu_tournament': row['host_place_id']=d.get('host_cycles',{}).get('great_jianghu_tournament_host')
            if 'duration_days' in spec: row['ends_on']=(when+timedelta(days=int(spec['duration_days'])-1)).isoformat()
            if 'advance_notice_days_before' in spec:
                row['advance_notice_on']=(when-timedelta(days=max(1,int(spec['advance_notice_days_before'])))).isoformat()
            if 'registration_closes_days_before' in spec:
                closes_on = when - timedelta(days=int(spec['registration_closes_days_before']))
                row['registration_closes_on'] = closes_on.isoformat()
                opens_before_close = tournament_preparation_days(
                    event_id,host_place_id=str(row.get('host_place_id') or '') or None,
                )
                row['registration_opens_on'] = (closes_on - timedelta(days=max(1, opens_before_close))).isoformat()
            if 'convergence_days_before' in spec:
                row['convergence_days_before'] = max(0, int(spec.get('convergence_days_before', 0)))
            out.append(row)
    return sorted(out,key=lambda r:(r['date'],r['event_id']))

def tournament_bracket(entrants:Sequence[Mapping[str,Any]])->list[tuple[str,str|None]]:
    """Seed by public qualifying score, then stable ID. No draw RNG."""
    rows=[]
    for e in entrants:
        ref=e.get('person_ref'); score=e.get('public_qualifying_score',0)
        if not isinstance(ref,str): raise ValueError('entrant ref')
        if isinstance(score,bool) or not isinstance(score,int): raise ValueError('entrant score')
        rows.append((score,ref))
    rows.sort(key=lambda x:(-x[0],x[1]))
    refs=[r for _,r in rows]; out=[]
    while len(refs)>1:
        out.append((refs.pop(0),refs.pop(-1)))
    if refs: out.append((refs[0],None))
    return out
