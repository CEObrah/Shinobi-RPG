#!/usr/bin/env python3
"""Build derived owner-index shards for bundled world institutions.

World institutions remain authoritative inside bounded world registry hosts.
This index makes stable institution/faction IDs directly addressable without
splitting those cold records into one file each.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WORLD_FILES=(
    'state/world/institutions-konoha.json',
    'state/world/institutions-minor-and-civil.json',
)
FACTION_FILES=tuple(
    str(path.relative_to(ROOT)) for path in sorted((ROOT/'state/reg/factions').glob('*.json'))
)

def read(rel): return json.loads((ROOT/rel).read_text(encoding='utf-8'))
def write(rel,val):
    p=ROOT/rel; p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(val,separators=(',',':'))+'\n',encoding='utf-8')

def main():
    by_prefix={'faction':{},'institution':{}}
    for rel in WORLD_FILES:
        rec=read(rel)
        for item in rec.get('payload',{}).get('institutions',[]):
            ident=item.get('id')
            if not isinstance(ident,str): continue
            prefix=ident.split('.',1)[0].split('_',1)[0]
            if prefix in by_prefix:
                if ident in by_prefix[prefix]: raise ValueError(f'duplicate {ident}')
                by_prefix[prefix][ident]=rel
    for rel in FACTION_FILES:
        rec=read(rel)
        faction=rec.get('faction') if isinstance(rec,dict) else None
        ident=faction.get('id') if isinstance(faction,dict) else None
        if not isinstance(ident,str): continue
        if ident in by_prefix['faction']: raise ValueError(f'duplicate {ident}')
        by_prefix['faction'][ident]=rel
    for prefix, owners in by_prefix.items():
        write(f'state/index/owners/{prefix}.json',{
            'schema':'owner-index-shard','prefix':prefix,'authority':False,
            'owners':dict(sorted(owners.items())),
        })
    index=read('state/index/owners.json')
    for prefix in by_prefix:
        index['prefix_index'][prefix]=f'state/index/owners/{prefix}.json'
    # Derived count is diagnostic only; compute it from every registered shard.
    total=0
    for shard_rel in index['prefix_index'].values():
        try: shard=read(shard_rel)
        except FileNotFoundError: continue
        total += len(shard.get('owners',{}))
    index['owner_count']=total
    index['prefix_index']=dict(sorted(index['prefix_index'].items()))
    write('state/index/owners.json',index)
    print('faction owners',len(by_prefix['faction']))
    print('institution owners',len(by_prefix['institution']))
    print('derived owner_count',total)
if __name__=='__main__': main()
