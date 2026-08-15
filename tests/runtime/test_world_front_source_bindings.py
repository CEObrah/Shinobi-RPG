from __future__ import annotations

import copy
import json
from pathlib import Path

from shinobi_runtime.commands.world_front_evidence import apply_event_evidence

ROOT = Path(__file__).resolve().parents[2]


def _rules() -> dict:
    return json.loads((ROOT / "game/rules/autonomy/world-fronts.json").read_text())


def _registry() -> dict:
    return copy.deepcopy(json.loads((ROOT / "state/canon/pressures.json").read_text()))


def _event(event_id: str, kind: str, *, hosts=(), actors=(), material=(), classification="restricted") -> dict:
    return {
        "id": event_id,
        "kind": kind,
        "host_refs": list(hosts),
        "actor_refs": list(actors),
        "material_consequence_refs": list(material),
        "timing": {"occurred_at": "SE-0061-06-11T09:00:00"},
        "visibility": {"classification": classification, "audience_refs": [], "witness_refs": []},
        "provenance": {"source_refs": list(hosts)},
    }


def _update_for(front_id: str, event: dict) -> tuple[dict, dict]:
    registry = _registry()
    updates = apply_event_evidence(registry=registry, rules=_rules(), event=event, player_ref="pc_wei_tang")
    match = next(row for row in updates if row["front_id"] == front_id)
    return match, registry["pressures"][front_id]


def test_suna_funding_front_requires_real_treasury_gap_and_preserves_resource_provenance() -> None:
    event = _event(
        "event.test.suna.gap", "economy_period_settled",
        hosts=("host.world.economy",),
        material=("funding_gap:treasury.suna:250000", "finance:treasury.suna:expected_out:500000"),
    )
    update, pressure = _update_for("pressure_suna_funding", event)
    assert update["phase_after"] == "developing"
    assert "event.test.suna.gap" in pressure["evidence_refs"]
    assert "host.world.economy" in pressure["source_refs"]
    assert "funding_gap:treasury.suna:250000" in pressure["resources"]

    no_gap = _event("event.test.suna.no_gap", "economy_period_settled", hosts=("host.world.economy",))
    registry = _registry()
    updates = apply_event_evidence(registry=registry, rules=_rules(), event=no_gap, player_ref="pc_wei_tang")
    assert all(row["front_id"] != "pressure_suna_funding" for row in updates)


def test_academy_graduation_front_requires_actual_graduation_event() -> None:
    event = _event(
        "event.test.academy.graduate", "academy_exact_graduation_recorded",
        hosts=("institution.konoha.academy", "force.konoha.shinobi"),
        actors=("canon_naruto",),
        material=("mission.academy.graduation",),
        classification="public",
    )
    _update, pressure = _update_for("pressure_konoha_academy_graduation", event)
    assert "canon_naruto" in pressure["actors"]
    assert "institution.konoha.academy" in pressure["source_refs"]
    assert "mission.academy.graduation" in pressure["resources"]


def test_mizuki_front_requires_mizuki_causal_work() -> None:
    valid = _event(
        "event.test.mizuki.work", "institutional_operation_completed",
        hosts=("faction.mizuki_conspiracy",), actors=("canon_mizuki",),
    )
    _update, pressure = _update_for("pressure_mizuki_forbidden_scroll", valid)
    assert "canon_mizuki" in pressure["actors"]
    assert "faction.mizuki_conspiracy" in pressure["source_refs"]

    wrong_actor = _event(
        "event.test.mizuki.wrong", "institutional_operation_completed",
        hosts=("faction.mizuki_conspiracy",), actors=("canon_iruka",),
    )
    registry = _registry()
    updates = apply_event_evidence(registry=registry, rules=_rules(), event=wrong_actor, player_ref="pc_wei_tang")
    assert all(row["front_id"] != "pressure_mizuki_forbidden_scroll" for row in updates)


def test_gato_and_akatsuki_fronts_require_their_materialized_faction_work() -> None:
    gato = _event(
        "event.test.gato.operation", "institutional_operation_completed",
        hosts=("faction.gato_company",), actors=("canon_gato",),
    )
    _update, pressure = _update_for("pressure_wave_gato_bridge", gato)
    assert "faction.gato_company" in pressure["source_refs"]
    assert "canon_gato" in pressure["actors"]

    akatsuki = _event(
        "event.test.akatsuki.operation", "institutional_operation_completed",
        hosts=("faction.akatsuki",), actors=("canon_nagato", "canon_itachi"),
    )
    _update, pressure = _update_for("pressure_akatsuki_intelligence", akatsuki)
    assert "faction.akatsuki" in pressure["source_refs"]
    assert {"canon_nagato", "canon_itachi"}.issubset(set(pressure["actors"]))


def test_kiri_transition_does_not_advance_from_generic_institution_work() -> None:
    generic = _event(
        "event.test.kiri.generic", "institutional_operation_completed",
        hosts=("faction_kiri",), actors=("canon_mei",),
    )
    registry = _registry()
    updates = apply_event_evidence(registry=registry, rules=_rules(), event=generic, player_ref="pc_wei_tang")
    assert all(row["front_id"] != "pressure_kiri_transition" for row in updates)

    political = _event(
        "event.test.kiri.policy", "governance_set_policy",
        hosts=("faction_kiri", "jurisdiction.kiri"), actors=("canon_mei",),
        material=("jurisdiction.kiri",),
    )
    _update, pressure = _update_for("pressure_kiri_transition", political)
    assert "faction_kiri" in pressure["source_refs"]
    assert "canon_mei" in pressure["actors"]


def test_archived_academy_graduation_reconciles_into_current_front_without_rewriting_history() -> None:
    from types import SimpleNamespace
    from shinobi_runtime.commands.core import _BuiltPlan
    from shinobi_runtime.commands.paths import WORLD_EVENT_REGISTRY_PATH
    from shinobi_runtime.commands.world_front_plan import _historical_source_reconciliation
    from shinobi_runtime.store.repository import RepositoryStore

    repository = RepositoryStore(ROOT)
    registry = _registry()
    assert not registry["pressures"]["pressure_konoha_academy_graduation"]["evidence_refs"]
    plan = _BuiltPlan("test", (), {}, {}, lambda *_args: None)
    changed, update_count = _historical_source_reconciliation(
        SimpleNamespace(repository=repository),
        plan,
        repository.read_json(WORLD_EVENT_REGISTRY_PATH),
        registry,
        _rules(),
        "pc_wei_tang",
    )
    assert changed is True
    assert update_count >= 1
    evidence = registry["pressures"]["pressure_konoha_academy_graduation"]["evidence_refs"]
    assert any(ref.startswith("event.academy_exact_graduation_recorded.") for ref in evidence)
    assert registry["historical_source_reconciliation"]["archived_event_count"] > 0


def test_mizuki_bootstrap_requires_real_academy_front_evidence() -> None:
    from shinobi_runtime.autonomy import AutonomousDecision
    from shinobi_runtime.commands.world_front_progression import route_world_front_decision

    rules = _rules()
    registry = _registry()
    profile = json.loads((ROOT / "game/rules/autonomy/policies.json").read_text())["profiles"]["autonomy.irregular"]
    decision = AutonomousDecision(
        kind="routine_summary",
        actor_ref="canon_mizuki",
        reason="scheduled private review",
        payload={"faction_id": "faction.mizuki_conspiracy"},
        material=False,
    )
    unchanged, front_ref = route_world_front_decision(
        decision, at="SE-0061-06-18T07:00:00", rules=rules, registry=registry, profile=profile
    )
    assert front_ref is None
    assert unchanged.kind == "routine_summary"

    registry["pressures"]["pressure_konoha_academy_graduation"]["evidence_refs"] = ["event.academy.real"]
    routed, front_ref = route_world_front_decision(
        decision, at="SE-0061-06-18T07:00:00", rules=rules, registry=registry, profile=profile
    )
    assert front_ref == "pressure_mizuki_forbidden_scroll"
    assert routed.kind in {"information_report", "mission_generate"}
    assert routed.payload["world_front_ref"] == "pressure_mizuki_forbidden_scroll"
