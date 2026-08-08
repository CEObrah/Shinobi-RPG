#!/usr/bin/env python3
from __future__ import annotations
import json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

EXCLUDED_PREFIXES=(
    'schemas/',
    'data/runtime/templates/',
    'data/runtime/blank-owners/',
    'data/runtime/template-index',
)

def rj(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))

def schemas_in(x,out):
    if isinstance(x,dict):
        s=x.get('schema')
        if isinstance(s,str):out.add(s)
        for v in x.values():schemas_in(v,out)
    elif isinstance(x,list):
        for v in x:schemas_in(v,out)

active_schemas=set()
for p in ROOT.rglob('*.json'):
    rel=p.relative_to(ROOT).as_posix()
    if any(rel.startswith(x) for x in EXCLUDED_PREFIXES):continue
    try:schemas_in(rj(p),active_schemas)
    except Exception:pass

# Structural owner types explicitly required by live system contracts.
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

print('ACTIVE_SCHEMA_IDS',len(active_schemas))
print('CONTRACT_OWNER_TYPES',len(contract_owner_types))
print('\nSUPERSEDED_TEMPLATE_SIBLINGS')
count=0
for target,path,source,shard in sorted(entries):
    base=base_name(target)
    if base==target or base not in entry_names:continue
    used=target in active_schemas
    contract=target in contract_owner_types
    blank=target in blank_index
    print(json.dumps({'target':target,'current_sibling':base,'path':path,'source_schema':source,'index_shard':shard,'active_schema':used,'contract_owner':contract,'blank_owner':blank},sort_keys=True))
    count+=1
print('COUNT',count)

print('\nVERSIONED_TEMPLATE_ENTRIES_WITHOUT_CURRENT_SIBLING')
for target,path,source,shard in sorted(entries):
    base=base_name(target)
    if base==target or base in entry_names:continue
    print(json.dumps({'target':target,'path':path,'source_schema':source,'active_schema':target in active_schemas,'contract_owner':target in contract_owner_types,'blank_owner':target in blank_index},sort_keys=True))

print('\nBLANK_OWNER_CANDIDATES_WITHOUT_INSTANCE_OR_CONTRACT')
for owner_type,rel in sorted(blank_index.items()):
    if owner_type in active_schemas or owner_type in contract_owner_types:continue
    print(json.dumps({'owner_type':owner_type,'blank':rel},sort_keys=True))
