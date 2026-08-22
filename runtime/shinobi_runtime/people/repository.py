"""Persistent Jianghu person-sheet resolution through deterministic roster routes."""
from __future__ import annotations
import copy
from datetime import datetime
from typing import Any, Mapping, Optional
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.martial_world.health import combat_status_families, functional_penalties, vision_state
from shinobi_runtime.martial_world.live_state import roster_person
from shinobi_runtime.martial_world.faction_state import read_faction, roster_path
from shinobi_runtime.martial_world.person_state import hydrate_roster_state
from shinobi_runtime.martial_world.training import advance_faction_training_epoch, apply_institutional_training


def _campaign_time_iso(value: Any) -> str:
    """Normalize persisted campaign chronology for Jianghu domain mechanics."""
    text = str(value or "")
    if text.startswith("SE-"):
        text = text[3:]
    try:
        datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("campaign time invalid for person-sheet projection") from exc
    return text


class RepositoryPersonSheetResolver:
    def __init__(self, repository: RepositoryStore) -> None:
        self.repository = repository

    def _standing_retinues_for_player(self, person_id: str) -> list[dict[str, Any]]:
        try:
            meta = self.repository.read_json("state/meta.json")
            if not isinstance(meta, Mapping) or meta.get("player_id") != person_id:
                return []
            state = self.repository.read_json("state/martial-world/deployments.json")
        except FileNotFoundError:
            return []
        rows = state.get("deployments", {}) if isinstance(state, Mapping) else {}
        if not isinstance(rows, Mapping):
            return []
        out: list[dict[str, Any]] = []
        for retinue_ref in sorted(str(ref) for ref in rows if isinstance(ref, str)):
            row = rows.get(retinue_ref)
            if not isinstance(row, Mapping) or row.get("operation_kind") != "standing_retinue" or row.get("leader_ref") != person_id:
                continue
            status = str(row.get("status") or "")
            if status not in {"assignment_pending", "assignment_blocked", "active"}:
                continue
            member_refs = [str(ref) for ref in row.get("member_refs", []) if isinstance(ref, str)] if isinstance(row.get("member_refs"), list) else []
            member_roles_raw = row.get("member_roles", {}) if isinstance(row.get("member_roles"), Mapping) else {}
            out.append({
                "retinue_ref": retinue_ref,
                "status": status,
                "chooser_ref": row.get("chooser_ref"),
                "requested_count": max(0, int(row.get("requested_count", 0))),
                "member_refs": member_refs[:8],
                "member_roles": {ref: str(member_roles_raw.get(ref) or "") for ref in member_refs[:8]},
                "requested_at": row.get("requested_at"),
                "assigned_at": row.get("assigned_at"),
                "assignment_reviewed_at": row.get("assignment_reviewed_at"),
                "assignment_blocked_reason": row.get("assignment_blocked_reason"),
                "training_policy": row.get("training_policy"),
            })
            if len(out) >= 8:
                break
        return out

    def _project_institutional_training(self, person: Mapping[str, Any]) -> dict[str, Any]:
        """Return a read-only logical sheet caught up to current campaign time.

        Persistent faction training is intentionally lazy.  Stored faction epochs
        only need to advance at causal write frontiers, but a current person-sheet
        read must not expose a capability snapshot that is days behind the campaign
        clock.  Advance an in-memory faction/roster copy through ``state/meta.json``
        and materialize the person's pending institutional gains without writing
        either owner.
        """
        out = copy.deepcopy(dict(person))
        faction_ref = out.get("faction_ref")
        if not isinstance(faction_ref, str) or not faction_ref:
            return out
        meta = self.repository.read_json("state/meta.json")
        if not isinstance(meta, Mapping):
            raise ValueError("campaign meta invalid for person-sheet projection")
        at_iso = _campaign_time_iso(meta.get("time"))
        _fpath, faction = read_faction(self.repository, faction_ref)
        raw_roster = self.repository.read_json(roster_path(faction_ref))
        if not isinstance(raw_roster, Mapping):
            raise ValueError("jianghu roster invalid for person-sheet projection")
        roster = hydrate_roster_state(raw_roster, faction=faction)
        advanced, _summary = advance_faction_training_epoch(
            faction,
            roster,
            at_iso=at_iso,
            refresh_environment=False,
        )
        people = roster.get("people", [])
        if not isinstance(people, list):
            raise ValueError("jianghu roster people invalid for person-sheet projection")
        return apply_institutional_training(
            out,
            faction=advanced,
            roster_people=[row for row in people if isinstance(row, Mapping)],
        )

    def __call__(self, person_id: str) -> Optional[Mapping[str, Any]]:
        try:
            _path, _roster, _ordinal, person = roster_person(self.repository, person_id)
        except (FileNotFoundError, KeyError):
            return None
        person = self._project_institutional_training(person)
        person.pop("__state_defaults", None)
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
        person["derived_condition"] = {
            "vision": vision_state(wounds),
            "functional_penalties": functional_penalties(wounds),
            "combat_status_families": list(combat_status_families(wounds)),
        }
        retinues = self._standing_retinues_for_player(person_id)
        if retinues:
            person["standing_retinues"] = retinues
        return person
