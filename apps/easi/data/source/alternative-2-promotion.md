# Alternative 2 scoring promotion

Alternative 2 uses 34 frozen curves: nine NARS-9 regions plus a national fallback
for each of natural riparian cover, woody riparian cover and monthly flow
variability; three slope classes plus a national fallback for entrenchment.
The promoted reference-curves.json is byte-identical to the completed study's
Alternative 2 candidate. No curve was refitted or rounded during promotion.

Four function slots can change relative to Alternative 1: low-flow connectivity,
carbon processing, habitat provision and the woody component of light and thermal
regime. Existing 13/8/3 scores, weights, missingness, composite rules and rating
anchors are retained. Entrenchment curves and all unrelated data assets are
unchanged. The catalog has four prose corrections from Level II to NARS-9;
its executable definitions match the candidate exactly.

`scoring-identity.json` supplies the alternative name, count and artifact hashes.
`EASI_CRITERIA_SET=regional` selects the promoted method. The existing `legacy`
setting retains the historical baseline, which is distinct from Alternative 1.
Alternative 1 remains in the immutable completed study snapshot. There is no
interactive scoring-method selector.

## Reproduce

From the repository root, use the workspace interpreter:

```powershell
& .\.venv\Scripts\python.exe -B apps/easi/scripts/promote_alternative_2.py --study D:/Data/easi-national/review/alternative-studies/2026-09-15-controlled-alternatives
```

The command verifies the completed study, candidate, Alternative 1 catalog and
curves, original registry identity, NARS geography and unrelated live assets
before writing. It refuses unrecognized changes to live target files. Outputs
are deterministic and each file is replaced atomically. Repeating the command
preserves timestamps for unchanged outputs. It does not alter the source study,
run analysis, rescore reaches, create tiles, enqueue jobs or publish anything.

`alternative-2-promotion.json` records the source and output hashes. The two
promoted JSON files retain their candidate CRLF bytes through targeted Git
`-text` attributes, including their canonical vendored copies. The method digest
also includes the scoring identity and official NARS geography asset, so method
identity changes when either scoring metadata or region boundaries change.

Promoting a scoring method does not make older national maps current. A separate,
verified local rebuild must bind the same method and assets to its scores,
statistics, tiles and completion receipt before those outputs are offered.
