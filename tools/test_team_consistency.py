#!/usr/bin/env python3
"""Validate exact named teams without coupling team identity to scheduler plumbing."""
import json, pathlib, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
errors=[]

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

owners={}
for path in [ROOT/'state/player.json', *sorted((ROOT/'state/char').glob('*.json'))]:
    data=load(path); oid=data.get('owner_id')
    if oid: owners[oid]=(path,data)

models=load(ROOT/'game/rules/training/models.json').get('models') or {}
if set(models) != {'training.self_directed','training.team','training.cohort'}:
    errors.append(f'training_model_set_invalid:{sorted(models)}')

active_teams={}
for path in sorted((ROOT/'state/team').glob('*.json')):
    data=load(path)
    if data.get('schema')!='exact-team' or data.get('status')!='active':
        continue
    team_id=data.get('id')
    if not isinstance(team_id,str) or not team_id:
        errors.append(f'active_team_missing_id:{path.relative_to(ROOT)}'); continue
    if team_id in active_teams: errors.append(f'duplicate_active_team_id:{team_id}')
    active_teams[team_id]=(path,data)
    members=list(data.get('member_refs') or [])
    member_set=set(members)
    if len(members)!=len(member_set): errors.append(f'active_team_duplicate_member:{team_id}')
    leader=data.get('leader_ref'); deputy=data.get('deputy_ref')
    if leader not in member_set: errors.append(f'active_team_leader_not_member:{team_id}:{leader}')
    if deputy is not None and deputy not in member_set: errors.append(f'active_team_deputy_not_member:{team_id}:{deputy}')
    if deputy is not None and deputy==leader: errors.append(f'active_team_command_collision:{team_id}')
    for role_key in sorted(set((data.get('roles') or {}))-member_set): errors.append(f'active_team_role_for_nonmember:{team_id}:{role_key}')
    for member_id in members:
        if member_id not in owners: errors.append(f'active_team_missing_member_owner:{team_id}:{member_id}')
    training=data.get('training')
    if not isinstance(training,dict):
        errors.append(f'active_team_training_missing:{team_id}')
    else:
        model_ref=training.get('model_ref')
        if model_ref!='training.team':
            errors.append(f'active_team_training_model_not_generic:{team_id}:{model_ref}')
        instructors=training.get('instructor_refs')
        if not isinstance(instructors,list) or not instructors:
            errors.append(f'active_team_training_instructors_invalid:{team_id}')
        else:
            for instructor in instructors:
                if instructor not in member_set:
                    errors.append(f'active_team_training_instructor_nonmember:{team_id}:{instructor}')
        facilities=training.get('facility_refs')
        if not isinstance(facilities,list) or any(not isinstance(x,str) for x in facilities):
            errors.append(f'active_team_training_facilities_invalid:{team_id}')
    # Current team mirrors remain useful for exact members, but the exact team owner is roster authority.
    for member_id in member_set:
        entry=owners.get(member_id)
        if entry is None: continue
        owner_path,member=entry
        career=member.get('career_state')
        career_owner=career.get('current_unit_or_office') if isinstance(career,dict) else None
        if isinstance(career_owner,str) and career_owner.startswith('team.') and career_owner==team_id and member_id not in member_set:
            errors.append(f'stale_team_career_mirror:{team_id}:{owner_path.relative_to(ROOT)}')

# Doctrine is subordinate practiced state. It must resolve to the exact team roster.
doctrine_dir=ROOT/'state/team/doctrine'
if doctrine_dir.exists():
    seen=set()
    for path in sorted(doctrine_dir.glob('*.json')):
        doctrine=load(path)
        if doctrine.get('schema')!='team-doctrine': continue
        team_id=doctrine.get('team_id')
        if team_id in seen: errors.append(f'duplicate_team_doctrine:{team_id}')
        seen.add(team_id)
        entry=active_teams.get(team_id)
        if entry is None:
            errors.append(f'doctrine_team_missing_or_inactive:{path.relative_to(ROOT)}:{team_id}'); continue
        _,team=entry
        members=set(team.get('member_refs') or [])
        leader=team.get('leader_ref'); deputy=team.get('deputy_ref')
        command=doctrine.get('command') or {}
        if command.get('captain')!=leader: errors.append(f'doctrine_captain_drift:{team_id}:{command.get("captain")}:{leader}')
        if deputy is not None and command.get('deputy')!=deputy: errors.append(f'doctrine_deputy_drift:{team_id}:{command.get("deputy")}:{deputy}')
        if set((doctrine.get('familiarity') or {}).keys())!=members: errors.append(f'doctrine_familiarity_roster_drift:{team_id}')
        if set((doctrine.get('roles') or {}).keys())!=members: errors.append(f'doctrine_role_roster_drift:{team_id}')
        training=doctrine.get('training') or {}
        if set((training.get('role_focus') or {}).keys())!=members: errors.append(f'doctrine_training_role_roster_drift:{team_id}')
        referenced=set(command.get('succession_order',[])); referenced.update(training.get('lead_instructors',[])); referenced.update((training.get('role_focus') or {}).keys()); referenced.update((doctrine.get('extraction') or {}).get('primary_members',[]))
        for phase in doctrine.get('phases',[]): referenced.update(phase.get('primary_members',[]))
        extra=referenced-members
        if extra: errors.append(f'doctrine_nonmember_reference:{team_id}:{sorted(extra)}')

if errors:
    print('TEAM CONSISTENCY FAILED')
    for error in errors: print('-',error)
    sys.exit(1)
print(f'TEAM CONSISTENCY OK active_exact_teams={len(active_teams)} training_models={len(models)}')
