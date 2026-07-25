"""Portable runtime bounds for short-lived hook processes.

This module deliberately uses only the Python standard library.  HookDeadline
provides the same hard wall-clock boundary on every platform; LeaseLock uses
portable exclusive-create semantics instead of platform-specific advisory
locking.
"""

from __future__ import annotations

import json
import os
import random
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path


_DEADLINE_LOCAL = threading.local()


class HookDeadline:
    """Hard process deadline enforced by a daemon watchdog thread."""

    def __init__(self, seconds: float, message: bytes | None = None):
        self.seconds = max(0.0, float(seconds))
        self.end = time.monotonic() + self.seconds
        self.message = message or (
            b"[Token Optimizer] hook budget exceeded; skipping\n"
        )
        self._cancelled = threading.Event()
        self._thread = threading.Thread(
            target=self._watch,
            name="token-optimizer-hook-deadline",
            daemon=True,
        )
        self._started = False
        self._previous = None

    def start(self):
        if not self._started:
            self._started = True
            self._previous = getattr(_DEADLINE_LOCAL, "current", None)
            _DEADLINE_LOCAL.current = self
            self._thread.start()
        return self

    def remaining(self) -> float:
        return max(0.0, self.end - time.monotonic())

    def expires_wall(self) -> float:
        return time.time() + self.remaining()

    def _watch(self):
        if not self._cancelled.wait(self.remaining()):
            try:
                os.write(2, self.message)
            except OSError:
                pass
            finally:
                os._exit(0)

    def cancel(self):
        if not self._started:
            return
        self._cancelled.set()
        self._thread.join(timeout=0.1)
        if getattr(_DEADLINE_LOCAL, "current", None) is self:
            _DEADLINE_LOCAL.current = self._previous

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc):
        self.cancel()


def current_deadline() -> HookDeadline | None:
    """Return the active deadline for the calling thread, if any."""

    deadline = getattr(_DEADLINE_LOCAL, "current", None)
    if deadline is not None and deadline.remaining() > 0:
        return deadline
    return None


class LeaseLock:
    """Portable, bounded, fail-open lock backed by an exclusive-create file."""

    def __init__(
        self,
        path,
        *,
        deadline: HookDeadline | None = None,
        acquire_timeout: float = 0.075,
        lease_seconds: float = 10.0,
        reclaim_grace: float = 0.25,
    ):
        # Preserve an already-materialized concrete path. This also lets tests
        # simulate Windows by changing os.name after pathlib created PosixPath
        # objects, without asking pathlib to instantiate an unsupported flavor.
        self.path = path if hasattr(path, "read_text") else Path(path)
        self.deadline = deadline
        self.acquire_timeout = max(0.0, float(acquire_timeout))
        self.lease_seconds = max(0.1, float(lease_seconds))
        self.reclaim_grace = max(0.0, float(reclaim_grace))
        self.nonce = secrets.token_hex(16)
        self.acquired = False

    def _metadata(self):
        now = time.time()
        expires = (
            self.deadline.expires_wall()
            if self.deadline is not None
            else now + self.lease_seconds
        )
        return {
            "pid": os.getpid(),
            "nonce": self.nonce,
            "created_wall": now,
            "expires_wall": expires,
        }

    def _try_create(self):
        metadata = json.dumps(
            self._metadata(), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        try:
            fd = os.open(
                str(self.path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            return False
        except OSError:
            return None
        try:
            os.write(fd, metadata)
            try:
                os.fsync(fd)
            except OSError:
                pass
        except OSError:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            return None
        finally:
            os.close(fd)
        self.acquired = True
        return True

    def _read_owner(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
            owner = json.loads(raw)
            nonce = owner.get("nonce")
            created = float(owner.get("created_wall"))
            expires = float(owner.get("expires_wall"))
            if (
                not isinstance(nonce, str)
                or not nonce
                or created <= 0
                or expires < created
            ):
                return None
            return owner, created, expires
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _reclaim_if_expired(self):
        parsed = self._read_owner()
        if parsed is None:
            return False
        owner, created, expires = parsed
        now = time.time()
        # Future creation times indicate clock rollback or malformed metadata.
        # Fail open without stealing a lock whose lease cannot be assessed.
        if created > now + self.reclaim_grace:
            return False
        if now <= expires + self.reclaim_grace:
            return False
        tombstone = self.path.with_name(
            f"{self.path.name}.stale-{owner['nonce']}-{self.nonce}"
        )
        try:
            os.replace(str(self.path), str(tombstone))
        except OSError:
            return False
        try:
            moved = json.loads(tombstone.read_text(encoding="utf-8"))
            if moved.get("nonce") != owner["nonce"]:
                # Ownership changed during reclamation.  Never delete it.
                try:
                    os.replace(str(tombstone), str(self.path))
                except OSError:
                    pass
                return False
            return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        finally:
            try:
                tombstone.unlink()
            except OSError:
                pass

    def acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        wait_for = self.acquire_timeout
        if self.deadline is not None:
            wait_for = min(wait_for, self.deadline.remaining())
        stop = time.monotonic() + wait_for
        while True:
            created = self._try_create()
            if created is True:
                return True
            if created is None:
                return False
            if self._reclaim_if_expired():
                # Reclamation freed the pathname. Retry exclusive creation once
                # even when acquire_timeout is zero.
                created = self._try_create()
                if created is not False:
                    return created is True
            if time.monotonic() >= stop:
                return False
            remaining = stop - time.monotonic()
            time.sleep(min(remaining, random.uniform(0.004, 0.012)))

    def release(self):
        if not self.acquired:
            return
        try:
            owner = json.loads(self.path.read_text(encoding="utf-8"))
            if owner.get("nonce") == self.nonce:
                self.path.unlink()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        finally:
            self.acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()


@contextmanager
def lease_lock(path, **kwargs):
    """Yield whether the lease was acquired; callers skip mutation on False."""

    lock = LeaseLock(path, **kwargs)
    acquired = lock.acquire()
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
