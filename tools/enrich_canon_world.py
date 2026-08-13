#!/usr/bin/env python3
"""Enrich the cold Shinobi world without adding hot scheduler fanout."""
from __future__ import annotations
import hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LAST='SE-0061-02-05T07:00:00'
NEXT='SE-0061-03-01T07:00:00'

def seed(ref:str)->int:
    return int(hashlib.sha256(ref.encode()).hexdigest()[:16],16)

def settlement(ref:str, *, clan=False, risks=()):
    return {
      'active_goal_ids':[], 'cadence':'monthly','dependency_ids':[],
      'last_settled_at':LAST,'next_due_at':NEXT,'owner_id':ref,'owner_type':'institution',
      'player_choice_protected':False,'risk_flags':list(risks),
      'routine_profile_id':'ROUTINE_CLAN_INSTITUTION' if clan else 'ROUTINE_INSTITUTION',
      'stable_seed':seed(ref),'standing_order_ids':['maintain_current_duties'],
      'unresolved_player_dependency_ids':[]
    }

def institution(id,name,country,doctrine,branches,leader=None,risks=()):
    return {'branches':branches,'country_id':country,'doctrine_id':doctrine,'id':id,'leader_id':leader,'name':name,'settlement':settlement(id,risks=risks)}

def clan(id,name,specialization,leader=None):
    return {'id':id,'leader_id':leader,'name':name,'settlement':settlement(id,clan=True),'specialization':specialization,'village':'Konoha'}

def place(id,name,country,kind,status='extant',authority=None,timeline='current',classification='public',provenance='canon_reference_baseline'):
    return {'id':id,'name':name,'country_id':country,'kind':kind,'status':status,'authority_ref':authority,'timeline_status':timeline,'knowledge_classification':classification,'provenance':provenance}

def merge_unique(items, additions):
    out={item['id']:item for item in items}
    for item in additions: out[item['id']]=item
    return [out[k] for k in sorted(out)]

# Konoha clans and institutions
p=ROOT/'state/world/institutions-konoha.json'; d=json.loads(p.read_text())
d['payload']['clans']=merge_unique(d['payload'].get('clans',[]),[
    clan('org_sarutobi','Sarutobi Clan','long-serving Konoha shinobi lineage','canon_hiruzen'),
    clan('org_senju_legacy','Senju Clan Legacy','founding-clan legacy and surviving descendants',None),
])
konoha=[
 institution('institution.konoha.hokage_administration','Hokage Administration','land_fire','doc.force_konoha_shinobi.field',['hokage_office','council_liaison','mission_authority'],'canon_hiruzen'),
 institution('institution.konoha.mission_assignment','Mission Assignment Desk','land_fire','doc.force_konoha_shinobi.field',['mission_intake','assignment','client_liaison'],'canon_hiruzen'),
 institution('institution.konoha.academy','Konoha Academy','land_fire','doc.force_konoha_shinobi.field',['instruction','graduation_testing','cadet_records'],'canon_hiruzen'),
 institution('institution.konoha.anbu','Konoha ANBU','land_fire','doc.force_konoha_shinobi.field',['black_operations','protection','counterintelligence'],'canon_hiruzen',risks=('restricted',)),
 institution('institution.konoha.root','Root','land_fire','doc.force_konoha_shinobi.field',['covert_training','compartmented_operations','subterranean_facilities'],'canon_danzo',risks=('secret','compartmented')),
 institution('institution.konoha.barrier_team','Konoha Barrier Team','land_fire','doc.force_konoha_shinobi.field',['village_barrier','intrusion_detection'],None),
 institution('institution.konoha.intelligence_division','Konoha Intelligence Division','land_fire','doc.force_konoha_shinobi.field',['analysis','communications','sensor_support'],'canon_inoichi'),
 institution('institution.konoha.interrogation','Konoha Torture and Interrogation Force','land_fire','doc.force_konoha_shinobi.field',['interrogation','prisoner_processing'],'canon_ibiki',risks=('restricted',)),
 institution('institution.konoha.medical_corps','Konoha Medical Corps','land_fire','doc.force_konoha_shinobi.field',['field_medicine','hospital_support','casualty_recovery'],None),
 institution('institution.konoha.hospital','Konoha Hospital','land_fire','doc.force_konoha_shinobi.field',['emergency_care','inpatient_care','rehabilitation'],None),
 institution('institution.konoha.archive_library','Konoha Archive Library','land_fire','doc.force_konoha_shinobi.field',['records','archives','restricted_stacks'],None,risks=('restricted_sections',)),
 institution('institution.konoha.aviary','Konoha Aviary','land_fire','doc.force_konoha_shinobi.field',['messenger_birds','dispatch'],None),
 institution('institution.konoha.jonin_standby','Jonin Standby Station','land_fire','doc.force_konoha_shinobi.field',['ready_roster','rapid_assignment'],None),
 institution('institution.konoha.orphanage','Konoha Orphanage','land_fire','doc.force_konoha_shinobi.field',['child_care','records'],None),
 institution('institution.konoha.military_police_archive','Konoha Military Police Legacy','land_fire','doc.force_konoha_shinobi.field',['inactive_records','former_precinct_assets'],None,risks=('inactive',)),
]
d['payload']['institutions']=merge_unique(d['payload'].get('institutions',[]),konoha)
p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')

# Great-village sub-institutions.
p=ROOT/'state/world/institutions-great-villages.json'; d=json.loads(p.read_text())
great=[]
for prefix,country,doctrine,leader,records in [
 ('suna','land_wind','doc.force_suna_shinobi.field','canon_rasa',[('kazekage_administration','Kazekage Administration'),('academy','Suna Academy'),('hospital','Suna Hospital'),('puppet_corps','Suna Puppet Corps'),('poison_corps','Suna Poison and Antidote Corps')]),
 ('kiri','land_water','doc.force_kiri_shinobi.field',None,[('mizukage_administration','Mizukage Administration'),('academy','Kiri Academy'),('hospital','Kiri Hospital'),('hunter_nin','Kiri Hunter-nin'),('maritime_corps','Kiri Maritime Corps')]),
 ('kumo','land_lightning','doc.force_kumo_shinobi.field','canon_raikage_a',[('raikage_administration','Raikage Administration'),('academy','Kumo Academy'),('medical','Kumo Medical Corps'),('sensor_relay','Kumo Sensor and Relay Network'),('strategic_guard','Kumo Strategic Asset Guard')]),
 ('iwa','land_earth','doc.force_iwa_shinobi.field','canon_onoki',[('tsuchikage_administration','Tsuchikage Administration'),('academy','Iwa Academy'),('medical','Iwa Medical Corps'),('earthworks','Iwa Earthworks Corps'),('demolition','Iwa Demolition Corps')]),
]:
    for suffix,name in records:
        great.append(institution(f'institution.{prefix}.{suffix}',name,country,doctrine,[suffix],leader if 'administration' in suffix else None))
d['payload']['institutions']=merge_unique(d['payload'].get('institutions',[]),great)
p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')

# Civil government, merchant, religious, and criminal institutions.
p=ROOT/'state/world/institutions-minor-and-civil.json'; d=json.loads(p.read_text())
minor=[
 institution('institution.fire.daimyo_court','Fire Daimyo Court','land_fire','doc.force_konoha_shinobi.field',['ruling_household','civil_ministers','provincial_liaison','shinobi_contracts'],None),
 institution('institution.fire.fire_temple','Fire Temple','land_fire','doc.force_konoha_shinobi.field',['monastic_order','temple_guard','pilgrim_hospitality'],None),
 institution('institution.konoha.ichiraku','Ramen Ichiraku','land_fire','doc.force_konoha_shinobi.field',['food_service','local_trade'],None),
 institution('institution.konoha.yamanaka_flowers','Yamanaka Flowers','land_fire','doc.force_konoha_shinobi.field',['retail','floral_trade'],None),
 institution('institution.konoha.yakiniku_q','Yakiniku Q','land_fire','doc.force_konoha_shinobi.field',['food_service','local_trade'],None),
 institution('institution.waves.gato_enterprise','Gato Company','land_waves','doc.force_kiri_shinobi.field',['shipping','private_security','coercive_trade'],'canon_gato',risks=('criminal_pressure',)),
 institution('institution.bounty_station_network','Bounty Station Network','land_rivers','doc.force_kusa_shinobi.field',['bounty_brokerage','criminal_contracts','cash_exchange'],None,risks=('criminal','restricted')),
 institution('institution.ame.central_authority','Amegakure Central Authority','land_rain','doc.force_ame_shinobi.field',['central_tower','surveillance','bridge_control'],'canon_nagato',risks=('secret_leadership',)),
 institution('institution.taki.village_administration','Takigakure Administration','land_waterfalls','doc.force_taki_shinobi.field',['village_administration','waterfall_guard'],None),
 institution('institution.kusa.village_administration','Kusagakure Administration','land_grass','doc.force_kusa_shinobi.field',['village_administration','border_watch'],None),
 institution('institution.oto.hideout_network','Otogakure Hideout Network','land_rice','doc.force_oto_network.field',['laboratories','safe_houses','retrieval'],'canon_orochimaru',risks=('secret','criminal')),
 institution('institution.iron.samurai_command','Land of Iron Samurai Command','land_iron','doc.force_iron_samurai.field',['central_command','pass_garrisons','forge_support'],'canon_mifune'),
]
d['payload']['institutions']=merge_unique(d['payload'].get('institutions',[]),minor)
p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')

# Bounded place catalog. Existence is cold state; it creates no scheduler entry.
p=ROOT/'state/world/routes-and-settlements.json'; d=json.loads(p.read_text())
places=[
 place('loc_konoha','Konohagakure','land_fire','hidden_village',authority='faction_konoha'),
 place('loc_fire_capital','Fire Capital','land_fire','capital',authority='office_fire_daimyo'),
 place('loc_fire_daimyo_court','Fire Daimyo Court','land_fire','government_complex',authority='office_fire_daimyo'),
 place('loc_hokage_residence','Hokage Residence','land_fire','government_facility',authority='faction_konoha'),
 place('loc_konoha_academy','Konoha Academy','land_fire','academy',authority='institution.konoha.academy'),
 place('loc_konoha_hospital','Konoha Hospital','land_fire','hospital',authority='institution.konoha.hospital'),
 place('loc_konoha_intelligence_division','Konoha Intelligence Division','land_fire','military_facility',authority='institution.konoha.intelligence_division',classification='restricted'),
 place('loc_konoha_interrogation','Konoha Interrogation Facility','land_fire','military_facility',authority='institution.konoha.interrogation',classification='restricted'),
 place('loc_konoha_anbu_headquarters','Konoha ANBU Operations Facility','land_fire','military_facility',authority='institution.konoha.anbu',classification='restricted',provenance='simulation_detail_on_canon_institution'),
 place('loc_konoha_root_subterranean','Root Subterranean Facility','land_fire','military_facility',authority='institution.konoha.root',classification='secret',provenance='simulation_detail_on_canon_institution'),
 place('loc_konoha_archive_library','Konoha Archive Library','land_fire','archive',authority='institution.konoha.archive_library'),
 place('loc_konoha_aviary','Konoha Aviary','land_fire','communications_facility',authority='institution.konoha.aviary'),
 place('loc_konoha_cemetery','Konoha Cemetery','land_fire','cemetery',authority='faction_konoha'),
 place('loc_konoha_memorial_stone','Memorial Stone','land_fire','memorial',authority='faction_konoha'),
 place('loc_jonin_standby_station','Jonin Standby Station','land_fire','military_facility',authority='institution.konoha.jonin_standby'),
 place('loc_mission_assignment_desk','Mission Assignment Desk','land_fire','government_facility',authority='institution.konoha.mission_assignment'),
 place('loc_third_training_ground','Third Training Ground','land_fire','training_ground',authority='faction_konoha'),
 place('loc_forty_fourth_training_ground','Forty-Fourth Training Ground','land_fire','training_ground',authority='faction_konoha',classification='restricted'),
 place('loc_zeroth_training_ground','Zeroth Training Ground','land_fire','training_ground',authority='faction_konoha'),
 place('loc_hokage_rock','Hokage Rock','land_fire','landmark',authority='faction_konoha'),
 place('loc_naka_river','Naka River','land_fire','natural_feature',authority=None),
 place('loc_naka_shrine','Naka Shrine','land_fire','historical_religious_site',authority='org_uchiha_remnant',classification='restricted'),
 place('loc_nara_clan_forest','Nara Clan Forest','land_fire','clan_land',authority='org_nara',classification='restricted'),
 place('loc_hyuga_compound','Hyuga Compound','land_fire','clan_compound',authority='org_hyuga'),
 place('loc_uchiha_district','Uchiha District','land_fire','historical_district',status='depopulated',authority='org_uchiha_remnant'),
 place('loc_uzumaki_mask_storage_temple','Uzumaki Clan Mask Storage Temple','land_fire','historical_religious_site',authority=None,classification='restricted'),
 place('loc_ramen_ichiraku','Ramen Ichiraku','land_fire','merchant',authority='institution.konoha.ichiraku'),
 place('loc_yamanaka_flowers','Yamanaka Flowers','land_fire','merchant',authority='institution.konoha.yamanaka_flowers'),
 place('loc_yakiniku_q','Yakiniku Q','land_fire','merchant',authority='institution.konoha.yakiniku_q'),
 place('loc_fire_temple','Fire Temple','land_fire','temple',authority='institution.fire.fire_temple'),
 place('loc_tanzaku_quarters','Tanzaku Quarters','land_fire','town',authority=None),
 place('loc_hacho_village','Hacho Village','land_fire','village',authority=None),
 place('loc_valley_end','Valley of the End','land_fire','historical_landmark',authority=None),
 place('loc_kikyo_pass','Kikyo Pass','land_fire','border_pass',authority='force_land_fire_civilian_security'),
 place('loc_kikyo_castle','Kikyo Castle','land_fire','historical_fortification',authority=None),
 place('loc_fire_northern_border','Northern Fire Border','land_fire','border_region',authority='force_land_fire_civilian_security',provenance='simulation_geography_anchor'),
 place('loc_fire_western_border','Western Fire Border','land_fire','border_region',authority='force_land_fire_civilian_security',provenance='simulation_geography_anchor'),
 place('loc_fire_northeast','Northeastern Fire Border','land_fire','border_region',authority='force_land_fire_civilian_security',provenance='simulation_geography_anchor'),
 place('loc_fire_northwest','Northwestern Fire Border','land_fire','border_region',authority='force_land_fire_civilian_security',provenance='simulation_geography_anchor'),
 place('loc_suna','Sunagakure','land_wind','hidden_village',authority='faction_suna'),
 place('loc_kiri','Kirigakure','land_water','hidden_village',authority='faction_kiri'),
 place('loc_kumo','Kumogakure','land_lightning','hidden_village',authority='faction_kumo'),
 place('loc_iwa','Iwagakure','land_earth','hidden_village',authority='faction_iwa'),
 place('loc_ame','Amegakure','land_rain','hidden_village',authority='faction_ame'),
 place('loc_kusa','Kusagakure','land_grass','hidden_village',authority='faction_kusa'),
 place('loc_taki','Takigakure','land_waterfalls','hidden_village',authority='faction_taki'),
 place('loc_yuga','Yugakure','land_hot_water','former_hidden_village',authority='faction_yuga'),
 place('loc_oto_network','Otogakure Network','land_rice','distributed_hidden_village',authority='faction_oto',classification='restricted'),
 place('loc_kannabi_bridge','Kannabi Bridge','land_grass','historical_bridge',authority=None),
 place('loc_tenchi_bridge','Tenchi Bridge','land_grass','bridge',authority=None),
 place('loc_wave_town','Land of Waves Town','land_waves','town',authority='government_wave_local_council'),
 place('loc_wave_coast','Land of Waves Coast','land_waves','coastal_region',authority=None),
 place('loc_gato_shipping','Gato Company Shipping Compound','land_waves','merchant_criminal_facility',authority='institution.waves.gato_enterprise',classification='restricted',provenance='simulation_detail_on_canon_institution'),
 place('loc_iron_capital','Land of Iron Capital','land_iron','capital',authority='office_iron_shogunate'),
 place('loc_iron_northern_pass','Northern Iron Pass','land_iron','border_pass',authority='faction_iron',provenance='simulation_geography_anchor'),
]
d['payload']['places']=merge_unique(d['payload'].get('places',[]),places)
# Add bounded local routes useful for real mission/training gameplay.
routes=d['payload'].get('routes',[])
add_routes=[
 {'from':'loc_konoha','id':'route_konoha_third_training','mode':'local_road','status':'open','to':'loc_third_training_ground','travel_days_band':[0,1],'reference_travel_days':0.08,'travel_mechanics_ref':'game/data/mechanics/travel.json'},
 {'from':'loc_konoha','id':'route_konoha_forty_fourth_training','mode':'local_road','status':'controlled','to':'loc_forty_fourth_training_ground','travel_days_band':[0,1],'reference_travel_days':0.12,'travel_mechanics_ref':'game/data/mechanics/travel.json'},
 {'from':'loc_konoha','id':'route_konoha_fire_temple','mode':'road','status':'open','to':'loc_fire_temple','travel_days_band':[1,3],'reference_travel_days':2.0,'travel_mechanics_ref':'game/data/mechanics/travel.json'},
 {'from':'loc_konoha','id':'route_konoha_tanzaku','mode':'road','status':'open','to':'loc_tanzaku_quarters','travel_days_band':[1,3],'reference_travel_days':2.0,'travel_mechanics_ref':'game/data/mechanics/travel.json'},
 {'from':'loc_konoha','id':'route_konoha_valley_end','mode':'forest_road','status':'patrolled','to':'loc_valley_end','travel_days_band':[1,3],'reference_travel_days':2.0,'travel_mechanics_ref':'game/data/mechanics/travel.json'},
 {'from':'loc_konoha','id':'route_konoha_kikyo_pass','mode':'military_road','status':'guarded','to':'loc_kikyo_pass','travel_days_band':[2,5],'reference_travel_days':3.0,'travel_mechanics_ref':'game/data/mechanics/travel.json'},
]
d['payload']['routes']=merge_unique(routes,add_routes)
p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')

print(json.dumps({
 'konoha_clans':len(json.loads((ROOT/'state/world/institutions-konoha.json').read_text())['payload']['clans']),
 'konoha_institutions':len(json.loads((ROOT/'state/world/institutions-konoha.json').read_text())['payload']['institutions']),
 'great_village_institutions':len(json.loads((ROOT/'state/world/institutions-great-villages.json').read_text())['payload']['institutions']),
 'minor_civil_institutions':len(json.loads((ROOT/'state/world/institutions-minor-and-civil.json').read_text())['payload']['institutions']),
 'places':len(d['payload']['places']), 'routes':len(d['payload']['routes'])
},indent=2))
