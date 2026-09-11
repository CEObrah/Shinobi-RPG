from shinobi_runtime.api.parley_operations import ParleyAwareCampaignOperations
from shinobi_runtime.api.transition_operations import (
    TransitionAwareCampaignOperations, apply_transition_handoff, transition_handoff_from_result,
)


def test_transition_operations_remains_the_single_production_transition_owner():
    assert issubclass(TransitionAwareCampaignOperations, ParleyAwareCampaignOperations)


def test_protected_combat_transition_survives_fresh_context_projection():
    result = {
        "command_type": "jianghu_combat_resolution",
        "scope_stop_reason": "protected_player_decision",
        "continuation_required": False,
    }
    handoff = transition_handoff_from_result(result, committed_revision=64)
    context = apply_transition_handoff({"campaign": {"revision": 64}}, result, committed_revision=64)
    assert handoff is not None and handoff["protected_player_decision"] is True
    assert context["current_transition_handoff"] == handoff
    assert context["unresolved_decision"] == {
        "kind": "combat_transition_decision",
        "source": "current_committed_transition",
        "scope_stop_reason": "protected_player_decision",
        "committed_revision": 64,
    }


def test_stagnation_checkpoint_is_a_protected_transition_handoff():
    result = {
        "command_type": "jianghu_combat_resolution",
        "scope_stop_reason": "stagnation_checkpoint",
        "continuation_required": False,
    }
    handoff = transition_handoff_from_result(result, committed_revision=65)
    assert handoff is not None
    assert handoff["protected_player_decision"] is True
