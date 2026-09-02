"""Deterministic mechanics for the original Jianghu campaign."""

from .agriculture import climate_suitability_milli, harvest_quote
from .events import calendar_events_between, tournament_bracket
from .economy import base_value_cash, lot_value_cash, market_price_cash
from .production import consume_inputs, medicine_dose_effect, medicine_quote, workshop_quote
from .equipment import (
    bow_shot_profile,
    sensory_obstruction,
    encumbrance_effects,
    projectile_contact_profile,
    transition_seconds,
    weapon_contact_profile,
    stealth_noise_milli,
)
from .infrastructure import (building_upgrade_quote, building_upgrade_requirements, consume_building_start, building_expansion_quote, building_expansion_requirements, start_building_expansion, advance_building_expansion, estate_land_summary, facility_physical_effects, enterprise_upgrade_quote, enterprise_upgrade_requirements, enterprise_scale_expansion_quote, enterprise_scale_expansion_requirements, start_enterprise_scale_expansion, advance_enterprise_scale_expansion, enterprise_scale_value, enterprise_scale_basis)
from .recruitment import background_for_ordinal, deterministic_candidate, screening_report
from .training import training_gain_milli
from .travel import shortest_route, travel_plan
from .weather import weather_snapshot

from .medicine import administer_dose, active_recovery_modifiers, blank_medicine_state, settle_medicine_state
from .regional_economy import execute_purchase, initial_market_state, quote_purchase, region_for_place, settle_cycles, unit_market_price_cash
from .contracts import escort_quote, settle_payment, transition as contract_transition
from .outlaws import attack_decision as outlaw_attack_decision, route_threat_score
from .factions import autonomy_review, derived_scale
from .staffing import capability_vector, select_party
from .handoffs import classify_handoff
from .procedures import procedure_duration_minutes

from .people import person_lite
from .upkeep import monthly_upkeep_quote
from .scheduler import due_events, initial_schedule

from .life_course import biological_aging_rate, inherited_appearance, inherited_aptitudes, natural_lifespan_years
from .poison import apply_poison, settle_poison
from .government import attention_from_evidence, mobilization
from .rankings import public_score, publish_rankings
from .tournaments import close_registration, open_tournament, register as register_tournament
from .upkeep import monthly_upkeep_quote

from .combat import active_defense_available, allocate_qi, commit_active_defense, control_efficiency_milli, redistribution_latency_ms, safe_qi_flow_milli_per_second
from .health import blood_volume_ml, functional_penalties, lethal_state, recovery_advance, settle_physiology, wound_from_contact

__all__ = [
    "bow_shot_profile",
    "building_upgrade_requirements",
    "building_expansion_quote",
    "building_expansion_requirements",
    "start_building_expansion",
    "advance_building_expansion",
    "estate_land_summary",
    "facility_physical_effects",
    "sensory_obstruction",
    "deterministic_candidate",
    "encumbrance_effects",
    "enterprise_upgrade_requirements",
    "enterprise_scale_expansion_quote",
    "enterprise_scale_expansion_requirements",
    "start_enterprise_scale_expansion",
    "advance_enterprise_scale_expansion",
    "enterprise_scale_value",
    "enterprise_scale_basis",
    "projectile_contact_profile",
    "transition_seconds",
    "weapon_contact_profile",
    "shortest_route",
    "stealth_noise_milli",
    "training_gain_milli",
    "travel_plan",
    "weather_snapshot",
    "base_value_cash",
    "climate_suitability_milli",
    "consume_inputs",
    "harvest_quote",
    "lot_value_cash",
    "market_price_cash",
    "medicine_quote",
    "workshop_quote",
    "building_upgrade_quote",
    "consume_building_start",
    "enterprise_upgrade_quote",
    "background_for_ordinal",
    "screening_report",
    "calendar_events_between",
    "tournament_bracket",
    "medicine_dose_effect",
    "administer_dose",
    "active_recovery_modifiers",
    "blank_medicine_state",
    "settle_medicine_state",
    "execute_purchase",
    "initial_market_state",
    "quote_purchase",
    "region_for_place",
    "settle_cycles",
    "unit_market_price_cash",
    "escort_quote",
    "settle_payment",
    "contract_transition",
    "outlaw_attack_decision",
    "route_threat_score",
    "autonomy_review",
    "derived_scale",
    "capability_vector",
    "select_party",
    "classify_handoff",
    "procedure_duration_minutes",
    "person_lite",
    "monthly_upkeep_quote",
    "due_events",
    "initial_schedule",
    "biological_aging_rate",
    "inherited_appearance",
    "inherited_aptitudes",
    "natural_lifespan_years",
    "apply_poison",
    "settle_poison",
    "attention_from_evidence",
    "mobilization",
    "public_score",
    "publish_rankings",
    "close_registration",
    "open_tournament",
    "register_tournament",
    "active_defense_available",
    "allocate_qi",
    "commit_active_defense",
    "control_efficiency_milli",
    "redistribution_latency_ms",
    "safe_qi_flow_milli_per_second",
    "blood_volume_ml",
    "functional_penalties",
    "lethal_state",
    "recovery_advance",
    "settle_physiology",
    "wound_from_contact",
]

from .faction_relations import apply_relation_event, diplomacy_score, evaluate_proposal
from .membership import grade_eligibility, office_candidate_score, select_office_candidate
from .route_activity import route_exposure, local_travel_minutes
from .crime_custody import crime_attention, create_custody_record, custody_transition
from .equipment_lifecycle import expend_ammunition, apply_wear, repair_quote
from .family_life import courtship_eligible, marriage_eligible, due_birth_at, child_identity
from .field_command import build_deployment_structure, command_score, formation_kind_for_headcount, validate_deployment_structure

from .combat_friendly_line_safety import install as _install_combat_friendly_line_safety
_install_combat_friendly_line_safety()
