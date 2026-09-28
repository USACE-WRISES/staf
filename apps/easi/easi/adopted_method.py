"""The EASI assessment this deployment scores with: the explicitly adopted version of
``easi-screening`` in the STAF assessment library (owner decision D20, 2026-09-28).

StreamCurves publishes EASI's method as a library assessment, and the rolling
``library`` release carries every version's method package
(``<id>-v<N>-<sha8>.easi-method.zip``), listed in ``library-v2.json`` with its sha256
and method version. The pin ``data/source/adopted-method.json`` (written by
StreamCurves' exporter when a version is activated) names one version exactly.
At startup, before any scoring code reads its data folder, this module gives EASI
that package, taking the first source that verifies:

1. a cached copy whose sha256 is the pin's;
2. the pinned version's package from the library release: the feed must list the
   same file and method version, the downloaded bytes must carry the pin's sha256,
   and the package digest and method version must be the pin's; then it is cached;
3. offline, the bundled method in ``data/``, when its package digest is the pin's
   (the exporter wrote the same version there).

Nothing else is ever substituted. A newer publication in the feed is noted and
ignored (activating it is an explicit change of the pin), and when neither the
package nor the bundled copy verifies, startup stops with the reason.
``EASI_METHOD_PACKAGE`` still names a package for tests and studies (it wins), a
vendored copy of EASI never runs this, and ``EASI_ADOPTED_METHOD=0`` switches it off
for offline development (the bundled method is then used as it is).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Optional

from . import method_package as mp

PIN_NAME = "adopted-method.json"
PIN_SCHEMA = 1
FEED_NAME = "library-v2.json"
DEFAULT_URL = "https://github.com/USACE-WRISES/staf/releases/download/library/"
ENV_SWITCH = "EASI_ADOPTED_METHOD"
ENV_URL = "EASI_LIBRARY_URL"
TIMEOUT = (5.0, 30.0)
_OFF = ("0", "false", "off")

#: How the last startup resolved the adopted method (for the status line and logs).
LAST: dict = {}


class AdoptedMethodError(RuntimeError):
    """The pinned method could not be verified from any source."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _requests_get(url: str, timeout) -> tuple[int, bytes]:
    import requests
    r = requests.get(url, timeout=timeout)
    return r.status_code, r.content


_HTTP: dict = {"get": _requests_get}


def set_http_get(fn: Optional[Callable]) -> Callable:
    """Swap the HTTP getter (``(url, timeout) -> (status, bytes)``); None restores it."""
    old = _HTTP["get"]
    _HTTP["get"] = fn or _requests_get
    return old


def pin_path() -> Path:
    return mp.builtin_data_dir() / "source" / PIN_NAME


def load_pin(path: Optional[Path] = None) -> Optional[dict]:
    """The pin, or None when the deployment carries none."""
    p = Path(path) if path else pin_path()
    if not p.is_file():
        return None
    pin = json.loads(p.read_text(encoding="utf-8"))
    for key in ("assessmentId", "version", "packageDigest", "methodVersion", "package"):
        if not pin.get(key):
            raise AdoptedMethodError(f"{p} lacks {key}")
    pkg = pin["package"]
    if not (pkg.get("name") and pkg.get("sha256")):
        raise AdoptedMethodError(f"{p} names no package file and sha256")
    return pin


def _label(pin: dict) -> str:
    return f"{pin['assessmentId']} v{int(pin['version'])}"


def _check_bytes(blob: bytes, pin: dict) -> None:
    """Raise unless ``blob`` is exactly the pinned package."""
    if _sha(blob) != str(pin["package"]["sha256"]).lower():
        raise AdoptedMethodError("the package file's sha256 is not the pin's")
    pkg = mp.read_package(blob)
    if pkg.digest != pin["packageDigest"]:
        raise AdoptedMethodError("the package's method files are not the pinned ones")
    got = str((pkg.identity or {}).get("methodVersion") or "")
    if got and got != str(pin["methodVersion"]):
        raise AdoptedMethodError(f"the package records method {got}, the pin {pin['methodVersion']}")


def _feed_version(feed: dict, pin: dict) -> tuple[Optional[dict], list[int]]:
    """The pinned version's feed record and the newer versions the feed lists."""
    for entry in feed.get("assessments") or []:
        if str(entry.get("id")) != str(pin["assessmentId"]):
            continue
        record, newer = None, []
        for v in entry.get("versions") or []:
            n = int(v.get("version") or 0)
            if n == int(pin["version"]):
                record = v
            elif n > int(pin["version"]):
                newer.append(n)
        return record, sorted(newer)
    return None, []


def _bundled_matches(pin: dict) -> bool:
    data_dir = mp.builtin_data_dir()
    files = {}
    for name in mp.METHOD_FILES:
        p = data_dir / name
        if not p.is_file():
            return False
        files[name] = _sha(p.read_bytes())
    return mp.package_digest(files) == pin["packageDigest"]


def resolve(pin: dict, *, base_url: Optional[str] = None,
            cache: Optional[Path] = None) -> dict:
    """Where the pinned package comes from: ``{"source": "cache"|"release"|"bundled",
    "path": <zip or None>, "notes": [...]}``. Raises :class:`AdoptedMethodError` when
    no source verifies."""
    base = (base_url or os.environ.get(ENV_URL) or DEFAULT_URL).rstrip("/") + "/"
    folder = Path(cache) if cache else mp.cache_root() / "adopted"
    target = folder / str(pin["package"]["name"])
    notes: list[str] = []
    if target.is_file():
        try:
            _check_bytes(target.read_bytes(), pin)
            return {"source": "cache", "path": str(target), "notes": notes}
        except (AdoptedMethodError, mp.MethodPackageError, OSError) as exc:
            notes.append(f"cached copy refused: {exc}")
    try:
        status, raw = _HTTP["get"](base + FEED_NAME, TIMEOUT)
        if status != 200:
            raise AdoptedMethodError(f"the library feed answered HTTP {status}")
        record, newer = _feed_version(json.loads(raw.decode("utf-8")), pin)
        if newer:
            notes.append(f"the library lists newer {pin['assessmentId']} version(s) "
                         f"{', '.join(str(n) for n in newer)}; the pin keeps v{int(pin['version'])}")
        if record is None:
            raise AdoptedMethodError(f"the library feed does not list {_label(pin)}")
        asset = (record.get("assets") or {}).get("method") or {}
        if (str(asset.get("name")) != str(pin["package"]["name"])
                or str(asset.get("sha256") or "").lower() != str(pin["package"]["sha256"]).lower()):
            raise AdoptedMethodError(f"the library feed's package for {_label(pin)} is not the pinned file")
        if str(record.get("methodVersion") or pin["methodVersion"]) != str(pin["methodVersion"]):
            raise AdoptedMethodError("the library feed records another method version for the pin")
        status, blob = _HTTP["get"](base + str(asset["name"]), TIMEOUT)
        if status != 200:
            raise AdoptedMethodError(f"the package download answered HTTP {status}")
        _check_bytes(blob, pin)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(blob)
        os.replace(tmp, target)
        return {"source": "release", "path": str(target), "notes": notes}
    except Exception as exc:  # noqa: BLE001 - every failure falls to the bundled copy
        notes.append(f"library release not used: {exc}")
    if _bundled_matches(pin):
        return {"source": "bundled", "path": None, "notes": notes}
    raise AdoptedMethodError(
        f"EASI cannot verify its adopted method {_label(pin)} (method {pin['methodVersion']}): "
        + "; ".join(notes + ["the bundled method files are not the pinned version"])
        + ". Nothing else is substituted; restore the network or redeploy the pinned version.")


def apply_to_env() -> dict:
    """Called by ``easi/__init__.py`` before a method package is materialized: point
    ``EASI_METHOD_PACKAGE`` at the verified pinned package, or leave the bundled copy
    when it is the pinned version. Records the outcome in :data:`LAST`."""
    LAST.clear()
    if mp.is_vendored():
        return LAST
    if os.environ.get(mp.ENV_PACKAGE):
        LAST.update({"source": "override", "path": os.environ[mp.ENV_PACKAGE]})
        return LAST
    if str(os.environ.get(ENV_SWITCH, "")).strip().lower() in _OFF:
        LAST.update({"source": "off"})
        return LAST
    pin = load_pin()
    if pin is None:
        LAST.update({"source": "unpinned"})
        return LAST
    got = resolve(pin)
    if got["path"]:
        os.environ[mp.ENV_PACKAGE] = got["path"]
    LAST.update({**got, "assessmentId": pin["assessmentId"], "version": int(pin["version"]),
                 "methodVersion": pin["methodVersion"], "packageDigest": pin["packageDigest"]})
    return LAST


def status_line() -> str:
    """One line for the app's about panel and the log."""
    src = LAST.get("source")
    if src in ("cache", "release", "bundled"):
        where = {"cache": "the verified cache of the library release",
                 "release": "the library release", "bundled": "the bundled copy"}[src]
        return (f"Scoring method: {LAST['assessmentId']} v{LAST['version']} "
                f"(method {LAST['methodVersion']}), loaded from {where}, verified.")
    if src == "override":
        return "Scoring method: the package named by EASI_METHOD_PACKAGE."
    if src == "off":
        return "Scoring method: the bundled method (adopted-version check switched off)."
    return "Scoring method: the bundled method (no adopted version pinned)."
