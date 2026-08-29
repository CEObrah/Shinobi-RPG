from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"patch anchor missing: {label}")
    return text.replace(old, new, 1)


template_path = Path("runtime/contracts/templates/jianghu-combat-state-1.0.template.json")
template = template_path.read_text()
template = replace_once(
    template,
    '        "observed_refs",\n        "awareness_confidence_milli",',
    '        "observed_refs",\n        "observed_status_families",\n        "awareness_confidence_milli",',
    "combatant allowed key",
)
template = replace_once(
    template,
    '    "/combats/*/combatants/*/qi_allocation_milli": {\n      "mode": "open_map"\n    },',
    '    "/combats/*/combatants/*/observed_status_families": {\n      "mode": "open_map"\n    },\n    "/combats/*/combatants/*/qi_allocation_milli": {\n      "mode": "open_map"\n    },',
    "observation object contract",
)
template = replace_once(
    template,
    '    "/combats/*/combatants/*/observed_refs/*": [\n      "string"\n    ],\n    "/combats/*/combatants/*/awareness_confidence_milli": [',
    '    "/combats/*/combatants/*/observed_refs/*": [\n      "string"\n    ],\n    "/combats/*/combatants/*/observed_status_families": [\n      "object"\n    ],\n    "/combats/*/combatants/*/observed_status_families/*": [\n      "array"\n    ],\n    "/combats/*/combatants/*/observed_status_families/*/*": [\n      "string"\n    ],\n    "/combats/*/combatants/*/awareness_confidence_milli": [',
    "observation type contracts",
)
template = replace_once(
    template,
    '    "/combats/*/combatants/*/observed_refs": {\n      "item_types": [\n        "string"\n      ]\n    },',
    '    "/combats/*/combatants/*/observed_refs": {\n      "item_types": [\n        "string"\n      ]\n    },\n    "/combats/*/combatants/*/observed_status_families/*": {\n      "item_types": [\n        "string"\n      ]\n    },',
    "observation array contract",
)
template_path.write_text(template)


test_path = Path("tests/current/test_combat_observation_projection.py")
test = test_path.read_text()
test = replace_once(
    test,
    'assert result["count_semantics"] == "confirmed_observed_hostiles_not_total_force"',
    'assert result["count_semantics"] == "confirmed_observed_hostiles_ever_detected_not_current_active_or_total_force"',
    "observation count semantics assertion",
)
test_path.write_text(test)
