import copy, json
from datetime import datetime, timedelta
from pathlib import Path

from shinobi_runtime.martial_world.scheduler import initial_schedule,due_events,settle_schedule,sync_route_activity
from shinobi_runtime.martial_world.compensation import settle_monthly_compensation
from shinobi_runtime.martial_world.training import training_gain_milli
from shinobi_runtime.martial_world.infrastructure import building_upgrade_quote
from shinobi_runtime.martial_world.regional_economy import initial_market_state, quote_purchase, execute_purchase

ROOT=Path(__file__).resolve().parents[2]
def load(rel): return json.loads((ROOT/rel).read_text())


def test_scheduler_uses_due_frontier_and_resumable_owner_cursor():
    start=datetime(61,10,6)
    owners=[f'f{i:03}' for i in range(130)]
    s=initial_schedule(start=start,faction_ids=owners,region_ids=['r1'],route_ids=[])

    # Annual life reviews are intentionally staggered per faction instead of
    # waking the whole world on one anniversary. The exact one-off schedule is
    # deterministic and covers every faction once.
    annual=[row for row in s['one_off'].values() if row.get('kind')=='annual_faction_life_review']
    assert len(annual)==len(owners)
    assert {row['owner_ref'] for row in annual}==set(owners)
    assert len({row['due_at'] for row in annual})>30

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
    assert notice['registration_opens_on']=='0063-12-28'
    assert notice['registration_closes_on']=='0064-08-24'
    assert notice['host_place_id']=='luoyang'


def test_canonical_midseason_scheduler_has_life_reviews_and_valid_standing_crops_when_present():
    from shinobi_runtime.martial_world.agriculture import harvest_quote
    schedule=load('state/martial-world/scheduler.json')
    start=datetime.fromisoformat(schedule['settled_through'])
    rows=list(schedule.get('one_off',{}).values())
    annual=[row for row in rows if row.get('kind')=='annual_faction_life_review']
    harvest=[row for row in rows if row.get('kind')=='agriculture_harvest_due' and row.get('standing_crop_at_campaign_anchor')]
    faction_paths=sorted((ROOT/'state/martial-world/factions').glob('*.json'))
    faction_docs=[load(f'state/martial-world/factions/{p.stem}.json') for p in faction_paths]
    assert len(annual)==len(faction_docs)==240
    assert {row.get('owner_ref') for row in annual}=={f['faction_id'] for f in faction_docs}
    for row in annual:
        due=datetime.fromisoformat(row['due_at'])
        # Equality is lawful for an unresolved same-timestamp one-off. The
        # production frontier will consume it before time advances past it.
        assert start <= due <= start+timedelta(days=365)
        assert row.get('recurrence_days')==365

    agriculture={}
    meta=load('state/meta.json')
    for faction in faction_docs:
        level=int((faction.get('enterprises') or {}).get('agriculture_landholding',0) or 0)
        scale=((faction.get('enterprise_scale') or {}).get('agriculture_landholding') or {})
        managed=int(scale.get('managed_land_mu',0) or 0)
        if level>0 and managed>0:
            agriculture[faction['faction_id']]=(faction,level,managed)
    # Anchor-time crops disappear lawfully after harvest. If any remain in the
    # current save, every one must still be physically and economically valid.
    assert {row['faction_ref'] for row in harvest}.issubset(agriculture)
    for row in harvest:
        faction,level,managed=agriculture[row['faction_ref']]
        planted=datetime.fromisoformat(row['planted_at']); due=datetime.fromisoformat(row['due_at'])
        assert planted < start < due
        assert row['crop_ref']=='staple_grain'
        assert 1 <= int(row['planted_mu']) <= managed
        quote=harvest_quote(
            world_seed=meta['world_seed'], place_id=row['place_id'], crop_ref=row['crop_ref'],
            planted_mu=int(row['planted_mu']), planted_at=planted, agriculture_level=level, labor_coverage_milli=1000,
        )
        assert quote['harvest_at']==row['due_at']
        output_days=int(quote['output_units'])/max(1,int(faction['population']))
        assert 150 <= output_days <= 200


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
    from shinobi_runtime.martial_world.time_integration import settle_martial_world_frontier
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
    from shinobi_runtime.martial_world.time_integration import settle_martial_world_frontier
    sites=load('game/data/martial-world/local-sites.json').get('sites',{})
    places=load('game/data/martial-world/geography.json').get('places',{})
    player=load('state/meta.json').get('player_id')
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
            if not isinstance(person,dict) or person.get('person_id')==player: continue
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
