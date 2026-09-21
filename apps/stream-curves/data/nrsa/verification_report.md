# NRSA archive verification

Written by `scripts/nrsa/verify_inputs.py`. Do not edit by hand.

- Visits: 6,473, independent stations: 4,378. A pool counts stations, never visits.
- Visits by cycle: {'1314': 2261, '1819': 2110, '2324': 2102}
- Protocol by cycle: {'1314 BOATABLE': 909, '1314 WADEABLE': 1331, '1819 BOATABLE': 884, '1819 WADEABLE': 1226, '2324 BOATABLE': 819, '2324 WADEABLE': 1281}
- Fish sampling flags (one per station and cycle): {'(not recorded)': 3479, 'YES-SUFFICIENT': 1420, 'NO-SITE CONDITIONS': 362, 'YES->20 CW SAMPLED,<500 INDIV': 49, 'YES-50-90% OF REACH': 47, 'SEINING ONLY': 8, 'NO->20 CW SAMPLED, <500 INDIV': 7, 'NO-<20 CW SAMPLED,<500 INDIV': 5, 'NO->50% OF REACH,<25 INDIV': 5, 'YES-<20 CW SAMPLED, >500 INDIV': 4, 'NO-<40 CW SAMPLED': 4, 'YES->50% OF REACH,>=25 INDIV': 1, 'NO-<20 CW SAMPLED': 1}
- Strict screen passes: 774, relaxed tier: 1173
- EPA NRSA reference (R) sites by NARS-9 region, 2013-14 designations: {'CPL': 23, 'NAP': 21, 'NPL': 50, 'SAP': 11, 'SPL': 18, 'TPL': 8, 'UMW': 20, 'WMT': 43, 'XER': 24}

## Checks

- All checks pass.

## Cycle compatibility of the mapped metrics

| Metric | Cycles with values | Pooled cycles | Origin | Units check |
|---|---|---|---|---|
| bent_EPT_NTAX | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:legacy_r_app; 2324:epa_published | ok |
| bent_HPRIME | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:legacy_r_app; 2324:epa_published | ok |
| bent_TOLRPIND | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:legacy_r_app; 2324:epa_published | ok |
| bent_TOTLNTAX | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:legacy_r_app; 2324:epa_published | ok |
| chem_CHLA | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| chem_COND | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| chem_DOC | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| chem_NTL | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| chem_PH | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| chem_PTL | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| chem_TURB | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| fish_NAT_NTOLNTAX | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:legacy_r_app; 2324:epa_published | ok |
| fish_NAT_TOTLNTAX | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:legacy_r_app; 2324:epa_published | ok |
| phab_BFWD_RAT | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_LRBS_use | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_LSUB_DMM | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_LWDeqVolM100 | 1314,1819,2324 | 1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_PCT_FAST | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_PCT_SAFN | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_RP100_cm | 1819,2324 | 1819,2324 | 1819:epa_published; 2324:epa_published | ok |
| phab_SINU | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_XBKA | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_XBKF_H | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_XCDENMID | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_XCMGW | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_XEMBED | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
| phab_XFC_NAT | 1314,1819,2324 | 1314,1819,2324 | 1314:epa_published; 1819:epa_published; 2324:epa_published | ok |
