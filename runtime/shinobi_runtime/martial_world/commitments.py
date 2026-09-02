"""Universal conserved availability reservations for Jianghu activities."""
from __future__ import annotations
import copy
from typing import Any, Mapping, Sequence

from .mobile_party import active_mobile_parties

def _active_resources(state:Mapping[str,Any])->set[tuple[str,str]]:
    used=set()
    for row in state.get('commitments',{}).values():
        if not isinstance(row,Mapping) or row.get('status','active')!='active': continue
        for r in row.get('resources',[]):
            if isinstance(r,Mapping) and isinstance(r.get('kind'),str) and isinstance(r.get('ref'),str): used.add((r['kind'],r['ref']))
    return used

def reserve_resources(state:Mapping[str,Any],*,resources:Sequence[tuple[str,str,str]],actor_ref:str,owner_ref:str,activity_ref:str,activity_kind:str,started_at:str,location_ref:str|None=None)->dict[str,Any]:
    out=copy.deepcopy(dict(state)); rows=out.setdefault('commitments',{}); index=out.setdefault('person_index',{})
    if not isinstance(rows,dict): raise ValueError('commitments invalid')
    used=_active_resources(out); material=[]
    for kind,ref,res_owner in resources:
        key=(str(kind),str(ref))
        if key in used: raise ValueError(f'resource already committed:{key[0]}:{key[1]}')
        used.add(key); material.append({'kind':key[0],'ref':key[1],'owner_ref':str(res_owner)})
    cid=f'commitment:{activity_ref}'
    if cid in rows: raise ValueError('activity already committed')
    people=[r['ref'] for r in material if r['kind']=='person']
    row={'commitment_ref':cid,'activity_ref':str(activity_ref),'activity_kind':str(activity_kind),'kind':str(activity_kind),'actor_ref':str(actor_ref),'owner_ref':str(owner_ref),'resources':material,'person_refs':people,'started_at':str(started_at),'status':'active'}
    if location_ref: row['location_ref']=str(location_ref)
    rows[cid]=row
    if isinstance(index,dict):
        for p in people: index[p]=cid
    return out

def extend_commitment_resources(state:Mapping[str,Any],*,activity_ref:str,resources:Sequence[tuple[str,str,str]])->dict[str,Any]:
    """Add newly mobilized exact resources to one existing active activity."""
    out=copy.deepcopy(dict(state)); rows=out.setdefault('commitments',{}); index=out.setdefault('person_index',{})
    if not isinstance(rows,dict) or not isinstance(index,dict): raise ValueError('commitments invalid')
    cid=f'commitment:{activity_ref}'; row=rows.get(cid)
    if not isinstance(row,dict) or row.get('status','active')!='active': raise ValueError('active commitment not found')
    used=_active_resources(out)
    existing={(str(r.get('kind')),str(r.get('ref'))) for r in row.get('resources',[]) if isinstance(r,Mapping) and isinstance(r.get('kind'),str) and isinstance(r.get('ref'),str)}
    material=row.setdefault('resources',[]); people=row.setdefault('person_refs',[])
    if not isinstance(material,list) or not isinstance(people,list): raise ValueError('commitment resources invalid')
    for kind,ref,res_owner in resources:
        key=(str(kind),str(ref))
        if key in existing: continue
        if key in used: raise ValueError(f'resource already committed:{key[0]}:{key[1]}')
        material.append({'kind':key[0],'ref':key[1],'owner_ref':str(res_owner)}); existing.add(key); used.add(key)
        if key[0]=='person':
            if key[1] not in people: people.append(key[1])
            index[key[1]]=cid
    return out

def remove_people_from_commitments(state:Mapping[str,Any],*,person_refs:Sequence[str])->dict[str,Any]:
    """Remove unavailable/dead people from all current reservations without deleting other resources."""
    out=copy.deepcopy(dict(state)); rows=out.setdefault('commitments',{}); index=out.setdefault('person_index',{})
    if not isinstance(rows,dict) or not isinstance(index,dict): raise ValueError('commitments invalid')
    removed={str(x) for x in person_refs if isinstance(x,str) and x}
    if not removed:return out
    for cid,raw in list(rows.items()):
        if not isinstance(raw,Mapping):continue
        people=[str(x) for x in raw.get('person_refs',[]) if isinstance(x,str)] if isinstance(raw.get('person_refs'),list) else []
        if not any(ref in removed for ref in people):continue
        row=copy.deepcopy(dict(raw)); resources=row.get('resources',[]) if isinstance(row.get('resources'),list) else []
        row['resources']=[r for r in resources if not (isinstance(r,Mapping) and r.get('kind')=='person' and str(r.get('ref')) in removed)]
        row['person_refs']=[ref for ref in people if ref not in removed]
        for ref in people:
            if ref in removed and index.get(ref)==cid:index.pop(ref,None)
        has_nonperson=any(isinstance(r,Mapping) and r.get('kind')!='person' for r in row['resources'])
        if not row['person_refs'] and not has_nonperson:rows.pop(cid,None)
        else:rows[cid]=row
    return out

def release_resources(state:Mapping[str,Any],*,activity_ref:str)->dict[str,Any]:
    out=copy.deepcopy(dict(state)); rows=out.setdefault('commitments',{}); index=out.setdefault('person_index',{}); cid=f'commitment:{activity_ref}'
    row=rows.pop(cid,None)
    if isinstance(row,Mapping) and isinstance(index,dict):
        for p in row.get('person_refs',[]):
            if index.get(p)==cid: index.pop(p,None)
    return out


def derived_commitment_state(read_json: Any) -> dict[str, Any]:
    """Derive current finite-activity occupancy from real activity owners.

    This preserves the small reservation API for same-frontier conflict checks,
    but the returned mapping is never campaign state. Projects, deployments,
    route movements and active tournament pairs are the writable authorities.
    """
    state: dict[str, Any] = {"schema": "jianghu-derived-availability", "commitments": {}, "person_index": {}}
    commitments = state["commitments"]
    person_index = state["person_index"]
    used_resources: set[tuple[str, str]] = set()

    transient_path = "__runtime__/jianghu-finite-activity.json"

    def read(path: str) -> Mapping[str, Any]:
        try:
            row = read_json(path)
        except FileNotFoundError:
            return {}
        return row if isinstance(row, Mapping) else {}

    def add(activity_ref: str, activity_kind: str, refs: Sequence[str], *, owner_ref: str = "", actor_ref: str = "", started_at: str = "", location_ref: str | None = None) -> None:
        people = [str(ref) for ref in refs if isinstance(ref, str) and ref]
        if not people or not activity_ref:
            return
        commitment_ref = f"commitment:{activity_ref}"
        if commitment_ref in commitments:
            raise ValueError(f"activity owners double-book person: {activity_ref}")
        material: list[dict[str, str]] = []
        for person_ref in people:
            key = ("person", person_ref)
            if key in used_resources:
                raise ValueError(f"activity owners double-book person: {activity_ref}")
            used_resources.add(key)
            material.append({"kind": "person", "ref": person_ref, "owner_ref": str(owner_ref)})
        row: dict[str, Any] = {
            "commitment_ref": commitment_ref,
            "activity_ref": str(activity_ref),
            "activity_kind": str(activity_kind),
            "kind": str(activity_kind),
            "actor_ref": str(actor_ref or people[0]),
            "owner_ref": str(owner_ref),
            "resources": material,
            "person_refs": people,
            "started_at": str(started_at),
            "status": "active",
        }
        if location_ref:
            row["location_ref"] = str(location_ref)
        commitments[commitment_ref] = row
        for person_ref in people:
            person_index[person_ref] = commitment_ref


    transient = read(transient_path).get("activities", {})
    if isinstance(transient, Mapping):
        for ref, row in sorted(transient.items(), key=lambda item: str(item[0])):
            if not isinstance(row, Mapping):
                continue
            people = row.get("person_refs", [])
            if isinstance(people, (list, tuple)):
                add(
                    str(ref), str(row.get("activity_kind") or "finite_activity"),
                    [str(x) for x in people if isinstance(x, str)],
                    owner_ref=str(row.get("owner_ref") or ""),
                    actor_ref=str(row.get("actor_ref") or ""),
                    started_at=str(row.get("started_at") or ""),
                    location_ref=str(row.get("location_ref") or "") or None,
                )

    projects = read("state/martial-world/projects.json").get("projects", {})
    if isinstance(projects, Mapping):
        for ref, row in sorted(projects.items(), key=lambda item: str(item[0])):
            if not isinstance(row, Mapping) or bool(row.get("completed")):
                continue
            people = []
            for key in ("skilled_worker_refs", "management_worker_refs", "general_worker_refs", "worker_refs"):
                values = row.get(key, [])
                if isinstance(values, list):
                    people.extend(str(x) for x in values if isinstance(x, str))
            add(
                str(ref), str(row.get("project_type") or "project"), list(dict.fromkeys(people)),
                owner_ref=str(row.get("faction_ref") or ""), started_at=str(row.get("started_at") or ""),
                location_ref=str(row.get("site_ref") or "") or None,
            )

    for party in active_mobile_parties(read_json):
        add(
            str(party.get("party_ref") or ""),
            str(party.get("purpose_kind") or "mobile_party"),
            party.get("member_refs", []) if isinstance(party.get("member_refs"), list) else [],
            owner_ref=str(party.get("owner_ref") or ""),
            actor_ref=str(party.get("leader_ref") or ""),
            started_at=str(party.get("started_at") or ""),
            location_ref=str(party.get("origin_place_ref") or "") or None,
        )

    # Custody is a genuine current authority over a person's availability. When
    # the captive is physically moving with a raid/escort party, that movement
    # already owns occupancy and we do not duplicate it. Once the party ends,
    # the custody record itself remains the sole finite availability owner.
    custody_rows = read("state/martial-world/custody.json").get("records", [])
    if isinstance(custody_rows, list):
        for row in custody_rows:
            if not isinstance(row, Mapping) or row.get("status") in {"released", "escaped", "rescued", "executed"}:
                continue
            person_ref = str(row.get("person_ref") or "")
            if not person_ref or person_ref in state.get("person_index", {}):
                continue
            add(
                str(row.get("custody_id") or f"custody:{person_ref}"), "custody", [person_ref],
                owner_ref=str(row.get("holder_faction_ref") or row.get("captor_ref") or ""),
                actor_ref=str(row.get("captor_ref") or ""), started_at=str(row.get("started_at") or ""),
                location_ref=str(row.get("location_ref") or "") or None,
            )

    tournaments = read("state/martial-world/tournaments.json").get("tournaments", {})
    if isinstance(tournaments, Mapping):
        for ref, row in sorted(tournaments.items(), key=lambda item: str(item[0])):
            if not isinstance(row, Mapping):
                continue
            pair = row.get("active_pair")
            if isinstance(pair, list) and len(pair) == 2:
                add(f"tournament_match:{ref}", "tournament_match", pair, owner_ref=str(ref), started_at=str(row.get("active_match_started_at") or row.get("started_at") or ""))

    return state

__all__=['derived_commitment_state','extend_commitment_resources','release_resources','remove_people_from_commitments','reserve_resources']
