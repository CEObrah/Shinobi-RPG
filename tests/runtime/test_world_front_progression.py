from shinobi_runtime.commands.world_front_evidence import apply_evidence, apply_event_evidence
from shinobi_runtime.commands.world_front_rules import front_phase


def policy():
    return {"phase_thresholds":{"developing_evidence":1,"operational_evidence":3,"crisis_evidence":6},"material_action_kinds":["mission_generate","information_report"],"fronts":{"pressure_test":{"faction_roles":{"faction.source":"source","faction.defender":"opposition"}}}}


def pressure(identity="pressure_test"):
    return {"id":identity,"status":"latent_active","actors":[],"resources":[],"opposition":[],"current_step":None,"source_refs":[],"evidence_refs":[],"visibility":{"classification":"hidden","basis_refs":[]},"knowledge":{"player_refs":[],"npc_refs":[]},"chronology":[]}


def event(audience=()):
    return {"id":"event.mission.001","kind":"autonomous_mission_created","host_refs":["faction.source"],"actor_refs":["actor.one"],"timing":{"occurred_at":"SE-0061-06-11T07:00:00"},"visibility":{"classification":"hidden","audience_refs":list(audience),"witness_refs":[]}}


def test_front_phase_comes_from_committed_evidence_count():
    row=pressure();assert front_phase(row,policy())=="latent"
    row["evidence_refs"].append("event.one");assert front_phase(row,policy())=="developing"
    row["evidence_refs"].extend(["event.two","event.three"]);assert front_phase(row,policy())=="operational"


def test_front_evidence_records_proven_source_without_inventing_player_knowledge():
    registry={"pressures":{"pressure_test":pressure()}}
    update=apply_evidence(registry=registry,rules=policy(),action={"kind":"mission_generate","event_id":"event.mission.001"},event=event(),player_ref="pc.player")
    assert update["phase_after"]=="developing" and update["player_visible"] is False
    row=registry["pressures"]["pressure_test"]
    assert row["evidence_refs"]==["event.mission.001"] and row["source_refs"]==["faction.source"]
    assert row["knowledge"]["player_refs"]==[] and row["actors"]==["actor.one"]


def test_visibility_is_projection_not_knowledge_write():
    registry={"pressures":{"pressure_test":pressure()}}
    update=apply_evidence(registry=registry,rules=policy(),action={"kind":"mission_generate","event_id":"event.mission.001"},event=event(("pc.player",)),player_ref="pc.player")
    assert update["player_visible"] is True
    assert registry["pressures"]["pressure_test"]["knowledge"]["player_refs"]==[]


def test_ambiguous_faction_event_needs_explicit_front():
    rules=policy();rules["fronts"]["pressure_second"]={"faction_roles":{"faction.source":"source"}}
    registry={"pressures":{"pressure_test":pressure(),"pressure_second":pressure("pressure_second")}}
    assert apply_evidence(registry=registry,rules=rules,action={"kind":"mission_generate","event_id":"event.mission.001"},event=event(),player_ref="pc.player") is None
    update=apply_evidence(registry=registry,rules=rules,action={"kind":"mission_generate","event_id":"event.mission.001","world_front_ref":"pressure_second"},event=event(),player_ref="pc.player")
    assert update["front_id"]=="pressure_second"


def test_direct_domain_event_can_advance_front_without_autonomy_action_wrapper():
    rules={
        "phase_thresholds":{"developing_evidence":1,"operational_evidence":3,"crisis_evidence":6},
        "material_action_kinds":[],
        "fronts":{"pressure_test":{
            "faction_roles":{},"player_visibility":"public_or_knowledge",
            "event_sources":[{
                "event_kinds":["academy_exact_graduation_recorded"],
                "host_refs":["institution.konoha.academy"],"actor_refs":[],
                "material_ref_prefixes":[],"role":"source"
            }]
        }}
    }
    registry={"pressures":{"pressure_test":pressure()}}
    ev={
        "id":"event.academy.001","kind":"academy_exact_graduation_recorded",
        "host_refs":["institution.konoha.academy","force.konoha.shinobi"],
        "actor_refs":["canon_naruto"],
        "material_consequence_refs":["mission.academy.graduation"],
        "timing":{"occurred_at":"SE-0061-06-11T07:00:00"},
        "visibility":{"classification":"public","audience_refs":[],"witness_refs":[]},
        "provenance":{"source_refs":["institution.konoha.academy"]},
    }
    updates=apply_event_evidence(registry=registry,rules=rules,event=ev,player_ref="pc.player")
    assert len(updates)==1 and updates[0]["phase_after"]=="developing"
    assert updates[0]["player_visible"] is True
    row=registry["pressures"]["pressure_test"]
    assert row["evidence_refs"]==["event.academy.001"]
    assert "institution.konoha.academy" in row["source_refs"]
    assert "mission.academy.graduation" in row["resources"]
    assert row["knowledge"]["player_refs"]==[]
