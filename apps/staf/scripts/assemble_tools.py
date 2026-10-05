"""Copy EASI, SFARI and DEEP into apps/staf/_tools/ for a STAF deploy.

Posit Publisher uploads one folder, so the STAF app carries its tools inside it. Each tool's
tracked Publisher config (``apps/<tool>/.posit/publish/<name>.toml``; never its ``deployments/``
records) already lists what that tool deploys. This copies exactly those files, as a commit would
take them (tracked files and new ones git does not ignore, working-tree bytes, so an uncommitted
change can be tried), into ``_tools/<tool>/`` (gitignored)
and writes ``_tools/MANIFEST.json`` with the commit, whether the tools had uncommitted changes, and
every file's sha256; ``tests/test_assembled.py`` checks it against the repo. DEEP's
``data/bundles/`` (the bake's per-assessment copies, never read at runtime) stays out.

    python apps/staf/scripts/assemble_tools.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]           # apps/staf
APPS = HERE.parent
REPO = APPS.parent
OUT = HERE / "_tools"
TOOLS = ("easi", "sfari", "deep")
#: Publisher entries the STAF app supplies itself (its own pins and Python) or never ships
SKIP_ENTRIES = ("/.posit/", "/requirements.txt", "/.python-version")
#: tracked files a tool deploys on its own but STAF leaves out
SKIP_PATHS = dict(deep=("data/bundles/",))


def publisher_config(tool: str) -> Path:
    configs = sorted((APPS / tool / ".posit" / "publish").glob("*.toml"))
    if len(configs) != 1:
        raise SystemExit(f"{tool}: expected one Publisher config, found {len(configs)}")
    return configs[0]


def tracked(tool: str) -> list[str]:
    """The tool's files as a commit would take them (tracked, plus new files git does not ignore),
    relative to apps/<tool>."""
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
                          f"apps/{tool}"], cwd=REPO, capture_output=True, check=True).stdout.decode("utf-8")
    prefix = f"apps/{tool}/"
    return sorted(p[len(prefix):] for p in out.split("\0") if p.startswith(prefix))


def selection(tool: str, files: list[str]) -> list[str]:
    """The files of ``files`` that the tool's Publisher list deploys, less what STAF leaves out."""
    entries = tomllib.loads(publisher_config(tool).read_text(encoding="utf-8"))["files"]
    keep = [e.lstrip("/") for e in entries if not e.startswith(SKIP_ENTRIES)]
    skip = SKIP_PATHS.get(tool, ())
    return [rel for rel in files
            if not rel.startswith(skip)
            and any(rel.startswith(e) if e.endswith("/") else rel == e for e in keep)]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    manifest = dict(head=git("rev-parse", "HEAD"),
                    dirty=bool(git("status", "--porcelain", "--", *[f"apps/{t}" for t in TOOLS])),
                    tools=dict())
    total = 0
    for tool in TOOLS:
        files = selection(tool, tracked(tool))
        hashes = dict()
        for rel in files:
            src, dest = APPS / tool / rel, OUT / tool / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            hashes[rel] = sha256(dest)
            total += dest.stat().st_size
        manifest["tools"][tool] = dict(files=hashes)
        print(f"{tool}: {len(files)} files")
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    state = "with uncommitted changes" if manifest["dirty"] else "clean"
    print(f"_tools/: {total / 2**20:.1f} MB from {manifest['head'][:12]} ({state})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
