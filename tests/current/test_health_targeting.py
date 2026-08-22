import copy, json
from pathlib import Path

import pytest

from shinobi_runtime.martial_world.health import (
    combat_status_families,
    functional_capacity_factors,
    functional_penalties,
    target_zone,
    vision_state,
    wound_from_contact,
    recovery_advance,
)
from shinobi_runtime.martial_world.targeting import doctrine_target, resolve_combat_doctrine, resolve_structure_selector
from shinobi_runtime.martial_world.exact_combat import _refresh_structural_statuses, capability_from_person
from shinobi_runtime.martial_world.manpower import combat_eligible, combat_readiness_score
from shinobi_runtime.people.repository import RepositoryPersonSheetResolver
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _eye(ref):
    return wound_from_contact(structure_ref=ref, cut=0, pierce=180, blunt=0, penetration=180, created_at="test")


def _person():
    return copy.deepcopy(RepositoryPersonSheetResolver(RepositoryStore(ROOT))('pc_wei_tang'))

def _set_wounds(person, wounds):
    person['health']=copy.deepcopy(person.get('health',{}))
    person['health']['injuries']=list(wounds)


def test_named_structures_map_to_armor_zones():
    assert target_zone(structure_ref='left_eye') == 'eyes'
    assert target_zone(structure_ref='left_wrist') == 'wrist'
    assert target_zone(structure_ref='right_knee') == 'knee'
    assert target_zone(structure_ref='left_ankle') == 'ankle'


def test_one_destroyed_eye_is_monocular_with_depth_loss_and_directional_status():
    wound=_eye('left_eye')
    state=vision_state([wound])
    assert state['state']=='monocular'
    assert state['left_eye_vision_pct']==0
    assert state['right_eye_vision_pct']==100
    assert state['depth_perception_loss_pct']>=45
    assert 'blind_left_eye' in combat_status_families([wound])
    penalties=functional_penalties([wound])
    assert penalties['vision']>0
    assert penalties['depth_perception']>=45


def test_two_destroyed_eyes_are_blind():
    wounds=[_eye('left_eye'),_eye('right_eye')]
    state=vision_state(wounds)
    assert state['state']=='blind'
    assert state['visual_perception_loss_pct']==100
    assert state['depth_perception_loss_pct']==100
    assert 'blind_both_eyes' in combat_status_families(wounds)


def test_wrist_knee_and_ankle_damage_have_functional_consequences():
    wrist=wound_from_contact(structure_ref='left_wrist',cut=150,pierce=80,blunt=40,penetration=80,created_at='test')
    knee=wound_from_contact(structure_ref='right_knee',cut=40,pierce=40,blunt=180,penetration=40,created_at='test')
    ankle=wound_from_contact(structure_ref='left_ankle',cut=40,pierce=40,blunt=180,penetration=40,created_at='test')
    p=functional_penalties([wrist,knee,ankle])
    assert p['weapon_control_left']>0
    assert p['leg_right']>0
    assert p['footwork_left']>0


def test_targeting_doctrine_is_static_data_referenced_by_player_state_and_resolves_real_structure():
    person=_person(); target=_person(); target['person_id']='target'
    assert person['combat_doctrine_ref']=='doctrine.tang_wei.precision_function_denial'
    doctrine=resolve_combat_doctrine(person)
    assert doctrine['principle']=='precision_function_denial'
    chosen=doctrine_target(person,intent='disable',target=target)
    assert chosen in {'left_wrist','right_wrist'}
    # Damage one wrist heavily; family selection should prefer the other one.
    _set_wounds(target,[wound_from_contact(structure_ref=chosen,cut=180,pierce=80,blunt=30,penetration=80,created_at='test')])
    next_choice=resolve_structure_selector('wrist',target=target)
    assert next_choice != chosen


def test_injury_penalties_change_capability_without_saved_derived_flags():
    healthy=_person()
    one_eye=copy.deepcopy(healthy); _set_wounds(one_eye,[_eye('left_eye')])
    blind=copy.deepcopy(healthy); _set_wounds(blind,[_eye('left_eye'),_eye('right_eye')])
    h=capability_from_person(healthy,action_skill='bow')
    m=capability_from_person(one_eye,action_skill='bow')
    b=capability_from_person(blind,action_skill='bow')
    assert h.perception > m.perception > b.perception
    assert h.offense > m.offense >= b.offense
    assert 'blind_both_eyes' not in blind['health']


def test_destroyed_wrist_and_knee_reduce_live_combat_capability():
    healthy=_person()
    wrist=copy.deepcopy(healthy)
    _set_wounds(wrist,[wound_from_contact(structure_ref='right_wrist',cut=180,pierce=100,blunt=60,penetration=100,created_at='test')])
    knee=copy.deepcopy(healthy)
    _set_wounds(knee,[wound_from_contact(structure_ref='right_knee',cut=20,pierce=40,blunt=220,penetration=80,created_at='test')])
    hs=capability_from_person(healthy,action_skill='sword')
    ws=capability_from_person(wrist,action_skill='sword')
    ks=capability_from_person(knee,action_skill='sword')
    assert ws.offense < hs.offense
    assert ks.mobility < hs.mobility
    assert ks.reaction < hs.reaction


def test_monocular_blind_side_reduces_attack_detection_margin():
    from shinobi_runtime.combat.models import ActionProfile, CapabilityProfile, CombatIntent, InformationState, Participant, PersonnelState, PositionState
    from shinobi_runtime.combat.physical_defense import detect_attack

    cap=CapabilityProfile(offense=100,defense=100,control=100,mobility=100,perception=100,stealth=0,capture=50,escape=100,reaction=100)
    personnel=PersonnelState(total=1,active=1)
    info=InformationState(observed_refs=('attacker',))
    intent=CombatIntent(action='attack',target_refs=('defender',))
    attack=ActionProfile(method_ref='thrust',effect_kind='physical',delivery='direct',startup_ms=300,external_contact=True,speed_score=100,effect_parameters={'physical_reach_m':1.2})
    attacker=Participant(participant_ref='attacker',authoritative_owner_ref='attacker',side_ref='a',sequence=0,representation='exact',capability=cap,personnel=personnel,position=PositionState(zone_ref='z',x_mm=0,y_mm=3000,facing_mdeg=270000),information=InformationState(observed_refs=('defender',)),intent=intent,initiative=100,readiness=100,morale=100,cohesion=100,action_profile=attack)
    normal=Participant(participant_ref='defender',authoritative_owner_ref='defender',side_ref='b',sequence=0,representation='exact',capability=cap,personnel=personnel,position=PositionState(zone_ref='z',x_mm=0,y_mm=0,facing_mdeg=0),information=info,intent=CombatIntent(action='hold'),initiative=100,readiness=100,morale=100,cohesion=100)
    blind_left=Participant(participant_ref='defender',authoritative_owner_ref='defender',side_ref='b',sequence=0,representation='exact',capability=cap,personnel=personnel,position=normal.position,information=info,intent=CombatIntent(action='hold'),initiative=100,readiness=100,morale=100,cohesion=100,status_families=('monocular_vision','blind_left_eye'))
    _,normal_margin,_,_=detect_attack(attacker=attacker,defender=normal,attacker_position=attacker.position,defender_position=normal.position,attacker_capability=cap,defender_capability=cap,profile=attack,line_of_sight=True)
    _,blind_margin,_,_=detect_attack(attacker=attacker,defender=blind_left,attacker_position=attacker.position,defender_position=blind_left.position,attacker_capability=cap,defender_capability=cap,profile=attack,line_of_sight=True)
    assert blind_margin < normal_margin


def test_destroyed_eye_stabilizes_but_never_regenerates_after_recovery():
    wound=_eye('left_eye')
    healed=recovery_advance(wound,elapsed_hours=200000)
    assert healed['healed'] is True
    assert healed['permanent'] is True
    assert healed['permanent_outcome']=='left_eye_destroyed'
    assert healed['bleeding_ml_per_min']==0 and healed['pain']==0
    state=vision_state([healed])
    assert state['state']=='monocular' and state['left_eye_vision_pct']==0
    assert 'blind_left_eye' in combat_status_families([healed])


def test_severed_hand_stabilizes_as_permanent_loss_and_reduces_field_readiness():
    healthy=_person()
    severed=wound_from_contact(structure_ref='left_wrist',cut=230,pierce=20,blunt=20,penetration=40,created_at='test')
    healed=recovery_advance(severed,elapsed_hours=200000)
    assert healed['permanent_outcome']=='left_hand_lost'
    assert functional_penalties([healed])['grip_left']==100
    injured=copy.deepcopy(healthy); _set_wounds(injured,[healed])
    assert combat_readiness_score(injured,year=61) < combat_readiness_score(healthy,year=61)


def test_cut_achilles_is_permanent_and_deterministically_destroys_footwork():
    wound=wound_from_contact(structure_ref='left_achilles_tendon',cut=120,pierce=0,blunt=0,penetration=20,created_at='test')
    assert wound['permanent'] is True
    assert wound['permanent_outcome']=='left_achilles_severed'
    healed=recovery_advance(wound,elapsed_hours=200000)
    penalties=functional_penalties([healed])
    assert penalties['footwork_left']>=95
    assert penalties['leg_left']>=65
    assert 'left_achilles_severed' in combat_status_families([healed])


def test_nonpermanent_wound_can_fully_heal_without_permanent_residual():
    wound=wound_from_contact(zone='forearm',cut=8,pierce=0,blunt=4,penetration=0,created_at='test')
    healed=recovery_advance(wound,elapsed_hours=200000)
    assert healed['healing_progress_milli']==100000
    assert healed['healed'] is True
    assert healed['bleeding_ml_per_min']==0 and healed['pain']==0 and healed['severity']==0
    assert healed['functional_effects']=={} and healed['function_loss_pct']==0
    assert not healed.get('permanent')
    assert not healed.get('permanent_outcome')


def test_lower_limb_destruction_has_distinct_task_capacities_without_erasing_skill():
    person=_person(); skills=copy.deepcopy(person['martial_skills'])
    achilles=recovery_advance(
        wound_from_contact(structure_ref='left_achilles_tendon',cut=120,pierce=0,blunt=0,penetration=20,created_at='test'),
        elapsed_hours=200000,
    )
    knee=recovery_advance(
        wound_from_contact(structure_ref='left_knee',cut=0,pierce=0,blunt=350,penetration=0,created_at='test'),
        elapsed_hours=200000,
    )
    missing=recovery_advance(
        wound_from_contact(structure_ref='left_knee',cut=260,pierce=40,blunt=40,penetration=60,created_at='test'),
        elapsed_hours=200000,
    )
    assert achilles['permanent_outcome']=='left_achilles_severed'
    assert knee['permanent_outcome']=='destroyed:left_knee'
    assert missing['permanent_outcome']=='left_leg_lost'
    a=functional_capacity_factors([achilles]); k=functional_capacity_factors([knee]); m=functional_capacity_factors([missing])
    for key in ('walking_milli','running_milli','standing_milli','climbing_milli','mounted_stability_milli','labor_milli','combat_movement_milli'):
        assert 0 <= m[key] < k[key] < a[key] < 1000, (key,m[key],k[key],a[key])
    injured=copy.deepcopy(person); _set_wounds(injured,[missing])
    assert injured['martial_skills']==skills
    assert capability_from_person(injured,action_skill='sword').mobility < capability_from_person(person,action_skill='sword').mobility
    # Unilateral loss is not deletion from combat society. It remains technically
    # deployable but its readiness is so poor that healthy alternatives rank first.
    assert combat_eligible(injured,year=61) is True
    assert combat_readiness_score(injured,year=61) < combat_readiness_score(person,year=61)


def test_bilateral_leg_loss_is_not_ordinary_field_deployable():
    person=_person()
    left=recovery_advance(wound_from_contact(structure_ref='left_knee',cut=260,pierce=40,blunt=40,penetration=60,created_at='test'),elapsed_hours=200000)
    right=recovery_advance(wound_from_contact(structure_ref='right_knee',cut=260,pierce=40,blunt=40,penetration=60,created_at='test'),elapsed_hours=200000)
    _set_wounds(person,[left,right])
    assert functional_penalties([left,right])['leg_left']>=95
    assert functional_penalties([left,right])['leg_right']>=95
    assert combat_eligible(person,year=61) is False


def test_new_structural_loss_refreshes_combat_status_immediately():
    person=_person()
    state={'status_families':['committed_attack']}
    _set_wounds(person,[_eye('left_eye')])
    _refresh_structural_statuses(state,person)
    assert 'committed_attack' in state['status_families']
    assert 'blind_left_eye' in state['status_families']
