# Review list: expn_fce sidecar 0.1.0

Generated with the sidecar. 24 columns are described; the ones below still need a person's decision. To settle one, add it to the table's overrides file (`sidecars/overrides/`) and generate the sidecar again; the sidecar itself is never edited by hand.

| Reason | Columns | Count |
| --- | --- | --- |
| Not in the data dictionary | `qyear`, `rec_orig`, `fcecode_`, `fcesav_`, `fcemo_`, `fces_hpd`, `fcesmtx_`, `fcenum2_`, `jfce_mqv` | 9 |
| Role and scale not assigned | `fcecode_`, `fcesav_`, `fcemo_`, `fces_hpd`, `fcesmtx_`, `fcenum2`, `jfcenmqv`, `fcenum2_`, `jfce_mqv` | 9 |
| Blank values: candidate for ce:missingCodes [""] | `fcesav`, `fcemo`, `fceschpd`, `fcepurch` | 4 |
| Stored values not in the dictionary's codes | `fcecode` (310, 330, 335, 400) | 1 |
| Still VERIFY: ce:unit | `fcesmtx` | 1 |
