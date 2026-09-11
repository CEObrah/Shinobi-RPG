import copy
import json
from pathlib import Path

from shinobi_runtime.martial_world.infrastructure import building_upgrade_requirements
from shinobi_runtime.martial_world.security import breach_repair_quote

ROOT=Path(__file__).resolve().parents[2]


def test_breach_repair_uses_registered_wall_recipe_fraction():
    recipe=building_upgrade_requirements('walls_gate',1)
    quote=breach_repair_quote({'walls_gate':1},{'walls_gate_integrity_milli':500,'walls_gate_breached':True})
    assert quote['damage_milli']==500
    assert quote['materials']=={key:(int(value)+1)//2 for key,value in recipe['materials'].items()}
    assert quote['general_labor_hours']==(int(recipe['general_labor_hours'])+1)//2
    assert quote['skilled_labor_hours']==(int(recipe['skilled_labor_hours'])+1)//2
    assert quote['labor_hours']==quote['general_labor_hours']+quote['skilled_labor_hours']
    assert quote['required_crafting_or_administration']==int(recipe['required_crafting_or_administration'])


def test_only_stateful_services_claim_simulation_effects():
    data=json.loads((ROOT/'game/data/martial-world/services.json').read_text())
    claimed={}
    for menu in data['menus'].values():
        for ref,row in menu.items():
            if isinstance(row,dict) and row.get('simulation_effect'):
                claimed[ref]=row['simulation_effect']
    assert claimed=={
        'packed_rations_day':'add_personal_travel_ration_day',
        'basic_dressings':'stabilize_current_wounds',
    }


def test_aggregate_transport_has_no_exact_horse_feed_or_stabling_authority():
    economy=json.loads((ROOT/'game/data/martial-world/economy.json').read_text())
    assert 'horse_feed_day' not in economy.get('consumables',{})
    services=(ROOT/'game/data/martial-world/services.json').read_text()
    assert 'aggregate_transport_stabling' not in services
    runtime='\n'.join(path.read_text(errors='ignore') for path in (ROOT/'runtime/shinobi_runtime').rglob('*.py'))
    assert 'jianghu_combat_riding_horses_unavailable' not in runtime


def test_canonical_route_rows_are_seconds_native_only():
    rows=json.loads((ROOT/'state/martial-world/route-operations.json').read_text())['movements']
    assert rows
    for row in rows.values():
        assert 'required_hours' not in row and 'elapsed_hours' not in row
        # Mobilizing pursuit owners are real commitments but do not acquire a
        # physical travel duration until their ready boundary fires. Every
        # movement that is already traversing a route remains seconds-native.
        if row.get('movement_kind') == 'route_pursuit' and row.get('status') == 'pursuing' and not row.get('required_seconds'):
            assert row.get('ready_at')
        else:
            assert int(row.get('required_seconds',0)) > 0


def test_rescue_force_scales_beyond_old_small_encounter_ceiling():
    from shinobi_runtime.martial_world.captivity_lifecycle import rescue_force_size
    small=rescue_force_size(available_count=12,captive_value_cash=10_000,close_kin_count=0,risk_tolerance=40)
    large=rescue_force_size(available_count=120,captive_value_cash=200_000,close_kin_count=2,risk_tolerance=80)
    assert 1 <= small <= 12
    assert large > 12
    assert large <= 120


def test_government_attention_is_compact_current_state_not_evidence_history():
    from shinobi_runtime.martial_world.government import compact_attention_row
    row=compact_attention_row(attention=999,bounty_cash=50,prior_offenses=3)
    assert row=={'attention':300,'bounty_cash':50,'prior_offenses':3}
    assert 'last_evidence_ref' not in row and 'last_updated_at' not in row


def test_route_journey_persists_only_current_weather_not_future_weather_snapshots():
    from datetime import datetime
    from shinobi_runtime.martial_world.physical_travel import build_route_journey, begin_next_segment
    plan={
        'edges':['route.a','route.b'], 'nodes':['a','b','c'],
        'segments':[
            {'hours':2,'provisioning_hours':2,'weather':{'condition':'rain','visibility_milli':700}},
            {'hours':3,'provisioning_hours':3,'weather':{'condition':'clear','visibility_milli':1000}},
        ],
    }
    row=build_route_journey(
        movement_ref='m',movement_kind='test',purpose_ref='p',plan=plan,participants=['x'],
        leader_ref='x',beneficiary_ref='f',started_at=datetime(61,1,1),mode='foot',
    )
    assert row['route_weather']['condition']=='rain'
    assert 'segment_weather' not in row
    nxt=begin_next_segment(row,at=datetime(61,1,1,2))
    assert nxt is not None and nxt['route_ref']=='route.b'
    assert nxt['route_weather']=={}
    assert 'segment_weather' not in nxt
