import pytest

from paper_agent.services.paper_operations import (
    PaperBusyError,
    PaperDeletingError,
    PaperOperationCoordinator,
)


def test_delete_is_rejected_while_a_write_operation_is_active() -> None:
    coordinator = PaperOperationCoordinator()
    with coordinator.operation("paper-a"):
        with pytest.raises(PaperBusyError):
            with coordinator.deletion("paper-a"):
                raise AssertionError("deletion must not start")


def test_new_write_is_rejected_while_deletion_is_active() -> None:
    coordinator = PaperOperationCoordinator()
    with coordinator.deletion("paper-a"):
        with pytest.raises(PaperDeletingError):
            with coordinator.operation("paper-a"):
                raise AssertionError("operation must not start")


def test_different_papers_and_recovery_after_exception_are_supported() -> None:
    coordinator = PaperOperationCoordinator()
    with coordinator.operation("paper-a"):
        with coordinator.operation("paper-b"):
            pass

    with pytest.raises(RuntimeError, match="failed operation"):
        with coordinator.operation("paper-a"):
            raise RuntimeError("failed operation")

    with coordinator.deletion("paper-a"):
        pass

    with coordinator.operation("paper-a"):
        pass


def test_repeated_deletion_returns_busy() -> None:
    coordinator = PaperOperationCoordinator()
    with coordinator.deletion("paper-a"):
        with pytest.raises(PaperBusyError):
            with coordinator.deletion("paper-a"):
                raise AssertionError("second deletion must not start")
