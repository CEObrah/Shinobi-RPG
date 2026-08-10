"""Shared semantic-command constants.

These are current gameplay/runtime invariants, not release/version metadata.
Domain modules import them so command behavior has one source of truth.
"""

ACTIVE_PRESSURE_STATUSES = frozenset(("active", "active_hidden", "latent_active"))
QUIESCENT_CONTINUOUS_SOURCES = frozenset(("house_training_and_life", "team_training"))
MISSION_TRANSITION_TARGETS = frozenset(("accepted", "active", "resolving", "aborted"))
OBJECTIVE_TARGETS = frozenset(("in_progress", "succeeded", "failed"))
TERMINAL_MISSION_STATES = frozenset(("succeeded", "failed", "aborted", "expired"))
TERMINAL_WORLD_EVENT_STATES = frozenset(("resolved", "failed"))
OBJECTIVE_EVIDENCE_EVENT_KINDS = {
    "reach": frozenset(("travel_completed",)),
    "observe": frozenset(("information_claim_created", "information_delivered")),
    "identify": frozenset(("information_claim_created", "information_delivered")),
    "investigate": frozenset(("information_claim_created", "information_delivered")),
    "intercept": frozenset(("combat_resolved", "aggregate_combat_resolved")),
    "recover": frozenset(("asset_transferred", "combat_resolved", "aggregate_combat_resolved")),
    "deliver": frozenset(("asset_transferred", "information_delivered", "travel_completed")),
    "secure": frozenset(("combat_resolved", "aggregate_combat_resolved", "asset_transferred")),
    "protect": frozenset(("combat_resolved", "aggregate_combat_resolved")),
    "preserve": frozenset(("combat_resolved", "aggregate_combat_resolved", "asset_transferred")),
    "escort": frozenset(("travel_completed", "combat_resolved", "aggregate_combat_resolved")),
    "rescue": frozenset(("combat_resolved", "aggregate_combat_resolved", "travel_completed")),
    "capture": frozenset(("combat_resolved", "aggregate_combat_resolved")),
    "restrain": frozenset(("combat_resolved", "aggregate_combat_resolved")),
    "defeat": frozenset(("combat_resolved", "aggregate_combat_resolved")),
    "hold": frozenset(("combat_resolved", "aggregate_combat_resolved")),
    "destroy": frozenset(("combat_resolved", "aggregate_combat_resolved")),
    "sabotage": frozenset(("combat_resolved", "aggregate_combat_resolved", "asset_transferred")),
    "extract": frozenset(("travel_completed", "combat_resolved", "aggregate_combat_resolved")),
    "escape": frozenset(("travel_completed", "combat_resolved", "aggregate_combat_resolved")),
    "survive": frozenset(("combat_resolved", "aggregate_combat_resolved")),
    "negotiate": frozenset(("relationship_changed", "information_delivered", "commitment_promise")),
    "conceal": frozenset(("information_claim_created", "information_delivered", "combat_resolved")),
    "prevent": frozenset(("combat_resolved", "aggregate_combat_resolved", "information_claim_created")),
}
TRAINABLE_ROOTS = frozenset(
    (
        "attributes",
        "chakra_dimensions",
        "domain_proficiencies",
        "martial_skills",
        "operational_skills",
        "repertoire.method_mastery",
    )
)
