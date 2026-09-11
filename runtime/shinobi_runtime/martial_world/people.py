"""Deterministic initial Person Lite materialization for exact martial factions."""
from __future__ import annotations
import hashlib, json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from .recruitment import deterministic_candidate
from .faction_state import faction_admission_policy

SURNAMES=("Li","Wang","Zhang","Liu","Chen","Yang","Huang","Zhao","Wu","Zhou","Xu","Sun","Ma","Zhu","Hu","Guo","He","Gao","Lin","Luo","Tang","Han","Cao","Deng","Xiao","Lu","Jiang","Shen","Qian","Du","Peng","Yuan","Pan","Cui","Qiao","Fan","Pei","Gu","Yue","Ren")
GIVEN=("An","Bo","Chen","Dai","Feng","Guang","Hai","Hao","Jian","Jun","Kai","Lei","Liang","Ming","Ning","Ping","Qiang","Qiu","Rong","Shan","Tao","Wei","Wen","Xian","Yan","Yi","Yong","Yu","Zhen","Zhi","Lan","Mei","Qing","Rui","Xiu","Ying","Yue","Lin","Hua","Jing")


def _n(seed:str,label:str,lo:int,hi:int)->int:
    v=int.from_bytes(hashlib.sha256((seed+'\0'+label).encode()).digest()[:8],'big')
    return lo+v%(hi-lo+1)


@lru_cache(maxsize=1)
def _identity_rules()->Mapping[str,Any]:
    root=Path(__file__).resolve().parents[3]
    data=json.loads((root/'game/data/martial-world/faction-identities.json').read_text(encoding='utf-8'))
    rows=data.get('identities',{}) if isinstance(data,Mapping) else {}
    return rows if isinstance(rows,Mapping) else {}


def deterministic_sex(*,stable:str,faction_id:str,admission_policy:Mapping[str,Any]|None=None)->str:
    policy=faction_admission_policy(faction_id, {'admission_policy': admission_policy} if isinstance(admission_policy,Mapping) else None)
    allowed=[str(x) for x in policy.get('allowed_sexes',[]) if str(x) in {'male','female'}]
    if not allowed: raise ValueError(f'jianghu faction demography has no lawful sex:{faction_id}')
    return allowed[_n(stable,'sex',0,len(allowed)-1)]


def deterministic_body_mass_kg(*,stable:str,sex:str,age:int|None=None)->int:
    adult=_n(stable,'adult_body_mass_kg',58,82) if sex=='male' else _n(stable,'adult_body_mass_kg',48,72)
    if age is None or age>=18: return adult
    if age<=0: return _n(stable,'infant_mass',3,5)
    if age==1: return _n(stable,'toddler_mass',8,12)
    milli=max(180,min(940,200+(age-2)*740//15))
    return max(9,adult*milli//1000)

def apply_age_development(*,age:int,attributes:Mapping[str,Any],martial_skills:Mapping[str,Any],professional_skills:Mapping[str,Any],qi:int,qi_control:int)->dict[str,Any]:
    if age>=18: scales=(1000,1000,1000)
    else:
        physical=max(40,min(950,40+age*52)); martial=max(0,min(900,max(0,age-4)*70)); professional=max(0,min(800,max(0,age-7)*70)); scales=(physical,martial,professional)
    physical,martial,professional=scales
    attrs={k:max(0,int(v)*(physical if k not in {'perception','intelligence','willpower'} else min(1000,physical+100))//1000) for k,v in attributes.items()}
    return {'attributes':attrs,'martial_skills':{k:max(0,int(v)*martial//1000) for k,v in martial_skills.items()},'professional_skills':{k:max(0,int(v)*professional//1000) for k,v in professional_skills.items()},'qi':max(0,int(qi)*martial//1000),'qi_control':max(0,int(qi_control)*martial//1000)}

def deterministic_name(*,stable:str,sex:str)->str:
    surname=SURNAMES[_n(stable,'surname',0,len(SURNAMES)-1)]
    first=GIVEN[_n(stable,'given',0,len(GIVEN)-1)]
    second=GIVEN[_n(stable,'given2',0,len(GIVEN)-1)]
    return f'{surname} {first}{second}' if first!=second else f'{surname} {first}'

def person_lite(*,world_seed:str,faction_id:str,headquarters:str,ordinal:int,training:Mapping[str,int],recruitment_policy:Mapping[str,Any],current_year:int=61)->dict[str,Any]:
    stable=f'{world_seed}\0{faction_id}\0{ordinal}'
    origin=f'{headquarters}.resident_pool'
    cand=deterministic_candidate(world_seed=world_seed,origin_population_id=origin,ordinal=ordinal)
    # Existing factions include children and elders; applicant generation is used
    # only for stable correlated aptitude/attribute seeds, not its applicant age.
    policy=faction_admission_policy(faction_id)
    minimum=max(0,int(policy.get('minimum_entry_age',8)))
    # Persistent faction rosters include children, adults and elders, but
    # newly generated martial members must satisfy the faction's entry age.
    age=_n(stable,'age',minimum,72)
    sex=deterministic_sex(stable=stable,faction_id=faction_id,admission_policy=policy)
    name=deterministic_name(stable=stable,sex=sex)
    apt=dict(cand['aptitudes'])
    appearance=int(cand['appearance'])
    attrs=dict(cand['attributes'])
    skills=dict(cand['martial_skills'])
    service_years=max(0,age-14)
    apt_factor=max(20,int(apt.get('martial',100)))
    combat_keys=('sword','spear','bow','hidden_weapons','unarmed')
    combat_weights={k:max(0,int(training.get(k,0))) for k in combat_keys}
    allowed={k:w for k,w in combat_weights.items() if w>0}
    # Institutional breadth consumes one finite combat-training budget.
    # Adding a third discipline redistributes the same historical hours; it
    # never grants a third full-rate stream of free development.
    if allowed:
        total_weight=sum(allowed.values())
        base_pool=sum(max(0,int(skills.get(k,0))) for k in allowed)
        historical_budget=service_years*max(allowed.values())*apt_factor//12000
        pool=base_pool+historical_budget
        raw={k:pool*w for k,w in allowed.items()}
        apportioned={k:v//total_weight for k,v in raw.items()}
        remainder=pool-sum(apportioned.values())
        order=sorted(raw,key=lambda k:(-(raw[k]%total_weight),-allowed[k],k))
        for k in order[:remainder]: apportioned[k]+=1
        for k in combat_keys:
            if k in apportioned and apportioned[k]>0: skills[k]=apportioned[k]
            else: skills.pop(k,None)
    else:
        for k in combat_keys: skills.pop(k,None)
    # Role/field subjects remain separate current capabilities but only
    # receive historical institutional growth when the faction actually
    # teaches them. Live future training uses the stricter shared-hours
    # institutional budget in martial_world.training.
    for key in ('stealth_scouting','command'):
        emphasis=max(0,int(training.get(key,0)))
        if emphasis>0:
            skills[key]=max(0,int(skills.get(key,0))+service_years*emphasis*apt_factor//12000)
        else:
            skills.pop(key,None)
    qi=max(0,service_years*int(training.get('qi',50))*int(apt.get('qi',100))//10000)
    qi_control=max(0,service_years*int(training.get('qi_control',50))*int(apt.get('qi',100))//10000)
    professional={
      'medicine':_n(stable,'prof:medicine',0,35),'administration':_n(stable,'prof:admin',0,35),'commerce':_n(stable,'prof:commerce',0,35),'crafting':_n(stable,'prof:crafting',0,35),'instruction':_n(stable,'prof:instruction',0,30)}
    peak=max(skills.values()) if skills else 0
    if age>=58 and peak>=130 and qi>=110 and qi_control>=110: grade='elder'
    elif peak>=110: grade='elite'
    elif peak>=80: grade='senior'
    elif peak>=50: grade='full'
    elif peak>=25: grade='junior'
    else: grade='probationary'
    developed=apply_age_development(age=age,attributes=attrs,martial_skills=skills,professional_skills=professional,qi=qi,qi_control=qi_control)
    attrs=developed['attributes']; skills=developed['martial_skills']; professional=developed['professional_skills']; qi=developed['qi']; qi_control=developed['qi_control']
    body_mass_kg=deterministic_body_mass_kg(stable=stable,sex=sex,age=age)
    return {
      'person_id':f'mw.person.{faction_id}.{ordinal:04d}','name':name,
      'birth_year':current_year-age,'sex':sex,'body_mass_kg':body_mass_kg,'appearance':appearance,
      'aptitudes':apt,'attributes':attrs,'martial_skills':skills,'professional_skills':professional,'qi':qi,'qi_control':qi_control,
      'membership_grade':grade}
