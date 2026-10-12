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
  - `library/`: the `library` release, downloaded to `D:\Data\staf-web\library-release`, and
    `deep-regions.json`: DEEP's assessment regions sidebar, what DEEP's server sends it
    (`webdata/deep_regions.py` runs DEEP's own code over that folder, so the list is the one a DEEP
    deployed from this checkout serves: 85 regions, 1.06 MB, on 2026-10-11).
  - `points/`: the bundle's national point tables (NID, NWIS gages, WQP stations and results, NAS)
    sorted by place (`webdata/points.py`): the same rows, along a Hilbert curve, in row groups of 16,384
    (results 131,072) cut into pages of 512 (1,024) rows with a page index, so a lookup near a site reads
    a few pages; NAS by HUC12, then HUC8.
  - **Page indexes** (`webdata/pages.py`): the files the browser reads a few rows of at a time (each
    region's stream lines, StreamCat rows, V2 attributes, point extras and catchment values: land cover,
    riparian strips, roads, soils, crossings; the grid's numbers, sections and median profiles) are
    written again with the same rows, order and values (the lines in row groups of 16,384, where the
    bundle's 4,096 gave 0512 a 0.87 MB footer), in pages of 32 to 1,024
    rows with a page index (dictionaries only in the grid's files, for columns of 256 values or fewer:
    a page read takes its column's dictionary too; zstd level 12), so one row costs kilobytes, not the
    row group's whole columns (1.6 MB for one StreamCat row in 0108). The copies are about a fifth
    larger than their sources.
  - `topo/`: each region's network topology (`hr_<vpu>.parquet`, `v2_<vpu>.parquet`: id, hydroseq, steps,
    outlets, drainage area, in the lines file's row order) on its own, read in one request where the lines
    file takes dozens (`webdata/topo.py`).
  - The JSON and GeoJSON files (read whole: DEEP's bundles and region list, the catalogs, the engine's
    polygons) are stored gzipped (`.json.gz`, `.geojson.gz`), since the host sends every file as it is:
    35.5 MB become 4.5 MB (a DEEP bundle a tenth, the polygons a third). Their entries describe the
    stored file and keep the content's size and sha256 (`raw_bytes`, `raw_sha256`).
  - `web-index.json.gz`: what the app reads of the manifest (paths and sizes, the library's sha256, the
    sizes of the files stored gzipped, each parquet file's footer size so its first read is exact, the
    bundle datasets' regions and vintages), gzipped: about 130 KB where the manifests are 1.7 MB, which
    the host sends uncompressed. The app reads it and falls back to `web-manifest.json`.
  - The copies sit in the build's cache (`.cache/`, never uploaded) and are written again when their
    source or their layout version changes; so are the point tables.
  - Every file is stored under a content-hashed name, hard-linked when on the same volume, so a file
    never changes in place. `web-manifest.json`, written last, maps each logical name to its file, size
    and sha256. A full build removes files the manifest no longer names; a `--regions` build doesn't.
- **`upload`** (`webdata/hf.py`, a Python with `huggingface_hub`, after `hf auth login` with a Write
  token) creates the dataset if needed and uploads the layout (resumable, many commits, never `.cache/`),
  then `web-index.json.gz` and `web-manifest.json` last. It uploads only what the dataset lacks; files
  a new layout replaced stay on the hub until deleted (with the owner's go).

**Before an upload, check the bundle against what's published.** `build` reads the working bundle
folder, so a local change that isn't published (such as the 3 m quad catalog fix of 2026-10-09) would
otherwise reach the mirror. On 2026-10-11, all 3,930 mirrored bundle files equalled the published
`staf-data-current`: the zips by sha256, their members by size and CRC-32, and the plain tables by
sha256.
