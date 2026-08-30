import json
import shutil
from pathlib import Path

import pytest

from fixture_support import remove_people_from_active_combat_fixture, remove_people_from_route_fixture
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store.repository import RepositoryStore

ROOT=Path(__file__).resolve().parents[2]


def _clone(tmp_path, name):
    dst=tmp_path/name
    shutil.copytree(ROOT,dst,ignore=shutil.ignore_patterns('.pytest_cache','__pycache__','*.pyc'))
    return dst


def _write(root, rel, obj):
    (root/rel).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def _prepare_pair(root, other_ref):
    meta=json.loads((root/'state/meta.json').read_text()); player=meta['player_id']
    remove_people_from_route_fixture(root,player,other_ref)
    remove_people_from_active_combat_fixture(root,player,other_ref)
    roster_path=root/'state/martial-world/people/house_tang.json'
    roster=json.loads(roster_path.read_text())
    player_row=next(row for row in roster['people'] if row.get('person_id')==player)
    other_row=next(row for row in roster['people'] if row.get('person_id')==other_ref)
    site=str(player_row.get('location_ref') or 'site.changan.inn')
    player_row['location_ref']=site; other_row['location_ref']=site
    _write(root,'state/martial-world/people/house_tang.json',roster)
    scene=json.loads((root/'state/scene.json').read_text()); scene['location_id']=site; scene['present_person_ids']=[player,other_ref]; scene['visible_person_ids']=[player,other_ref]
    _write(root,'state/scene.json',scene)
    social=json.loads((root/'state/martial-world/social.json').read_text())
    social.setdefault('relationships',{})[f'{player}|{other_ref}']={'trust':60,'affection':60,'respect':20,'familiarity':60}
    social['relationships'][f'{other_ref}|{player}']={'trust':60,'affection':60,'respect':20,'familiarity':60}
    _write(root,'state/martial-world/social.json',social)
    return meta,player


def _command(meta, player, action, other_ref, request_id):
    return CommandEnvelope(
        campaign_id=meta['campaign_id'],request_id=request_id,actor_id=player,
        command_type='jianghu_family_resolution',expected_revision=meta['revision'],
        submitted_at='2026-08-29T00:00:00Z',payload={'action':action,'other_ref':other_ref},
    )


def test_manual_courtship_cannot_target_already_married_person(tmp_path):
    root=_clone(tmp_path,'married')
    meta,player=_prepare_pair(root,'char.ling')
    command=_command(meta,player,'courtship','char.ling','request.family.married')
    with pytest.raises(CommandRejectedError) as exc:
        RepositoryCommandPlanner(RepositoryStore(root)).preview(command)
    assert exc.value.code=='jianghu_family_party_already_married'


def test_manual_courtship_cannot_overlap_another_active_courtship(tmp_path):
    other='mw.person.house_tang.1004'; existing_partner='mw.person.house_tang.1008'
    root=_clone(tmp_path,'overlap')
    meta,player=_prepare_pair(root,other)
    social=json.loads((root/'state/martial-world/social.json').read_text())
    social.setdefault('courtships',{})['existing|pair']={'person_refs':[other,existing_partner],'status':'active','started_at':'0061-08-01T00:00:00'}
    _write(root,'state/martial-world/social.json',social)
    command=_command(meta,player,'courtship',other,'request.family.overlap')
    with pytest.raises(CommandRejectedError) as exc:
        RepositoryCommandPlanner(RepositoryStore(root)).preview(command)
    assert exc.value.code=='jianghu_family_party_already_courting'


def test_manual_marriage_requires_courtship_to_precede_current_timestamp(tmp_path):
    other='mw.person.house_tang.1004'
    root=_clone(tmp_path,'instant')
    meta,player=_prepare_pair(root,other)
    social=json.loads((root/'state/martial-world/social.json').read_text())
    pair='|'.join(sorted((player,other)))
    social.setdefault('courtships',{})[pair]={'person_refs':sorted((player,other)),'status':'active','started_at':meta['time'].removeprefix('SE-')}
    _write(root,'state/martial-world/social.json',social)
    command=_command(meta,player,'marriage',other,'request.family.instant')
    with pytest.raises(CommandRejectedError) as exc:
        RepositoryCommandPlanner(RepositoryStore(root)).preview(command)
    assert exc.value.code=='jianghu_marriage_requires_elapsed_courtship'
