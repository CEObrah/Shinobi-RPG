"""Player-facing physiology projection parity for legacy Jianghu wounds.

Exact combat already applies the production legacy-trauma fallback installed by
``combat_hardening``.  This module keeps the read-only person-sheet projection on
the same derived-physiology path so an old severe wound cannot mechanically
impair a fighter while the GM-facing sheet still reports zero functional loss.
No campaign owner is rewritten.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.combat_hardening import CombatHardenedCampaignOperations
from shinobi_runtime.martial_world import health


def project_person_sheet_functional_penalties(result: Mapping[str, Any]) -> dict[str, Any]:
    """Refresh only the derived functional-penalty view from current injuries.

    Stored injury rows remain byte-for-byte campaign truth.  The fallback is a
    compatibility derivation for legacy rows whose historical
    ``function_loss_pct`` may be stale.  Side-specific fields remain zero when
    the committed wound itself has no lawful side.
    """

    out = copy.deepcopy(dict(result))
    sheet = out.get("sheet")
    if not isinstance(sheet, Mapping):
        return out
    health_state = sheet.get("health")
    derived = sheet.get("derived_condition")
    if not isinstance(health_state, Mapping) or not isinstance(derived, Mapping):
        return out
    injuries = health_state.get("injuries")
    if not isinstance(injuries, list):
        return out

    current_wounds = [row for row in injuries if isinstance(row, Mapping)]
    updated_sheet = copy.deepcopy(dict(sheet))
    updated_derived = copy.deepcopy(dict(derived))
    updated_derived["functional_penalties"] = dict(
        health.functional_penalties(current_wounds)
    )
    updated_sheet["derived_condition"] = updated_derived
    out["sheet"] = updated_sheet
    return out


class PhysiologyProjectedCampaignOperations(CombatHardenedCampaignOperations):
    """Production operations whose person reads match exact-combat physiology."""

    def person_sheet(self, person_id: str) -> Mapping[str, Any]:
        return project_person_sheet_functional_penalties(
            super().person_sheet(person_id)
        )


__all__ = [
    "PhysiologyProjectedCampaignOperations",
    "project_person_sheet_functional_penalties",
]
