from shinobi_runtime.commands.time_planner_error_guard import _stage_from_filename


def test_stage_sanitizer_reports_domain_module_without_values():
    assert _stage_from_filename(
        "/app/runtime/shinobi_runtime/commands/domains/time.py"
    ) == "domains_time"


def test_stage_sanitizer_reports_top_level_command_module():
    assert _stage_from_filename(
        "/app/runtime/shinobi_runtime/commands/world_front_progression.py"
    ) == "world_front_progression"


def test_stage_sanitizer_ignores_paths_outside_runtime():
    assert _stage_from_filename("/tmp/test.py") is None
