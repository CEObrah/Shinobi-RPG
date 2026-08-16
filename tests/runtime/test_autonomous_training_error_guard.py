from shinobi_runtime.commands.autonomous_training_error_guard import _training_error_stage


def _raised_from(filename: str, error_type):
    namespace = {}
    source = "def boom():\n    raise ERROR('test')\n"
    code = compile(source, filename, "exec")
    namespace["ERROR"] = error_type
    exec(code, namespace)
    try:
        namespace["boom"]()
    except error_type as exc:
        return exc
    raise AssertionError("expected synthetic error")


def test_classifier_reports_known_training_source_stage_without_values():
    exc = _raised_from(
        "/app/runtime/shinobi_runtime/commands/standing_training_participation.py",
        ValueError,
    )
    assert _training_error_stage(exc) == "standing_training_participation"


def test_classifier_falls_back_to_unknown_for_unrelated_source():
    exc = _raised_from("/app/runtime/shinobi_runtime/commands/other.py", TypeError)
    assert _training_error_stage(exc) == "unknown"
