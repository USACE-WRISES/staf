# NRSA harmonization audit

Written by `scripts/nrsa/build_harmonization_audit.py` from the multi-cycle archive under `data/nrsa/`. Do not edit by hand: `--check` and `tests/test_harmonization_audit.py` compare both files with a fresh build. The per-metric rows and every column's derivation are in `nrsa_harmonization_audit.csv` and the script's docstring; this file holds the counts.

- Mapped NRSA metrics (`config/metric_map.yaml`, source nrsa): 30. Rows: 120 (90 cycle rows, 30 pooled rows).
- Run frame (rule DATA-10, wadeable, non-canal): 3,267 stations, 770 pass the strict screen. The screen table's order-only `wadeable` flag marks 3,190 non-canal stations; the 77 further stations in the frame have no stream order and were sampled with the wadeable protocol.
- Site-visits: 6,473; second visits in the same cycle: 567; `R`-coded repeat rows, not paired: 2.
- Metrics with repeat-visit pairs: 30 of 30; pairs over the pooled cycles of every metric: 13,344. Second visits of a backfilled cycle, copies of the first, not counted: 1,577.
- DATA-11 selection: 118,600 station values over every metric, 1,306 not from the station's newest sampled cycle.
- Rows with problems: 3.
  - phab_BFWD_RAT 1314: 3 non-finite value(s)
  - phab_BFWD_RAT pooled: 1 non-finite selected value(s)
  - phab_RP100_cm 1314: eligible cycle carries no values

Columns the archive cannot fill:

- `detection_limit_handling` documents what EPA publishes beside a value column; the archive carries only the value columns, so non-detects are not counted.
- `units` for 8 count and proportion metrics that neither the dictionary nor the catalog gives a unit for: bent_EPT_NTAX, bent_HPRIME, bent_TOLRPIND, bent_TOTLNTAX, fish_NAT_NTOLNTAX, fish_NAT_TOTLNTAX, phab_BFWD_RAT, phab_SINU.
- `domain_min`, `domain_max` and `out_of_domain_n` for 3 metrics with no declared physical domain: chem_DOC, phab_LRBS_use, phab_LSUB_DMM.
- `epa_definition` on 23 cycle rows where EPA published no definition for that cycle.
