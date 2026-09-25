# Provenance notes for files under data/

The NRSA archive documents itself (`nrsa/manifest.json`, `nrsa/sources.lock.json`,
`nrsa_provenance.json`, `nrsa/verification_report.md`) and the SQT registry names its
inputs (`sqt/README.md`). This note records what the remaining reference files can and
cannot say about where they came from.

## ecoregions_l3.geojson

EPA Level III ecoregions of the conterminous United States as a GeoJSON
FeatureCollection: 85 features, each with the properties `US_L3CODE`, `US_L3NAME` and
`NA_L3NAME`, and no `crs`, `name` or edition member. The codes run 1 to 85, the numbering
of EPA's 2013 revision of the Level III map, which is the edition the properties imply.

The download the file was made from and the date it was retrieved were not recorded when
it was added, and the file carries nothing that states them; they stay unrecorded rather
than guessed. What can be verified, and was on 2026-09-25 in a fresh LF worktree
(`scripts/check_geojson_editions.py` in the campaign notes): this copy,
`apps/easi/data/ecoregions_l3.geojson` and `apps/deep/data/ecoregions_l3.geojson` are
byte-identical, 1,054,416 bytes, sha256 `e91f411d4976ef44...`, with identical codes,
properties and geometry. A working copy checked out with CRLF line endings hashes
differently; the bytes above are git's.

The apps draw the file as a map overlay and read the codes and names from it. A station's
ecoregion code in the NRSA tables comes from EPA's site files, not from this file.
