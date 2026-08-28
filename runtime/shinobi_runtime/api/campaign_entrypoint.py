"""Production ASGI bootstrap for the single Jianghu campaign.

Production play uses the travel-aware read projection plus the reversible
combat-parley handoff so exact co-travelers, observer-specific combat knowledge,
and active opposing-side conversation remain recoverable from Runtime state.
The underlying mechanical authority stays in physical_presence.py and exact
combat reducers; this only selects the richer CampaignOperations projection.
"""


def create_app_from_env():
    from shinobi_runtime.api import app as app_module
    from shinobi_runtime.api.parley_operations import ParleyAwareCampaignOperations

    # create_app/create_app_from_env intentionally construct through the module
    # class binding. Select the production projection at the composition boundary
    # without changing the base operations contract used by isolated API tests.
    app_module.CampaignOperations = ParleyAwareCampaignOperations
    return app_module.create_app_from_env()


__all__ = ["create_app_from_env"]
