"""Composed living-world autonomy mixin."""
from .living_world_policy import LivingWorldPolicyMixin
from .living_world_mission_briefing import LivingWorldMissionBriefingMixin
from .living_world_mission import LivingWorldMissionMixin
from .living_world_consequences import LivingWorldConsequencesMixin
from .living_world_social import LivingWorldSocialMixin
from .living_world_training import LivingWorldTrainingMixin
from .living_world_academy import LivingWorldAcademyMixin
from .living_world_assignment import LivingWorldAssignmentMixin
from .living_world_history import LivingWorldHistoryMixin
from .living_world_house_exact import LivingWorldHouseExactMixin
from .living_world_house import LivingWorldHouseMixin


class LivingWorldIntelligenceMixin(
    LivingWorldPolicyMixin,
    LivingWorldMissionBriefingMixin,
    LivingWorldMissionMixin,
    LivingWorldConsequencesMixin,
    LivingWorldSocialMixin,
    LivingWorldTrainingMixin,
    LivingWorldAcademyMixin,
    LivingWorldAssignmentMixin,
    LivingWorldHistoryMixin,
    LivingWorldHouseExactMixin,
    LivingWorldHouseMixin,
):
    """Integrated bounded living-world behavior over generic domain mechanics."""


__all__ = ["LivingWorldIntelligenceMixin"]
