"""Integrated bounded living-world behavior for exact teams and autonomous factions.

This layer connects already-authoritative domain mechanics rather than creating a
second NPC-only game: exact-team composition/doctrine, team training, health and
population reconciliation, inventory conservation, information, reputation and
relationship state are reused in one scheduled autonomous transaction.
"""
from __future__ import annotations

import copy
import hashlib
import re
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.autonomy import AutonomousDecision, AutonomousPolicyBook
from shinobi_runtime.combat import PersonnelState
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.commands.paths import (
    DEVELOPMENT_BANK_PATH,
    INFORMATION_INDEX_PATH,
    INVENTORY_REGISTRY_PATH,
    POPULATION_REGISTRY_PATH,
    RELATIONSHIP_INDEX_PATH,
    RELATIONSHIP_RULES_PATH,
    REPUTATION_INDEX_PATH,
    REPUTATION_MECHANICS_PATH,
    REPUTATION_SIGNALS_PATH,
)
from shinobi_runtime.commands.team_composition import capability_profile_from_record, derive_member_roles, select_complementary_roster
from shinobi_runtime.domain import ReputationEvidence, update_axis
from shinobi_runtime.reducers import TrainingInputs, apply_personnel_effect, settle_training
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry, HostState, SchedulerHost, recurring_event


_RELATIONSHIP_ROOT = "state/reg/relationship-edges"
_TEAM_HISTORY_ROOT = "state/team/history"
_FACTION_MEMORY_ROOT = "state/autonomy/faction-memory"
_MAX_TEAM_HISTORY_EVENTS = 24
_MAX_REPORT_HISTORY = 32
_CONSUMABLE_PRIORITY = (
    "item_explosive_tag",
    "item_smoke_bomb",
    "item_medical_dressing",
    "item_field_ration_1day",
    "item_basic_antidote",
    "weapon_senbon",
    "weapon_shuriken",
    "weapon_kunai",
)
_OBJECTIVE_DIMENSIONS = {
    "observe": ("reconnaissance", "stealth"),
    "identify": ("reconnaissance", "control"),
    "investigate": ("reconnaissance", "stealth"),
    "protect": ("support", "control", "assault"),
    "escort": ("support", "mobility", "reconnaissance"),
    "deliver": ("mobility", "stealth", "reconnaissance"),
    "recover": ("reconnaissance", "mobility", "support"),
    "rescue": ("support", "assault", "mobility"),
    "secure": ("control", "assault", "support"),
    "capture": ("capture", "control", "mobility"),
    "restrain": ("capture", "control"),
    "sabotage": ("engineering", "stealth", "control"),
    "conceal": ("stealth", "reconnaissance"),
}
_ROLE_TRAINING_TARGETS = (
    (("recon", "sensor", "track", "scout"), "operational_skills.investigation"),
    (("control", "restraint", "capture"), "operational_skills.tactics"),
    (("medical", "support", "recovery"), "operational_skills.medicine"),
    (("engineer", "trap", "demolition", "sabotage"), "operational_skills.traps"),
    (("assault", "frontline", "sword", "strike"), "martial_skills.movement"),
    (("infiltration", "stealth"), "operational_skills.infiltration"),
)


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9._-]+", "_", value.lower()).strip("._-")
    return clean[:96] or hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _stable_roll(*parts: object, modulo: int = 100) -> int:
    data = "\x00".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big") % max(1, modulo)


# The living-world modules intentionally use explicit star imports from this
# one support surface.  Keep private-looking shared constants/helpers here in
# __all__ so refactors cannot silently drop them from sibling modules.
__all__ = [
    "copy", "hashlib", "re", "Decimal", "SimpleNamespace",
    "Any", "Dict", "Mapping", "Optional", "Sequence", "Tuple",
    "CommandRejectedError", "AutonomousDecision", "AutonomousPolicyBook",
    "_OwnerResolutionCache", "CommandEnvelope", "MissionOwner", "mission_owner_path",
    "DEVELOPMENT_BANK_PATH", "INFORMATION_INDEX_PATH", "INVENTORY_REGISTRY_PATH",
    "POPULATION_REGISTRY_PATH", "RELATIONSHIP_INDEX_PATH", "RELATIONSHIP_RULES_PATH",
    "REPUTATION_INDEX_PATH", "REPUTATION_MECHANICS_PATH", "REPUTATION_SIGNALS_PATH",
    "capability_profile_from_record", "derive_member_roles", "select_complementary_roster",
    "ReputationEvidence", "update_axis", "PersonnelState", "TrainingInputs",
    "apply_personnel_effect", "settle_training", "CampaignTime", "CausalSchedulerRegistry",
    "HostState", "SchedulerHost", "recurring_event", "_RELATIONSHIP_ROOT",
    "_TEAM_HISTORY_ROOT", "_FACTION_MEMORY_ROOT",
    "_MAX_TEAM_HISTORY_EVENTS", "_MAX_REPORT_HISTORY",
    "_CONSUMABLE_PRIORITY", "_OBJECTIVE_DIMENSIONS", "_ROLE_TRAINING_TARGETS",
    "_slug", "_stable_roll",
]
