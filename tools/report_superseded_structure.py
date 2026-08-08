#!/usr/bin/env python3
from __future__ import annotations
import json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def rj(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))

def schemas_in(x,out):
    if isinstance(x,dict):
        s=x.get('schema')
        if isinstance(s,str):out.add(s)
        for v in x.values():schemas_in(v,out)
    elif isinstance(x,list):
        for v in x:schemas_in(v,out)

# Count every instantiated schema, including structural metadata such as file-template.v1
# and template-index-shard.v1. Schema definition files themselves do not instantiate types.
active_schemas=set()
for p in ROOT.rglob('*.json'):
    rel=p.relative_to(ROOT).as_posix()
    if rel.startswith('schemas/'):continue
    try:schemas_in(rj(p),active_schemas)
    except Exception:pass

contract_owner_types=set()
contract_index=rj(ROOT/'data/runtime/system-contract-index.json')
for rel in contract_index.get('systems',{}).values():
    d=rj(ROOT/rel)
    contract_owner_types.update(x for x in d.get('owner_templates',[]) if isinstance(x,str))
blank_index=rj(ROOT/'data/runtime/blank-owner-index.json').get('owners',{})

entries=[]
for shard in sorted((ROOT/'data/runtime/template-index-shards').glob('*.json')):
    d=rj(shard)
    for target,meta in d.get('templates',{}).items():
        entries.append((target,meta['path'],meta.get('source_schema'),shard.relative_to(ROOT).as_posix()))
entry_names={x[0] for x in entries}

def base_name(name):
    return re.sub(r'\.v\d+$','',name)

def payload(target,path,source,shard):
    return {'target':target,'path':path,'source_schema':source,'index_shard':shard,
            'active_schema':target in active_schemas,'contract_owner':target in contract_owner_types,
            'blank_owner':target in blank_index}

print('ACTIVE_SCHEMA_IDS',len(active_schemas))
print('CONTRACT_OWNER_TYPES',len(contract_owner_types))
print('\nSUPERSEDED_TEMPLATE_SIBLINGS')
count=0
for target,path,source,shard in sorted(entries):
    base=base_name(target)
    if base==target or base not in entry_names:continue
    d=payload(target,path,source,shard);d['current_sibling']=base
    print(json.dumps(d,sort_keys=True));count+=1
print('COUNT',count)

print('\nUNUSED_REGISTERED_TEMPLATES')
for target,path,source,shard in sorted(entries):
    if target in active_schemas or target in contract_owner_types or target in blank_index:continue
    print(json.dumps(payload(target,path,source,shard),sort_keys=True))

print('\nBLANK_OWNER_CANDIDATES_WITHOUT_INSTANCE_OR_CONTRACT')
for owner_type,rel in sorted(blank_index.items()):
    if owner_type in active_schemas or owner_type in contract_owner_types:continue
    print(json.dumps({'owner_type':owner_type,'blank':rel},sort_keys=True))
