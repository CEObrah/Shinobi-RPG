"""Production ASGI bootstrap for the single Jianghu campaign.

Production play composes travel/public-place context, reversible combat parley,
and current-revision transition recovery so exact co-travelers, observer-specific
combat knowledge, active opposing-side conversation, and interrupted committed
scene chronology remain recoverable from Runtime authority.
"""


def create_app_from_env():
    from shinobi_runtime.api import app as app_module
    from shinobi_runtime.api.transition_operations import TransitionAwareCampaignOperations

    # create_app/create_app_from_env intentionally construct through the module
    # class binding. Select the production projection at the composition boundary
    # without changing the base operations contract used by isolated API tests.
    app_module.CampaignOperations = TransitionAwareCampaignOperations
    return app_module.create_app_from_env()


__all__ = ["create_app_from_env"]
