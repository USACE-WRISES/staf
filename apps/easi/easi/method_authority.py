"""Who writes EASI's method files.

The eight method files in ``apps/easi/data`` (``method_package.METHOD_FILES``) are the bytes
``easi.national.method_version`` hashes. Under the authority flip the owner decides on
(``apps/stream-curves/AUTHORING.md``, Authority; ADOPTION step 3) they have one writer,
StreamCurves' exporter::

    python apps/stream-curves/scripts/export_easi_method.py <library version> \\
        --out <pkg.zip> --write-easi-data apps/easi/data

and ``tests/test_method_data_matches_library.py`` keeps them equal, byte for byte, to the
library's EASI version (``apps/library/assessments/easi-screening``, the version its
manifest names). The three older writers (``scripts/promote_alternative_2.py``,
``scripts/build_easi_metrics.py``, ``scripts/fetch_nars_ecoregions.py``) ask
``authority_active()`` before they write and, while it holds, refuse with a message that
names the exporter; their ``--allow-direct-write`` is the transition escape and goes away
at adoption.

The authority is active exactly when the library entry exists beside this EASI and its
default version's method files are the bytes ``data/`` holds. A copy of EASI without the
library (a deployment, StreamCurves' vendored copy) has no entry, so nothing is refused
there. Nothing here imports ``easi.config``: the comparison reads bytes only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import method_package as mp

ASSESSMENT_ID = "easi-screening"
EXPORTER = "apps/stream-curves/scripts/export_easi_method.py"
DIRECT_WRITE_FLAG = "--allow-direct-write"


def library_entry() -> Optional[Path]:
    """``apps/library/assessments/easi-screening`` of the checkout this EASI is part of
    (``apps/easi`` and ``apps/library`` side by side), or None when there is none."""
    entry = mp.package_root().parent.parent / "library" / "assessments" / ASSESSMENT_ID
    return entry if (entry / "manifest.json").is_file() else None


def _entry(entry: Optional[Path]) -> Path:
    found = Path(entry) if entry is not None else library_entry()
    if found is None:
        raise FileNotFoundError("no assessment library beside this EASI")
    return found


def read_manifest(entry: Optional[Path] = None) -> dict:
    return json.loads((_entry(entry) / "manifest.json").read_text(encoding="utf-8"))


def default_version(entry: Optional[Path] = None) -> int:
    """The version ``data/`` must equal: the manifest's ``defaultVersion`` when it names
    one, else its ``latestVersion``, else the highest version it lists."""
    manifest = read_manifest(entry)
    for key in ("defaultVersion", "latestVersion"):
        if manifest.get(key) is not None:
            return int(manifest[key])
    versions = [int(v["version"]) for v in manifest.get("versions") or []]
    if not versions:
        raise ValueError("the library entry lists no version")
    return max(versions)


def version_dir(entry: Optional[Path] = None, version: Optional[int] = None) -> Path:
    entry = _entry(entry)
    return entry / f"v{int(version if version is not None else default_version(entry))}"


def library_files(entry: Optional[Path] = None, version: Optional[int] = None) -> dict[str, bytes]:
    """The version's eight method files, byte for byte."""
    folder = version_dir(entry, version) / mp.METHOD_DIR
    return {name: (folder / name).read_bytes() for name in mp.METHOD_FILES}


def recorded_method_version(entry: Optional[Path] = None,
                            version: Optional[int] = None) -> Optional[str]:
    """The method version the library recorded for the version: its envelope's
    ``identity.methodVersion``, else the manifest row's ``methodVersion``."""
    entry = _entry(entry)
    version = int(version if version is not None else default_version(entry))
    envelope = version_dir(entry, version) / mp.ENVELOPE
    if envelope.is_file():
        ident = json.loads(envelope.read_text(encoding="utf-8")).get("identity") or {}
        if ident.get("methodVersion"):
            return str(ident["methodVersion"])
    for row in read_manifest(entry).get("versions") or []:
        if int(row.get("version") or 0) == version and row.get("methodVersion"):
            return str(row["methodVersion"])
    return None


def differences(entry: Optional[Path] = None, version: Optional[int] = None,
                data_dir: Optional[Path] = None) -> list[str]:
    """The method files whose bytes in ``data_dir`` (the built-in data folder) differ
    from the library version's, or are missing there."""
    data_dir = Path(data_dir) if data_dir is not None else mp.builtin_data_dir()
    out = []
    for name, blob in library_files(entry, version).items():
        p = data_dir / name
        if not p.is_file() or p.read_bytes() != blob:
            out.append(name)
    return out


def authority_active() -> bool:
    """True when the library entry exists and its default version's method files are
    exactly what the built-in data folder holds. Any unreadable or missing piece is
    False: a refusal never rests on a half-read library."""
    try:
        entry = library_entry()
        if entry is None:
            return False
        return not differences(entry, default_version(entry))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def refuse_direct_write(script_name: str) -> None:
    """Stop a script that would write method files under ``apps/easi/data`` itself."""
    raise SystemExit(
        f"{script_name}: refusing to write under apps/easi/data. The library's EASI version "
        f"({ASSESSMENT_ID}) is the authority for the method files and StreamCurves' exporter "
        f"is their only writer:\n"
        f"  python {EXPORTER} apps/library/assessments/{ASSESSMENT_ID}/v<N> "
        f"--out <pkg.zip> --write-easi-data apps/easi/data\n"
        f"Bring a change in as an authored version (import, revise, publish), then export it. "
        f"{DIRECT_WRITE_FLAG} skips this refusal during the transition and is removed at "
        f"adoption.")
