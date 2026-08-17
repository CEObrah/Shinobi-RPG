from shinobi_runtime.api.player_training_model_projection import _training_model_guidance


class Repo:
    def read_json(self, path):
        assert path == "game/rules/training/models.json"
        return {
            "schema": "training-model-registry",
            "models": {
                "training.self_directed": {
                    "context_kind": "individual",
                    "requires_instructor": False,
                },
                "training.team": {
                    "context_kind": "exact_team",
                    "requires_instructor": True,
                },
            },
        }


class Operations:
    repository = Repo()


def test_training_contract_discovery_exposes_self_directed_model():
    guidance = _training_model_guidance(Operations())
    models = guidance["model_ref"]
    assert "training.self_directed" in models["allowed_values"]
    assert guidance["self_directed"]["context_ref"] is None
    assert guidance["self_directed"]["instructor_ref"] is None
    assert models["models"]["training.team"]["requires_instructor"] is True
