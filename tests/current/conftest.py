"""Current-suite production composition bootstrap.

The maintained suite must exercise the same exact-combat function graph that the
production API installs. Loading it here happens before test modules bind direct
function imports, preventing a silent split between base-import and production
behavior.
"""
from shinobi_runtime.api.combat_production import install_production_combat_runtime

install_production_combat_runtime()
