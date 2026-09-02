"""Production ASGI bootstrap for the single Jianghu campaign.

Production play composes travel/public-place context, reversible combat parley,
current-revision transition recovery, and the bounded standing-combat policy so
exact co-travelers, observer-specific combat knowledge, active opposing-side
conversation, interrupted committed scene chronology, and long delegated combat
intent remain safe and recoverable from Runtime authority. Historical one-off
repair anchors are intentionally excluded from live composition once their repaired
state is part of the canonical campaign baseline.
"""


def create_app_from_env():
    from shinobi_runtime.api import app as app_module
    from shinobi_runtime.api.transition_envelope_safety import (
        install_production_transition_envelope_safety,
    )
    from shinobi_runtime.api.transition_operations import TransitionAwareCampaignOperations
    from shinobi_runtime.commands.combat_span_safety import install_production_combat_span_safety

    # Do not install historical one-off repair anchors here. The canonical
    # packaged baseline already contains their repaired truth, while a fresh
    # private recovery store intentionally lacks the legacy WAL chain required
    # to prove those old incidents. The forensic helper remains importable for
    # explicit disposable-copy investigations and regression tests only.

    # Campaign-specific standing combat intent is composed before the service
    # starts accepting commands.  The exact reducer remains deterministic; the
    # production wrapper only bounds one transaction's simulated-time footprint
    # and preserves the explicit rapid-lethal target-selection semantics.
    install_production_combat_span_safety()

    # Current-transition recovery keeps the exact event page as primary evidence.
    # Rich combat receipts may also carry a compact narrative spine; bound only
    # that optional duplicate view so public object reads stay inside the same
    # response envelope without weakening global validation.
    install_production_transition_envelope_safety()

    # create_app/create_app_from_env intentionally construct through the module
    # class binding. Select the production projection at the composition boundary
    # without changing the base operations contract used by isolated API tests.
    app_module.CampaignOperations = TransitionAwareCampaignOperations
    return app_module.create_app_from_env()


__all__ = ["create_app_from_env"]
