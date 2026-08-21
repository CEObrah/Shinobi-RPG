"""Closed Jianghu command planner.

There is one writable Jianghu command authority.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Tuple

from shinobi_runtime.api.contracts import CommandPlan, CommandPreview, CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu import JianghuCommandsMixin
from shinobi_runtime.commands.jianghu_extended import JianghuExtendedCommandsMixin
from shinobi_runtime.commands.jianghu_time import JianghuTimeCommandsMixin
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_SUBMITTED_AT=re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")

class _ExpandedCommand:
    def __init__(self, base: CommandEnvelope, payload: Mapping[str,Any]):
        self.campaign_id=base.campaign_id; self.request_id=base.request_id; self.actor_id=base.actor_id; self.command_type=base.command_type; self.expected_revision=base.expected_revision; self.submitted_at=base.submitted_at; self.payload=payload; self.mode=base.mode; self.digest=base.digest

class RepositoryCommandPlanner(JianghuExtendedCommandsMixin,JianghuCommandsMixin,JianghuTimeCommandsMixin):
    COMMAND_TYPES=frozenset(COMMAND_SPECS)
    def __init__(self, repository: RepositoryStore, *, meta_path="state/meta.json", scene_path="state/scene.json", **_ignored):
        self.repository=repository; self.meta_path=meta_path; self.scene_path=scene_path
    def _base(self,command:CommandEnvelope)->Tuple[dict[str,Any],CampaignTime]:
        if command.mode not in {"gameplay","autonomous"}: raise CommandRejectedError("gameplay_mode_required")
        if command.command_type not in self.COMMAND_TYPES: raise CommandRejectedError("unsupported_command_type")
        if not _SUBMITTED_AT.fullmatch(command.submitted_at): raise CommandRejectedError("submitted_at_must_be_utc_rfc3339")
        try: parsed=datetime.fromisoformat(command.submitted_at.removesuffix("Z")+"+00:00")
        except ValueError as exc: raise CommandRejectedError("submitted_at_must_be_utc_rfc3339") from exc
        if parsed.tzinfo!=timezone.utc: raise CommandRejectedError("submitted_at_must_be_utc_rfc3339")
        if self.repository.campaign_id(self.meta_path)!=command.campaign_id: raise CommandRejectedError("campaign_mismatch")
        self.repository.require_revision(command.expected_revision,self.meta_path)
        meta=self.repository.read_json(self.meta_path)
        if not isinstance(meta,dict) or meta.get("schema")!="meta" or meta.get("game")!="jianghu": raise CommandRejectedError("campaign_meta_invalid")
        if command.mode=="gameplay" and meta.get("player_id")!=command.actor_id: raise CommandRejectedError("actor_not_campaign_player")
        try: now=CampaignTime.parse(meta.get("time"))
        except (TypeError,ValueError) as exc: raise CommandRejectedError("campaign_time_invalid") from exc
        return meta,now
    @staticmethod
    def _meta_after(meta:Mapping[str,Any],command:CommandEnvelope,*,world_time:CampaignTime)->dict[str,Any]:
        out=copy.deepcopy(dict(meta)); out["time"]=str(world_time); out["revision"]=command.expected_revision+1; return out
    def _prune_noop_writes(self,writes:Mapping[str,bytes])->dict[str,bytes]:
        return {p:b for p,b in writes.items() if self.repository.read_optional_bytes(p)!=b}
    @staticmethod
    def _assert_meta(overlay:StagedOverlay,manifest:TransactionManifest,*,meta_path:str,command:CommandEnvelope,world_time:CampaignTime)->None:
        meta=overlay.read_json(meta_path)
        if not isinstance(meta,dict) or meta.get("schema")!="meta" or meta.get("campaign_id")!=command.campaign_id or meta.get("revision")!=command.expected_revision+1 or meta.get("time")!=str(world_time) or manifest.base_revision!=command.expected_revision or manifest.target_revision!=command.expected_revision+1:
            raise ValueError("planned meta does not preserve campaign transaction law")
    def _build(self,command:CommandEnvelope)->_BuiltPlan:
        meta,now=self._base(command); spec=COMMAND_SPECS[command.command_type]
        # A live exact-combat owner is a hard causal boundary.  No travel,
        # training, economy or time-skip command may silently advance around it.
        # The combat resolver itself clears scene.active_combat_ref when the
        # fight resolves, after which dependent route/tournament commands can
        # consume the result normally.
        scene=self.repository.read_json(self.scene_path)
        active_combat=scene.get("active_combat_ref") if isinstance(scene,Mapping) else None
        if isinstance(active_combat,str) and active_combat and command.command_type!="jianghu_combat_resolution":
            raise CommandRejectedError("jianghu_active_combat_requires_resolution")
        if spec.variants:
            expanded=spec.expand_variant_payload(command.payload)
            if expanded is None: raise CommandRejectedError(command.command_type+"_payload_fields_invalid")
            # Reducers expect only variant fields, not null union fields.
            action=expanded.get("action"); variant=spec.variants.get(action)
            payload={k:command.payload[k] for k in (*variant.required_fields,*variant.optional_fields) if k in command.payload}
            command=_ExpandedCommand(command,payload)
        else:
            required=set(spec.required_fields); actual=set(command.payload)
            if actual!=required: raise CommandRejectedError(command.command_type+"_payload_fields_invalid")
        fn=getattr(self,"_"+command.command_type,None)
        if not callable(fn): raise RuntimeError("missing command reducer for "+command.command_type)
        return fn(command,meta,now)
    def preview(self,command:CommandEnvelope)->CommandPreview:
        built=self._build(command); return CommandPreview(status="ready",code=built.code,target_revision=command.expected_revision+1,affected_refs=built.affected_refs)
    def plan(self,command:CommandEnvelope)->CommandPlan:
        built=self._build(command); return CommandPlan(transaction_id=("tx.autonomous." if command.mode=="autonomous" else "tx.gameplay.")+command.digest,created_at=command.submitted_at,writes=built.writes,result=built.result,validator=built.validator)
