"""Direct player participation in deterministic recurring Jianghu gatherings."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.martial_world.calendar_participation import (
    active_event_opportunities,
    derived_calendar_event_attendance,
    occurrence_active_at,
    occurrence_for_ref,
)
from shinobi_runtime.martial_world.civic import civic_people
from shinobi_runtime.martial_world.faction_state import read_faction, roster_path as faction_roster_path
from shinobi_runtime.martial_world.faction_registry import current_faction_refs_at_place
from shinobi_runtime.martial_world.live_state import set_roster_person
from shinobi_runtime.martial_world.training import apply_deliberate_training_session
from shinobi_runtime.martial_world.membership import grade_eligibility
from shinobi_runtime.martial_world.rankings import apply_personal_fame_evidence, publish_rankings
from shinobi_runtime.martial_world.person_state import hydrate_roster_state
from shinobi_runtime.martial_world.character_rules import martial_discipline_keys
from shinobi_runtime.martial_world.relationships import apply_relationship_event

_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_SOCIAL = "state/martial-world/social.json"
_REPUTATION = "state/martial-world/reputation.json"


class JianghuCalendarCommandsMixin:
    def _calendar_attendees(
        self, *, occurrence: Mapping[str, Any], site_ref: str, actor_ref: str,
        actor_faction_ref: str, at: datetime, limit: int | None = None,
    ) -> list[str]:
        site_data = self.repository.read_json(_LOCAL_SITES)
        sites = site_data.get("sites", {}) if isinstance(site_data, Mapping) else {}
        site = sites.get(site_ref) if isinstance(sites, Mapping) else None
        if not isinstance(site, Mapping):
            raise CommandRejectedError("jianghu_calendar_event_site_unresolved")
        parent = str(site.get("parent_place_ref") or "")
        faction_refs = current_faction_refs_at_place(
            self.repository.read_json, place_ref=parent, sites=sites,
        )
        faction_people: list[tuple[str, list[Mapping[str, Any]]]] = []
        headquarters: dict[str, str] = {}
        for faction_ref in sorted(str(x) for x in faction_refs if isinstance(x, str)):
            try:
                _fpath, faction = read_faction(self.repository, faction_ref)
                roster = hydrate_roster_state(
                    self.repository.read_json(faction_roster_path(faction_ref)), faction=faction
                )
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
            people = roster.get("people", []) if isinstance(roster, Mapping) else []
            if isinstance(people, list):
                faction_people.append((faction_ref, [row for row in people if isinstance(row, Mapping)]))
            headquarters[faction_ref] = str(faction.get("headquarters") or parent)
        return derived_calendar_event_attendance(
            occurrence=occurrence,
            site_ref=site_ref,
            site=site,
            faction_people=faction_people,
            faction_headquarters=headquarters,
            sites=sites,
            at=at,
            unavailable_refs=self._unavailable_person_refs(),
            exclude_refs={actor_ref},
            player_faction_ref=actor_faction_ref,
            civic_people=civic_people(self.repository),
            limit=limit,
        )

    def _jianghu_calendar_event_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        self._require_jianghu(meta)
        action = str(command.payload.get("action") or "")
        event_ref = str(command.payload.get("event_ref") or "")
        supported = {"attend", "socialize", "instruction", "demonstrate", "assess", "review"}
        if action not in supported or not event_ref:
            raise CommandRejectedError("jianghu_calendar_event_payload_invalid")
        occurrence = occurrence_for_ref(event_ref)
        at = datetime(current_time.year,current_time.month,current_time.day,current_time.hour,current_time.minute,current_time.second)
        if not isinstance(occurrence, Mapping) or not occurrence_active_at(occurrence, at):
            raise CommandRejectedError("jianghu_calendar_event_not_active")
        self._require_person_available_for_activity(command.actor_id)
        actor_path, actor_roster, actor_idx, actor = self._person(command.actor_id)
        site_ref = self._effective_person_location(command.actor_id, actor)
        faction_ref = str(actor.get("faction_ref") or "")
        if not site_ref or not faction_ref:
            raise CommandRejectedError("jianghu_calendar_event_requires_presence")
        site_data = self.repository.read_json(_LOCAL_SITES)
        sites = site_data.get("sites", {}) if isinstance(site_data, Mapping) else {}
        site = sites.get(site_ref) if isinstance(sites, Mapping) else None
        if not isinstance(site, Mapping):
            raise CommandRejectedError("jianghu_calendar_event_site_unresolved")
        try:
            _fpath, faction = read_faction(self.repository, faction_ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_calendar_event_faction_unresolved") from exc
        opportunities = active_event_opportunities(
            at=at, player_site_ref=site_ref, player_faction_ref=faction_ref,
            player_faction_headquarters=str(faction.get("local_site_ref") or ""), sites=sites,
        )
        opportunity = next((row for row in opportunities if row.get("event_ref") == event_ref), None)
        if not isinstance(opportunity, Mapping) or not bool(opportunity.get("local_available")):
            raise CommandRejectedError("jianghu_calendar_event_requires_eligible_site")
        modes = {str(x) for x in opportunity.get("player_modes", []) if isinstance(x, str)}
        if action not in modes:
            raise CommandRejectedError("jianghu_calendar_event_action_unavailable")
        event_id = str(occurrence.get("event_id") or "")

        def attendees(limit: int | None = None) -> list[str]:
            return self._calendar_attendees(
                occurrence=occurrence, site_ref=site_ref, actor_ref=command.actor_id,
                actor_faction_ref=faction_ref, at=at, limit=limit,
            )

        if action == "attend":
            exact = attendees(24)
            time_plan, extra, _target = self._timed_person_activity_plan(
                command, meta, current_time, person_refs=[command.actor_id], seconds=3600,
                activity_ref=f"calendar-attend:{command.request_id}", activity_kind="calendar_event_attendance",
                owner_ref=event_ref, location_ref=site_ref,
            )
            return self._combine_time_plan(command,time_plan,extra_records=extra,code="jianghu_calendar_event_attended",result={
                "command_type":command.command_type,"action":action,"event_ref":event_ref,"event_id":event_id,
                "display_name":occurrence.get("display_name"),"site_ref":site_ref,"exact_attendee_person_ids":exact,
            })

        if action in {"socialize", "instruction", "assess"}:
            other_ref = str(command.payload.get("other_ref") or "")
            if not other_ref or other_ref == command.actor_id:
                raise CommandRejectedError("jianghu_calendar_event_social_target_invalid")
            exact = attendees(None)
            if other_ref not in exact:
                raise CommandRejectedError("jianghu_calendar_event_person_not_attending")
            try:
                other_path, other_roster, other_idx, other = self._person(other_ref)
            except CommandRejectedError as exc:
                raise CommandRejectedError("jianghu_calendar_event_person_unresolved") from exc
            self._require_person_available_for_activity(other_ref, "jianghu_calendar_event_person_unavailable")

            if action == "socialize":
                time_plan, extra, _target = self._timed_person_activity_plan(
                    command, meta, current_time, person_refs=[command.actor_id, other_ref], seconds=1800,
                    activity_ref=f"calendar-social:{command.request_id}", activity_kind="calendar_event_socializing",
                    owner_ref=event_ref, location_ref=site_ref,
                )
                base_social = self._time_after_record(time_plan, _SOCIAL, self.repository.read_json(_SOCIAL))
                first = apply_relationship_event(base_social,observer_ref=command.actor_id,subject_ref=other_ref,event_kind="conversation",observer_knows=True,severity_milli=700,protected_player_ref=command.actor_id)
                second = apply_relationship_event(first["state_after"],observer_ref=other_ref,subject_ref=command.actor_id,event_kind="conversation",observer_knows=True,severity_milli=700,protected_player_ref=command.actor_id)
                extra[_SOCIAL] = second["state_after"]
                return self._combine_time_plan(command,time_plan,extra_records=extra,code="jianghu_calendar_event_socialized",result={
                    "command_type":command.command_type,"action":action,"event_ref":event_ref,"event_id":event_id,
                    "site_ref":site_ref,"other_ref":other_ref,"player_relationship_delta":first.get("delta",{}),"other_relationship_delta":second.get("delta",{}),
                })

            if action == "instruction":
                if event_id != "winter_martial_lectures":
                    raise CommandRejectedError("jianghu_calendar_event_action_unavailable")
                instruction_skill = max(0,int((other.get("professional_skills") or {}).get("instruction",0))) if isinstance(other.get("professional_skills"),Mapping) else 0
                offices = {str(x).split(":",1)[0] for x in other.get("standing_offices",[]) if isinstance(x,str)}
                domains = list(martial_discipline_keys()) + ["qi", "qi_control"]
                def value(person: Mapping[str,Any], domain: str) -> int:
                    if domain in {"qi","qi_control"}: return max(0,int(person.get(domain,0)))
                    skills=person.get("martial_skills",{}) if isinstance(person.get("martial_skills"),Mapping) else {}
                    return max(0,int(skills.get(domain,0)))
                domain=max(domains,key=lambda d:(value(other,d)-value(actor,d),value(other,d),-domains.index(d)))
                if value(other,domain) <= value(actor,domain) or (instruction_skill <= 0 and "chief_martial_instructor" not in offices):
                    raise CommandRejectedError("jianghu_calendar_event_instructor_unqualified")
                time_plan, extra, target = self._timed_person_activity_plan(
                    command,meta,current_time,person_refs=[command.actor_id,other_ref],seconds=7200,
                    activity_ref=f"calendar-instruction:{command.request_id}",activity_kind="calendar_event_instruction",owner_ref=event_ref,location_ref=site_ref,
                )
                final_actor_path, final_actor_roster, final_actor_idx, final_actor = self._person(command.actor_id)
                if actor_path in extra:
                    final_actor_roster=copy.deepcopy(extra[actor_path])
                    rows=final_actor_roster.get("people",[]) if isinstance(final_actor_roster,Mapping) else []
                    final_actor_idx=next(i for i,r in enumerate(rows) if isinstance(r,Mapping) and r.get("person_id")==command.actor_id)
                    final_actor=copy.deepcopy(dict(rows[final_actor_idx]))
                result=apply_deliberate_training_session(final_actor,domain=domain,hours_milli=2000,at_iso=str(target).removeprefix("SE-"),instructor_skill=value(other,domain),instruction_skill=instruction_skill,facilities={"training_hall":1,"training_grounds":1,"qi_hall":1})
                extra[actor_path]=set_roster_person(final_actor_roster,final_actor_idx,result["person_after"])
                social_before=self._time_after_record(time_plan,_SOCIAL,self.repository.read_json(_SOCIAL))
                teaching=apply_relationship_event(
                    social_before,observer_ref=command.actor_id,subject_ref=other_ref,
                    event_kind="teaching",observer_knows=True,severity_milli=1000,
                    protected_player_ref=command.actor_id,
                )
                extra[_SOCIAL]=teaching["state_after"]
                return self._combine_time_plan(command,time_plan,extra_records=extra,code="jianghu_calendar_event_instruction_completed",result={
                    "command_type":command.command_type,"action":action,"event_ref":event_ref,"event_id":event_id,"instructor_ref":other_ref,"domain":domain,"skill_before":result["before"],"skill_after":result["after"],"residual_milli":result["residual_milli"],"relationship_delta":teaching.get("delta",{}),
                })

            if action == "assess":
                if event_id != "year_end_member_assessments" or str(other.get("faction_ref") or "") != faction_ref:
                    raise CommandRejectedError("jianghu_calendar_event_action_unavailable")
                actor_offices={str(x).split(":",1)[0] for x in actor.get("standing_offices",[]) if isinstance(x,str)}
                if not actor_offices & {"leader","deputy_leader","chief_martial_instructor"}:
                    raise CommandRejectedError("jianghu_calendar_event_assessment_not_authorized")
                grades=("probationary","junior","full","senior","elite","elder")
                current_grade=str(other.get("membership_grade") or "probationary")
                if current_grade not in grades or current_grade=="elder":
                    raise CommandRejectedError("jianghu_calendar_event_assessment_no_higher_grade")
                next_grade=grades[grades.index(current_grade)+1]
                training=faction.get("training",{}) if isinstance(faction.get("training"),Mapping) else {}
                martial=martial_discipline_keys()
                primary=max(martial,key=lambda k:(int(training.get(k,0)),-martial.index(k)))
                service_days=max(0,(current_time.year-int(other.get("joined_year",current_time.year)))*365)
                elder_count=sum(1 for r in actor_roster.get("people",[]) if isinstance(r,Mapping) and r.get("membership_grade")=="elder" and (r.get("health") or {}).get("status")!="dead")
                living=sum(1 for r in actor_roster.get("people",[]) if isinstance(r,Mapping) and (r.get("health") or {}).get("status")!="dead")
                elder_cap=max(1,living//50) if living>=25 else 0
                check=grade_eligibility(other,target_grade=next_grade,service_days=service_days,primary_discipline=primary,discipline_clean=True,elder_open_seat=elder_count<elder_cap)
                time_plan, extra, _target = self._timed_person_activity_plan(
                    command,meta,current_time,person_refs=[command.actor_id,other_ref],seconds=1800,
                    activity_ref=f"calendar-assess:{command.request_id}",activity_kind="calendar_member_assessment",owner_ref=event_ref,location_ref=site_ref,
                )
                promoted=False
                if check["eligible"]:
                    final_roster=copy.deepcopy(extra.get(other_path,other_roster)); rows=final_roster.get("people",[])
                    oi=next(i for i,r in enumerate(rows) if isinstance(r,Mapping) and r.get("person_id")==other_ref)
                    updated=copy.deepcopy(dict(rows[oi])); updated["membership_grade"]=next_grade; extra[other_path]=set_roster_person(final_roster,oi,updated); promoted=True
                return self._combine_time_plan(command,time_plan,extra_records=extra,code="jianghu_calendar_event_assessment_completed",result={
                    "command_type":command.command_type,"action":action,"event_ref":event_ref,"event_id":event_id,"subject_ref":other_ref,"grade_before":current_grade,"target_grade":next_grade,"eligible":bool(check["eligible"]),"reasons":list(check["reasons"]),"promoted":promoted,
                })

        if action == "demonstrate":
            if event_id != "lantern_city_martial_exhibitions":
                raise CommandRejectedError("jianghu_calendar_event_action_unavailable")
            time_plan, extra, _target = self._timed_person_activity_plan(
                command,meta,current_time,person_refs=[command.actor_id],seconds=1800,
                activity_ref=f"calendar-demonstrate:{command.request_id}",activity_kind="calendar_public_exhibition",owner_ref=event_ref,location_ref=site_ref,
            )
            base_rep=self._time_after_record(time_plan,_REPUTATION,self.repository.read_json(_REPUTATION))
            audience_ref=str(site.get("parent_place_ref") or "local_public")
            extra[_REPUTATION]=apply_personal_fame_evidence(base_rep,audience_ref=audience_ref,person_ref=command.actor_id,evidence_kind="public_exhibition",delivered=True,reliability_milli=900)
            return self._combine_time_plan(command,time_plan,extra_records=extra,code="jianghu_calendar_event_demonstrated",result={"command_type":command.command_type,"action":action,"event_ref":event_ref,"event_id":event_id,"audience_ref":audience_ref})

        if action == "review":
            if event_id != "jianghu_ranking_publication":
                raise CommandRejectedError("jianghu_calendar_event_action_unavailable")
            time_plan, extra, _target = self._timed_person_activity_plan(
                command,meta,current_time,person_refs=[command.actor_id],seconds=1800,
                activity_ref=f"calendar-review:{command.request_id}",activity_kind="calendar_publication_review",owner_ref=event_ref,location_ref=site_ref,
            )
            rep=self._time_after_record(time_plan,_REPUTATION,self.repository.read_json(_REPUTATION))
            audiences=rep.get("audiences",{}) if isinstance(rep,Mapping) else {}
            records=[]
            if isinstance(audiences,Mapping):
                for ref,row in audiences.items():
                    if isinstance(ref,str) and isinstance(row,Mapping): records.append({"person_id":ref,**dict(row)})
            published=publish_rankings(records)[:20]
            return self._combine_time_plan(command,time_plan,extra_records=extra,code="jianghu_calendar_event_reviewed",result={"command_type":command.command_type,"action":action,"event_ref":event_ref,"event_id":event_id,"public_rankings":published})

        raise CommandRejectedError("jianghu_calendar_event_action_unavailable")


__all__ = ["JianghuCalendarCommandsMixin"]
