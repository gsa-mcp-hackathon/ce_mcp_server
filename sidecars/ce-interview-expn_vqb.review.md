# Review list: expn_vqb sidecar 0.1.0

Generated with the sidecar. 18 columns are described; the ones below still need a person's decision. To settle one, add it to the table's overrides file (`sidecars/overrides/`) and generate the sidecar again; the sidecar itself is never edited by hand.

| Reason | Columns | Count |
| --- | --- | --- |
| Not in the data dictionary | `qyear`, `rec_orig`, `vqbcode_`, `vqbl_bor`, `vqbmo_`, `vqbexpx_` | 6 |
| Role and scale not assigned | `vqbcode_`, `vqbl_bor`, `vqbmo_`, `vqbexpx_` | 4 |
| Stored values not in the dictionary's codes | `vqbcode` (900), `vqbmo` (01, 02, 03, 04, 05 ...) | 2 |
| Blank values: candidate for ce:missingCodes [""] | `vqblabor`, `vqbpurch` | 2 |
| Still VERIFY: ce:unit | `vqbexpx` | 1 |
