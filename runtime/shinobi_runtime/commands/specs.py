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
 "advance_time":_s(("target_time",),"Advance campaign time through the compact causal frontier.",availability="scene_must_allow_time_passage"),
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
 }),
 "jianghu_local_travel_resolution":_s(("destination_site_ref",),"Walk between registered local sites using actual local distance."),
 "jianghu_security_resolution":CommandSpec(("action","target_faction_ref","approach"),(),"Attempt physical infiltration or forced entry against an actual staffed compound.",{},variants={
    "infiltrate":CommandVariant(("action","target_faction_ref"),("approach",)),
    "force_entry":CommandVariant(("action","target_faction_ref"),("approach",)),
 }),
 "jianghu_library_research_resolution":_s(("record_ref","minutes"),"Research one record actually held by the current faction library using real time and researcher capability."),
 "jianghu_contract_resolution":CommandSpec(("action","contract_ref","participant_refs"),(),"Accept or start a funded Jianghu contract.",{},variants={"accept":_v(("action","contract_ref","participant_refs")),"start":_v(("action","contract_ref"))}),
 "jianghu_tournament_resolution":CommandSpec(("action","tournament_ref"),(),"Register for or advance a live exact-combat tournament.",{},variants={"register":_v(("action","tournament_ref")),"advance":_v(("action","tournament_ref"))}),
 "jianghu_deployment_resolution":CommandSpec(("action","deployment_ref","member_refs","objective"),(),"Form or release a conserved Jianghu deployment.",{},variants={"form":_v(("action","deployment_ref","member_refs","objective")),"release":_v(("action","deployment_ref"))}),
 "jianghu_infrastructure_resolution":CommandSpec(("action","faction_ref","project_ref","building_type","enterprise_type","target_level","additional_footprint_m2","additional_land_m2","additional_scale","target_time"),(),"Upgrade quality, expand the existing physical facility/estate/enterprise scale, or advance a conserved faction project.",{},variants={"start_building":_v(("action","faction_ref","project_ref","building_type","target_level")),"expand_building":_v(("action","faction_ref","project_ref","building_type","additional_footprint_m2")),"expand_estate":_v(("action","faction_ref","project_ref","additional_land_m2")),"start_enterprise":_v(("action","faction_ref","project_ref","enterprise_type","target_level")),"expand_enterprise":_v(("action","faction_ref","project_ref","enterprise_type","additional_scale")),"advance":_v(("action","project_ref","target_time"))}),
 "jianghu_recruitment_resolution":_s(("faction_ref","place_ref","requested_count"),"Recruit conserved bodies from an aggregate civilian population into persistent faction identities."),

 "jianghu_medicine_resolution":CommandSpec(("action","subject_ref","medicine_ref","faction_ref","poison_ref","duration_minutes"),(),"Administer one finite physiological medicine dose or deliberately purge an established toxin burden with Qi.",{},variants={"administer":_v(("action","subject_ref","medicine_ref","faction_ref")),"qi_purge":_v(("action","subject_ref","poison_ref","duration_minutes"))}),
 "jianghu_production_resolution":CommandSpec(("action","faction_ref","recipe_ref","count"),(),"Produce registered workshop equipment, medicine, or fictional poison from conserved inputs and labor time.",{},variants={"workshop":_v(("action","faction_ref","recipe_ref","count")),"medicine":_v(("action","faction_ref","recipe_ref","count")),"poison":_v(("action","faction_ref","recipe_ref","count"))}),
 "jianghu_strategic_travel_resolution":_s(("destination_site_ref","mode"),"Travel between strategic places over registered routes, weather and tolls."),
 "jianghu_equipment_resolution":CommandSpec(("action","subject_ref","item_ref","quantity"),(),"Issue, return or repair conserved equipment.",{},variants={"issue":_v(("action","subject_ref","item_ref","quantity")),"return":_v(("action","subject_ref","item_ref","quantity")),"repair":_v(("action","subject_ref","item_ref","quantity"))}),
 "jianghu_diplomacy_resolution":_s(("target_faction_ref","proposal_kind","value_cash","cost_cash"),"Resolve a bounded one-off faction silver exchange with conserved treasuries. proposal_kind is silver_exchange."),
 "jianghu_family_resolution":CommandSpec(("action","other_ref"),(),"Begin consensual courtship or commit an eligible marriage.",{},variants={"courtship":_v(("action","other_ref")),"marriage":_v(("action","other_ref"))}),
 "jianghu_custody_resolution":CommandSpec(("action","person_ref"),(),"Create, release, or escape conserved custody of an existing person.",{},variants={k:_v(("action","person_ref")) for k in ("restrain","release","escape_attempt")}),
 "jianghu_crime_report_resolution":_s(("subject_ref","offense","confidence","evidence_ref"),"Commit delivered criminal evidence into government attention and bounty authority."),
 "jianghu_combat_resolution":CommandSpec(("action","combat_ref","side_a_refs","side_b_refs","objective","awareness_mode","initial_range_band","mounted_refs","action_kind","target_ref","weapon_ref","hit_zone","target_structure_ref","targeting_intent","poison_ref"),(),"Start or resolve anatomy-first exact Jianghu combat on authoritative local geometry and named anatomy.",{},variants={
    "start":CommandVariant(("action","combat_ref","side_a_refs","side_b_refs","objective","awareness_mode","initial_range_band"),("mounted_refs",)),
    "exchange":CommandVariant(("action","combat_ref","action_kind","target_ref","weapon_ref","hit_zone"),("target_structure_ref","targeting_intent","poison_ref")),
    "disengage":_v(("action","combat_ref")),
    "end":_v(("action","combat_ref")),
 }),
}
