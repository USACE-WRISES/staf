"""EASI screening methods authored in StreamCurves.

EASI applies one national screening method; StreamCurves is where it is inspected,
revised, compared and packaged. The method itself is the eight files EASI scores with
(``_vendor/easi/method_package.py`` defines them and the package EASI loads); an EASI
project holds them byte for byte with the authoring record around them (lineage, the
candidate register and decisions, evidence references, notes, the preview case set, the
edit history).

- ``model``: the project (``EasiProject``) and its parts in a project file.
- ``io``: the explicit importer (EASI's files -> project), the exporter (project ->
  method package), ``fork`` (a draft revision) and project-file read/write.
- ``edit``: supported edits (band edges, regional edges, curve knots, text) and the
  semantic difference between versions.
- ``register``: the candidate register and the review flags it derives.
- ``evaluate`` / ``worker``: scoring any method version on a case set, always in a worker
  process, never in this app's own copy of EASI (DEEP authoring reads that copy).
"""
