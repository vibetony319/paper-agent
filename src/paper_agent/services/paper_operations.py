"""Single-process coordination between paper writes and permanent deletion."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import threading


class PaperBusyError(RuntimeError):
    pass


class PaperDeletingError(RuntimeError):
    pass


@dataclass
class _PaperState:
    active_operations: int = 0
    deleting: bool = False


class PaperOperationCoordinator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[str, _PaperState] = {}

    @contextmanager
    def operation(self, paper_id: str) -> Iterator[None]:
        with self._lock:
            state = self._states.setdefault(paper_id, _PaperState())
            if state.deleting:
                raise PaperDeletingError("paper deletion is active")
            state.active_operations += 1
        try:
            yield
        finally:
            with self._lock:
                state.active_operations -= 1
                self._prune(paper_id, state)

    @contextmanager
    def deletion(self, paper_id: str) -> Iterator[None]:
        with self._lock:
            state = self._states.setdefault(paper_id, _PaperState())
            if state.deleting or state.active_operations:
                raise PaperBusyError("paper has active operations")
            state.deleting = True
        try:
            yield
        finally:
            with self._lock:
                state.deleting = False
                self._prune(paper_id, state)

    def _prune(self, paper_id: str, state: _PaperState) -> None:
        if not state.active_operations and not state.deleting:
            if self._states.get(paper_id) is state:
                del self._states[paper_id]
