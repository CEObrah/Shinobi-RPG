from pathlib import Path
import json,glob,sys,re
R=Path(__file__).resolve().parents[1]
GAME='sword' if (R/'state/char-roster').exists() else 'shinobi'
errs=[]
def err(x): errs.append(x)
def rj(rel):
    try:return json.loads((R/rel).read_text(encoding='utf-8'))
    except Exception as e:err(f'json:{rel}:{e}');return {}

m=rj('runtime/contracts/repository-map.json')
# Hot startup contract exists exactly as declared.
for rel in m.get('hot',[]):
    if not (R/rel).exists():err(f'hot_missing:{rel}')
# Route shards and route_index must agree.
all_routes=dict(m.get('routes',{})); shard_routes={}
for shard,rel in m.get('route_shards',{}).items():
    if not (R/rel).exists():err(f'route_shard_missing:{shard}:{rel}');continue
    d=rj(rel); rs=d.get('routes',{}); shard_routes[shard]=rs; all_routes.update(rs)
for name,shard in m.get('route_index',{}).items():
    if shard not in shard_routes:err(f'route_index_unknown_shard:{name}:{shard}')
    elif name not in shard_routes[shard]:err(f'route_index_route_missing_from_shard:{name}:{shard}')
# Direct route references must exist. Globs are allowed to be empty only for on-demand state.
for name,spec in all_routes.items():
    if not isinstance(spec,dict):continue
    for key in ('r','w','i'):
        for rel in spec.get(key,[]) or []:
            if not (R/rel).exists():err(f'route_ref_missing:{name}:{key}:{rel}')
    for pat in spec.get('g',[]) or []:
        parent=pat.split('*',1)[0].rstrip('/')
        pp=R/parent
        if parent and not pp.exists() and not pp.parent.exists():err(f'route_glob_parent_missing:{name}:{pat}')
# Rule router refs must exist.
rr=rj('runtime/contracts/rule-router.json')
rule_domains=rr.get('domains',{})
if not isinstance(rule_domains,dict):
    err('rule_domains_not_object');rule_domains={}
for name,spec in all_routes.items():
    if not isinstance(spec,dict):continue
    domain=spec.get('domain')
    if domain is None:continue
    if not isinstance(domain,str) or not domain:err(f'route_domain_invalid:{name}')
    elif domain not in rule_domains:err(f'route_domain_missing:{name}:{domain}')
for dom,refs in rule_domains.items():
    if not isinstance(refs,list):err(f'rule_domain_not_list:{dom}');continue
    for rel in refs:
        if not (R/rel).exists():err(f'rule_ref_missing:{dom}:{rel}')
# Structural indexes and every system contract target must exist.
for rel in ('runtime/contracts/template-index.json','runtime/contracts/system-contract-index.json','runtime/contracts/narration-router.json'):
    if not (R/rel).exists():err(f'router_support_missing:{rel}')
sc=rj('runtime/contracts/system-contract-index.json')
for sid,rel in sc.get('systems',{}).items():
    if not (R/rel).exists():err(f'system_contract_missing:{sid}:{rel}')
# Canonical human update cookbook lives in the Shinobi Game Master Skill, not retired root manuals.
skill_root=R/'plugins/shinobi-rpg/skills/shinobi-game-master'
human_map=skill_root/'references/repository-map.md'
if not human_map.exists():
    err('canonical_skill_repository_map_missing')
    h=''
else:
    h=human_map.read_text(encoding='utf-8')
for phrase in ('Minimum-context development routes','Structural write contract','Common update matrix','People and materialization','Teams, forces, and formations','Large battle workflow'):
    if phrase.lower() not in h.lower():err(f'human_map_missing:{phrase}')
for phrase in ('template-index.json','system-contract-index.json','authority','validator'):
    if phrase.lower() not in h.lower():err(f'human_map_write_contract_missing:{phrase}')
# Family direct kinship, behavior depth, and clan profile routing should be discoverable.
for route in ('family_kinship','character_behavior','clan_known_id'):
    if route not in all_routes:err(f'important_route_missing:{route}')
# Game isolation: routing/runtime/Skill docs may not teach the other game's vocabulary/representation.
texts=[]
doc_paths=(
    'plugins/shinobi-rpg/skills/shinobi-game-master/references/runtime-architecture.md',
    'plugins/shinobi-rpg/skills/shinobi-game-master/references/narration.md',
    'plugins/shinobi-rpg/skills/shinobi-game-master/references/repository-map.md',
    'plugins/shinobi-rpg/skills/shinobi-game-master/references/player-interface.md',
    'runtime/contracts/repository-map.json',
    'runtime/contracts/rule-router.json',
)
for rel in doc_paths:
    p=R/rel
    if not p.exists():
        err(f'canonical_routing_doc_missing:{rel}')
        continue
    texts.append((rel,p.read_text(encoding='utf-8').lower()))
if GAME=='sword':
    banned=('shinobi','konoha','anbu','chakra','jutsu')
else:
    banned=('qin infantry','zhao army','household champion unit','sword and banners')
for rel,t in texts:
    for b in banned:
        if b in t:err(f'cross_game_routing_leak:{rel}:{b}')
if errs:
    print('ROUTING CONTRACT FAIL',len(errs));print('\n'.join('- '+x for x in errs));sys.exit(1)
print(f'ROUTING CONTRACT OK game={GAME} routes={len(all_routes)} rule_domains={len(rule_domains)} systems={len(sc.get("systems",{}))}')