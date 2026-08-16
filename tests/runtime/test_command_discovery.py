from shinobi_runtime.api.command_discovery import command_domain


def test_promotion_exam_registration_is_a_social_command():
    assert command_domain("promotion_exam_registration_resolution") == "social"
