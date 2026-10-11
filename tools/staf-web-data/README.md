# STAF web data: the browser layout of STAF's releases

The static web app (`apps/staf-web`) reads STAF's published data by HTTP range. A browser can't read
GitHub release downloads (no CORS header), so this tool lays the releases out as plain files and copies
them to a Hugging Face dataset, `huggingface.co/datasets/gtmenichino/staf-data`. GitHub's releases stay
the source; nothing here is published anywhere else.

```bash
python tools/staf-web-data/run.py build [--regions 0108 0710]
C:\Users\gtmen\anaconda3\python.exe tools/staf-web-data/run.py upload --repo gtmenichino/staf-data
```

- **`build`** (`webdata/build.py`, the repo's `.venv`) writes `D:\Data\staf-web\site`:
  - `bundle/`: the STAF data bundle (`staf-data-current`), unzipped, read from `apps/hr-data/data`. It
    leaves out the 3DEP tile catalogs, which the browser never reads.
  - `xs/`: the cross-section grid as packed (`D:\Data\xs-grid\release`, `staf-xs-current`).
  - `library/`: the `library` release, downloaded to `D:\Data\staf-web\library-release`.
  - Every file is stored under a content-hashed name, hard-linked when on the same volume, so a file
    never changes in place. `web-manifest.json`, written last, maps each logical name to its file, size
    and sha256. A full build removes files the manifest no longer names; a `--regions` build doesn't.
- **`upload`** (`webdata/hf.py`, a Python with `huggingface_hub`, after `hf auth login` with a Write
  token) creates the dataset if needed and uploads the layout (resumable, many commits), then
  `web-manifest.json` last. It uploads only what the dataset lacks.

**Before an upload, check the bundle against what's published.** `build` reads the working bundle
folder, so a local change that isn't published (such as the 3 m quad catalog fix of 2026-10-09) would
otherwise reach the mirror. On 2026-10-11, all 3,930 mirrored bundle files equalled the published
`staf-data-current`: the zips by sha256, their members by size and CRC-32, and the plain tables by
sha256.
