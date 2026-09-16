"""Reuse HTTP connections within each national-data worker thread.

Sessions are never shared across workers. A thread-local owner closes its
session when the worker exits; the weak registry also permits process-shutdown
cleanup without keeping completed workers or their sessions alive.
"""
from __future__ import annotations

import atexit
import threading
import weakref

import requests
from requests.adapters import HTTPAdapter

_LOCAL = threading.local()
_OWNERS: weakref.WeakSet = weakref.WeakSet()
_LOCK = threading.Lock()


class _ThreadSession:
    def __init__(self):
        self.session = requests.Session()
        try:
            for scheme in ("https://", "http://"):
                self.session.mount(scheme, HTTPAdapter(
                    pool_connections=4, pool_maxsize=1,
                    pool_block=True, max_retries=0))
        except Exception:
            self.session.close()
            raise
        self.cleanup = weakref.finalize(self, self.session.close)


def session() -> requests.Session:
    """The calling worker's session, created only for an HTTP operation."""
    owner = getattr(_LOCAL, "owner", None)
    if owner is None or not owner.cleanup.alive:
        owner = _ThreadSession()
        _LOCAL.owner = owner
        with _LOCK:
            _OWNERS.add(owner)
    return owner.session


def close_sessions() -> None:
    """Close live sessions at shutdown or while test workers are quiescent.

    Do not call during active requests. Subsequent calls to ``session()``
    create fresh sessions, including on workers whose local owner survives.
    """
    with _LOCK:
        owners = list(_OWNERS)
        _OWNERS.clear()
    for owner in owners:
        owner.cleanup()


atexit.register(close_sessions)
