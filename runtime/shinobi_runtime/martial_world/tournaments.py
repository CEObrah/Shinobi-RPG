"""Deterministic tournament registration/funding around the shared combat engine."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any,Mapping,Sequence
from .events import tournament_bracket
from .combat_simulation import simulate_exact_combat
from .exact_combat import initialize_combat
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'tournaments.json').read_text(encoding='utf-8'))

def event_profile(tournament_kind:str)->dict[str,Any]:
    data=_data(); raw=data.get('event_profiles',{}).get(tournament_kind,{})
    if not isinstance(raw,Mapping): raise KeyError(tournament_kind)
    funding=data.get('funding',{})
    if not isinstance(funding,Mapping): funding={}
    placement_faction_share=max(0,min(1000,int(funding.get('placement_faction_share_permille',700))))
    placement_personal_share=max(0,min(1000,int(funding.get('placement_personal_share_permille',300))))
    if placement_faction_share+placement_personal_share!=1000:
        raise ValueError('tournament placement payout shares must total 1000 permille')
    payout_raw=raw.get('prize_payout_permille',{})
    if not isinstance(payout_raw,Mapping): payout_raw={}
    payout={
        'first':max(0,int(payout_raw.get('first',0))),
        'second':max(0,int(payout_raw.get('second',0))),
        'third':max(0,int(payout_raw.get('third',0))),
        'fourth':max(0,int(payout_raw.get('fourth',0))),
    }
    if sum(payout.values())!=1000:
        raise ValueError('tournament placement payout permille must total 1000')
    return {
        'entry_fee_cash': max(0,int(raw.get('entry_fee_cash',0))),
        'placement_faction_share_permille': placement_faction_share,
        'placement_personal_share_permille': placement_personal_share,
        'prize_payout_permille': payout,
        'prestige_weight': max(0,min(100,int(raw.get('prestige_weight',50)))),
        'allows_outlaw_factions': bool(raw.get('allows_outlaw_factions',False)),
        'faction_interest_floor_permille': max(0,min(1000,int(raw.get('faction_interest_floor_permille',0)))),
        'major_sect_population_threshold': max(1,int(raw.get('major_sect_population_threshold',100))),
        'major_sect_competitor_floor': max(0,int(raw.get('major_sect_competitor_floor',0))),
        'major_institution_population_threshold': max(1,int(raw.get('major_institution_population_threshold',100))),
        'major_institution_competitor_floor': max(0,int(raw.get('major_institution_competitor_floor',0))),
        'ordinary_competitor_floor': max(0,int(raw.get('ordinary_competitor_floor',0))),
        'additional_competitor_interest_permille': max(0,min(1000,int(raw.get('additional_competitor_interest_permille',0)))),
        'additional_competitor_decay_permille': max(0,int(raw.get('additional_competitor_decay_permille',0))),
        'additional_competitor_relative_strength_permille': max(0,min(1000,int(raw.get('additional_competitor_relative_strength_permille',0)))),
        'convergence_days_before': max(0,int(raw.get('convergence_days_before',0))),
        'arrival_lead_hours_min': max(0,int(raw.get('arrival_lead_hours_min',12))),
        'arrival_lead_hours_max': max(0,int(raw.get('arrival_lead_hours_max',36))),
        'convergence_contacts_per_faction_per_day': max(0,int(raw.get('convergence_contacts_per_faction_per_day',0))),
        'safe_conduct_on_official_grounds': bool(raw.get('safe_conduct_on_official_grounds',False)),
        'matches_per_competition_session': max(1,int(raw.get('matches_per_competition_session',16))),
        'competition_sessions_per_day': max(1,int(raw.get('competition_sessions_per_day',1))),
        'max_exchanges_per_match': max(1,int(raw.get('max_exchanges_per_match',96))),
        'spectator_delegation_floor': max(0,int(raw.get('spectator_delegation_floor',0))),
        'major_spectator_population_threshold': max(1,int(raw.get('major_spectator_population_threshold',100))),
        'major_spectator_delegation_floor': max(0,int(raw.get('major_spectator_delegation_floor',0))),
        'major_sect_spectator_delegation_floor': max(0,int(raw.get('major_sect_spectator_delegation_floor',0))),
        'leader_attendance_permille': max(0,min(1000,int(raw.get('leader_attendance_permille',0)))),
        'spectator_marginal_interest_permille': max(0,min(1000,int(raw.get('spectator_marginal_interest_permille',0)))),
        'spectator_marginal_decay_permille': max(0,int(raw.get('spectator_marginal_decay_permille',0))),
        'attendee_host_cash_per_person_day': max(0,int(raw.get('attendee_host_cash_per_person_day',0))),
        'public_spectator_ticket_cash_per_day': max(0,int(raw.get('public_spectator_ticket_cash_per_day',0))),
        'faction_delegate_ticket_cash_per_day': max(0,int(raw.get('faction_delegate_ticket_cash_per_day',0))),
        'public_faction_standings_count': max(0,int(raw.get('public_faction_standings_count',0))),
        'faction_performance_max_martial_respect': max(0,min(20,int(raw.get('faction_performance_max_martial_respect',0)))),
    }


def convergence_day_theme(tournament_kind: str, day_index: int) -> str:
    rows = _data().get('convergence_programs', {}).get(tournament_kind, [])
    if not isinstance(rows, list) or not rows:
        return 'delegation_gathering'
    idx = max(1, int(day_index)) - 1
    return str(rows[min(idx, len(rows) - 1)])

def estimated_host_days(tournament_kind: str, planned_field_size: int) -> int:
    """Budget host-city attendance from the actual uncapped planned field.

    The championship bracket needs ``N - 1`` matches. Every championship loser
    except the final runner-up enters the full losers bracket, whose winner
    earns third place; that bracket needs ``N - 3`` more matches for ``N >= 3``.
    Venue throughput therefore determines the real number of competition days
    without imposing an entrant cap. Arrival and awards/departure receive one
    additional host day each.
    """
    profile=event_profile(tournament_kind)
    throughput=max(
        1,
        int(profile['matches_per_competition_session'])*int(profile['competition_sessions_per_day']),
    )
    field=max(0,int(planned_field_size))
    if field <= 1:
        matches=0
    elif field == 2:
        matches=1
    else:
        matches=(field-1)+(field-3)
    competition_days=max(1,(matches+throughput-1)//throughput)
    return int(profile['convergence_days_before'])+competition_days+2

def open_tournament(*,event_id:str,format_ref:str,organizer_ref:str,great:bool=False)->dict[str,Any]:
    """Open a self-funded tournament with an initially empty prize escrow.

    The organizer supplies jurisdiction/venue administration, not a conjured
    purse or faction-style treasury. Every sponsoring faction registration fee
    enters prize escrow in full. Host-city attendance economics are separate.
    """
    if format_ref!='individual': raise KeyError(format_ref)
    kind='great_jianghu_tournament' if great else 'regional_martial_tournament'
    profile=event_profile(kind)
    return {
        'event_id':event_id,'format':format_ref,'organizer_ref':organizer_ref,
        'prize_escrow_cash':0,'entry_fees_collected_cash':0,
        'delegate_ticket_cash_collected':0,
        'public_ticket_cash_collected':0,
        'entry_fee_cash':int(profile['entry_fee_cash']),
        'placement_faction_share_permille':int(profile['placement_faction_share_permille']),
        'placement_personal_share_permille':int(profile['placement_personal_share_permille']),
        'prize_payout_permille':dict(profile['prize_payout_permille']),
        'registrations':[],'delegations':{},'status':'registration_open',
    }
def register(tournament:Mapping[str,Any],*,entrant_ref:str,qualifying_score:int,payer_cash:int,alive:bool=True,medically_eligible:bool=True)->dict[str,Any]:
    if tournament.get('status')!='registration_open': raise ValueError('registration closed')
    if not alive or not medically_eligible: raise ValueError('entrant ineligible')
    fmt=str(tournament['format'])
    if fmt!='individual': raise ValueError('unsupported tournament format')
    fee=max(0,int(tournament.get('entry_fee_cash',0)))
    if payer_cash<fee: raise ValueError('entry fee insufficient')
    out={**tournament,'registrations':[dict(x) for x in tournament.get('registrations',[])]}
    if any(r['entrant_ref']==entrant_ref for r in out['registrations']): raise ValueError('duplicate registration')
    prize_contribution=fee
    out['prize_escrow_cash']=max(0,int(out.get('prize_escrow_cash',0)))+prize_contribution
    out['entry_fees_collected_cash']=max(0,int(out.get('entry_fees_collected_cash',0)))+fee
    out['registrations'].append({
        'entrant_ref':entrant_ref,'public_qualifying_score':int(qualifying_score),
        'fee_cash':fee,'prize_contribution_cash':prize_contribution,
    })
    return {
        'tournament_after':out,'payer_cash_after':payer_cash-fee,
        'prize_contribution_cash':prize_contribution,
    }
def add_attendance_prize_cash(tournament: Mapping[str, Any], *, amount_cash: int, source_kind: str) -> dict[str, Any]:
    """Move already-conserved paid attendance cash into prize escrow.

    The civic/Imperial host is an aggregate jurisdiction authority, not a
    faction wallet. Faction-delegation admissions and aggregate public tickets
    therefore join the same fighter prize escrow instead of funding a fake host
    treasury. Lodging, food and ordinary host-city spending remain separate
    regional-economy flows.
    """
    amount=max(0,int(amount_cash))
    out=dict(tournament)
    if amount<=0:
        return out
    if source_kind=='faction_delegate_ticket':
        key='delegate_ticket_cash_collected'
    elif source_kind=='public_spectator_ticket':
        key='public_ticket_cash_collected'
    else:
        raise KeyError(source_kind)
    out['prize_escrow_cash']=max(0,int(out.get('prize_escrow_cash',0)))+amount
    out[key]=max(0,int(out.get(key,0)))+amount
    return out


def close_registration(tournament:Mapping[str,Any])->dict[str,Any]:
    if tournament.get('status')!='registration_open': raise ValueError('registration not open')
    rows=[{'person_ref':r['entrant_ref'],'public_qualifying_score':int(r['public_qualifying_score'])} for r in tournament.get('registrations',[])]
    out={
        **tournament,
        'status':'bracket_ready',
        'phase':'championship',
        'bracket':[list(x) for x in tournament_bracket(rows)],
        'round_number':1,
        'round_participant_count':len(rows),
        'round_winners':[],
        'championship_losers':[],
        'placements':{},
    }
    return out


def merge_delegation_presence(
    tournament: Mapping[str, Any], *, faction_ref: str, camp: str = "",
    entrant_refs: Sequence[str] = (), spectator_refs: Sequence[str] = (),
    leader_refs: Sequence[str] = (), senior_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Merge physically present faction representatives into one delegation.

    Competitors and non-competing delegates travel through different causal
    operations, but they are one institutional delegation once physically at
    the host.  The merge is set-like and idempotent so a sect master who enters
    the bracket remains a leader/witness instead of being reduced to an entrant
    row, and a person can never inflate ``present_count`` by arriving through
    more than one presentation role.
    """
    fid = str(faction_ref or "")
    if not fid:
        raise ValueError("tournament delegation faction missing")
    out = dict(tournament)
    raw_delegations = out.get("delegations", {})
    delegations = {
        str(key): dict(value)
        for key, value in raw_delegations.items()
        if isinstance(key, str) and isinstance(value, Mapping)
    } if isinstance(raw_delegations, Mapping) else {}
    row = dict(delegations.get(fid, {"faction_ref": fid}))
    for key, refs in (
        ("entrant_refs", entrant_refs),
        ("spectator_refs", spectator_refs),
        ("leader_refs", leader_refs),
        ("senior_refs", senior_refs),
    ):
        existing = {str(x) for x in row.get(key, []) if isinstance(x, str) and x}
        existing.update(str(x) for x in refs if isinstance(x, str) and x)
        row[key] = sorted(existing)
    if camp:
        row["camp"] = str(camp)
    row["present_count"] = len(
        set(row.get("entrant_refs", [])) | set(row.get("spectator_refs", []))
    )
    delegations[fid] = row
    out["delegations"] = delegations
    return out


def convergence_pairs(
    faction_refs: Sequence[str], *, tournament_ref: str, day_index: int,
    contacts_per_faction: int = 1,
) -> list[tuple[str, str]]:
    """Return bounded deterministic delegation contacts for one gathering day.

    This is not an all-pairs social graph.  Each contact pass hashes the same
    physically present faction set into a stable ordering and pairs neighbors.
    Multiple passes can broaden a major event without allowing O(N^2) state
    growth.  Duplicate pairs within the same day are removed.
    """
    import hashlib
    refs = sorted(set(str(x) for x in faction_refs if isinstance(x, str) and x))
    if len(refs) < 2:
        return []
    passes = max(0, int(contacts_per_faction))
    pairs: set[tuple[str, str]] = set()
    for contact_index in range(passes):
        ordered = sorted(
            refs,
            key=lambda ref: (
                hashlib.sha256(
                    f"{tournament_ref}|{int(day_index)}|{contact_index}|{ref}".encode("utf-8")
                ).digest(),
                ref,
            ),
        )
        for idx in range(0, len(ordered) - 1, 2):
            a, b = ordered[idx], ordered[idx + 1]
            pairs.add((a, b) if a < b else (b, a))
    return sorted(pairs)

def themed_convergence_pairs(
    faction_refs: Sequence[str], *, tournament_ref: str, day_index: int,
    tournament_kind: str, theme: str, contacts_per_faction: int,
    senior_faction_refs: Sequence[str] = (),
    camp_by_faction: Mapping[str, str] | None = None,
    hostility_by_pair: Mapping[tuple[str, str], int] | None = None,
) -> list[tuple[str, str]]:
    """Return bounded contacts whose pairing reflects the official program.

    Great-Tournament senior assembly days pair senior delegations primarily
    inside their political camp so bloc familiarity can grow from real face to
    face contact.  Private-negotiation days seat existing rivalry/feud/war pairs
    first, then fill unused senior tables with ordinary contacts.  Camps never
    create hostility by themselves and no branch creates an all-pairs graph.
    """
    refs = sorted(set(str(x) for x in faction_refs if isinstance(x, str) and x))
    contacts = max(0, int(contacts_per_faction))
    if contacts <= 0 or len(refs) < 2:
        return []
    if tournament_kind != "great_jianghu_tournament":
        return convergence_pairs(
            refs, tournament_ref=tournament_ref, day_index=day_index,
            contacts_per_faction=contacts,
        )
    senior = sorted(set(str(x) for x in senior_faction_refs if isinstance(x, str) and x) & set(refs))
    camps = dict(camp_by_faction or {})
    hostility = {
        (min(str(a), str(b)), max(str(a), str(b))): max(0, int(value))
        for (a, b), value in (hostility_by_pair or {}).items()
        if isinstance(a, str) and isinstance(b, str) and a and b and a != b
    }
    if theme == "senior_faction_assembly":
        grouped: dict[str, list[str]] = {}
        for fid in senior:
            grouped.setdefault(str(camps.get(fid) or "unclassified"), []).append(fid)
        out: set[tuple[str, str]] = set()
        for camp, group in sorted(grouped.items()):
            out.update(convergence_pairs(
                group, tournament_ref=f"{tournament_ref}|senior-assembly|{camp}",
                day_index=day_index, contacts_per_faction=contacts,
            ))
        return sorted(out)
    if theme == "private_negotiations_and_rivalry_mediation":
        import hashlib
        candidates: list[tuple[int, int, str, str]] = []
        senior_set = set(senior)
        for (fa, fb), value in hostility.items():
            if fa not in senior_set or fb not in senior_set or value < 30:
                continue
            roll = int.from_bytes(
                hashlib.sha256(
                    f"tournament-negotiation-table|{tournament_ref}|{int(day_index)}|{fa}|{fb}".encode("utf-8")
                ).digest()[:8], "big"
            ) % 1000
            candidates.append((-value, roll, fa, fb))
        candidates.sort()
        used: dict[str, int] = {}
        selected: list[tuple[str, str]] = []
        selected_set: set[tuple[str, str]] = set()
        for _neg_hostility, _roll, fa, fb in candidates:
            if used.get(fa, 0) >= contacts or used.get(fb, 0) >= contacts:
                continue
            pair = (fa, fb) if fa < fb else (fb, fa)
            selected.append(pair); selected_set.add(pair)
            used[fa] = used.get(fa, 0) + 1; used[fb] = used.get(fb, 0) + 1
        for fa, fb in convergence_pairs(
            senior, tournament_ref=f"{tournament_ref}|negotiation-fill",
            day_index=day_index, contacts_per_faction=contacts,
        ):
            pair = (fa, fb) if fa < fb else (fb, fa)
            if pair in selected_set:
                continue
            if used.get(fa, 0) >= contacts or used.get(fb, 0) >= contacts:
                continue
            selected.append(pair); selected_set.add(pair)
            used[fa] = used.get(fa, 0) + 1; used[fb] = used.get(fb, 0) + 1
        return sorted(selected)
    return convergence_pairs(
        refs, tournament_ref=tournament_ref, day_index=day_index,
        contacts_per_faction=contacts,
    )


def _seed_phase_bracket(refs: Sequence[str], regs: Mapping[str, int], *, bonuses: Mapping[str, int] | None = None) -> list[list[str | None]]:
    rows=[]
    bonus_map=bonuses or {}
    for ref in refs:
        stable_ref=str(ref)
        rows.append({
            'person_ref':stable_ref,
            'public_qualifying_score':max(0,int(bonus_map.get(stable_ref,0)))*1_000_000+int(regs.get(stable_ref,0)),
        })
    return [list(x) for x in tournament_bracket(rows)]


def _settle_bracket_frontier(tournament: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Advance only completed rounds/phases without consuming a new match."""
    out=dict(tournament)
    regs={str(r.get('entrant_ref')):int(r.get('public_qualifying_score',0)) for r in out.get('registrations',[]) if isinstance(r,Mapping) and isinstance(r.get('entrant_ref'),str)}
    out.setdefault('phase','championship')
    out.setdefault('placements',{})
    while True:
        if out.get('active_pair'):
            return out,False
        bracket=[list(x) for x in out.get('bracket',[]) if isinstance(x,(list,tuple)) and len(x)==2]
        if bracket:
            out['bracket']=bracket
            return out,False
        phase=str(out.get('phase') or 'championship')
        winners=[str(x) for x in out.get('round_winners',[]) if isinstance(x,str)]
        if len(winners)>1:
            out['bracket']=_seed_phase_bracket(winners,regs)
            out['round_winners']=[]
            out['round_number']=max(1,int(out.get('round_number',1)))+1
            out['round_participant_count']=len(winners)
            continue
        if len(winners)==1:
            phase_winner=winners[0]
            placements=dict(out.get('placements',{})) if isinstance(out.get('placements'),Mapping) else {}
            if phase=='championship':
                out['champion_ref']=phase_winner
                placements['first']=phase_winner
                runner_up=str(out.get('runner_up_ref') or '')
                if runner_up:
                    placements['second']=runner_up
                out['placements']=placements
                losers=[dict(row) for row in out.get('championship_losers',[]) if isinstance(row,Mapping) and isinstance(row.get('person_ref'),str)]
                losers_bracket_refs=[]; bonuses={}; seen=set()
                for row in losers:
                    ref=str(row['person_ref'])
                    if not ref or ref in seen or ref in {phase_winner,runner_up}:
                        continue
                    seen.add(ref); losers_bracket_refs.append(ref)
                    bonuses[ref]=max(0,int(row.get('lost_round',0)))
                if losers_bracket_refs:
                    out['phase']='losers_bracket'
                    out['bracket']=_seed_phase_bracket(losers_bracket_refs,regs,bonuses=bonuses)
                    out['round_winners']=[]
                    out['round_number']=1
                    out['round_participant_count']=len(losers_bracket_refs)
                    out['status']='in_progress'
                    continue
                out['status']='completed'; out.pop('round_winners',None); out.pop('bracket',None)
                return out,True
            placements['third']=phase_winner
            fourth=str(out.get('losers_bracket_runner_up_ref') or '')
            if fourth:
                placements['fourth']=fourth
            out['placements']=placements
            out['status']='completed'; out.pop('round_winners',None); out.pop('bracket',None)
            return out,True
        if phase=='championship':
            out['status']='completed'; out['champion_ref']=None; out['placements']={}
        else:
            out['status']='completed'
        out.pop('round_winners',None); out.pop('bracket',None)
        return out,True


def begin_next_match(tournament:Mapping[str,Any])->dict[str,Any]:
    """Pop the next match across championship then full losers-bracket play.

    One loss in the championship bracket moves a fighter into the losers bracket
    field unless that loss is the championship final, which establishes second
    place. The losers-bracket winner earns third and its finalist earns fourth.
    """
    out={**tournament}
    if out.get('status') not in {'bracket_ready','in_progress'}: raise ValueError('tournament bracket not ready')
    if out.get('active_pair'): raise ValueError('tournament match already active')
    while True:
        out,completed=_settle_bracket_frontier(out)
        if completed:
            return {'tournament_after':out,'pair':None,'completed':True,'champion_ref':out.get('champion_ref'),'placements':dict(out.get('placements',{})) if isinstance(out.get('placements'),Mapping) else {}}
        bracket=[list(x) for x in out.get('bracket',[]) if isinstance(x,(list,tuple)) and len(x)==2]
        winners=[str(x) for x in out.get('round_winners',[]) if isinstance(x,str)]
        a,b=bracket.pop(0); out['bracket']=bracket
        if b is None:
            winners.append(str(a)); out['round_winners']=winners
            continue
        phase=str(out.get('phase') or 'championship')
        pair=[str(a),str(b)]
        out['active_pair']=pair; out['active_phase']=phase
        out['active_round_participant_count']=max(2,int(out.get('round_participant_count',2)))
        out['status']='in_progress'
        return {'tournament_after':out,'pair':pair,'completed':False,'champion_ref':out.get('champion_ref'),'placements':dict(out.get('placements',{})) if isinstance(out.get('placements'),Mapping) else {}}

def record_match_winner(tournament:Mapping[str,Any],*,winner_ref:str)->dict[str,Any]:
    out={**tournament}
    pair=out.get('active_pair')
    if not isinstance(pair,(list,tuple)) or len(pair)!=2 or winner_ref not in pair: raise ValueError('tournament winner not in active pair')
    loser_ref=str(pair[1] if str(pair[0])==str(winner_ref) else pair[0])
    phase=str(out.get('active_phase') or out.get('phase') or 'championship')
    round_size=max(2,int(out.get('active_round_participant_count',out.get('round_participant_count',2))))
    if phase=='championship':
        if round_size<=2:
            out['runner_up_ref']=loser_ref
        else:
            losers=[dict(x) for x in out.get('championship_losers',[]) if isinstance(x,Mapping)]
            if not any(str(x.get('person_ref') or '')==loser_ref for x in losers):
                losers.append({'person_ref':loser_ref,'lost_round':max(1,int(out.get('round_number',1)))})
            out['championship_losers']=losers
    elif round_size<=2:
        out['losers_bracket_runner_up_ref']=loser_ref
    winners=[str(x) for x in out.get('round_winners',[]) if isinstance(x,str)]
    winners.append(str(winner_ref)); out['round_winners']=winners
    out.pop('active_pair',None); out.pop('active_phase',None); out.pop('active_round_participant_count',None)
    out['status']='in_progress'
    return out

def placement_payouts(tournament: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Allocate the entire funded purse across the earned placements.

    Configured weights sum to 1000 for a normal four-place field.  If a tiny
    tournament cannot produce every placement, the available places are
    renormalized so no entrant, spectator or public ticket money is stranded or
    diverted to the host.
    """
    purse=max(0,int(tournament.get('prize_escrow_cash',0)))
    placements=tournament.get('placements',{}) if isinstance(tournament.get('placements'),Mapping) else {}
    weights=tournament.get('prize_payout_permille',{}) if isinstance(tournament.get('prize_payout_permille'),Mapping) else {}
    ordered=[]
    for place in ('first','second','third','fourth'):
        ref=str(placements.get(place) or '')
        weight=max(0,int(weights.get(place,0)))
        if ref and weight>0:
            ordered.append((place,ref,weight))
    if purse<=0 or not ordered:
        return []
    total_weight=sum(weight for _place,_ref,weight in ordered)
    if total_weight<=0:
        return []
    rows=[]; paid=0
    for index,(place,ref,weight) in enumerate(ordered):
        cash=purse-paid if index==len(ordered)-1 else purse*weight//total_weight
        paid+=cash
        rows.append({'place':place,'entrant_ref':ref,'cash':cash})
    return rows


def _combatant_usable(person:Mapping[str,Any]|None)->bool:
    if not isinstance(person,Mapping): return False
    health=person.get('health',{}) if isinstance(person.get('health'),Mapping) else {}
    return health.get('status')!='dead' and int(health.get('consciousness',100))>0

def _referee_winner(a:str,b:str,people:Mapping[str,Mapping[str,Any]])->str:
    """Deterministic stoppage decision after the bounded exact-combat window."""
    def score(ref:str)->tuple[int,int,int,int,str]:
        person=people.get(ref,{})
        health=person.get('health',{}) if isinstance(person,Mapping) and isinstance(person.get('health'),Mapping) else {}
        injuries=health.get('injuries',[]) if isinstance(health.get('injuries'),list) else []
        consciousness=max(0,int(health.get('consciousness',100)))
        shock=max(0,int(health.get('shock',0)))
        severe=sum(max(0,int(row.get('severity',0))) for row in injuries if isinstance(row,Mapping))
        dead=1 if health.get('status')=='dead' else 0
        # Higher tuple wins; the final stable-ID inverse is handled outside.
        return (-dead,consciousness,-shock,-severe,ref)
    sa=score(a); sb=score(b)
    if sa[:-1]>sb[:-1]: return a
    if sb[:-1]>sa[:-1]: return b
    return min(a,b)


def faction_performance_standings(
    performance_points: Mapping[str, Any],
    entrant_owner_map: Mapping[str, str],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return a compact public faction table for one tournament.

    Match-win points show institutional depth while the individual champion is
    still reported separately.  Entrant count is included so equal win totals
    prefer the faction that achieved them with the stronger advancement rate.
    The table is a bounded result projection, not an entrant or participation
    cap.
    """
    registrations: dict[str, int] = {}
    for faction_ref in entrant_owner_map.values():
        if isinstance(faction_ref, str) and faction_ref:
            registrations[faction_ref] = registrations.get(faction_ref, 0) + 1
    rows = [
        {
            "faction_ref": str(faction_ref),
            "match_wins": max(0, int(points)),
            "entrant_count": max(0, int(registrations.get(str(faction_ref), 0))),
            "wins_per_entrant_milli": max(0, int(points)) * 1000 // max(1, int(registrations.get(str(faction_ref), 0))),
        }
        for faction_ref, points in performance_points.items()
        if isinstance(faction_ref, str) and int(points) > 0
    ]
    rows.sort(key=lambda row: (-int(row["match_wins"]), -int(row["wins_per_entrant_milli"]), str(row["faction_ref"])))
    return rows[: max(0, int(limit))]

def advance_individual_competition(
    tournament:Mapping[str,Any], *, people:Mapping[str,Mapping[str,Any]],
    equipment_ledger:Mapping[str,Any], doctrines:Mapping[str,Mapping[str,Any]],
    combats_state:Mapping[str,Any], zone_ref:str, at_iso:str, player_ref:str|None=None,
    max_exchanges:int=240, max_matches:int|None=None,
)->dict[str,Any]:
    """Advance a live individual tournament through one bounded competition session.

    NPC matches use the authoritative exact-combat engine.  A match involving
    the player creates one normal combat owner and returns immediately.  When
    called again after that combat resolves, the same tournament consumes the
    combat result and continues.  ``max_matches`` is venue/day throughput, not
    an entrant or bracket cap: every registered entrant remains in the same
    bracket and later calls resume from the exact current frontier.  No separate
    tournament match history is kept.
    """
    out={**tournament}
    persons={str(k):dict(v) for k,v in people.items() if isinstance(k,str) and isinstance(v,Mapping)}
    ledger={**equipment_ledger}
    combats={**combats_state}
    combat_map=combats.setdefault('combats',{})
    if not isinstance(combat_map,dict): raise ValueError('jianghu combat state invalid')
    points:dict[str,int]={}
    resolved_pairs:list[list[str]]=[]
    match_count=0
    session_limit=None if max_matches is None else max(1,int(max_matches))

    def result_payload(*,waiting:bool,completed:bool,champion:Any=None,combat_ref:Any=None,continuation:bool=False)->dict[str,Any]:
        return {
            'tournament_after':out,'people_after':persons,'equipment_ledger_after':ledger,
            'combats_state_after':combats,'waiting_for_player':waiting,'completed':completed,
            'champion_ref':champion,'placements':dict(out.get('placements',{})) if isinstance(out.get('placements'),Mapping) else {},
            'winner_points':points,'combat_ref':combat_ref,
            'resolved_pairs':resolved_pairs,'matches_resolved_count':match_count,
            'continuation_required':bool(continuation),
        }

    def session_exhausted()->bool:
        return session_limit is not None and match_count>=session_limit

    if out.get('status')=='awaiting_player_match':
        combat_ref=str(out.get('pending_combat_ref') or '')
        combat=combat_map.get(combat_ref)
        pair=out.get('active_pair')
        if not combat_ref or not isinstance(combat,Mapping) or not isinstance(pair,(list,tuple)) or len(pair)!=2:
            raise ValueError('tournament pending combat invalid')
        if combat.get('status')!='resolved':
            return result_payload(waiting=True,completed=False,combat_ref=combat_ref)
        winner=str(pair[0] if combat.get('winner_side')=='side_a' else pair[1])
        phase=str(out.get('active_phase') or out.get('phase') or 'championship')
        out.pop('pending_combat_ref',None)
        out=record_match_winner(out,winner_ref=winner)
        points[winner]=points.get(winner,0)+(2 if phase=='championship' else 1)
        resolved_pairs.append([str(pair[0]),str(pair[1])])
        match_count+=1
        combat_map.pop(combat_ref,None)
        if session_exhausted():
            out, completed_now = _settle_bracket_frontier(out)
            if completed_now:
                return result_payload(waiting=False,completed=True,champion=out.get('champion_ref'))
            return result_payload(waiting=False,completed=False,continuation=True)

    if out.get('status') not in {'bracket_ready','in_progress'}:
        if out.get('status')=='completed':
            return result_payload(waiting=False,completed=True,champion=out.get('champion_ref'))
        raise ValueError('tournament competition not ready')

    while True:
        nxt=begin_next_match(out); out=nxt['tournament_after']
        if nxt['completed']:
            champion=nxt.get('champion_ref')
            return result_payload(waiting=False,completed=True,champion=champion)
        pair=nxt['pair']; a,b=str(pair[0]),str(pair[1])
        phase=str(out.get('active_phase') or out.get('phase') or 'championship')
        point_value=2 if phase=='championship' else 1
        usable_a=_combatant_usable(persons.get(a)); usable_b=_combatant_usable(persons.get(b))
        if not usable_a or not usable_b:
            winner=b if usable_b and not usable_a else a if usable_a and not usable_b else min(a,b)
            out=record_match_winner(out,winner_ref=winner); points[winner]=points.get(winner,0)+point_value
            match_count+=1
            if session_exhausted():
                out, completed_now = _settle_bracket_frontier(out)
                if completed_now:
                    return result_payload(waiting=False,completed=True,champion=out.get('champion_ref'))
                return result_payload(waiting=False,completed=False,continuation=True)
            continue
        if player_ref and player_ref in {a,b}:
            combat_ref=f"combat:{out.get('event_id','tournament')}:{phase}:{int(out.get('round_number',1))}:{a}:{b}"
            if combat_ref not in combat_map:
                combat_map[combat_ref]=initialize_combat(
                    combat_ref=combat_ref,side_a_refs=[a],side_b_refs=[b],people=persons,
                    zone_ref=zone_ref,started_at=at_iso,objective={'kind':'tournament_match','tournament_ref':out.get('tournament_ref') or out.get('event_id'),'phase':phase},
                    awareness_mode='mutual',initial_range_band=1,equipment_ledger=ledger,
                )
            out['status']='awaiting_player_match'; out['pending_combat_ref']=combat_ref
            return result_payload(waiting=True,completed=False,combat_ref=combat_ref)
        result=simulate_exact_combat(
            combat_ref=f"combat:{out.get('event_id','tournament')}:{phase}:{int(out.get('round_number',1))}:{a}:{b}",
            side_a_refs=[a],side_b_refs=[b],people=persons,equipment_ledger=ledger,
            doctrines=doctrines,zone_ref=zone_ref,started_at=at_iso,
            objective={'kind':'tournament_match','tournament_ref':out.get('tournament_ref') or out.get('event_id'),'phase':phase},
            targeting_intent='disable',max_exchanges=max_exchanges,
        )
        persons={str(k):dict(v) for k,v in result['people_after'].items() if isinstance(v,Mapping)}
        ledger={**result['equipment_ledger_after']}
        if result.get('resolved') and result.get('winner_side')=='side_a': winner=a
        elif result.get('resolved') and result.get('winner_side')=='side_b': winner=b
        else: winner=_referee_winner(a,b,persons)
        out=record_match_winner(out,winner_ref=winner); points[winner]=points.get(winner,0)+point_value
        resolved_pairs.append([a,b])
        match_count+=1
        if session_exhausted():
            out, completed_now = _settle_bracket_frontier(out)
            if completed_now:
                return result_payload(waiting=False,completed=True,champion=out.get('champion_ref'))
            return result_payload(waiting=False,completed=False,continuation=True)
