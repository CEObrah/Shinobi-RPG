import inspect

from shinobi_runtime.api.campaign_ooc import RepositoryOocAudit


def test_campaign_receipt_audit_filters_runtime_index_metadata():
    source = inspect.getsource(RepositoryOocAudit._audit_receipts)
    assert "receipt_only=True" in source
