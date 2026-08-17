"""Universal identity cores and cohort-backed person composition."""

from .core import (
    PersonCore,
    PersonSheet,
    assemble_sheet,
    core_from_exact,
    core_from_person,
    core_from_registry,
)
from .cohorts import CohortRoster, cohort_slot_baseline
from .repository import RepositoryPersonSheetResolver, repository_sheet_resolver
from .repertoire import field_usable_method_refs

__all__ = [
    "CohortRoster",
    "PersonCore",
    "PersonSheet",
    "assemble_sheet",
    "core_from_exact",
    "core_from_person",
    "core_from_registry",
    "cohort_slot_baseline",
    "RepositoryPersonSheetResolver",
    "repository_sheet_resolver",
    "field_usable_method_refs",
]

