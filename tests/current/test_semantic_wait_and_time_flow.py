from shinobi_runtime.commands.jianghu_time import JianghuTimeCommandsMixin


def test_wait_policy_keeps_one_reason_precise_and_supports_distinct_any_of_reasons():
    policy = JianghuTimeCommandsMixin._normalize_wait_policy(
        {
            "any_of": [
                {"event_kinds": ["world_arc_report"], "topic_terms": ["entry authority"]},
                {"classifications": ["hard_wake"]},
            ]
        }
    )

    unrelated_report = {
        "kind": "world_arc_report",
        "topic": "merchant prices changed in Luoyang",
    }
    matching_report = {
        "kind": "world_arc_report",
        "topic": "Qin entry authority changes at the frontier",
    }
    hard_wake = {"kind": "hostile_contact", "classification": "hard_wake"}

    assert JianghuTimeCommandsMixin._handoff_matches_wait_policy(unrelated_report, policy) is False
    assert JianghuTimeCommandsMixin._handoff_matches_wait_policy(matching_report, policy) is True
    assert JianghuTimeCommandsMixin._handoff_matches_wait_policy(hard_wake, policy) is True


def test_top_level_wait_fields_form_one_conjunctive_clause():
    policy = JianghuTimeCommandsMixin._normalize_wait_policy(
        {"event_kinds": ["world_arc_report"], "source_refs": ["arc.qin_frontier"]}
    )

    assert JianghuTimeCommandsMixin._handoff_matches_wait_policy(
        {"kind": "world_arc_report", "arc_ref": "arc.qin_frontier"}, policy
    ) is True
    assert JianghuTimeCommandsMixin._handoff_matches_wait_policy(
        {"kind": "world_arc_report", "arc_ref": "arc.unrelated"}, policy
    ) is False
    assert JianghuTimeCommandsMixin._handoff_matches_wait_policy(
        {"kind": "message", "arc_ref": "arc.qin_frontier"}, policy
    ) is False
