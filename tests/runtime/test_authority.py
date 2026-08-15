from pathlib import Path

import pytest

from shinobi_runtime.authority import AuthorityError, StructuralResolver


ROOT = Path(__file__).resolve().parents[2]


def test_resolves_exact_registered_world_event_closure():
    authority = StructuralResolver(ROOT).resolve("world-event-registry", "world_state")
    assert authority.template_path == "runtime/contracts/templates/world-event-registry.template.json"
    assert authority.blank_path == "runtime/contracts/blank-owners/world-event-registry.blank.json"
    assert authority.contract_path == "runtime/contracts/system-contracts/world_state.json"
    assert "tools/quick_check.py" in authority.validators
    assert "tools/test_changed.py" in authority.validators


def test_rejects_contract_that_does_not_authorize_owner_type():
    with pytest.raises(AuthorityError, match="does not authorize"):
        StructuralResolver(ROOT).resolve("world-event-registry", "characters")


def test_missing_structure_fails_instead_of_inferring_neighbor_shape():
    with pytest.raises(AuthorityError, match="no exact structural template"):
        StructuralResolver(ROOT).resolve_template("person-core-that-does-not-exist")

def test_career_pipeline_has_fact_free_blank_owner():
    path, blank = StructuralResolver(ROOT).resolve_blank("shinobi-career-pipeline")
    assert path == "runtime/contracts/blank-owners/shinobi-career-pipeline.blank.json"
    assert blank == {
        "schema": None,
        "version": None,
        "last_review_at": None,
        "villages": {},
        "history": [],
        "provenance": None,
    }

