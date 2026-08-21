from datetime import datetime
from shinobi_runtime.martial_world.family_simulation import review_conceptions, resolve_birth, advance_annual_life_course
from shinobi_runtime.martial_world.scheduler import initial_schedule, upsert_one_off_event, due_events, settle_schedule


def adult(ref, sex, birth_year=30):
    return {
        'person_id':ref,'name':ref,'birth_year':birth_year,'sex':sex,'appearance':50,
        'aptitudes':{'physical':100,'martial':100,'qi':100,'cognitive':100,'leadership':100},
        'attributes':{'strength':60,'speed':60,'dexterity':60,'endurance':60,'perception':60,'intelligence':60,'willpower':60},
        'martial_skills':{},'professional_skills':{},'qi':0,'qi_control':0,'membership_grade':'full',
    }


def test_conception_creates_one_exact_obligation_and_birth_consumes_pregnancy():
    family={'schema':'jianghu-family-state-1.0','marriages':{'m1':{'spouse_refs':['a','b'],'status':'married','faction_ref':'f'}},'parentage':{},'households':{'h':{'faction_ref':'f','head_ref':'a','member_refs':['a','b'],'residence_ref':'site.f','status':'active'}},'succession_claims':{}}
    people=[adult('a','female'),adult('b','male')]
    # Search a bounded 18-month deterministic cycle for this marriage's fertility month.
    hit=None
    for y in (61,62):
        for m in range(1,13):
            at=f'{y:04d}-{m:02d}-01T00:00:00'
            result=review_conceptions(family,faction_ref='f',roster_people=people,at_iso=at)
            if result['conceived_refs']:
                hit=(at,result); break
        if hit: break
    assert hit is not None
    at,result=hit
    assert len(result['one_off_events'])==1
    event=result['one_off_events'][0]
    schedule=initial_schedule(start=datetime.fromisoformat(at),faction_ids=[],region_ids=[],route_ids=[])
    schedule=upsert_one_off_event(schedule,event)
    due=datetime.fromisoformat(event['due_at'])
    from datetime import timedelta
    events=due_events(schedule,after=due-timedelta(seconds=1),through=due)
    assert events[0]['kind']=='family_birth_due'
    born=resolve_birth(result['family_after'],marriage_ref='m1',child_ref=event['child_ref'],faction_ref='f',roster_people=people,birth_at=event['due_at'])
    assert born['birth']['person_id']==event['child_ref']
    assert born['birth']['body_mass_kg']<=5
    assert born['family_after']['parentage'][event['child_ref']]['parent_refs']==['a','b']
    assert 'pregnancy' not in born['family_after']['marriages']['m1']
    settled=settle_schedule(schedule,through=due,processed_events=events)
    assert event['event_id'] not in settled['one_off']


def test_annual_child_maturation_is_current_fact_not_history():
    child=adult('c','female',birth_year=56)
    child['body_mass_kg']=4
    child['attributes']={k:1 for k in child['attributes']}
    result=advance_annual_life_course([child],year=61)
    after=result['people_after'][0]
    assert after['body_mass_kg']>4
    assert max(after['attributes'].values())>1
    assert result['matured_refs']==['c']
    assert not result['died_refs']


def test_authored_child_maturation_converges_without_one_year_body_snap():
    kai={
        'person_id':'char.kai','name':'Tang Kai','birth_year':55,'sex':'male','body_mass_kg':22,'appearance':100,
        'aptitudes':{'physical':200,'martial':200,'qi':200,'cognitive':200,'leadership':200},
        'attributes':{'strength':22,'speed':45,'dexterity':48,'endurance':30,'perception':70,'intelligence':100,'willpower':68},
        'martial_skills':{'sword':40,'unarmed':25},'professional_skills':{},'qi':125,'qi_control':35,'membership_grade':'probationary',
    }
    year7=advance_annual_life_course([kai],year=62,player_ref=None)['people_after'][0]
    assert 22 < year7['body_mass_kg'] <= 27
    assert 22 < year7['attributes']['strength'] <= 29
    assert year7['attributes']['intelligence'] == 100

    person=kai
    for year in range(62,72):
        person=advance_annual_life_course([person],year=year,player_ref=None)['people_after'][0]
    assert person['body_mass_kg'] >= 60
    assert person['attributes']['strength'] >= 80
    assert person['attributes']['strength'] <= 100
