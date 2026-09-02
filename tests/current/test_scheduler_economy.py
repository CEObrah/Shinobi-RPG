import copy, json
from datetime import datetime, timedelta
from pathlib import Path

from shinobi_runtime.martial_world.scheduler import initial_schedule,due_events,settle_schedule,sync_route_activity
from shinobi_runtime.martial_world.compensation import settle_monthly_compensation
from shinobi_runtime.martial_world.training import training_gain_milli
from shinobi_runtime.martial_world.infrastructure import building_upgrade_quote
from shinobi_runtime.martial_world.regional_economy import initial_market_state, quote_purchase, execute_purchase
from shinobi_runtime.martial_world.events import calendar_event_occurrence

ROOT=Path(__file__).resolve().parents[2]
def load(rel): return json.loads((ROOT/rel).read_text())


def test_scheduler_uses_due_frontier_and_resumable_owner_cursor():
    start=datetime(61,10,6)
    owners=[f'f{i:03}' for i in range(130)]
    s=initial_schedule(start=start,faction_ids=owners,region_ids=['r1'],route_ids=[])

    # Annual life review is one compact recurring class. Owner cursor chunking
    # provides resumability without materializing one future event per faction.
    annual=s['recurring']['faction_annual']
    assert annual['owner_refs']==owners
    assert annual['interval_days']==365
    assert annual['owner_cursor']==0
    assert not [row for row in s['one_off'].values() if row.get('kind')=='annual_faction_life_review']

    # Monthly region/faction classes still share one due boundary. Region work
    # reduces first; faction work then resumes in deterministic four-owner
    # chunks until every owner at that timestamp is settled.
    at=datetime.fromisoformat(s['recurring']['region_monthly']['next_due_at'])
    events=due_events(s,after=at-timedelta(seconds=1),through=at)
    assert {e['schedule_class'] for e in events}=={'region_monthly'}
    s=settle_schedule(s,through=at,processed_events=events)
    events2=due_events(s,after=at,through=at)
    assert {e['schedule_class'] for e in events2}=={'faction_monthly'}
    assert len({e['owner_ref'] for e in events2})==4
    s2=settle_schedule(s,through=at,processed_events=events2)
    events3=due_events(s2,after=at,through=at)
    assert len({e['owner_ref'] for e in events3})==4
    assert {e['owner_ref'] for e in events2}.isdisjoint({e['owner_ref'] for e in events3})
    assert s2['recurring']['faction_monthly']['next_due_at']==at.isoformat()


def test_great_tournament_advance_notice_is_discoverable_one_year_before_event():
    # Calendar lookahead must discover advance obligations even though the
    # competition itself lies far beyond the ordinary short horizon.
    start=datetime(63,9,1,9,0,0)
    through=datetime(63,9,2,9,0,0)
    rows=due_events({"recurring":{},"one_off":{}},after=start,through=through)
    notice=next(row for row in rows if row.get('kind')=='tournament_advance_notice')
    assert notice['tournament_kind']=='great_jianghu_tournament'
    assert notice['competition_date']=='0064-09-01'
    occurrence=calendar_event_occurrence('great_jianghu_tournament',64)
    assert notice['registration_opens_on']==occurrence['registration_opens_on']
    assert notice['registration_closes_on']=='0064-08-24'
    assert notice['host_place_id']=='luoyang'


def test_canonical_scheduler_uses_compact_annual_class_and_no_virtual_crop_timers():
    schedule=load('state/martial-world/scheduler.json')
    faction_docs=[load(f'state/martial-world/factions/{p.stem}.json') for p in sorted((ROOT/'state/martial-world/factions').glob('*.json'))]
    annual=schedule['recurring']['faction_annual']
    assert annual['interval_days']==365
    assert set(annual['owner_refs'])=={f['faction_id'] for f in faction_docs}
    assert len(annual['owner_refs'])==len(faction_docs)==240
    rows=list(schedule.get('one_off',{}).values())
    assert not [row for row in rows if row.get('kind')=='annual_faction_life_review']
    assert not [row for row in rows if row.get('kind')=='agriculture_harvest_due']
    # Agriculture remains a real faction enterprise, but monthly production is
    # derived from its current managed land instead of virtual crop entities.
    agricultural=[
        f for f in faction_docs
        if int((f.get('enterprises') or {}).get('agriculture_landholding',0) or 0)>0
        and int(((f.get('enterprise_scale') or {}).get('agriculture_landholding') or {}).get('managed_land_mu',0) or 0)>0
    ]
    assert agricultural



def test_routes_are_demand_driven_not_every_map_route_daily():
    start=datetime(61,10,6)
    s=initial_schedule(start=start,faction_ids=['f1'],region_ids=['r1'],route_ids=[f'road{i}' for i in range(700)])
    assert 'route_daily' not in s['recurring']
    s=sync_route_activity(s,active_route_ids=['road9','road2'],now=start)
    assert s['recurring']['route_daily']['owner_refs']==['road2','road9']
    route_due=datetime.fromisoformat(s['recurring']['route_daily']['next_due_at'])
    events=due_events(s,after=start,through=route_due)
    assert {e['owner_ref'] for e in events}=={'road2','road9'}
    s=settle_schedule(s,through=route_due,processed_events=events)
    s=sync_route_activity(s,active_route_ids=[],now=route_due)
    assert 'route_daily' not in s['recurring']


def test_faction_pay_transfers_treasury_to_personal_cash_without_new_ledger():
    faction=load('state/martial-world/factions/house_tang.json')
    roster=load('state/martial-world/people/house_tang.json')
    before_treasury=faction['treasury_cash']; before_cash=sum(int(p.get('personal_cash',0)) for p in roster['people'])
    result=settle_monthly_compensation(faction,roster)
    after_cash=sum(int(p.get('personal_cash',0)) for p in result['roster']['people'])
    assert before_treasury-result['faction']['treasury_cash']==result['paid_cash']
    assert after_cash-before_cash==result['paid_cash']
    assert 'payroll_ledger' not in result


def test_training_is_deterministic_and_uncapped():
    args=dict(current_skill=250,aptitude=200,hours_milli=48000,instructor_skill=400,instruction_skill=200,facility_level=5,health_milli=1000,novelty_milli=1000,recovery_milli=1000)
    a=training_gain_milli(**args); b=training_gain_milli(**args)
    assert a==b and a>0


def test_max_aptitude_is_one_and_half_times_ordinary_learning_coefficient_without_changing_ordinary_baseline():
    common=dict(current_skill=40,hours_milli=48000,instructor_skill=100,instruction_skill=80,facility_level=5,health_milli=1000,novelty_milli=1000,recovery_milli=1000)
    ordinary=training_gain_milli(aptitude=100,**common)
    maximum=training_gain_milli(aptitude=200,**common)
    assert ordinary>0
    assert abs(2 * maximum - 3 * ordinary) <= 2


def test_building_upgrade_has_real_material_labor_cash_and_time():
    q=building_upgrade_quote('armory_workshop',5)
    req=q['requirements']
    assert req['cash_overhead']>0
    assert req['general_labor_hours']>0 and req['skilled_labor_hours']>0
    assert req['minimum_calendar_days']>0
    assert sum(req['materials'].values())>0


def test_regional_market_purchase_conserves_stock_and_cash():
    market=initial_market_state('central_plain')
    item=next(iter(market['stock']))
    q=quote_purchase('central_plain',item,1,market)
    result=execute_purchase('central_plain',item,1,market,buyer_cash=q['total_price_cash']+100)
    assert result['buyer_cash_after']==100
    assert result['market_state_after']['stock'][item]==market['stock'][item]-1
    assert result['market_state_after']['cash_pool']-market['cash_pool']==q['total_price_cash']


def test_qi_has_no_direct_restoration_or_cultivation_pills():
    medicine=load('game/data/martial-world/medicine.json')
    assert all('qi' not in key.lower() for key in medicine['recipes'])
    assert all('qi' not in json.dumps(value).lower() for value in medicine['recipes'].values())
    from shinobi_runtime.martial_world.qi import qi_recovery_milli
    result=qi_recovery_milli(qi=100,qi_control=100,current_qi_milli=20_000,elapsed_minutes=60,rest_state='sleep')
    assert result['current_qi_milli_after']>20_000


def test_medicine_dose_consumes_inventory_and_persists_saturation():
    from shinobi_runtime.martial_world.medicine import administer_dose,blank_medicine_state
    state=blank_medicine_state('2026-08-20T00:00:00+00:00')
    result=administer_dose('stamina_tonic',at='2026-08-20T00:00:00+00:00',inventory={'stamina_tonic':2},person_state=state)
    assert result['inventory_after']['stamina_tonic']==1
    assert result['medicine_state_after']['category_saturation_milli']['stamina']>0


def test_one_off_causal_event_is_exact_and_consumed_not_logged_forever():
    from shinobi_runtime.martial_world.scheduler import upsert_one_off_event
    start=datetime(61,10,6,12)
    due=start+timedelta(days=17,hours=3)
    s=initial_schedule(start=start,faction_ids=['f1'],region_ids=['r1'],route_ids=[])
    s=upsert_one_off_event(s,{'event_id':'birth_due:m1','kind':'family_birth_due','due_at':due.isoformat(),'owner_ref':'f1','marriage_ref':'m1'})
    events=due_events(s,after=start,through=due)
    assert events==[{'event_id':'birth_due:m1','kind':'family_birth_due','due_at':due.isoformat(),'owner_ref':'f1','marriage_ref':'m1'}]
    after=settle_schedule(s,through=due,processed_events=events)
    assert 'birth_due:m1' not in after['one_off']
    assert due_events(after,after=due,through=due+timedelta(days=1))==[]


def test_trade_contracts_create_exact_expiry_obligations():
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier
    overlay={}
    def read_json(rel):
        if rel in overlay: return overlay[rel]
        return load(rel)
    schedule=copy.deepcopy(load('state/martial-world/scheduler.json'))
    schedule['one_off']={}
    schedule['recurring']={
        'region_monthly': copy.deepcopy(schedule['recurring']['region_monthly'])
    }
    # Calendar events are synthesized independently of one_off. Pin the query to
    # the exact regional due instant so this test settles the intended domain.
    at=datetime.fromisoformat(schedule['recurring']['region_monthly']['next_due_at'])
    events=due_events(schedule,after=at-timedelta(seconds=1),through=at)
    assert events and {e.get('schedule_class') for e in events}=={'region_monthly'}
    result=settle_martial_world_frontier(read_json=read_json,schedule=schedule,events=events,at=at)
    created=[]
    for review in result['reviews']:
        if review.get('kind')=='trade_demand_review':
            created.extend(review.get('funded_contracts_created',[]))
    assert created
    one_off=result['schedule_after'].get('one_off',{})
    for cid in created:
        row=one_off.get(f'contract_expiry_due:{cid}')
        assert row and row['kind']=='contract_expiry_due' and row['owner_ref']==cid
        contract=result['writes']['state/martial-world/contracts/index.json']['active'][cid]
        assert row['due_at']==contract['expires_at']


def test_government_warrant_monthly_frontier_can_physically_close_for_npc():
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier
    sites=load('game/data/martial-world/local-sites.json').get('sites',{})
    places=load('game/data/martial-world/geography.json').get('places',{})
    player=load('state/meta.json').get('player_id')
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    blocked=set(derived_commitment_state(load).get('person_index',{}))
    from shinobi_runtime.martial_world.faction_state import hydrate_faction_state
    from shinobi_runtime.martial_world.person_state import hydrate_roster_state
    chosen=None
    # Select a low-capability adult faction member using the same hydrated
    # location view that production government response consumes. Duty/rank is
    # unrelated to whether government can physically pursue somebody.
    for pth in sorted((ROOT/'state/martial-world/people').glob('*.json')):
        raw=json.loads(pth.read_text()); fid=str(raw.get('faction_ref') or '')
        if not fid: continue
        faction=hydrate_faction_state(load(f'state/martial-world/factions/{fid}.json'))
        roster=hydrate_roster_state(raw,faction=faction)
        for person in roster.get('people',[]):
            if not isinstance(person,dict) or person.get('person_id')==player or person.get('person_id') in blocked: continue
            age=61-int(person.get('birth_year',61)); peak=max((int(v) for v in person.get('martial_skills',{}).values()),default=0)
            if age<16 or peak>=35: continue
            site=sites.get(str(person.get('location_ref')))
            place=places.get(str(site.get('parent_place_ref'))) if isinstance(site,dict) else None
            region=str(place.get('climate_profile') or '') if isinstance(place,dict) else ''
            if region:
                chosen=(person['person_id'],region); break
        if chosen: break
    assert chosen
    subject,region=chosen
    gov=copy.deepcopy(load('state/martial-world/government.json'))
    gov.setdefault('attention',{})[subject]={'attention':300,'bounty_cash':1000,'prior_offenses':1}
    gov.setdefault('warrants',{})[f'warrant:{subject}']={'subject_ref':subject,'offense':'murder','bounty_cash':1000,'status':'active','jurisdiction_ref':region,'evidence_ref':'test'}
    overlay={'state/martial-world/government.json':gov}
    def read_json(rel):
        return overlay[rel] if rel in overlay else load(rel)
    schedule=copy.deepcopy(load('state/martial-world/scheduler.json'))
    schedule['one_off']={}
    schedule['recurring']={
        'region_monthly': copy.deepcopy(schedule['recurring']['region_monthly'])
    }
    at=datetime.fromisoformat(schedule['recurring']['region_monthly']['next_due_at'])
    events=due_events(schedule,after=at-timedelta(seconds=1),through=at)
    assert events and {e.get('schedule_class') for e in events}=={'region_monthly'}
    result=settle_martial_world_frontier(read_json=read_json,schedule=schedule,events=events,at=at)
    after=result['writes']['state/martial-world/government.json']
    assert f'warrant:{subject}' not in after['warrants']
    custody=result['writes']['state/martial-world/custody.json']
    assert any(r.get('person_ref')==subject and r.get('captor_ref')==f'government:{region}' and r.get('status')=='restrained' for r in custody['records'])
    assert any(r.get('kind')=='government_response' and r.get('detentions',0)>=1 for r in result['reviews'])


def test_route_traffic_uses_actual_geography_road_quality_authority():
    from shinobi_runtime.martial_world.route_activity import route_traffic_milli
    geography=load('game/data/martial-world/geography.json')
    qualities={str(row.get('road_quality')) for row in geography.get('routes',[]) if row.get('road_quality')}
    config=load('game/data/martial-world/route-activity.json')
    authored=set(config['traffic_milli_by_road_quality'])
    assert qualities <= authored
    assert route_traffic_milli('trunk_road') > route_traffic_milli('maintained') > route_traffic_milli('mountain_road') > route_traffic_milli('rough_road')


def test_due_person_physiology_preempts_same_timestamp_monthly_but_not_other_one_offs():
    start=datetime(61,9,13,21,15)
    schedule=initial_schedule(start=start,faction_ids=['f1'],region_ids=['r1'],route_ids=[])
    at=datetime.fromisoformat(schedule['recurring']['region_monthly']['next_due_at'])
    schedule['one_off']={
        'person_physiology_due:p1':{
            'event_id':'person_physiology_due:p1','kind':'person_physiology_due',
            'due_at':at.isoformat(),'owner_ref':'p1','last_settled_at':(at-timedelta(days=1)).isoformat(),
            'recovery_carry_minutes':0,'poison_clearance_carry_minutes':0,'requires_player_decision':False,
        },
        'operation:arrival':{
            'event_id':'operation:arrival','kind':'faction_operation_arrival',
            'due_at':at.isoformat(),'owner_ref':'operation:1','requires_player_decision':False,
        },
    }
    physiology=due_events(schedule,after=at,through=at)
    assert [row['kind'] for row in physiology]==['person_physiology_due']
    schedule=settle_schedule(schedule,through=at,processed_events=physiology)
    regional=due_events(schedule,after=at,through=at)
    assert {row.get('schedule_class') for row in regional}=={'region_monthly'}
    schedule=settle_schedule(schedule,through=at,processed_events=regional)
    faction=due_events(schedule,after=at,through=at)
    assert {row.get('schedule_class') for row in faction}=={'faction_monthly'}
    schedule=settle_schedule(schedule,through=at,processed_events=faction)
    ordinary=due_events(schedule,after=at,through=at)
    assert [row['kind'] for row in ordinary]==['faction_operation_arrival']


def test_monthly_review_rebases_future_person_physiology_before_treatment():
    from shinobi_runtime.martial_world.physiology_frontier import settle_review_faction_physiology
    person={
        'person_id':'p1','body_mass_kg':70,
        'attributes':{'endurance':50,'willpower':50},
        'health':{'status':'injured','injuries':[{'zone':'wrist','severity':20,'bleeding_ml_per_min':0,'healing_progress_milli':0}]},
    }
    last=datetime(61,10,13,9,15); review=datetime(61,10,13,21,15)
    schedule={'one_off':{'person_physiology_due:p1':{
        'event_id':'person_physiology_due:p1','kind':'person_physiology_due','due_at':(last+timedelta(days=1)).isoformat(),
        'owner_ref':'p1','last_settled_at':last.isoformat(),'recovery_carry_minutes':0,'poison_clearance_carry_minutes':0,
    }}}
    saved={}
    result=settle_review_faction_physiology(
        schedule,faction_refs=['f1'],at=review,
        load_roster=lambda _fid:('state/martial-world/people/f1.json',{'faction_ref':'f1','people':[person]}),
        save_person=lambda ref,row:saved.__setitem__(ref,dict(row)),
    )
    assert result['settled_refs']==['p1']
    assert result['replaced_event_ids']==['person_physiology_due:p1']
    assert saved['p1']['health']['status'] in {'injured','ready'}
