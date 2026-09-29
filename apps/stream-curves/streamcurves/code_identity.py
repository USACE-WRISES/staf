"""What code a build ran: one fingerprint over everything a stage reads from the app.

:func:`fingerprint` is the SHA-256 that ``scripts/run_region_batch.py::code_fingerprint`` has
computed since the batch mode existed: the relative path and the raw bytes of every file under
``streamcurves/`` (the vendored copies and their data included), ``config/`` and ``data/``, and
of ``scripts/*.py``, in sorted path order, ``__pycache__`` skipped. It is lifted here so the run
manifest (``provenance.digest_payload_from_manifest``, digest schema 2), ``stage_complete.json``
and the DEEP evidence package all name the same value, and so the batch script can delegate to
it without the value moving.

The git helpers are best effort: a tarball checkout, or a machine without git, records None
and never raises.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

from .paths import ROOT

#: (folder under the app root, glob patterns): what the fingerprint covers, in the order the
#: batch script hashed it. Every file under the first, third and fourth; the scripts' own code.
SCOPE = (("streamcurves", ("*",)), ("scripts", ("*.py",)), ("config", ("*",)), ("data", ("*",)))

#: The repository root of a checkout (apps/stream-curves -> apps -> the repo).
REPO_ROOT = ROOT.parent.parent


def fingerprint(app_root: Optional[Path] = None) -> str:
    """SHA-256 (hex, no prefix) over what a stage reads from the app at ``app_root`` (this
    app's root by default): relative POSIX path, NUL, raw bytes, NUL, file by file in sorted
    order. Equal to ``run_region_batch.code_fingerprint()`` on the same tree."""
    root = Path(app_root or ROOT)
    h = hashlib.sha256()
    for sub_dir, patterns in SCOPE:
        base = root / sub_dir
        if not base.is_dir():
            continue
        for pattern in patterns:
            for p in sorted(base.rglob(pattern)):
                if "__pycache__" in p.parts or not p.is_file():
                    continue
                h.update(str(p.relative_to(root)).replace("\\", "/").encode("utf-8"))
                h.update(b"\0")
                h.update(p.read_bytes())
                h.update(b"\0")
    return h.hexdigest()


def _git(args: list[str], repo: Path, timeout: float = 10.0) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def git_head(repo: Optional[Path] = None) -> Optional[str]:
    """The commit the checkout at ``repo`` (the STAF repository by default) is at, or None
    when git cannot say (no git, no checkout)."""
    out = _git(["rev-parse", "HEAD"], Path(repo or REPO_ROOT))
    text = (out or "").strip()
    return text or None


def git_dirty(repo: Optional[Path] = None) -> Optional[bool]:
    """True when the checkout at ``repo`` has uncommitted changes, False when it is clean,
    None when git cannot say. A None is never read as clean."""
    out = _git(["status", "--porcelain"], Path(repo or REPO_ROOT))
    if out is None:
        return None
    return bool(out.strip())


def identity(app_root: Optional[Path] = None, repo: Optional[Path] = None) -> dict:
    """The code identity a record names: the fingerprint, what it covers, and the commit
    and dirty flag of the checkout (None where git cannot say)."""
    return {"fingerprint": fingerprint(app_root),
            "scope": [f"{sub}/{','.join(patterns)}" for sub, patterns in SCOPE],
            "gitCommit": git_head(repo), "gitDirty": git_dirty(repo)}


__all__ = ["SCOPE", "REPO_ROOT", "fingerprint", "git_head", "git_dirty", "identity"]
