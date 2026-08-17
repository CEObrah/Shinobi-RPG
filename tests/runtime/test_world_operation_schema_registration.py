import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read_json(relative_path: str):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_world_operation_mutable_contract_is_fully_registered():
    registry = _read_json("game/schemas/registry.json")
    template_index = _read_json("runtime/contracts/template-index-shards/w.json")
    template = _read_json("runtime/contracts/templates/world-operation.template.json")
    blank_index = _read_json("runtime/contracts/blank-owner-index.json")
    blank = _read_json("runtime/contracts/blank-owners/world-operation.blank.json")
    schema = _read_json("game/schemas/world-operation.schema.json")

    assert registry["world-operation"] == "world-operation.schema.json"
    assert template_index["templates"]["world-operation"]["source_schema"] == "game/schemas/world-operation.schema.json"
    assert template["target_schema"] == "world-operation"
    assert template["source_schema"] == "game/schemas/world-operation.schema.json"
    assert blank_index["owners"]["world-operation"] == "runtime/contracts/blank-owners/world-operation.blank.json"
    assert schema["properties"]["schema"]["const"] == "world-operation"
    assert schema["additionalProperties"] is False

    required = set(template["required_top_level_keys"])
    assert set(schema["required"]) == required
    assert set(blank) == required
