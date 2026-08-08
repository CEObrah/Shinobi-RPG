#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

TARGETS={
    'campaign-objective',
    'faction-registry.v1',
    'home-establishments.v1',
    'house_units',
    'item-maneuvers.v1',
    'loadouts-index.v1',
    'loadouts-shard.v1',
    'loadouts',
    'ocular-inventory.v1',
    'ocular-registry',
    'repository-map.v1',
    'repository-map.v2',
    'repository-map.v3',
    'runtime-rule-router.v1',
    'technique-primitives',
    'template-index',
}


def read(rel):
    return json.loads((ROOT/rel).read_text(encoding='utf-8'))

def write(rel,data):
    (ROOT/rel).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def remove_file(rel,removed):
    p=ROOT/rel
    if p.exists():
        p.unlink();removed.append(rel)

# Capture registrations before pruning.
registrations={}
for shard in sorted((ROOT/'data/runtime/template-index-shards').glob('*.json')):
    data=json.loads(shard.read_text(encoding='utf-8'))
    changed=False
    for target in list(data.get('templates',{})):
        if target in TARGETS:
            registrations[target]=(data['templates'][target],shard.relative_to(ROOT).as_posix())
            del data['templates'][target]
            changed=True
    if changed:
        shard.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

missing=sorted(TARGETS-set(registrations))
if missing:
    raise SystemExit('targets not registered: '+','.join(missing))

# Remove dead owner construction authority for the old House sidecar.
blank=read('data/runtime/blank-owner-index.json')
old_blank=blank.get('owners',{}).pop('house_units',None)
write('data/runtime/blank-owner-index.json',blank)

contract=read('data/runtime/system-contracts/forces_institutions.json')
contract['owner_templates']=[x for x in contract.get('owner_templates',[]) if x!='house_units']
write('data/runtime/system-contracts/forces_institutions.json',contract)

# Remove obsolete schema registry IDs. Save mapped schema paths for safe deletion.
registry=read('schemas/registry.json')
schema_paths={}
for target in TARGETS:
    mapped=registry.pop(target,None)
    if mapped:schema_paths[target]='schemas/'+mapped
write('schemas/registry.json',registry)

# Delete template files and dead blank skeleton.
removed=[]
for target,(meta,_) in registrations.items():
    remove_file(meta['path'],removed)
if old_blank:remove_file(old_blank,removed)

# A schema file may be deleted only if no surviving registry entry or surviving template references it.
surviving_schema_refs={'schemas/'+v for v in registry.values()}
for shard in sorted((ROOT/'data/runtime/template-index-shards').glob('*.json')):
    data=json.loads(shard.read_text(encoding='utf-8'))
    for meta in data.get('templates',{}).values():
        src=meta.get('source_schema')
        if src:surviving_schema_refs.add(src)
for target,rel in schema_paths.items():
    if rel not in surviving_schema_refs:remove_file(rel,removed)
for target,(meta,_) in registrations.items():
    src=meta.get('source_schema')
    if src and src not in surviving_schema_refs:remove_file(src,removed)

# The obsolete House sidecar had a registry schema even though its template source_schema was null.
# The state projection itself was already removed in the population/House consolidation.
if (ROOT/'state/house/units.json').exists():
    raise SystemExit('obsolete state/house/units.json unexpectedly exists')

print('REMOVED_TARGETS',len(TARGETS))
for target in sorted(TARGETS):print('TARGET',target)
print('REMOVED_FILES',len(set(removed)))
for rel in sorted(set(removed)):print('FILE',rel)
