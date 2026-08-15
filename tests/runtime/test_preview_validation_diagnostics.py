from shinobi_runtime.api.preview_validation import _schema_validation_error_code


def test_schema_validation_error_code_exposes_only_schema_identity():
    error = ValueError(
        "staged mission-assignment-request-registry schema validation failed: "
        "'secret-value' is not valid under any schema"
    )
    assert _schema_validation_error_code(error) == (
        "preview_schema_validation_failed_mission_assignment_request_registry"
    )


def test_schema_validation_error_code_does_not_leak_path_or_field_detail():
    error = ValueError(
        "invalid JSON in staged output state/hidden/owner.json: secret-value"
    )
    code = _schema_validation_error_code(error)
    assert code == "preview_schema_validation_failed_invalid_json"
    assert "hidden" not in code
    assert "secret" not in code


def test_schema_validation_error_code_classifies_unregistered_schema():
    error = ValueError("unregistered top-level schema: secret-schema-name")
    assert _schema_validation_error_code(error) == (
        "preview_schema_validation_failed_unregistered_schema"
    )


def test_schema_validation_error_code_falls_back_closed():
    assert _schema_validation_error_code(ValueError("unexpected validation detail")) == (
        "preview_schema_validation_failed"
    )
