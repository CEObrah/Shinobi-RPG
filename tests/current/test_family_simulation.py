from datetime import datetime
from shinobi_runtime.martial_world.family_simulation import advance_npc_relationships, review_conceptions, resolve_birth, advance_annual_life_course
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


def test_faction_local_relationship_review_does_not_delete_other_faction_courtship():
    family={
        'schema':'jianghu-family-state-1.0','marriages':{},'parentage':{},
        'households':{},'succession_claims':{},
    }
    social={
        'schema':'jianghu-social-state-1.0',
        'relationships':{
            'a|b':{'familiarity':25,'trust':25,'affection':30,'respect':10},
            'b|a':{'familiarity':25,'trust':25,'affection':30,'respect':10},
        },
        'courtships':{
            'a|b':{'person_refs':['a','b'],'status':'active','started_at':'0061-09-01T00:00:00'},
        },
    }
    result=advance_npc_relationships(
        family,social,faction_ref='f_b',roster_people=[adult('c','male')],
        at_iso='0061-10-01T00:00:00',
    )
    assert result['social_after']['courtships']['a|b']['status']=='active'
    assert result['social_after']['relationships']['a|b']['trust']==25


def test_demographic_pairing_target_uses_full_adult_cohort_not_unmarried_remainder(monkeypatch):
    from shinobi_runtime.martial_world import family_simulation

    monkeypatch.setattr(family_simulation, '_cfg', lambda: {
        'courtship': {'minimum_age': 16},
        'demographic_family_formation': {
            'target_paired_adult_share_milli': 500,
            'low_family_faction_refs': [],
            'maximum_age_gap_years': 18,
            'monthly_base_start_permille': 1000,
            'monthly_deficit_bonus_permille_per_pair': 0,
            'monthly_max_start_permille': 1000,
        },
    })
    people = [
        adult('a','female'), adult('b','male'),
        adult('c','female'), adult('d','male'), adult('e','female'), adult('f','male'),
    ]
    family = {
        'schema':'jianghu-family-state-1.0',
        'marriages': {'m1': {'spouse_refs':['a','b'],'status':'married','faction_ref':'f'}},
        'parentage': {},
        'households': {'h1': {'faction_ref':'f','head_ref':'a','member_refs':['a','b'],'residence_ref':'site.f','status':'active'}},
        'succession_claims': {},
    }
    social = {'schema':'jianghu-social-state-1.0','relationships':{},'courtships':{}}
    result = family_simulation.advance_npc_relationships(
        family, social, faction_ref='f', roster_people=people,
        at_iso='0061-12-01T00:00:00', residence_ref='site.f',
    )
    # Six adults at a 50% target means at least three paired adults.  The
    # existing marriage covers only two, so another courtship must be allowed.
    # The old shrinking-unmarried denominator incorrectly reported no deficit.
    assert len(result['courtships_started']) == 1
    pair = result['courtships_started'][0]
    assert not {'a','b'} & set(pair.split('|'))


def test_relationship_locality_uses_parent_place_not_exact_site():
    family={
        'schema':'jianghu-family-state-1.0','marriages':{},'parentage':{},
        'households':{},'succession_claims':{},
    }
    social={
        'schema':'jianghu-social-state-1.0',
        'relationships':{
            'a|b':{'familiarity':25,'trust':25,'affection':30,'respect':10},
            'b|a':{'familiarity':25,'trust':25,'affection':30,'respect':10},
        },
        'courtships':{},
    }
    sites={
        'site.home':{'parent_place_ref':'wuhan'},
        'site.tea':{'parent_place_ref':'wuhan'},
        'site.away':{'parent_place_ref':'hengyang'},
    }
    a=adult('a','female'); a['location_ref']='site.home'
    b=adult('b','male'); b['location_ref']='site.tea'
    local=advance_npc_relationships(
        family,social,faction_ref='f',roster_people=[a,b],
        at_iso='0061-10-01T00:00:00',residence_ref='site.home',site_rows=sites,
    )
    assert local['courtships_started']==['a|b']

    b_remote=dict(b); b_remote['location_ref']='site.away'
    remote=advance_npc_relationships(
        family,social,faction_ref='f',roster_people=[a,b_remote],
        at_iso='0061-10-01T00:00:00',residence_ref='site.home',site_rows=sites,
    )
    assert remote['courtships_started']==[]
    assert remote['social_after']['courtships']=={}


def test_existing_courtship_waits_for_physical_reunion_before_marriage():
    family={
        'schema':'jianghu-family-state-1.0','marriages':{},'parentage':{},
        'households':{},'succession_claims':{},
    }
    social={
        'schema':'jianghu-social-state-1.0',
        'relationships':{
            'a|b':{'familiarity':25,'trust':25,'affection':30,'respect':10},
            'b|a':{'familiarity':25,'trust':25,'affection':30,'respect':10},
        },
        'courtships':{
            'a|b':{'person_refs':['a','b'],'status':'active','started_at':'0061-09-01T00:00:00'},
        },
    }
    sites={
        'site.home':{'parent_place_ref':'wuhan'},
        'site.tea':{'parent_place_ref':'wuhan'},
        'site.away':{'parent_place_ref':'hengyang'},
    }
    a=adult('a','female'); a['location_ref']='site.home'
    b=adult('b','male'); b['location_ref']='site.away'
    apart=advance_npc_relationships(
        family,social,faction_ref='f',roster_people=[a,b],
        at_iso='0061-10-01T00:00:00',residence_ref='site.home',site_rows=sites,
    )
    assert apart['marriages_created']==[]
    assert 'a|b' in apart['social_after']['courtships']

    b_home=dict(b); b_home['location_ref']='site.tea'
    reunited=advance_npc_relationships(
        family,social,faction_ref='f',roster_people=[a,b_home],
        at_iso='0061-10-01T00:00:00',residence_ref='site.home',site_rows=sites,
    )
    assert len(reunited['marriages_created'])==1
    assert 'a|b' not in reunited['social_after']['courtships']
