from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from shinobi_runtime.api.contracts import basic_ooc_audit
from shinobi_runtime.api.operations import CampaignOperations
from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner
from shinobi_runtime.martial_world import exact_combat as exact
from shinobi_runtime.martial_world.faction_state import read_faction
from shinobi_runtime.people import RepositoryPersonSheetResolver
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.tx import GitStager, ReceiptStore, TransactionCoordinator, WriteAheadLog


ROOT = Path(__file__).resolve().parents[2]


def test_current_active_combat_builds_complete_gm_npc_judgment_frontier() -> None:
    """The packaged r108 Black Lance fight must expose every fresh NPC choice."""
    repository = RepositoryStore(ROOT)
    meta = repository.read_json("state/meta.json")
    combats = repository.read_json("state/martial-world/combats.json")
    equipment_ledger = repository.read_json("state/martial-world/equipment-ledger.json")
    social = repository.read_json("state/martial-world/social.json")

    active = [
        row
        for row in combats.get("combats", {}).values()
        if isinstance(row, Mapping) and row.get("status") == "active"
    ]
    assert len(active) == 1
    combat = active[0]
    player_ref = str(meta["player_id"])

    resolver = RepositoryPersonSheetResolver(repository)
    participant_refs = {
        str(ref)
        for members in combat.get("sides", {}).values()
        if isinstance(members, list)
        for ref in members
        if isinstance(ref, str)
    }
    people = {}
    doctrines = {}
    for ref in sorted(participant_refs):
        person = resolver(ref)
        assert isinstance(person, Mapping), ref
        people[ref] = person
        faction_ref = str(person.get("faction_ref") or "")
        if faction_ref and faction_ref not in doctrines:
            _path, faction = read_faction(repository, faction_ref)
            doctrines[faction_ref] = (
                faction.get("doctrine", {}) if isinstance(faction, Mapping) else {}
            )

    rows = exact.combat_npc_judgment_envelopes(
        combat=combat,
        people=people,
        equipment_ledger=equipment_ledger,
        doctrines=doctrines,
        player_ref=player_ref,
        martial_familiarity=social,
    )

    scheduled = {
        str(row.get("actor_ref"))
        for row in combat.get("scheduled_actions", [])
        if isinstance(row, Mapping) and isinstance(row.get("actor_ref"), str)
    }
    expected = {
        ref
        for ref in participant_refs
        if ref != player_ref
        and ref not in scheduled
        and ref in combat.get("combatants", {})
        and exact._active(people[ref], combat["combatants"][ref])
    }
    assert {str(row["actor_ref"]) for row in rows} == expected
    assert all(row.get("requires_llm_authored_intent") is True for row in rows)


def test_current_play_context_exposes_complete_private_npc_frontier(tmp_path) -> None:
    """Live play context must not swallow a valid exact-combat frontier."""
    repository = RepositoryStore(ROOT)
    meta = repository.read_json("state/meta.json")
    player_ref = str(meta["player_id"])
    coordinator = TransactionCoordinator(
        repository,
        GitStager(ROOT),
        WriteAheadLog(tmp_path / "wal"),
        ReceiptStore(tmp_path / "receipts"),
        lock_path=tmp_path / "writer.lock",
        lock_timeout=1.0,
    )
    operations = CampaignOperations(
        repository=repository,
        coordinator=coordinator,
        command_planner=CampaignCommandPlanner(repository),
        sheet_resolver=RepositoryPersonSheetResolver(repository),
        audit_provider=basic_ooc_audit,
        allowed_actor_ids=frozenset({player_ref}),
        lock_timeout_seconds=1.0,
    )

    context = operations.play_context()
    scene = context["scene"]
    private = scene["gm_private_director_context"]["combat"]
    rows = private["npc_judgment_envelopes"]
    assert private["npc_judgment_contract"]["meaningful_choice_owner"] == "chatgpt"
    assert len(rows) == 29
    assert all(row.get("requires_llm_authored_intent") is True for row in rows)
