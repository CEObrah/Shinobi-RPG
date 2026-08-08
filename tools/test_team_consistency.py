#!/usr/bin/env python3
import json, pathlib, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
errors=[]

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

owners={}
for path in [ROOT/'state/player.json', *sorted((ROOT/'state/char').glob('*.json'))]:
    data=load(path)
    oid=data.get('owner_id')
    if oid: owners[oid]=(path,data)

registry_path=ROOT/'state/team/team-doctrine-registry.json'
registry=load(registry_path) if registry_path.exists() else {}
registered_active=set(registry.get('active_teams',[]))
frontier=load(ROOT/'state/time/frontier.json')
team_process=next((p for p in frontier.get('processes',[]) if p.get('id')=='process_team_doctrine_training'),{})
process_coverage=set(team_process.get('coverage',[]))
seen_active=set()
active_teams={}

def require_active_coverage(team_id):
    if team_id in seen_active: errors.append(f'duplicate_active_team_id:{team_id}')
    seen_active.add(team_id)
    if team_id not in registered_active: errors.append(f'active_team_missing_training_registry:{team_id}')
    if team_id not in process_coverage: errors.append(f'active_team_missing_training_process_coverage:{team_id}')

for path in sorted((ROOT/'state/team').glob('*.json')):
    data=load(path)
    schema=data.get('schema')
    if data.get('status')!='active' or schema not in {'team','special-mission-team'}:
        continue
    team_id=data.get('id')
    if not team_id:
        errors.append(f'active_team_missing_id:{path.relative_to(ROOT)}')
        continue
    active_teams[team_id]=(path,data)
    require_active_coverage(team_id)

    if schema=='special-mission-team':
        members=list(data.get('members',[]))
        if data.get('team_type')!='special_mission_cell': errors.append(f'special_team_wrong_type:{team_id}')
        if not 4 <= len(members) <= 8: errors.append(f'special_team_member_count:{team_id}:{len(members)}')
        if len(members)!=len(set(members)): errors.append(f'special_team_duplicate_member:{team_id}')
        for member_id in members:
            if member_id not in owners: errors.append(f'special_team_missing_member_owner:{team_id}:{member_id}')
        if data.get('commander') not in members: errors.append(f'special_team_commander_not_member:{team_id}')
        if data.get('deputy') not in members: errors.append(f'special_team_deputy_not_member:{team_id}')
        if data.get('commander')==data.get('deputy'): errors.append(f'special_team_command_collision:{team_id}')
        for role_key in sorted(set((data.get('roles') or {}))-set(members)):
            errors.append(f'special_team_role_for_nonmember:{team_id}:{role_key}')
        continue

    members=[x for x in [data.get('jonin_instructor'),*data.get('genin',[])] if x]
    if len(members)!=len(set(members)): errors.append(f'active_team_duplicate_member:{team_id}')
    for role_key in sorted(set((data.get('roles') or {}))-set(members)):
        errors.append(f'active_team_role_for_nonmember:{team_id}:{role_key}')
    for member_id in members:
        owner=owners.get(member_id)
        if owner is None:
            errors.append(f'active_team_missing_member_owner:{team_id}:{member_id}')
            continue
        owner_path,member=owner
        status=str(member.get('team_status','')).lower()
        if 'unassigned' in status or 'pending_assignment' in status:
            errors.append(f'stale_team_status_mirror:{team_id}:{owner_path.relative_to(ROOT)}:{member.get("team_status")}')
        current=member.get('current_unit_or_office')
        career=member.get('career_state')
        if member_id!=data.get('jonin_instructor'):
            if current!=team_id: errors.append(f'stale_team_unit_mirror:{team_id}:{owner_path.relative_to(ROOT)}:{current}')
            if isinstance(career,dict) and 'current_unit_or_office' in career and career.get('current_unit_or_office')!=team_id:
                errors.append(f'stale_team_career_mirror:{team_id}:{owner_path.relative_to(ROOT)}:{career.get("current_unit_or_office")}')
        else:
            command=str((member.get('career_state') or {}).get('command','')).lower()
            current_text=str(current or '').lower()
            team_name=str(data.get('name','')).lower()
            if 'no permanent team' in command: errors.append(f'stale_team_command_mirror:{team_id}:{owner_path.relative_to(ROOT)}:{command}')
            if team_name and team_name not in command and team_name not in current_text:
                errors.append(f'stale_instructor_team_mirror:{team_id}:{owner_path.relative_to(ROOT)}')

# Doctrine is a subordinate state owner for practiced doctrine/familiarity. It may
# remain separate for cold loading, but roster and command facts belong to the core
# team and every doctrine reference must resolve to that saved roster.
doctrine_dir=ROOT/'state/team/doctrine'
if doctrine_dir.exists():
    seen_doctrine_teams=set()
    for path in sorted(doctrine_dir.glob('*.json')):
        doctrine=load(path)
        if doctrine.get('schema')!='team-doctrine':
            continue
        team_id=doctrine.get('team_id')
        if team_id in seen_doctrine_teams: errors.append(f'duplicate_team_doctrine:{team_id}')
        seen_doctrine_teams.add(team_id)
        team_entry=active_teams.get(team_id)
        if team_entry is None:
            errors.append(f'doctrine_team_missing_or_inactive:{path.relative_to(ROOT)}:{team_id}')
            continue
        team_path,team=team_entry
        if team.get('schema')=='special-mission-team':
            members=set(team.get('members',[]))
            captain=team.get('commander')
            deputy=team.get('deputy')
        else:
            members=set([x for x in [team.get('jonin_instructor'),*team.get('genin',[])] if x])
            captain=team.get('jonin_instructor')
            deputy=None
        command=doctrine.get('command') or {}
        if command.get('captain')!=captain:
            errors.append(f'doctrine_captain_drift:{team_id}:{command.get("captain")}:{captain}')
        if deputy is not None and command.get('deputy')!=deputy:
            errors.append(f'doctrine_deputy_drift:{team_id}:{command.get("deputy")}:{deputy}')
        if set((doctrine.get('familiarity') or {}).keys())!=members:
            errors.append(f'doctrine_familiarity_roster_drift:{team_id}')
        if set((doctrine.get('roles') or {}).keys())!=members:
            errors.append(f'doctrine_role_roster_drift:{team_id}')
        training=doctrine.get('training') or {}
        if set((training.get('member_training_caps') or {}).keys())!=members:
            errors.append(f'doctrine_training_cap_roster_drift:{team_id}')
        if not set((training.get('role_focus') or {}).keys()).issubset(members):
            errors.append(f'doctrine_training_role_nonmember:{team_id}')
        referenced=set(command.get('succession_order',[]))
        referenced.update(training.get('lead_instructors',[]))
        referenced.update((training.get('role_focus') or {}).keys())
        referenced.update((doctrine.get('extraction') or {}).get('primary_members',[]))
        for phase in doctrine.get('phases',[]):
            referenced.update(phase.get('primary_members',[]))
        extra=referenced-members
        if extra: errors.append(f'doctrine_nonmember_reference:{team_id}:{sorted(extra)}')

for team_id in sorted(registered_active-seen_active): errors.append(f'training_registry_active_team_not_active:{team_id}')
for team_id in sorted(process_coverage-seen_active): errors.append(f'training_process_coverage_team_not_active:{team_id}')

if errors:
    print('TEAM CONSISTENCY FAILED')
    for error in errors: print('-',error)
    sys.exit(1)
print('TEAM CONSISTENCY OK')
