from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_runner():
    path = ROOT / "tools/run_pytest_shards.py"
    spec = importlib.util.spec_from_file_location("shinobi_test_shard_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_changed_gate():
    path = ROOT / "tools/test_changed.py"
    spec = importlib.util.spec_from_file_location("shinobi_test_changed", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shard_runner_discovers_top_level_and_class_test_nodes_without_importing(tmp_path, monkeypatch) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    path = tmp_path / "tests/current/test_sample.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "def test_one():\n    pass\n\n"
        "class TestGroup:\n"
        "    def test_two(self):\n        pass\n\n"
        "def helper():\n    pass\n",
        encoding="utf-8",
    )
    assert runner._test_selectors("tests/current/test_sample.py") == [
        "tests/current/test_sample.py::test_one",
        "tests/current/test_sample.py::TestGroup::test_two",
    ]


def test_shard_runner_disables_external_plugins_and_bytecode(monkeypatch, tmp_path) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    path = tmp_path / "tests/current/test_sample.py"
    path.parent.mkdir(parents=True)
    path.write_text("def test_one():\n    pass\n", encoding="utf-8")
    observed = {}

    def fake_run(args, *, env, timeout_seconds):
        observed.update(env)
        return 0, False, "1 passed", 0.01

    monkeypatch.setattr(runner, "_run_pytest", fake_run)
    assert runner.run(["tests/current/test_sample.py"], timeout_seconds=5, quiet_passes=True) == 0
    assert observed["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert observed["PYTHONDONTWRITEBYTECODE"] == "1"


def test_timed_out_file_can_be_proven_green_by_bounded_node_split(monkeypatch, tmp_path) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    path = tmp_path / "tests/current/test_sample.py"
    path.parent.mkdir(parents=True)
    path.write_text("def test_one():\n    pass\n\ndef test_two():\n    pass\n", encoding="utf-8")
    calls = []

    def fake_run(args, *, env, timeout_seconds):
        calls.append(tuple(args))
        if args == ["tests/current/test_sample.py"]:
            return None, True, "", timeout_seconds
        return 0, False, "2 passed", 0.01

    monkeypatch.setattr(runner, "_run_pytest", fake_run)
    assert runner.run(["tests/current/test_sample.py"], timeout_seconds=5, quiet_passes=True) == 0
    assert calls[0] == ("tests/current/test_sample.py",)
    assert any("::test_one" in node for call in calls[1:] for node in call)
    assert any("::test_two" in node for call in calls[1:] for node in call)


def test_large_test_file_is_pre_split_without_wasting_a_whole_file_timeout(monkeypatch, tmp_path) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    path = tmp_path / "tests/current/test_large.py"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(f"def test_{i}():\n    pass\n" for i in range(41)), encoding="utf-8")
    calls = []

    def fake_run(args, *, env, timeout_seconds):
        calls.append(tuple(args))
        assert args != ["tests/current/test_large.py"]
        return 0, False, "passed", 0.01

    monkeypatch.setattr(runner, "_run_pytest", fake_run)
    assert runner.run(["tests/current/test_large.py"], timeout_seconds=5, quiet_passes=True) == 0
    assert len(calls) == 3
    assert all("::" in node for call in calls for node in call)


def test_default_discovery_includes_current_and_playability_layers(monkeypatch, tmp_path) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    current = tmp_path / "tests/current/test_current.py"
    playability = tmp_path / "tests/playability/test_torture.py"
    current.parent.mkdir(parents=True)
    playability.parent.mkdir(parents=True)
    current.write_text("def test_one():\n    pass\n", encoding="utf-8")
    playability.write_text("def test_two():\n    pass\n", encoding="utf-8")
    assert runner._default_files() == [
        "tests/current/test_current.py",
        "tests/playability/test_torture.py",
    ]


def test_shard_runner_checkpoint_resumes_only_same_fingerprint(monkeypatch, tmp_path) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    first = tmp_path / "tests/current/test_a.py"
    second = tmp_path / "tests/current/test_b.py"
    runtime = tmp_path / "runtime/shinobi_runtime/core.py"
    first.parent.mkdir(parents=True)
    runtime.parent.mkdir(parents=True)
    first.write_text("def test_a():\n    pass\n", encoding="utf-8")
    second.write_text("def test_b():\n    pass\n", encoding="utf-8")
    runtime.write_text("BUILD = 1\n", encoding="utf-8")
    checkpoint = tmp_path / ".release-pytest-shards.json"
    files = ["tests/current/test_a.py", "tests/current/test_b.py"]
    calls: list[tuple[str, ...]] = []

    def first_run(args, *, env, timeout_seconds):
        calls.append(tuple(args))
        if args == ["tests/current/test_b.py"]:
            return 1, False, "failed", 0.01
        return 0, False, "1 passed", 0.01

    monkeypatch.setattr(runner, "_run_pytest", first_run)
    assert runner.run(files, timeout_seconds=5, quiet_passes=True, checkpoint_path=checkpoint) == 1
    assert checkpoint.exists()
    assert calls == [("tests/current/test_a.py",), ("tests/current/test_b.py",)]

    calls.clear()

    def resumed_run(args, *, env, timeout_seconds):
        calls.append(tuple(args))
        return 0, False, "1 passed", 0.01

    monkeypatch.setattr(runner, "_run_pytest", resumed_run)
    assert runner.run(files, timeout_seconds=5, quiet_passes=True, checkpoint_path=checkpoint) == 0
    assert calls == [("tests/current/test_b.py",)]
    assert not checkpoint.exists()

    # Recreate partial evidence, then change certified runtime source. The old
    # green file must no longer be trusted after the fingerprint changes.
    monkeypatch.setattr(runner, "_run_pytest", first_run)
    calls.clear()
    assert runner.run(files, timeout_seconds=5, quiet_passes=True, checkpoint_path=checkpoint) == 1
    runtime.write_text("BUILD = 2\n", encoding="utf-8")
    calls.clear()
    monkeypatch.setattr(runner, "_run_pytest", resumed_run)
    assert runner.run(files, timeout_seconds=5, quiet_passes=True, checkpoint_path=checkpoint) == 0
    assert calls == [("tests/current/test_a.py",), ("tests/current/test_b.py",)]


def test_release_verifier_uses_source_bound_resumable_pytest_checkpoint() -> None:
    release = (ROOT / "tools/verify_release.sh").read_text(encoding="utf-8")
    assert "python tools/run_pytest_shards.py --timeout 60 --quiet-passes --checkpoint .release-pytest-shards.json" in release


def test_changed_gate_maps_test_helpers_to_importing_tests_not_helper_itself(monkeypatch, tmp_path) -> None:
    changed = _load_changed_gate()
    monkeypatch.setattr(changed, "ROOT", tmp_path)
    tests = tmp_path / "tests/current"
    tests.mkdir(parents=True)
    helper = tests / "fixture_support.py"
    consumer = tests / "test_consumer.py"
    unrelated = tests / "test_unrelated.py"
    helper.write_text("def helper():\n    return 1\n", encoding="utf-8")
    consumer.write_text("from fixture_support import helper\n\ndef test_uses_helper():\n    assert helper() == 1\n", encoding="utf-8")
    unrelated.write_text("def test_other():\n    pass\n", encoding="utf-8")
    selected = changed.select(["tests/current/fixture_support.py"])
    assert "tests/current/fixture_support.py" not in selected
    assert "tests/current/test_consumer.py" in selected
    assert "tests/current/test_unrelated.py" not in selected
