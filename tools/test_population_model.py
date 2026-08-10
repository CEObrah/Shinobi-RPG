#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
errs=[]
def err(x): errs.append(x)
def read(rel):
    try:return json.loads((ROOT/rel).read_text(encoding='utf-8'))
    except Exception as exc: err(f'json:{rel}:{exc}'); return {}

reg=read('state/population/registry.json')
if reg.get('schema')!='population-registry': err('population_schema')
pools=reg.get('pools',{})
transfers=reg.get('transfers',[])
if not isinstance(pools,dict) or not pools: err('no_population_pools'); pools={}
if not isinstance(transfers,list): err('population_transfers_not_list'); transfers=[]

total=0
by_settlement={}
for pool_id,pool in pools.items():
    count=pool.get('count')
    if not isinstance(count,int) or count<0: err(f'bad_pool_count:{pool_id}:{count}'); continue
    total+=count
    settlement=pool.get('owner_ref') or 'unknown'
    by_settlement[settlement]=by_settlement.get(settlement,0)+count
    dims=(pool.get('profile') or {}).get('dimension_counts')
    if not isinstance(dims,dict): err(f'pool_dimensions:{pool_id}')
    provenance=pool.get('provenance')
    if not provenance: err(f'pool_provenance_missing:{pool_id}')

# Every transfer is an auditable conserved movement, never a source of people.
for i,t in enumerate(transfers):
    if not isinstance(t,dict): err(f'bad_transfer:{i}'); continue
    count=t.get('count')
    if not isinstance(count,int) or count<=0: err(f'bad_transfer_count:{i}:{count}')
    if t.get('source_pool_id') not in pools: err(f'transfer_source_missing:{i}:{t.get("source_pool_id")}')
    if t.get('destination_pool_id') not in pools: err(f'transfer_destination_missing:{i}:{t.get("destination_pool_id")}')
    if not t.get('transaction_ref') and not t.get('event_ref'): err(f'transfer_provenance_missing:{i}')

# The seeded five-village census is explicitly simulation-estimate data, not canon census fact.
expected={'faction_konoha':80000,'faction_iwa':64000,'faction_kumo':48000,'faction_suna':32000,'faction_kiri':32000}
for settlement,expected_count in expected.items():
    actual=by_settlement.get(settlement,0)
    if actual!=expected_count: err(f'great_village_population_drift:{settlement}:{actual}:{expected_count}')
if sum(expected.values())!=256000 or total<256000: err(f'population_total_too_small:{total}')

# Sword Manor identity materialization is conserved by roster representation, not extra headcount.
house=read('state/house/tang.json'); cores=read('state/person-core/house-tang.json').get('people',{})
if len(house.get('member_ids',[]))!=32 or len(cores)!=27: err('house_identity_conservation')

if errs:
    print('POPULATION MODEL TEST FAILED')
    for e in errs: print('-',e)
    sys.exit(1)
print('POPULATION MODEL TEST OK')
print(f'pools={len(pools)} represented_population={total} transfers={len(transfers)} house_roster_cores={len(cores)}')
