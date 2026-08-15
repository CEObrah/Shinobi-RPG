from contextvars import copy_context

import pytest

from shinobi_runtime.commands import academy_pipeline_transfer_ids as fix
from shinobi_runtime.commands.domains import autonomy as autonomy_module
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.sim.events import CampaignTime


def test_academy_pipeline_transfer_suffix_is_stable_and_boundary_specific() -> None:
    first = fix.academy_pipeline_transfer_suffix(
        "institution.konoha.academy", CampaignTime.parse("SE-0061-07-01T07:00:00")
    )
    repeated = fix.academy_pipeline_transfer_suffix(
        "institution.konoha.academy", CampaignTime.parse("SE-0061-07-01T07:00:00")
    )
    later = fix.academy_pipeline_transfer_suffix(
        "institution.konoha.academy", CampaignTime.parse("SE-0061-08-01T07:00:00")
    )
    assert first == repeated
    assert first != later
    assert len(first) == 20


def test_suffix_proxy_is_context_local() -> None:
    proxy = fix._SuffixProxy()
    with pytest.raises(RuntimeError, match="academy_pipeline_transfer_suffix_unbound"):
        format(proxy)

    token = fix._SUFFIX.set("alpha")
    try:
        assert f"{proxy}" == "alpha"
        isolated = copy_context()
        assert isolated.run(lambda: f"{proxy}") == "alpha"
    finally:
        fix._SUFFIX.reset(token)

    with pytest.raises(RuntimeError, match="academy_pipeline_transfer_suffix_unbound"):
        format(proxy)


def test_campaign_installer_binds_missing_autonomy_suffix_before_wrappers() -> None:
    fix.install_academy_pipeline_transfer_ids()
    assert isinstance(autonomy_module.suffix, fix._SuffixProxy)
    assert getattr(
        AutonomyCommandsMixin._apply_institution_autonomy_review,
        "_academy_pipeline_transfer_ids",
        False,
    ) is True


def test_base_academy_pipeline_still_uses_compatibility_suffix_name() -> None:
    # This sentinel keeps the compatibility fix honest. Once the base reducer is
    # edited to construct its own deterministic transfer IDs, remove this module
    # and this test together rather than carrying a dead monkey patch forever.
    import inspect

    source = inspect.getsource(AutonomyCommandsMixin._apply_institution_autonomy_review)
    if getattr(AutonomyCommandsMixin._apply_institution_autonomy_review, "__wrapped__", None):
        source = inspect.getsource(AutonomyCommandsMixin._apply_institution_autonomy_review.__wrapped__)
    assert 'transfer_id=f"autonomy.intake.{suffix}"' in source
    assert 'transfer_id=f"autonomy.graduation.{suffix}"' in source
