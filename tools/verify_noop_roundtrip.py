#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/'runtime'))

from shinobi_runtime.martial_world.faction_state import compact_faction_state, hydrate_faction_state
from shinobi_runtime.martial_world.person_state import compact_roster_state, hydrate_roster_state
from shinobi_runtime.martial_world.inventory_state import compact_inventory_state, hydrate_inventory_state
from shinobi_runtime.martial_world.equipment_state import compact_equipment_ledger, hydrate_equipment_ledger


def load(path:Path):
    return json.loads(path.read_text(encoding='utf-8'))

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--json',dest='json_path')
    args=ap.parse_args()
    identities=load(ROOT/'game/data/martial-world/faction-identities.json').get('identities',{})
    diffs=[]; checked_factions=checked_rosters=checked_inventories=0
    for fid in sorted(identities):
        fp=ROOT/f'state/martial-world/factions/{fid}.json'
        rp=ROOT/f'state/martial-world/people/{fid}.json'
        ip=ROOT/f'state/martial-world/inventories/{fid}.json'
        raw_f=load(fp); logical_f=hydrate_faction_state(raw_f); checked_factions+=1
        if compact_faction_state(logical_f)!=raw_f: diffs.append(str(fp.relative_to(ROOT)))
        raw_r=load(rp); logical_r=hydrate_roster_state(raw_r,faction=logical_f); checked_rosters+=1
        if compact_roster_state(logical_r,faction=logical_f)!=raw_r: diffs.append(str(rp.relative_to(ROOT)))
        raw_i=load(ip); checked_inventories+=1
        if compact_inventory_state(hydrate_inventory_state(raw_i))!=raw_i: diffs.append(str(ip.relative_to(ROOT)))
    ep=ROOT/'state/martial-world/equipment-ledger.json'; raw_e=load(ep)
    if compact_equipment_ledger(hydrate_equipment_ledger(raw_e))!=raw_e: diffs.append(str(ep.relative_to(ROOT)))
    result={'status':'PASS' if not diffs else 'FAIL','checked_factions':checked_factions,'checked_rosters':checked_rosters,'checked_inventories':checked_inventories,'checked_equipment_ledger':True,'differences':diffs}
    text=json.dumps(result,indent=2)+'\n'
    if args.json_path:
        out=ROOT/args.json_path; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(text,encoding='utf-8')
    print(text,end='')
    return 0 if not diffs else 1
if __name__=='__main__': raise SystemExit(main())
