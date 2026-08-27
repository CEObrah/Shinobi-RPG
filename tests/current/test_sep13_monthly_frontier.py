import copy
from pathlib import Path

from shinobi_runtime.store import RepositoryStore, RegisteredSchemaValidator, RegisteredTemplateValidator

ROOT = Path(__file__).resolve().parents[2]


def test_funded_contract_activity_handoff_is_valid_scene_state():
    repository = RepositoryStore(ROOT)
    scene = copy.deepcopy(repository.read_json("state/scene.json"))
    scene["activity_handoff"] = {
        "event_id": "funded_contract_offer:contract.regression",
        "kind": "funded_contract_offer",
        "requires_player_decision": False,
        "interrupts_continuation": True,
    }

    schema_validator = RegisteredSchemaValidator(repository)
    schema_validator.validators["scene"].validate(scene)

    template_validator = RegisteredTemplateValidator(repository)
    RegisteredTemplateValidator._validate_document(
        scene,
        template_validator.templates["scene"],
        label="state/scene.json",
    )
