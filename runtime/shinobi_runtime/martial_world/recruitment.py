"""Stable deterministic population candidates and non-rerolling screening."""
from __future__ import annotations
import hashlib,json,math
from pathlib import Path
from typing import Any,Mapping
_ROOT=Path(__file__).resolve().parents[3];_MW=_ROOT/'game/data/martial-world'
def _load()->Mapping[str,Any]: return json.loads((_MW/'population-recruitment.json').read_text(encoding='utf-8'))
def _u01(seed:str,label:str)->float:
    v=int.from_bytes(hashlib.sha256((seed+'\x00'+label).encode()).digest()[:8],'big');return (v+0.5)/(2**64)
def _normal(seed:str,label:str)->float:
    u1=max(1e-12,_u01(seed,label+':a'));u2=_u01(seed,label+':b');return math.sqrt(-2*math.log(u1))*math.cos(2*math.pi*u2)
def _sample(seed:str,label:str,spec:Mapping[str,Any],latent:float=0.0,weight_milli:int=0)->int:
    mean=float(spec.get('mean',0));sd=float(spec.get('sd',0));low=int(spec.get('min',0));high=int(spec.get('max',10**9))
    w=max(0,min(800,weight_milli))/1000.0; independent=_normal(seed,label);z=independent*(1-w)+latent*w
    return max(low,min(high,int(round(mean+sd*z))))
def background_for_ordinal(*,origin_population_id:str,ordinal:int)->str:
    d=_load();mix=d['settlement_background_mix_milli']; total=sum(int(v) for v in mix.values())
    if total!=1000: raise ValueError('background mix must sum to 1000')
    salt=int.from_bytes(hashlib.sha256(origin_population_id.encode()).digest()[:4],'big')%1000
    slot=(ordinal*613+salt)%1000;cursor=0
    for name,weight in mix.items():
        cursor+=int(weight)
        if slot<cursor:return name
    raise ValueError('background mix')
def deterministic_candidate(*,world_seed:str,origin_population_id:str,ordinal:int,background:str|None=None)->dict[str,Any]:
    d=_load(); bg=background or background_for_ordinal(origin_population_id=origin_population_id,ordinal=ordinal); profile=d['background_profiles'].get(bg)
    if not isinstance(profile,Mapping): raise KeyError(bg)
    stable=f'{world_seed}\x00{origin_population_id}\x00{ordinal}';cid='candidate.'+hashlib.sha256(stable.encode()).hexdigest()[:24]
    latent={name:_normal(stable,'latent:'+name) for name in d.get('correlation_factors',{})}
    weights:dict[tuple[str,str],tuple[float,int]]={}
    for fname,f in d.get('correlation_factors',{}).items():
        for section in ('attributes','skills','aptitudes'):
            for key in f.get(section,[]): weights[(section,key)]=(latent[fname],int(f.get('weight_milli',0)))
    def section(name:str):
        out={}
        for key,spec in profile[name].items():
            lat,w=weights.get((name,key),(0.0,0));out[key]=_sample(stable,f'{name}:{key}',spec,lat,w)
        return out
    return {'candidate_id':cid,'origin_population_id':origin_population_id,'origin_ordinal':ordinal,'background':bg,
            'age':_sample(stable,'age',profile['age']),'appearance':_sample(stable,'appearance',profile['appearance']),
            'aptitudes':section('aptitudes'),'attributes':section('attributes'),'martial_skills':section('skills'),
            'rule':'Same world seed + origin population + ordinal always identifies the same already-counted person. Screening and recruitment never reroll them.'}
def screening_report(candidate:Mapping[str,Any],*,evaluator_skill:int)->dict[str,Any]:
    """Poor evaluators see broader bands; they never alter true aptitude."""
    skill=max(0,evaluator_skill); resolution=max(1,25-skill//10)
    true=candidate.get('aptitudes',{}); observed={}
    for key,value in true.items():
        v=int(value); observed[key]={'band_low':max(0,(v//resolution)*resolution),'band_high':min(200,(v//resolution)*resolution+resolution-1)}
    return {'candidate_id':candidate['candidate_id'],'evaluator_skill':skill,'aptitude_observation':observed,'true_values_changed':False}
