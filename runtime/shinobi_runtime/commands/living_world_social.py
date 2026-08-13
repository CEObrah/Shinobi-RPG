from __future__ import annotations
from shinobi_runtime.commands.living_world_support import *

class LivingWorldSocialMixin:
    def _apply_autonomous_reputation_signal(self, *, subject_ref: str, audience_id: str, source_event_ref: str, source_event_kind: str, signal_ref: str, classification: str, at: CampaignTime, record_writes: Dict[str, Dict[str, Any]]) -> None:
        try:
            signal_registry=self.repository.read_json(REPUTATION_SIGNALS_PATH); mechanics=self.repository.read_json(REPUTATION_MECHANICS_PATH)
        except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("reputation_registry_invalid") from exc
        signals=signal_registry.get("signals") if isinstance(signal_registry,Mapping) else None; signal=signals.get(signal_ref) if isinstance(signals,Mapping) else None
        if not isinstance(signal,Mapping): raise CommandRejectedError("reputation_signal_unknown")
        allowed=signal.get("allowed_event_kinds")
        if not isinstance(allowed,list) or source_event_kind not in allowed: raise CommandRejectedError("reputation_signal_source_mismatch")
        evidence_cfg=mechanics.get("evidence_update") if isinstance(mechanics,Mapping) else None; prior_mass_cap=evidence_cfg.get("prior_mass_cap") if isinstance(evidence_cfg,Mapping) else None
        if isinstance(prior_mass_cap,bool) or not isinstance(prior_mass_cap,int) or prior_mass_cap<=0: raise CommandRejectedError("reputation_mechanics_invalid")
        audience_relevance=signal.get("audience_relevance",100); memory_class=signal.get("memory_class","normal")
        index=record_writes.get(REPUTATION_INDEX_PATH)
        if index is None: index=copy.deepcopy(self.repository.read_json(REPUTATION_INDEX_PATH)); record_writes[REPUTATION_INDEX_PATH]=index
        subjects=index.get("subjects") if isinstance(index,dict) else None
        if not isinstance(subjects,dict): raise CommandRejectedError("reputation_index_invalid")
        subject_path=subjects.get(subject_ref)
        if isinstance(subject_path,str):
            subject=record_writes.get(subject_path)
            if subject is None: subject=copy.deepcopy(self.repository.read_json(subject_path)); record_writes[subject_path]=subject
        else:
            try: _p,_d,subject_record=self._resolve_covered_owner_view(subject_ref,cache=_OwnerResolutionCache())
            except CommandRejectedError: subject_record=None
            subject_path=self._reputation_subject_path(subject_ref)
            subject={"schema":"reputation-subject","subject_id":subject_ref,"subject_type":self._reputation_subject_type(subject_ref,subject_record),"as_of":str(at),"authority":True,"audience_profiles":{},"institutional_status_sources":[],"notes":[]}
            subjects[subject_ref]=subject_path; index["subject_count"]=len(subjects); record_writes[subject_path]=subject
        profiles=subject.get("audience_profiles") if isinstance(subject,dict) else None
        if not isinstance(profiles,dict): raise CommandRejectedError("reputation_subject_invalid")
        profile_path=profiles.get(audience_id); new_profile=not isinstance(profile_path,str)
        if new_profile:
            profile_path=self._reputation_profile_path(subject_ref,audience_id); profile={"schema":"reputation-audience-profile","subject_id":subject_ref,"audience_id":audience_id,"as_of":str(at),"authority":True,"standing":{},"dimensions":{},"evidence_count":0,"last_event_refs":[],"memory_class":memory_class}; profiles[audience_id]=profile_path
        else:
            profile=record_writes.get(profile_path)
            if profile is None: profile=copy.deepcopy(self.repository.read_json(profile_path))
        rep_digest=hashlib.sha256(f"{source_event_ref}\x00{subject_ref}\x00{audience_id}\x00{signal_ref}".encode()).hexdigest()[:24]; rep_event_id=f"reputation.event.{rep_digest}"; rep_event_path=f"state/reputation/events/{rep_event_id}.json"
        if rep_event_path in record_writes or self.repository.read_optional_bytes(rep_event_path) is not None: return
        standing=profile.get("standing") if isinstance(profile,dict) else None; dimensions=profile.get("dimensions") if isinstance(profile,dict) else None
        if not isinstance(standing,dict) or not isinstance(dimensions,dict): raise CommandRejectedError("reputation_profile_invalid")
        for category,target in (("standing",standing),("dimensions",dimensions)):
            definitions=signal.get(category)
            if not isinstance(definitions,Mapping): raise CommandRejectedError("reputation_signal_invalid")
            for axis,spec in sorted(definitions.items()):
                if not isinstance(spec,Mapping): raise CommandRejectedError("reputation_signal_invalid")
                score,base_weight=spec.get("score"),spec.get("base_weight")
                if any(isinstance(v,bool) or not isinstance(v,int) for v in (score,base_weight)): raise CommandRejectedError("reputation_signal_invalid")
                evidence=ReputationEvidence(signal_score=score,base_weight=base_weight,source_reliability=95,clarity=95,channel_integrity=100,audience_relevance=int(audience_relevance),corroboration=100)
                updated=update_axis(target.get(axis) if isinstance(target.get(axis),Mapping) else None,evidence,prior_mass_cap=prior_mass_cap)
                if updated: target[axis]=updated
        profile["as_of"]=str(at); profile["memory_class"]=memory_class; profile["evidence_count"]=int(profile.get("evidence_count",0))+1
        last_refs=profile.setdefault("last_event_refs",[])
        if rep_event_id not in last_refs: last_refs.append(rep_event_id); del last_refs[:-12]
        subject["as_of"]=str(at); record_writes[subject_path]=subject; record_writes[profile_path]=profile
        record_writes[rep_event_path]={"schema":"reputation-event","event_id":rep_event_id,"subject_id":subject_ref,"event_type":signal_ref,"occurred_at":str(at),"source_event_ref":source_event_ref,"authority":True,"signals":copy.deepcopy(dict(signal.get("dimensions",{}))),"standing_signals":copy.deepcopy(dict(signal.get("standing",{}))),"visibility":{"audience_id":audience_id,"source_classification":classification},"witnesses":[],"report_routes":[source_event_ref],"deliveries":{audience_id:{"profile_ref":profile_path,"source_event_ref":source_event_ref}},"status":"applied"}
        if new_profile: index["audience_profile_count"]=int(index.get("audience_profile_count",0))+1
        index["event_count"]=int(index.get("event_count",0))+1

    def _relationship_shard_path(self, source_ref: str) -> str:
        return f"{_RELATIONSHIP_ROOT}/{_slug(source_ref)}.json"

    def _apply_relationship_edge(self, *, source_ref: str, target_ref: str, interaction_kind: str, event_ref: str, summary: str, at: CampaignTime, record_writes: Dict[str, Dict[str, Any]]) -> None:
        if source_ref==target_ref: return
        try: rules=self.repository.read_json(RELATIONSHIP_RULES_PATH)
        except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("relationship_rules_invalid") from exc
        baseline=rules.get("baseline") if isinstance(rules,Mapping) else None; interactions=rules.get("interactions") if isinstance(rules,Mapping) else None; effect=interactions.get(interaction_kind) if isinstance(interactions,Mapping) else None
        if not isinstance(baseline,Mapping) or not isinstance(effect,Mapping): raise CommandRejectedError("relationship_interaction_unknown")
        shard_path=self._relationship_shard_path(source_ref); shard=record_writes.get(shard_path); new_shard=shard is None and self.repository.read_optional_bytes(shard_path) is None
        if shard is None:
            if new_shard: shard={"schema":"relationship-edge-shard","source_id":source_ref,"relationship_edges":{}}
            else:
                try: shard=copy.deepcopy(self.repository.read_json(shard_path))
                except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("relationship_registry_invalid") from exc
        edges=shard.get("relationship_edges") if isinstance(shard,dict) else None
        if not isinstance(edges,dict) or shard.get("source_id")!=source_ref: raise CommandRejectedError("relationship_registry_invalid")
        relationship_type="professional_teammates"; edge_id=f"rel.{_slug(source_ref)}.{_slug(target_ref)}.{relationship_type}"; existing=edges.get(edge_id)
        if existing is not None and not isinstance(existing,Mapping): raise CommandRejectedError("relationship_registry_invalid")
        values=dict(existing) if isinstance(existing,Mapping) else {"id":edge_id,"source_id":source_ref,"target_id":target_ref,"relationship_type":relationship_type,"trust":int(baseline.get("trust",50)),"respect":int(baseline.get("respect",50)),"affection":int(baseline.get("affection",50)),"history":"","current_tension":str(baseline.get("current_tension","none_saved")),"duty":int(baseline.get("duty",0))}
        if existing is None:
            reputation=self._reputation_profile_for(target_ref,source_ref); standing=reputation.get("standing") if isinstance(reputation,Mapping) else {}; dimensions=reputation.get("dimensions") if isinstance(reputation,Mapping) else {}
            def rep_score(container: Mapping[str,Any], key: str) -> Optional[int]:
                axis=container.get(key) if isinstance(container,Mapping) else None; score=axis.get("score") if isinstance(axis,Mapping) else None; return score if isinstance(score,int) and not isinstance(score,bool) else None
            prestige=rep_score(standing,"prestige"); renown=rep_score(standing,"renown"); infamy=rep_score(standing,"infamy"); reliability=rep_score(dimensions,"mission_reliability"); loyalty=rep_score(dimensions,"institutional_loyalty")
            values["respect"]=max(0,min(100,int(values["respect"])+sum((v-50) for v in (prestige,renown) if v is not None)//10)); trust_shift=sum((v-50) for v in (reliability,loyalty) if v is not None)//12
            if infamy is not None: trust_shift-=max(0,infamy-50)//10
            values["trust"]=max(0,min(100,int(values["trust"])+trust_shift))
        for axis in ("trust","respect","affection","duty"):
            delta=effect.get(axis,0); prior=values.get(axis,baseline.get(axis,0))
            if isinstance(delta,bool) or not isinstance(delta,int) or isinstance(prior,bool) or not isinstance(prior,int): raise CommandRejectedError("relationship_rules_invalid")
            values[axis]=max(0,min(100,prior+delta))
        tension=effect.get("tension")
        if isinstance(tension,str) and tension: values["current_tension"]=tension
        prior_history=values.get("history") if isinstance(values.get("history"),str) else ""; addition=f"{at}: {summary} [{event_ref}]"; values["history"]=addition if not prior_history else (prior_history+" | "+addition)[-4000:]
        edges[edge_id]=values; record_writes[shard_path]=shard
        index=record_writes.get(RELATIONSHIP_INDEX_PATH)
        if index is None:
            try: index=copy.deepcopy(self.repository.read_json(RELATIONSHIP_INDEX_PATH))
            except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("relationship_index_invalid") from exc
            record_writes[RELATIONSHIP_INDEX_PATH]=index
        edge_index=index.get("edge_index") if isinstance(index,dict) else None; person_shards=index.get("person_shards") if isinstance(index,dict) else None
        if not isinstance(edge_index,dict) or not isinstance(person_shards,dict): raise CommandRejectedError("relationship_index_invalid")
        was_new_edge=edge_id not in edge_index; edge_index[edge_id]=shard_path
        for person_ref in (source_ref,target_ref):
            refs=person_shards.setdefault(person_ref,[])
            if not isinstance(refs,list): raise CommandRejectedError("relationship_index_invalid")
            if shard_path not in refs: refs.append(shard_path); refs.sort()
        if was_new_edge: index["edge_count"]=int(index.get("edge_count",0))+1
        if new_shard: index["source_shard_count"]=int(index.get("source_shard_count",0))+1

    def _apply_team_relationship_event(self, participant_refs: Sequence[str], *, event_ref: str, interaction_kind: str, summary: str, at: CampaignTime, record_writes: Dict[str, Dict[str, Any]], player_id: str) -> None:
        refs=list(dict.fromkeys(ref for ref in participant_refs if isinstance(ref,str)))[:16]; applied=0
        for source in refs:
            if source==player_id: continue
            for target in refs:
                if source==target: continue
                self._apply_relationship_edge(source_ref=source,target_ref=target,interaction_kind=interaction_kind,event_ref=event_ref,summary=summary,at=at,record_writes=record_writes); applied+=1
                if applied>=_MAX_RELATIONSHIP_UPDATES: return
