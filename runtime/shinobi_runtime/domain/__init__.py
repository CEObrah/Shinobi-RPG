"""Generic domain services shared by Shinobi gameplay planners.

These services encode causal concepts such as membership, authority, locations,
claims and commitments. They deliberately do not know campaign-specific names.
"""

from .authority import AuthorityDecision, DomainAuthorityResolver
from .locations import LocationGraph
from .reputation import ReputationEvidence, update_axis

__all__ = ["AuthorityDecision", "DomainAuthorityResolver", "LocationGraph", "ReputationEvidence", "update_axis"]
