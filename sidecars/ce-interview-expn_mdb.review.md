# Review list: expn_mdb sidecar 0.1.0

Generated with the sidecar. 37 columns are described; the ones below still need a person's decision. To settle one, add it to the table's overrides file (`sidecars/overrides/`) and generate the sidecar again; the sidecar itself is never edited by hand.

| Reason | Columns | Count |
| --- | --- | --- |
| Blank values: candidate for ce:missingCodes [""] | `medpcy1`, `medpcy2`, `medpcy3`, `medpcy4`, `medpcy5`, `medpcy6`, `medpcy7`, `medpcy8`, `medpcy9`, `medpnout` | 10 |
| Stored values not in the dictionary's codes | `medpcary` (415, 525, 645), `medpcy1` (415, 525, 645), `medpcy2` (415, 525, 645), `medpcy3` (525, 645), `medpcy4` (645), `medpcy5` (525) | 6 |
| Still VERIFY: ce:unit | `medpmtx` | 1 |
| Possibly top-coded (its flag shows T, U, V or W) | `medpmtx` | 1 |
