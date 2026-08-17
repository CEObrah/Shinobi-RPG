from shinobi_runtime.api.campaign_stable_operations import transaction_failure_code
from shinobi_runtime.tx.errors import GitCommitError, GitStageError, TransactionError


def test_transaction_failure_codes_are_specific_without_error_text() -> None:
    assert transaction_failure_code(GitStageError(1, "secret path")) == "transaction_git_stage_failed"
    assert transaction_failure_code(GitCommitError(1, "secret hook output")) == "transaction_git_commit_failed"


class _UnknownTransactionError(TransactionError):
    pass


def test_unknown_transaction_failure_remains_generic() -> None:
    assert transaction_failure_code(_UnknownTransactionError("details")) == "transaction_rejected"
