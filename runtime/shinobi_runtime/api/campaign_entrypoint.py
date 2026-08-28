"""Production ASGI bootstrap for the single Jianghu campaign.

Production play uses the travel-aware read projection so exact people already
owned by the player's active route movement remain available to scene craft.
The underlying mechanical authority stays in physical_presence.py; this only
selects the richer CampaignOperations implementation for the deployed service.
"""


def create_app_from_env():
    from shinobi_runtime.api import app as app_module
    from shinobi_runtime.api.travel_operations import TravelAwareCampaignOperations

    # create_app/create_app_from_env intentionally construct through the module
    # class binding. Select the travel-aware subclass at the production
    # composition boundary without changing the base operations contract used
    # by isolated API tests and tools.
    app_module.CampaignOperations = TravelAwareCampaignOperations
    return app_module.create_app_from_env()


__all__ = ["create_app_from_env"]
