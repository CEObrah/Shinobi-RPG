"""Single source of truth for the public semantic command surface.

The planner owns mechanics.  This registry owns command names, accepted payload
fields, and bounded player-facing discovery metadata so API documentation cannot
drift away from executable reducers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple


@dataclass(frozen=True)
class CommandVariant:
    required_fields: Tuple[str, ...]
    optional_fields: Tuple[str, ...] = ()
    payload_hints: Mapping[str, str] | None = None

    def descriptor(self) -> dict:
        hints = dict(self.payload_hints or {})
        payload = {name: hints.get(name, "<value>") for name in (*self.required_fields, *self.optional_fields)}
        result = {"payload": payload, "required_fields": list(self.required_fields)}
        if self.optional_fields:
            result["optional_fields"] = list(self.optional_fields)
        return result


@dataclass(frozen=True)
class CommandSpec:
    required_fields: Tuple[str, ...]
    optional_fields: Tuple[str, ...] = ()
    summary: str = ""
    payload_hints: Mapping[str, str] | None = None
    availability: str = "subject_to_domain_authority_and_state"
    variants: Mapping[str, CommandVariant] | None = None

    @property
    def exact_fields(self) -> Tuple[str, ...] | None:
        return self.required_fields if not self.optional_fields and not self.variants else None

    def public_descriptor(self) -> dict:
        result = {"description": self.summary, "availability": self.availability}
        if self.variants:
            result["discriminator"] = "action"
            result["variants"] = {name: variant.descriptor() for name, variant in self.variants.items()}
            return result
        hints = dict(self.payload_hints or {})
        payload = {name: hints.get(name, "<value>") for name in (*self.required_fields, *self.optional_fields)}
        result.update({"payload": payload, "required_fields": list(self.required_fields)})
        if self.optional_fields:
            result["optional_fields"] = list(self.optional_fields)
        return result

    def expand_variant_payload(self, payload: Mapping[str, object]) -> dict | None:
        if not self.variants:
            return None
        action = payload.get("action")
        variant = self.variants.get(action) if isinstance(action, str) else None
        if variant is None:
            return None
        required = set(variant.required_fields)
        allowed = required | set(variant.optional_fields)
        if not required.issubset(payload) or set(payload) - allowed:
            return None
        expanded = {name: None for name in (*self.required_fields, *self.optional_fields)}
        expanded.update(payload)
        return expanded


def _variant(required, *, optional=(), hints=None):
    return CommandVariant(tuple(required), tuple(optional), hints or {})


def _spec(fields, summary, *, hints=None, availability="subject_to_domain_authority_and_state", optional=(), variants=None):
    return CommandSpec(tuple(fields), tuple(optional), summary, hints or {}, availability, variants or None)


COMMAND_SPECS = {
    "advance_time": _spec(
        ("target_time",),
        "Advance campaign time until the requested time or the earliest unresolved causal boundary.",
        hints={"target_time": "SE-YYYY-MM-DDTHH:MM:SS"},
        availability="scene_must_allow_time_passage",
    ),
    "mission_creation": _spec(
        ("mission_id", "issuer_ref", "authority_ref", "mission_rank", "participant_refs", "objectives", "settlement_terms"),
        "Create one authorized executable mission owner.",
        optional=("deadline_at", "next_due_at", "operation_ref"),
    ),
    "mission_transition": _spec(("mission_id", "target_state"), "Move an existing mission through a lawful lifecycle transition."),
    "mission_objective_update": _spec(
        ("mission_id", "objective_id", "target_status", "progress_milli", "evidence_event_id"),
        "Apply a terminal objective update backed by persisted world-event evidence.",
    ),
    "mission_derive_and_settle": _spec(("mission_id",), "Derive mission outcome from authoritative objectives and settle declared terms once."),
    "training_resolution": _spec(
        ("actor_ref", "target", "model_ref", "target_time", "active_hours"),
        "Resolve one exact person's bounded training activity.",
        optional=("context_ref", "instructor_ref"),
    ),
    "breakthrough_resolution": _spec(
        ("subject_ref", "target", "evidence_event_ref", "summary"),
        "Advance one exact-person exceptional capability point from a persisted career dossier of consolidation and distinct mission/combat experience.",
    ),
    "team_training_session_resolution": _spec(
        ("team_ref", "member_targets", "instructor_ref", "target_time", "active_hours"),
        "Resolve one exact team's shared training session as one time-spanning transaction.",
    ),
    "team_development_resolution": _spec(
        ("team_ref", "doctrine_identity", "motto", "training_focus", "instructor_refs", "facility_refs"),
        "Adopt or revise an exact team's doctrine and training preferences.",
    ),
    "team_lifecycle_resolution": _spec(
        ("action", "team_ref", "name", "team_type", "parent_institution_ref", "assignment_authority_ref", "leader_ref", "member_refs", "roles", "classification", "assignment_ref", "reason"),
        "Form, reorganize, assign, unassign, or dissolve one exact named team.",
        variants={
            "form": _variant(("action","team_ref","name","team_type","assignment_authority_ref","leader_ref","member_refs","roles","classification"), optional=("parent_institution_ref","reason")),
            "reorganize": _variant(("action","team_ref","leader_ref","member_refs","roles"), optional=("name","team_type","classification","reason")),
            "assign": _variant(("action","team_ref","assignment_ref"), optional=("classification","reason")),
            "unassign": _variant(("action","team_ref"), optional=("classification","reason")),
            "dissolve": _variant(("action","team_ref"), optional=("classification","reason")),
        },
    ),
    "team_movement_resolution": _spec(
        ("team_ref", "route_id", "destination_id", "traveler_refs", "summary"),
        "Move an authorized exact-team party through a registered route without requiring the command actor to travel with them.",
    ),
    "recovery_resolution": _spec(("actor_ref", "target_time", "policy_ref"), "Resolve exact-person recovery without clearing permanent consequences."),
    "travel_resolution": _spec(
        ("route_id", "destination_id", "traveler_refs"),
        "Travel an exact party through one registered strategic or local route relation.",
        optional=("party_context_ref", "mission_ref"),
    ),
    "population_transfer": _spec(
        ("source_pool_id", "destination_pool_id", "count", "authority_ref"),
        "Conserve aggregate people while moving them between population pools.",
    ),
    "recruitment_resolution": _spec(
        ("source_pool_id", "destination_pool_id", "requested_count", "policy_ref", "authority_ref"),
        "Derive accepted recruitment from an eligible conserved source population.",
    ),
    "person_materialization": _spec(
        ("source_pool_id", "authority_ref", "name", "aliases", "pronouns", "birth_date", "origin", "location_ref", "role_profile_ref", "identity_cues"),
        "Materialize one already-existing aggregate person as sparse exact identity.",
    ),
    "relationship_resolution": _spec(
        ("target_ref", "relationship_type", "interaction_kind", "summary", "visibility"),
        "Apply one persisted social interaction to an exact relationship.",
    ),
    "asset_transfer_resolution": _spec(
        ("item_ref", "from_holder_ref", "to_holder_ref", "transfer_kind", "summary", "visibility"),
        "Transfer custody of one unique named asset without duplication.",
    ),
    "purchase_contract_resolution": _spec(
        ("action", "contract_ref", "buyer_ref", "stock_ref", "item_ref", "quantity", "unit_price_ryo", "summary", "visibility"),
        "Offer, accept, or cancel one negotiated/private purchase contract before conserved stock and currency move.",
        variants={
            "offer": _variant(("action","buyer_ref","stock_ref","item_ref","quantity","unit_price_ryo","summary","visibility")),
            "accept": _variant(("action","contract_ref","summary","visibility")),
            "cancel": _variant(("action","contract_ref","summary","visibility")),
        },
    ),
    "service_purchase_resolution": _spec(
        ("service_ref", "seller_ref", "quantity", "summary", "visibility"),
        "Purchase one registered service at its authoritative public price; ordinary provider revenue may settle to an aggregate local-economy account.",
    ),
    "inventory_resolution": _spec(
        ("action", "item_ref", "quantity", "stock_ref", "holder_ref", "loadout_ref", "contract_ref", "summary", "visibility"),
        "Issue, return, consume, refit, or purchase conserved ordinary stock; open-market retail can buy directly without a contract.",
        variants={
            "issue": _variant(("action","item_ref","quantity","stock_ref","holder_ref","summary","visibility")),
            "return": _variant(("action","item_ref","quantity","stock_ref","holder_ref","summary","visibility")),
            "consume": _variant(("action","item_ref","quantity","holder_ref","summary","visibility")),
            "refit": _variant(("action","stock_ref","holder_ref","loadout_ref","summary","visibility")),
            "purchase": _variant(("action","item_ref","quantity","stock_ref","holder_ref","summary","visibility"), optional=("contract_ref",)),
        },
    ),
    "institution_project_resolution": _spec(
        ("action", "project_ref", "institution_ref", "project_type", "place_ref", "stock_ref", "target_time", "active_hours", "summary", "visibility"),
        "Start, advance to completion, or cancel a real resource-backed institutional facility project.",
        variants={
            "start": _variant(("action","institution_ref","project_type","place_ref","stock_ref","summary","visibility")),
            "advance": _variant(("action","project_ref","institution_ref","target_time","active_hours","summary","visibility")),
            "cancel": _variant(("action","project_ref","institution_ref","summary","visibility")),
        },
    ),
    "medical_treatment_resolution": _spec(
        ("action", "patient_ref", "practitioner_ref", "facility_ref", "injury_ref", "implant_ref", "body_site", "target_time", "active_hours", "summary", "visibility"),
        "Resolve stabilization, treatment, surgery, or biological implant procedures.",
        variants={
            "stabilize": _variant(("action","patient_ref","practitioner_ref","target_time","active_hours","summary","visibility"), optional=("facility_ref","injury_ref")),
            "treat": _variant(("action","patient_ref","practitioner_ref","target_time","active_hours","summary","visibility"), optional=("facility_ref","injury_ref")),
            "surgery": _variant(("action","patient_ref","practitioner_ref","facility_ref","target_time","active_hours","summary","visibility"), optional=("injury_ref","body_site")),
            "implant": _variant(("action","patient_ref","practitioner_ref","facility_ref","implant_ref","body_site","target_time","active_hours","summary","visibility")),
            "remove_implant": _variant(("action","patient_ref","practitioner_ref","facility_ref","implant_ref","target_time","active_hours","summary","visibility"), optional=("body_site",)),
        },
    ),
    "ocular_procedure_resolution": _spec(
        ("action", "eye_ref", "patient_ref", "recipient_ref", "practitioner_ref", "facility_ref", "side", "target_time", "active_hours", "summary", "visibility"),
        "Extract or implant one conserved exact eye through medical storage and surgery.",
        variants={
            "extract": _variant(("action","eye_ref","patient_ref","practitioner_ref","facility_ref","target_time","active_hours","summary","visibility")),
            "implant": _variant(("action","eye_ref","recipient_ref","practitioner_ref","facility_ref","side","target_time","active_hours","summary","visibility")),
        },
    ),
    "career_status_resolution": _spec(
        ("action", "subject_ref", "target_rank_or_status", "institution_ref", "reason", "visibility"),
        "Promote, demote, graduate, retire, or otherwise change institutional career status.",
        variants={
            "promote": _variant(("action","subject_ref","target_rank_or_status","institution_ref","reason","visibility")),
            "demote": _variant(("action","subject_ref","target_rank_or_status","institution_ref","reason","visibility")),
            "graduate": _variant(("action","subject_ref","target_rank_or_status","institution_ref","reason","visibility")),
            "retire": _variant(("action","subject_ref","target_rank_or_status","reason","visibility"), optional=("institution_ref",)),
            "status_change": _variant(("action","subject_ref","target_rank_or_status","institution_ref","reason","visibility")),
        },
    ),
    "office_assignment_resolution": _spec(
        ("action", "subject_ref", "institution_ref", "office_ref", "reason", "visibility"),
        "Appoint, transfer, or remove one exact person from an institutional office.",
        variants={
            "appoint": _variant(("action","subject_ref","institution_ref","office_ref","reason","visibility")),
            "transfer": _variant(("action","subject_ref","institution_ref","office_ref","reason","visibility")),
            "remove": _variant(("action","subject_ref","institution_ref","reason","visibility")),
        },
    ),
    "institution_affiliation_resolution": _spec(
        ("action", "subject_ref", "institution_ref", "relationship_kind", "role", "grade", "reason", "visibility"),
        "Grant, revise, or revoke a secondary institutional affiliation without changing career rank, office, or legal membership.",
        variants={
            "grant": _variant(("action","subject_ref","institution_ref","relationship_kind","role","grade","reason","visibility")),
            "update": _variant(("action","subject_ref","institution_ref","relationship_kind","role","grade","reason","visibility")),
            "revoke": _variant(("action","subject_ref","institution_ref","reason","visibility")),
        },
    ),
    "technique_learning_resolution": _spec(
        ("action", "student_ref", "technique_ref", "teacher_ref", "target_time", "active_hours", "summary", "visibility"),
        "Begin, train, and validate field usability for a technique from authoritative prerequisites and mastery.",
        variants={
            "begin": _variant(("action","student_ref","technique_ref","teacher_ref","summary","visibility")),
            "practice": _variant(("action","student_ref","technique_ref","target_time","active_hours","summary","visibility"), optional=("teacher_ref",)),
            "evaluate": _variant(("action","student_ref","technique_ref","summary","visibility")),
        },
    ),
    "reputation_resolution": _spec(
        ("subject_ref", "audience_id", "source_event_ref", "signal_ref", "summary"),
        "Apply one registered, audience-visible reputation signal from a persisted causal event.",
    ),
    "family_proposal_resolution": _spec(
        ("action", "proposal_ref", "kind", "target_ref", "response", "summary", "visibility"),
        "Create, answer, or withdraw an explicit family/social proposal.",
        variants={
            "propose": _variant(("action","kind","target_ref","summary","visibility")),
            "respond": _variant(("action","proposal_ref","response","summary","visibility")),
            "withdraw": _variant(("action","proposal_ref","summary","visibility")),
        },
    ),
    "family_lifecycle_resolution": _spec(
        ("action", "record_ref", "proposal_ref", "participant_refs", "target_status", "child_ref", "parent_refs", "guardian_refs", "member_refs", "dependent_refs", "property_refs", "institution_refs", "subject_owner_ref", "candidate_order", "relation_kind", "recognition_basis", "summary", "visibility"),
        "Resolve courtship, union, household, parenthood, adoption, guardianship, kinship, or succession state.",
        variants={
            "courtship_start": _variant(("action","proposal_ref","participant_refs","summary","visibility"), optional=("recognition_basis",)),
            "courtship_end": _variant(("action","record_ref","summary","visibility")),
            "union_form": _variant(("action","proposal_ref","participant_refs","summary","visibility"), optional=("recognition_basis",)),
            "union_status": _variant(("action","record_ref","target_status","summary","visibility")),
            "household_form": _variant(("action","member_refs","summary","visibility"), optional=("dependent_refs","property_refs","institution_refs")),
            "household_update": _variant(("action","record_ref","target_status","member_refs","summary","visibility"), optional=("dependent_refs","property_refs","institution_refs")),
            "parenthood_begin": _variant(("action","parent_refs","summary","visibility"), optional=("recognition_basis",)),
            "parenthood_end": _variant(("action","record_ref","summary","visibility")),
            "adoption": _variant(("action","child_ref","parent_refs","summary","visibility"), optional=("record_ref",)),
            "guardianship": _variant(("action","child_ref","guardian_refs","summary","visibility"), optional=("record_ref",)),
            "kinship_record": _variant(("action","participant_refs","relation_kind","summary","visibility"), optional=("recognition_basis",)),
            "succession_set": _variant(("action","subject_owner_ref","candidate_order","summary","visibility"), optional=("record_ref","target_status","recognition_basis")),
        },
    ),
    "family_birth_resolution": _spec(
        ("parenthood_ref", "destination_pool_id", "gestational_parent_ref", "name", "pronouns", "origin", "location_ref", "household_ref", "summary", "visibility"),
        "Resolve one due live birth as one new physical body counted in aggregate population and sparse exact identity.",
    ),
    "information_claim_resolution": _spec(
        ("claim_id", "subject_ref", "source_ref", "holder_ref", "epistemic_kind", "confidence_milli", "evidence_refs", "context_ref"),
        "Create one provenance-backed information claim for a lawful knower.",
    ),
    "information_delivery": _spec(
        ("claim_id", "sender_ref", "recipient_ref", "channel", "channel_confidence_milli"),
        "Deliver an existing claim from a current knower to a recipient through a declared channel.",
    ),
    "scene_boundary_resolution": _spec(
        ("action_kind", "subject_ref", "target_ref", "boundary_event_id", "summary", "visibility"),
        "Resolve one due player-facing scene boundary without silently choosing a player decision.",
    ),
    "commitment_resolution": _spec(
        ("commitment_id", "kind", "subject_ref", "summary", "visibility"),
        "Create one persisted promise, order, or obligation with lawful authority.",
        optional=("target_ref", "host_ref", "due_at"),
    ),
    "commitment_transition": _spec(("commitment_id", "target_status", "summary"), "Complete, fail, or cancel an active commitment."),
    "conflict_resolution": _spec(
        ("action", "conflict_ref", "name", "side_refs", "objectives", "front_ref", "front_name", "place_refs", "route_refs", "formation_ref", "control_ref", "route_id", "route_status", "disruption_milli", "place_ref", "evidence_event_ref", "target_time", "active_hours", "summary"),
        "Start or update a bounded strategic conflict: fronts, formation assignments, route control, derived supply pressure, occupation, ceasefire, and conclusion.",
        variants={
            "start": _variant(("action","conflict_ref","name","side_refs","objectives"), optional=("summary",)),
            "open_front": _variant(("action","conflict_ref","front_ref","front_name","place_refs","route_refs"), optional=("summary",)),
            "assign_formation": _variant(("action","conflict_ref","front_ref","formation_ref"), optional=("summary",)),
            "unassign_formation": _variant(("action","conflict_ref","front_ref","formation_ref"), optional=("summary",)),
            "route_control": _variant(("action","conflict_ref","front_ref","route_id","route_status","disruption_milli","evidence_event_ref"), optional=("control_ref","summary")),
            "fortify": _variant(("action","conflict_ref","front_ref","formation_ref","target_time","active_hours"), optional=("summary",)),
            "occupy": _variant(("action","conflict_ref","front_ref","place_ref","control_ref","evidence_event_ref"), optional=("summary",)),
            "close_front": _variant(("action","conflict_ref","front_ref"), optional=("summary",)),
            "ceasefire": _variant(("action","conflict_ref"), optional=("summary",)),
            "resume": _variant(("action","conflict_ref"), optional=("summary",)),
            "end": _variant(("action","conflict_ref"), optional=("summary",)),
        },
    ),
    "custody_resolution": _spec(
        ("action", "custody_ref", "subject_ref", "force_ref", "count", "custodian_ref", "place_ref", "new_custodian_ref", "new_place_ref", "summary", "visibility"),
        "Detain, transfer, release, exchange, or attempt escape for exact or aggregate prisoners using real custody capacity and security.",
        variants={
            "detain": _variant(("action","custody_ref","custodian_ref","place_ref","summary","visibility"), optional=("subject_ref","force_ref","count")),
            "transfer": _variant(("action","custody_ref","new_custodian_ref","new_place_ref","summary","visibility")),
            "release": _variant(("action","custody_ref","summary","visibility")),
            "exchange": _variant(("action","custody_ref","summary","visibility")),
            "escape": _variant(("action","custody_ref","summary","visibility")),
        },
    ),
    "special_combat_state_resolution": _spec(
        ("action", "actor_ref", "gate", "target_state", "entity_ref"),
        "Activate or release bounded exact-combat support state: Eight Gates, jinchuriki transformation, puppets, or summons.",
        variants={
            "open_gate": _variant(("action","actor_ref","gate")),
            "close_gates": _variant(("action","actor_ref")),
            "jinchuriki_transform": _variant(("action","actor_ref","target_state")),
            "puppet_deploy": _variant(("action","actor_ref","entity_ref")),
            "puppet_withdraw": _variant(("action","actor_ref","entity_ref")),
            "summon_call": _variant(("action","actor_ref","entity_ref")),
            "summon_dismiss": _variant(("action","actor_ref","entity_ref")),
        },
    ),
    "combat_resolution": _spec(
        ("combat_id", "scale", "participants", "objectives"),
        "Resolve exact duel/skirmish or aggregate formation/battle combat through deterministic kernels.",
        optional=("mission_ref", "location_ref", "parent_combat_ref"),
        hints={
            "scale": "duel|skirmish|formation|battle",
            "participants": "exact: [{actor_ref,side_ref,action,target_refs,objective_ref,lethal}] | aggregate: [{participant_ref:'formation:<id>',committed_count,side_ref,action,target_refs,objective_ref,lethal,command_authority_ref,named_actor_refs}]",
            "objectives": "[{objective_ref,side_ref,kind,target_refs,zone_ref,deadline_tick}]",
            "parent_combat_ref": "required only for exact combat that resolves actors reserved by an aggregate parent",
        },
    ),
    "force_assignment_resolution": _spec(
        ("assignment_id", "force_ref", "grantor_ref", "recipient_ref", "allocated_count", "source_availability_class", "operational_attachment_ref", "authority_limits", "expires_at"),
        "Delegate a conserved slice of force authority/manpower responsibility to a commander.",
    ),
    "force_assignment_transition": _spec(("assignment_id", "target_status", "reason"), "Release, revoke, or complete an active force assignment."),
    "formation_movement_resolution": _spec(
        ("formation_ref", "route_id", "destination_id"),
        "Move one operational formation through a registered route while advancing campaign time; embedded exact teams move with it.",
        optional=("operational_attachment_ref", "movement_posture"),
        hints={"movement_posture": "standard|forced|cautious"},
    ),
    "formation_lifecycle_resolution": _spec(
        ("action", "force_ref", "formation_ref", "secondary_formation_ref", "formation_size", "target_personnel", "split_personnel", "max_operational_personnel", "operational_attachment_ref", "location_ref"),
        "Mobilize, drill, reconstitute, release, split, or merge conserved operational formations. New formations appear at the force-owned mobilization anchor; later relocation uses formation_movement_resolution.",
        variants={
            "mobilize": _variant(("action","force_ref","formation_size"), optional=("max_operational_personnel","operational_attachment_ref")),
            "drill": _variant(("action","formation_ref"), optional=("operational_attachment_ref",)),
            "reconstitute": _variant(("action","formation_ref"), optional=("target_personnel","max_operational_personnel","operational_attachment_ref")),
            "release": _variant(("action","formation_ref"), optional=("operational_attachment_ref",)),
            "split": _variant(("action","formation_ref","split_personnel"), optional=("operational_attachment_ref",)),
            "merge": _variant(("action","formation_ref","secondary_formation_ref"), optional=("operational_attachment_ref",)),
        },
    ),
}
