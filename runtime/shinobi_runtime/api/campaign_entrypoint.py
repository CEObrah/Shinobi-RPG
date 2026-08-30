"""Production ASGI bootstrap for the single Jianghu campaign.

Production play composes travel/public-place context, reversible combat parley,
current-revision transition recovery, and the bounded standing-combat policy so
exact co-travelers, observer-specific combat knowledge, active opposing-side
conversation, interrupted committed scene chronology, and long delegated combat
intent remain safe and recoverable from Runtime authority.
"""


def create_app_from_env():
    from shinobi_runtime.api import app as app_module
    from shinobi_runtime.api.transition_operations import TransitionAwareCampaignOperations
    from shinobi_runtime.commands.combat_span_safety import install_production_combat_span_safety

    # Campaign-specific standing combat intent is composed before the service
    # starts accepting commands.  The exact reducer remains deterministic; the
    # production wrapper only bounds one transaction's simulated-time footprint
    # and preserves the explicit rapid-lethal target-selection semantics.
    install_production_combat_span_safety()

    # create_app/create_app_from_env intentionally construct through the module
    # class binding. Select the production projection at the composition boundary
    # without changing the base operations contract used by isolated API tests.
    app_module.CampaignOperations = TransitionAwareCampaignOperations
    return app_module.create_app_from_env()


__all__ = ["create_app_from_env"]
