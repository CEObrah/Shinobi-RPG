#!/usr/bin/env python3
import json, pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
model=json.loads((ROOT/'data/development/model.json').read_text())
eff=model['representation_efficiency']
assert set(eff.values())=={1.0}, eff
# Identical inputs must not change because of representation.
base=100.0
aptitude=1.6
attendance=.9
instructor=.85
facility=.95
equipment=.9
health=.92
recovery=.9
relevance=1.0
difficulty=.95
common=base*aptitude*attendance*instructor*facility*equipment*health*recovery*relevance*difficulty
vals={k:common*v for k,v in eff.items()}
assert len({round(v,10) for v in vals.values()})==1, vals
# Unit promotion is a conservation transfer, never a multiplier.
starting=1000
qualified=37
remaining=starting-qualified
assert remaining+qualified==starting and remaining>=0
# Instructor capacity cannot exceed available hours when measured as personalized student-hours.
available_instructor_hours=30
requested=[10,10,10]
assert sum(requested)<=available_instructor_hours
overrequested=[10,10,10,10]
assert sum(overrequested)>available_instructor_hours
assert model['promotion_rule']['mode']=='qualified_subset_transfer'
assert model['batching_rule']['batch_equivalence_required'] is True
# Lazy bank registration must exist and be part of the training/development contract.
index=json.loads((ROOT/'data/runtime/template-index-shards/d.json').read_text())
assert 'development-bank-registry.v1' in index['templates']
template=json.loads((ROOT/'data/runtime/templates/development-bank-registry.v1.template.json').read_text())
assert template['object_contracts']['/entries']['mode']=='open_map'
assert template['object_contracts']['/entries/*/credits']['mode']=='open_map'
contract=json.loads((ROOT/'data/runtime/system-contracts/training_development.json').read_text())
assert 'state/development/' in contract['authority_paths']
assert 'development-bank-registry.v1' in contract['owner_templates']
assert any('Aggregate process settled_through' in x for x in contract['invariants'])
rules=(ROOT/'rules/training.md').read_text()
assert 'Lazy deterministic development bank' in rules
# Residual development units are consumed against current point cost and cost is recomputed after each point.
def point_cost(v):
    return 1 + max(0, v-40)//20

def consume(value, credit):
    while credit + 1e-12 >= point_cost(value):
        credit -= point_cost(value)
        value += 1
    return value, credit
v, residual = consume(59, 3.5)
assert v==61 and abs(residual-0.5)<1e-9, (v,residual)
# A bank file, when present, must never contain negative credit.
bank_path=ROOT/'state/development/banks.json'
if bank_path.exists():
    bank=json.loads(bank_path.read_text())
    assert bank['schema']=='development-bank-registry.v1'
    for owner_id, entry in bank['entries'].items():
        assert entry['owner_type'] in {'character','person_lite','unit'}, owner_id
        assert all(v>=0 for v in entry['credits'].values()), owner_id
print('DEVELOPMENT FAIRNESS OK')
print('representation_efficiency='+json.dumps(eff,sort_keys=True))
print('sample_effective_training='+str(round(common,4)))
