from __future__ import annotations
import copy, json
from typing import Any, Mapping
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.paths import WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.commands.world_front_evidence import apply_evidence, apply_event_evidence
from shinobi_runtime.commands.world_front_rules import PRESSURE_PATH, policy


def _json_after(plan: _BuiltPlan, path: str, repository: Any) -> dict:
    raw=plan.writes.get(path)
    if raw is not None:
        try: value=json.loads(raw.decode())
        except (AttributeError,UnicodeDecodeError,json.JSONDecodeError) as exc: raise CommandRejectedError("world_front_after_image_invalid") from exc
    else:
        try: value=repository.read_json(path)
        except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("world_front_after_image_invalid") from exc
    if not isinstance(value,dict): raise CommandRejectedError("world_front_after_image_invalid")
    return copy.deepcopy(value)


def _historical_source_reconciliation(owner: Any, plan: _BuiltPlan, events_record: Mapping[str, Any], registry: dict, rules: Mapping[str, Any], player_ref: str) -> tuple[bool, int]:
    marker = registry.get("historical_source_reconciliation")
    if isinstance(marker, Mapping) and marker.get("version") == 1:
        return False, 0
    refs = events_record.get("archive_refs")
    hot = events_record.get("events")
    if not isinstance(refs, list) or not isinstance(hot, list):
        raise CommandRejectedError("world_event_registry_invalid")
    archived_count = 0
    evidence_updates = 0
    for path in refs:
        if not isinstance(path, str):
            raise CommandRejectedError("world_event_registry_invalid")
        archive = _json_after(plan, path, owner.repository)
        archived = archive.get("events")
        if not isinstance(archived, list):
            raise CommandRejectedError("world_event_archive_invalid")
        archived_count += len(archived)
        for event in archived:
            if not isinstance(event, Mapping):
                raise CommandRejectedError("world_event_archive_invalid")
            evidence_updates += len(apply_event_evidence(registry=registry, rules=rules, event=event, player_ref=player_ref))
    for event in hot:
        if not isinstance(event, Mapping):
            raise CommandRejectedError("world_event_registry_invalid")
        evidence_updates += len(apply_event_evidence(registry=registry, rules=rules, event=event, player_ref=player_ref))
    registry["historical_source_reconciliation"] = {
        "version": 1,
        "archive_ref_count": len(refs),
        "archived_event_count": archived_count,
        "hot_event_count": len(hot),
    }
    return True, evidence_updates


class BaseOverlay:
    def __init__(self,overlay:Any,base:Mapping[str,bytes])->None: self._overlay,self._base,self.changed_paths=overlay,dict(base),tuple(sorted(base))
    def read_json(self,path:str)->Any:
        raw=self._base.get(path); return json.loads(raw.decode()) if raw is not None else self._overlay.read_json(path)
    def __getattr__(self,name:str)->Any:return getattr(self._overlay,name)


def progress_plan(owner:Any,plan:_BuiltPlan,command:Any)->_BuiltPlan:
    actions=plan.result.get("autonomous_actions")
    if not isinstance(actions,list):actions=[]
    rules=policy(owner.repository); events_record=_json_after(plan,WORLD_EVENT_REGISTRY_PATH,owner.repository)
    events={row.get("id"):row for row in events_record.get("events",[]) if isinstance(row,Mapping) and isinstance(row.get("id"),str)}
    registry=_json_after(plan,PRESSURE_PATH,owner.repository); changed=False; visible=[]
    reconciled,reconciled_updates=_historical_source_reconciliation(owner,plan,events_record,registry,rules,command.actor_id)
    if reconciled:
        changed=True
    for action in actions:
        if not isinstance(action,Mapping):continue
        event=events.get(action.get("event_id"))
        if not isinstance(event,Mapping):continue
        update=apply_evidence(registry=registry,rules=rules,action=action,event=event,player_ref=command.actor_id)
        if update is None:continue
        changed=True
        if update.get("player_visible") is True:visible.append(update)

    # World fronts also listen to committed domain events directly. This is what
    # connects Academy graduation, real finance settlement, governance, research,
    # commerce, and institutional operations to canon pressure without forcing canon.
    try:
        before_record=owner.repository.read_json(WORLD_EVENT_REGISTRY_PATH)
    except (FileNotFoundError,ValueError) as exc:
        raise CommandRejectedError("world_front_after_image_invalid") from exc
    before_ids={row.get("id") for row in before_record.get("events",[]) if isinstance(row,Mapping) and isinstance(row.get("id"),str)} if isinstance(before_record,Mapping) else set()
    for event_id,event in sorted(events.items()):
        if event_id in before_ids:continue
        updates=apply_event_evidence(registry=registry,rules=rules,event=event,player_ref=command.actor_id)
        if not updates:continue
        changed=True
        visible.extend(update for update in updates if update.get("player_visible") is True)
    if not changed:return plan
    base=dict(plan.writes);writes=dict(base);writes[PRESSURE_PATH]=_json_bytes(registry);expected=tuple(sorted(writes));validator=plan.validator
    def validate(overlay:Any,manifest:Any)->None:
        validator(BaseOverlay(overlay,base),manifest)
        if overlay.changed_paths!=expected or overlay.read_json(PRESSURE_PATH)!=registry:raise ValueError("world front progression after-image mismatch")
    result=dict(plan.result)
    if reconciled:
        result["world_front_source_reconciliation"]={"version":1,"evidence_updates":reconciled_updates}
    if visible:result["world_front_updates"]=visible
    return _BuiltPlan(plan.code,expected,writes,result,validate)
