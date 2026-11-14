"""Cross-platform single-instance lock for dataset refreshes.

The lock is held for the whole refresh. OS-level file locking is released
automatically if the process exits, and an in-process guard prevents two
threads in the same process from entering the same refresh.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from app.core.logging import get_logger


logger = get_logger(__name__)


class RefreshLocked(RuntimeError):
    """Raised when another refresh instance already owns the lock."""


_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_THREAD_GUARD = threading.Lock()


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _THREAD_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


class ExecutionLock:
    """Advisory lock backed by an OS file lock plus a process-wide thread lock."""

    def __init__(self, path: Path | str, timeout: float = 0.0) -> None:
        self.path = Path(path)
        self.timeout = max(0.0, float(timeout))
        self._handle = None
        self._thread_lock: Optional[threading.Lock] = None

    def _lock_file(self) -> None:
        assert self._handle is not None
        if os.name == "nt":  # pragma: no cover - exercised on Windows
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - exercised on POSIX
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - exercised on POSIX
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            logger.warning("Failed to release refresh file lock cleanly", exc_info=True)

    def __enter__(self) -> "ExecutionLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_lock = _thread_lock_for(self.path)
        if not self._thread_lock.acquire(timeout=self.timeout):
            raise RefreshLocked(
                f"Another refresh is already running in this process ({self.path})."
            )

        try:
            handle = self.path.open("a+b")
            self._handle = handle
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"lock")
                handle.flush()
            handle.seek(0)

            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    self._lock_file()
                    return self
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise RefreshLocked(
                            f"Another refresh is already running ({self.path})."
                        ) from exc
                    time.sleep(0.1)
        except Exception:
            if self._handle is not None:
                try:
                    self._handle.close()
                except OSError:
                    pass
                self._handle = None
            if self._thread_lock is not None:
                self._thread_lock.release()
                self._thread_lock = None
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        self._unlock_file()
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
        if self._thread_lock is not None:
            self._thread_lock.release()
            self._thread_lock = None
