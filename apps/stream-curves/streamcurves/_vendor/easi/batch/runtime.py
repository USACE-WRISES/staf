"""Engine-level runtime setup for headless / vendored callers.

The interactive app sets the HyRiver on-disk cache location in ``app.py`` *before*
importing HyRiver (Connect Cloud's filesystem is ephemeral, and the default cache
lands in the cwd). A batch runner or the vendored StreamCurves screening imports
the engine directly and never runs ``app.py``, so they must call ``ensure_cache()``
once (the batch API does this at the top of ``run_batch``/``run_site``).

``ensure_cache`` is idempotent and honors an already-set ``HYRIVER_CACHE_NAME`` (so a
host that configured its own cache is left untouched). For it to take effect it must
run before the first HyRiver request; the batch API guarantees that ordering.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_CACHE_EXPIRE_SECONDS = 7 * 24 * 60 * 60  # 7 days, matching app.py
_configured = False


def ensure_cache(cache_dir: str | os.PathLike | None = None) -> str:
    """Point HyRiver's on-disk cache at a writable location. Returns the path used.

    Idempotent: only sets the env vars if unset, and only does work once per process.
    """
    global _configured
    existing = os.environ.get("HYRIVER_CACHE_NAME")
    if existing and _configured:
        return existing
    base = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir())
    default = str(base / "easi_hyriver.sqlite")
    os.environ.setdefault("HYRIVER_CACHE_NAME", default)
    os.environ.setdefault("HYRIVER_CACHE_EXPIRE", str(_CACHE_EXPIRE_SECONDS))
    _configured = True
    return os.environ["HYRIVER_CACHE_NAME"]
