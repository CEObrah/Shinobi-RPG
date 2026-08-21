"""Bounded read-only Jianghu runtime audit."""
from __future__ import annotations
from typing import Optional, Tuple
from shinobi_runtime.api.contracts import OocAuditResult
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.martial_world.civilian_state import civilian_population_total

class RepositoryOocAudit:
    def __init__(self, repository:RepositoryStore, runtime_root:object=None, **_kw): self.repository=repository
    def __call__(self, focus:Optional[str], observations:Tuple[str,...])->OocAuditResult:
        diagnostics=[]; suggestions=[]
        try:
            meta=self.repository.read_json('state/meta.json')
            diagnostics.append(f"campaign:game={meta.get('game')} revision={meta.get('revision')} time={meta.get('time')}")
            if meta.get('game')!='jianghu': suggestions.append('campaign_game_mismatch')
        except Exception:
            diagnostics.append('campaign:invalid'); suggestions.append('repair_campaign_meta')
        try:
            sch=self.repository.read_json('state/martial-world/scheduler.json')
            diagnostics.append(f"jianghu_scheduler:settled_through={sch.get('settled_through')} classes={len(sch.get('recurring',{}))}")
        except Exception:
            diagnostics.append('jianghu_scheduler:invalid'); suggestions.append('repair_jianghu_scheduler')
        try:
            idx=self.repository.read_json('state/martial-world/person-routes.json')
            diagnostics.append(f"jianghu_people:routed={idx.get('person_count',0)}")
        except Exception:
            diagnostics.append('jianghu_people:routing_invalid'); suggestions.append('rebuild_person_routes')
        try:
            civ=self.repository.read_json('state/martial-world/civilian-populations.json')
            diagnostics.append(f"civilian_population:aggregate={civilian_population_total(civ)}")
        except Exception:
            diagnostics.append('civilian_population:invalid'); suggestions.append('repair_civilian_population_authority')
        return OocAuditResult(tuple(diagnostics[:48]),tuple(suggestions[:48]),None)
