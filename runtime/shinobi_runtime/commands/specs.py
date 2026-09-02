"""Single public semantic command registry for the Jianghu campaign."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping, Tuple

@dataclass(frozen=True)
class CommandVariant:
    required_fields: Tuple[str, ...]
    optional_fields: Tuple[str, ...] = ()
    payload_hints: Mapping[str, str] | None = None
    def descriptor(self) -> dict:
        hints=dict(self.payload_hints or {})
        out={"payload":{k:hints.get(k,"<value>") for k in (*self.required_fields,*self.optional_fields)},"required_fields":list(self.required_fields)}
        if self.optional_fields: out["optional_fields"]=list(self.optional_fields)
        return out

@dataclass(frozen=True)
class CommandSpec:
    required_fields: Tuple[str, ...]
    optional_fields: Tuple[str, ...]=()
    summary: str=""
    payload_hints: Mapping[str,str]|None=None
    availability: str="subject_to_domain_authority_and_state"
    variants: Mapping[str,CommandVariant]|None=None
    @property
    def exact_fields(self): return self.required_fields if not self.optional_fields and not self.variants else None
    def public_descriptor(self)->dict:
        out={"description":self.summary,"availability":self.availability}
        if self.variants:
            out["discriminator"]="action"; out["variants"]={k:v.descriptor() for k,v in self.variants.items()}; return out
        hints=dict(self.payload_hints or {}); out["payload"]={k:hints.get(k,"<value>") for k in (*self.required_fields,*self.optional_fields)}; out["required_fields"]=list(self.required_fields)
        if self.optional_fields: out["optional_fields"]=list(self.optional_fields)
        return out
    def expand_variant_payload(self,payload):
        if not self.variants:return None
        action=payload.get("action"); variant=self.variants.get(action) if isinstance(action,str) else None
        if variant is None:return None
        req=set(variant.required_fields); allowed=req|set(variant.optional_fields)
        if not req.issubset(payload):return None
        all_fields=set(self.required_fields)|set(self.optional_fields)
        for k,v in payload.items():
            if k in allowed:continue
            if k not in all_fields or v is not None:return None
        out={k:None for k in (*self.required_fields,*self.optional_fields)}; out.update(payload); return out

def _v(fields): return CommandVariant(tuple(fields))
def _s(fields,summary,*,availability="subject_to_domain_authority_and_state",variants=None): return CommandSpec(tuple(fields),(),summary,{},availability,variants)

COMMAND_SPECS={
 "advance_time":CommandSpec(("target_time",),("wait_policy","scene_policy"),"Advance campaign time through causal frontiers, settling routine maintenance internally and stopping only at a matching/material player boundary.",{"wait_policy":"<optional semantic stop object; use any_of for distinct alternative stop reasons>","scene_policy":"<preserve_active_scene|finish_active_scene|leave_active_scene|skip_to_conclusion>"},availability="scene_requires_explicit_policy_when_active"),
 "jianghu_interaction_resolution":CommandSpec(("action","target_ref"),("process_ref","player_statement","posture","topic","scopes","expects_response"),"Record one player-authored social attempt without inventing the target's response; typed topic/scopes route ordinary conversation without prose keyword matching.",{}),
 "jianghu_scene_session_resolution":CommandSpec(("action","session_ref","kind","participant_refs","process_ref","purpose","agenda","close_reason","speaker_ref","statement","speech_kind","basis_refs","resolves_thread_ref","resolves_question_ref","actor_ref","fact_kind","description","improvised_prop","continuity_kind","subject_refs"),(),"Persist presentation continuity for an LLM-directed people scene: open/close an optional reversible scene session, persist important attributed speech, preserve a salient reversible scene-local fact, or store a derived literary-continuity note grounded in cited scene history. Narrative scenes may start/end without this command; successful mechanical commands never imply scene completion. This command has no mechanical-consequence authority. For a mundane prop that may later cross into combat, the first object_state may carry the bounded form/material/condition descriptor; a later handling object_state must cite that exact earlier fact and repeat the exact descriptor before combat may derive transient physics.",{},variants={
    "open":CommandVariant(("action","kind","participant_refs"),("process_ref","purpose","agenda")),
    "close":CommandVariant(("action","session_ref","close_reason")),
    "record_speech":CommandVariant(("action","session_ref","speaker_ref","statement","speech_kind"),("basis_refs","resolves_thread_ref","resolves_question_ref")),
    "record_fact":CommandVariant(("action","session_ref","actor_ref","fact_kind","description"),("participant_refs","basis_refs","improvised_prop")),
    "record_continuity":CommandVariant(("action","session_ref","continuity_kind","description","basis_refs"),("subject_refs",)),
 }),
 "jianghu_training_focus_resolution":_s(("subject_ref","focus"),"Set Tang Wei's personal training focus or return to faction curriculum."),
 "jianghu_service_purchase_resolution":_s(("site_ref","service_ref"),"Purchase one local service with personal cash and real procedure time."),
 "jianghu_market_trade_resolution":CommandSpec(("action","item_ref","quantity","payer","faction_ref"),(),"Buy or sell finite regional goods using explicit personal or House funds.",{},variants={
    "buy":CommandVariant(("action","item_ref","quantity","payer"),("faction_ref",)),
    "sell":CommandVariant(("action","item_ref","quantity","payer"),("faction_ref",)),
 }),
 "jianghu_property_transfer_resolution":CommandSpec(("action","other_ref","item_ref","quantity","cash"),(),"Transfer or physically seize conserved personal property when a lawful physical basis exists.",{},variants={
    "give_item":CommandVariant(("action","other_ref","item_ref","quantity")),
    "seize_item":CommandVariant(("action","other_ref","item_ref","quantity")),
    "return_item":CommandVariant(("action","other_ref","item_ref","quantity")),
    "give_cash":CommandVariant(("action","other_ref","cash")),
    "seize_cash":CommandVariant(("action","other_ref","cash")),
    "claim_extinct_estate":CommandVariant(("action","other_ref")),
 }),
 "jianghu_local_travel_resolution":_s(("destination_site_ref",),"Walk between registered local sites using actual local distance."),
 "jianghu_security_resolution":CommandSpec(("action","target_faction_ref","approach"),(),"Attempt physical infiltration/forced entry or repair a breached controlled compound.",{},variants={
    "infiltrate":CommandVariant(("action","target_faction_ref"),("approach",)),
    "force_entry":CommandVariant(("action","target_faction_ref"),("approach",)),
    "repair_breach":CommandVariant(("action","target_faction_ref"),()),
 }),
 "jianghu_library_research_resolution":_s(("record_ref","minutes"),"Research one record actually held by the current faction library using real time and researcher capability."),
 "jianghu_public_disclosure_resolution":_s(("movement_ref","claim_kind","claimed_value_cash"),"Speak a mechanically consequential operational claim at a real public site; exact causally relevant listeners can retain a bounded current belief while hidden truth remains hidden."),
 "jianghu_institutional_operation_resolution":CommandSpec(("action","operation_ref","mission_kind","objective","target_faction_ref","target_site_ref","target_person_ref","linked_contract_ref","attendee_refs","commander_ref","member_refs","operation_kind","doctrine","reward_cash","reward_mode","ally_faction_ref","proposal_kind","value_cash","cost_cash","source_captive_refs","target_captive_refs"),(),"Create, brief, authorize, dispatch, reinforce, negotiate, settle, decline, cancel or review one persistent House mission while reusing physical contract, travel, custody, diplomacy and warfare authorities.",{},variants={
  "propose":_v(("action","operation_ref","mission_kind","objective","target_faction_ref","target_site_ref","target_person_ref","linked_contract_ref","reward_cash","reward_mode")),
  "accept_assignment":_v(("action","operation_ref")),
  "convene":_v(("action","operation_ref","attendee_refs")),
  "submit_plan":CommandVariant(("action","operation_ref","commander_ref","member_refs","operation_kind","doctrine"),("proposal_kind","value_cash","cost_cash","source_captive_refs","target_captive_refs")),
  "dispatch":_v(("action","operation_ref")),
  "request_aid":_v(("action","operation_ref","ally_faction_ref")),
  "decline_assignment":_v(("action","operation_ref")),
  "cancel":_v(("action","operation_ref")),
  "settle_reward":_v(("action","operation_ref")),
  "service_review":_v(("action","operation_ref")),
  "accept_career_offer":_v(("action","operation_ref")),
  "decline_career_offer":_v(("action","operation_ref")),
 }),
 "jianghu_contract_resolution":CommandSpec(("action","contract_ref","participant_refs"),(),"Accept or start a funded Jianghu contract. Escort departure uses accepted principals plus the leader's active permanent travel team, then assigns only the additional temporary House mission staff required by the physical escort minimum.",{},variants={"accept":_v(("action","contract_ref","participant_refs")),"start":_v(("action","contract_ref"))}),
 "jianghu_calendar_event_resolution":CommandSpec(("action","event_ref","other_ref"),(),"Attend or take one real supported action at an active deterministic Jianghu calendar gathering while physically present at an eligible site.",{},variants={
    "attend":CommandVariant(("action","event_ref")),
    "socialize":CommandVariant(("action","event_ref","other_ref")),
    "instruction":CommandVariant(("action","event_ref","other_ref")),
    "demonstrate":CommandVariant(("action","event_ref")),
    "assess":CommandVariant(("action","event_ref","other_ref")),
    "review":CommandVariant(("action","event_ref")),
 }),
 "jianghu_tournament_resolution":CommandSpec(("action","tournament_ref"),(),"Register for, spectate, or advance a live exact-combat tournament.",{},variants={"register":_v(("action","tournament_ref")),"spectate":_v(("action","tournament_ref")),"advance":_v(("action","tournament_ref"))}),
 "jianghu_deployment_resolution":CommandSpec(("action","deployment_ref","member_refs","objective"),(),"Form or release a conserved Jianghu deployment.",{},variants={"form":_v(("action","deployment_ref","member_refs","objective")),"release":_v(("action","deployment_ref"))}),
 "jianghu_retinue_resolution":CommandSpec(("action","retinue_ref","chooser_refs","requested_count","member_ref","role"),(),"Request, expand, reduce, or release Wei's persistent personal travel team. Membership has no fixed cap; temporary escort staffing remains mission-specific and never becomes permanent automatically.",{},variants={"request":_v(("action","retinue_ref","chooser_refs","requested_count")),"add_member":_v(("action","retinue_ref","member_ref","role")),"remove_member":_v(("action","retinue_ref","member_ref")),"release":_v(("action","retinue_ref"))}),
 "jianghu_infrastructure_resolution":CommandSpec(("action","faction_ref","project_ref","building_type","enterprise_type","target_level","additional_footprint_m2","additional_land_m2","additional_scale","target_time"),(),"Upgrade quality, expand the existing physical facility/estate/enterprise scale, or advance a conserved faction project.",{},variants={"start_building":_v(("action","faction_ref","project_ref","building_type","target_level")),"expand_building":_v(("action","faction_ref","project_ref","building_type","additional_footprint_m2")),"expand_estate":_v(("action","faction_ref","project_ref","additional_land_m2")),"start_enterprise":_v(("action","faction_ref","project_ref","enterprise_type","target_level")),"expand_enterprise":_v(("action","faction_ref","project_ref","enterprise_type","additional_scale")),"advance":_v(("action","project_ref","target_time"))}),
 "jianghu_recruitment_resolution":_s(("faction_ref","place_ref","requested_count"),"Recruit conserved bodies from an aggregate civilian population into persistent faction identities."),

 "jianghu_faction_lifecycle_resolution":CommandSpec(("action","new_faction_ref","name","faction_type","member_refs","startup_cash","startup_ration_days","source_faction_ref","target_faction_ref","treasury_cash","food_ration_days","inventory_transfer","estate_site_refs","headquarters_place_ref","headquarters_site_ref","membership_tenure","jianghu_camp"),(),"Found, split, merge, or dissolve a current faction by moving conserved exact people and assets.",{},variants={
    "found":CommandVariant(("action","new_faction_ref","name","faction_type","member_refs","startup_cash","startup_ration_days","headquarters_place_ref","headquarters_site_ref"),("membership_tenure","jianghu_camp")),
    "split":CommandVariant(("action","source_faction_ref","new_faction_ref","name","member_refs","treasury_cash","food_ration_days","headquarters_place_ref","headquarters_site_ref"),("faction_type","membership_tenure","jianghu_camp","inventory_transfer","estate_site_refs")),
    "merge":CommandVariant(("action","source_faction_ref","target_faction_ref")),
    "dissolve":CommandVariant(("action","source_faction_ref")),
 }),

 "jianghu_medicine_resolution":CommandSpec(("action","subject_ref","medicine_ref","faction_ref","poison_ref","duration_minutes"),(),"Administer one finite physiological medicine dose or deliberately purge an established toxin burden with Qi.",{},variants={"administer":_v(("action","subject_ref","medicine_ref","faction_ref")),"qi_purge":_v(("action","subject_ref","poison_ref","duration_minutes"))}),
 "jianghu_production_resolution":CommandSpec(("action","faction_ref","recipe_ref","count"),(),"Produce registered workshop equipment, medicine, or fictional poison from conserved inputs and labor time.",{},variants={"workshop":_v(("action","faction_ref","recipe_ref","count")),"medicine":_v(("action","faction_ref","recipe_ref","count")),"poison":_v(("action","faction_ref","recipe_ref","count"))}),
 "jianghu_strategic_travel_resolution":_s(("destination_site_ref","mode"),"Travel between strategic places over registered routes, weather and tolls. An active permanent travel team accompanies its leader as one real party when available and co-located."),
 "jianghu_equipment_resolution":CommandSpec(("action","subject_ref","item_ref","quantity"),(),"Issue, return or repair conserved equipment.",{},variants={"issue":_v(("action","subject_ref","item_ref","quantity")),"return":_v(("action","subject_ref","item_ref","quantity")),"repair":_v(("action","subject_ref","item_ref","quantity"))}),
 "jianghu_diplomacy_resolution":CommandSpec(("target_faction_ref","proposal_kind","value_cash","cost_cash"),("source_captive_refs","target_captive_refs","institutional_operation_ref"),"Resolve one bounded faction negotiation: conserved silver settlement, truce/non-aggression, mutual defense/alliance, tribute/restitution, or exchange of exact current captives. A non-officeholding player may act only under an exact House-approved diplomatic mission whose authorized terms match the proposal.",{}),
 "jianghu_social_resolution":CommandSpec(("action","other_ref","promise_kind","strength","vow_kind","subject_ref","faction_ref","vow_ref","obligation_ref","belief_ref","source_ref","claim_kind","claimed_value_cash","evidence_ref"),(),"Create or resolve one sparse personal promise/vow/obligation, retain one claim actually heard from a present person, or investigate one current belief through a registered evidence path.",{},variants={
    "promise":CommandVariant(("action","other_ref","promise_kind","strength")),
    "make_vow":CommandVariant(("action","vow_kind","strength"),("subject_ref","faction_ref")),
    "release_vow":CommandVariant(("action","vow_ref")),
    "forgive_obligation":CommandVariant(("action","obligation_ref")),
    "renounce_obligation":CommandVariant(("action","obligation_ref")),
    "hear_claim":CommandVariant(("action","source_ref","subject_ref","claim_kind"),("claimed_value_cash","evidence_ref")),
    "investigate":CommandVariant(("action","belief_ref")),
 }),
 "jianghu_family_resolution":CommandSpec(("action","other_ref"),(),"Begin consensual courtship or commit an eligible marriage.",{},variants={"courtship":_v(("action","other_ref")),"marriage":_v(("action","other_ref"))}),
 "jianghu_custody_resolution":CommandSpec(("action","person_ref"),(),"Create, release, or escape conserved custody of an existing person.",{},variants={k:_v(("action","person_ref")) for k in ("restrain","release","escape_attempt","rescue","deliver_to_government")}),
 "jianghu_crime_report_resolution":_s(("subject_ref","offense","confidence","evidence_ref"),"Commit state-verifiable delivered property-crime evidence into government attention and funded bounty authority; observed route crimes use their own witness path."),
 "jianghu_combat_resolution":CommandSpec(("action","combat_ref","side_a_refs","side_b_refs","objective","awareness_mode","initial_range_band","mounted_refs","action_kind","target_ref","weapon_ref","hit_zone","target_structure_ref","targeting_intent","poison_ref","qi_allocation_milli","exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref","rally_allies"),(),"Start or resolve anatomy-first exact Jianghu combat on authoritative local geometry and named anatomy. A bare exchange means attack under standing doctrine: omitted target, action, weapon, anatomical aim, Qi, and poison are resolved from lawful perception, current geometry, Wei's personal doctrine, and active team doctrine. Combat Qi allocation uses only body, movement, and sensing flow; Qi temporarily improves the fighter's ordinary capabilities and never adds a separate weapon-damage channel. Every concrete player detail overrides only that detail. Optional exchange_count, duration_seconds, or until_resolution extends the same high-level attack intent across a bounded combat span; doctrine is re-evaluated each exchange. Optional rally_allies records a real contested leadership attempt to arrest noncritical allied withdrawal; it never guarantees obedience or overrides critical physical condition. An exact improvised_prop_fact_ref may promote one already-established mundane scene object into a transient server-derived combat profile without minting inventory or value.",{},variants={
    "start":CommandVariant(("action","combat_ref","side_a_refs","side_b_refs","objective","awareness_mode","initial_range_band"),("mounted_refs",)),
    "exchange":CommandVariant(("action","combat_ref"),("action_kind","target_ref","weapon_ref","hit_zone","target_structure_ref","targeting_intent","poison_ref","qi_allocation_milli","exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref","rally_allies")),
    "disengage":_v(("action","combat_ref")),
    "end":_v(("action","combat_ref")),
 }),
}
