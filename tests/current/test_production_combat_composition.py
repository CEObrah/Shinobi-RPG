from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from shinobi_runtime.api.combat_production import (
    PRODUCTION_COMBAT_OWNERS,
    install_production_combat_runtime,
)


def test_production_combat_reports_static_canonical_owners():
    assert PRODUCTION_COMBAT_OWNERS == (
        "commands.jianghu_extended",
        "martial_world.exact_combat",
        "combat.physical_defense",
        "combat.team_tactics",
        "martial_world.health",
        "martial_world.equipment",
    )
    assert install_production_combat_runtime() == PRODUCTION_COMBAT_OWNERS
    assert install_production_combat_runtime() == PRODUCTION_COMBAT_OWNERS


def test_production_handshake_neither_replaces_functions_nor_sets_legacy_install_markers():
    script = r'''
import json
from shinobi_runtime.martial_world import exact_combat as exact
from shinobi_runtime.martial_world import health
from shinobi_runtime.combat import team_tactics
from shinobi_runtime.commands import jianghu_extended as extended

def identities():
    return {
        "schedule": id(exact._schedule_action),
        "resolve_scheduled": id(exact._resolve_scheduled_action),
        "resolve_exchange": id(exact.resolve_exchange),
        "defense_selector": id(exact.select_physical_defense),
        "disengage": id(exact._disengage_step),
        "functional_penalties": id(health.functional_penalties),
        "span": id(extended._resolve_player_combat_span),
        "team_plan": id(team_tactics.plan_team_exchange),
    }

before = identities()
from shinobi_runtime.api.combat_production import install_production_combat_runtime
owners = install_production_combat_runtime()
after = identities()
legacy_markers = [
    "_combat_pressure_integrity_installed",
    "_combat_readiness_integrity_installed",
    "_combat_reaction_timing_integrity_installed",
    "_combat_tactical_movement_integrity_installed",
    "_combat_retreat_integrity_installed",
]
print(json.dumps({
    "same": before == after,
    "owners": owners,
    "legacy_marker_values": [bool(getattr(exact, name, False)) for name in legacy_markers],
}))
'''
    env = dict(os.environ)
    runtime = str(Path(__file__).resolve().parents[2] / "runtime")
    env["PYTHONPATH"] = runtime + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    data = json.loads(subprocess.check_output([sys.executable, "-c", script], text=True, env=env))
    assert data["same"] is True
    assert tuple(data["owners"]) == PRODUCTION_COMBAT_OWNERS
    assert not any(data["legacy_marker_values"])
