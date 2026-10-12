"""Upload the browser layout to a Hugging Face dataset, ``web-index.json.gz`` and ``web-manifest.json`` last.

The static STAF app reads its data from there by HTTP range (GitHub's release downloads send no CORS
header). The releases on GitHub stay the source; this copies the layout ``build`` wrote. It needs a
Python with ``huggingface_hub`` and a login with write access (``hf auth login``; the token is read
by the library, never by this code):

    C:\\Users\\gtmen\\anaconda3\\python.exe tools/staf-web-data/run.py upload --repo gtmenichino/staf-data
"""
from __future__ import annotations

import json
from pathlib import Path

MANIFEST = "web-manifest.json"
#: the app's copy of the manifest (build.app_index)
INDEX = "web-index.json.gz"


def upload(site: Path, repo_id: str, *, private: bool = False, workers: int | None = None, log=print) -> str:
    """Create the dataset if needed (public unless ``private``), upload every file of ``site`` but the
    manifest (resumable, many commits), then the manifest. Returns the dataset's URL."""
    from huggingface_hub import HfApi
    site = Path(site)
    manifest = json.loads((site / MANIFEST).read_text(encoding="utf-8"))
    api = HfApi()
    user = api.whoami()["name"]                          # raises when not logged in
    log(f"logged in to Hugging Face as {user}; uploading {len(manifest['files'])} files "
        f"({sum(e['bytes'] for e in manifest['files'].values()) / 1e9:.2f} GB) to datasets/{repo_id}")
    api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
    # never the build's cache (.cache/: the copies the layout is made from, about as large as the layout)
    api.upload_large_folder(repo_id=repo_id, folder_path=str(site), repo_type="dataset",
                            ignore_patterns=[MANIFEST, INDEX, "*.tmp", "*.lock", ".cache", ".cache/*", ".cache/**"],
                            num_workers=workers)
    # the app's index (what the app reads), then the manifest, once every file they name is up
    if (site / INDEX).exists():
        api.upload_file(path_or_fileobj=str(site / INDEX), path_in_repo=INDEX, repo_id=repo_id,
                        repo_type="dataset", commit_message=f"{INDEX} ({manifest.get('built')})")
    api.upload_file(path_or_fileobj=str(site / MANIFEST), path_in_repo=MANIFEST, repo_id=repo_id,
                    repo_type="dataset", commit_message=f"{MANIFEST}, written last ({manifest.get('built')})")
    url = f"https://huggingface.co/datasets/{repo_id}"
    log(f"uploaded; the app reads {url}/resolve/main/{MANIFEST}")
    return url
