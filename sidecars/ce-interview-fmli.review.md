# Review list: fmli sidecar 0.4.0

Generated with the sidecar. 786 columns are described; the ones below still need a person's decision. To settle one, add it to the table's overrides file (`sidecars/overrides/`) and generate the sidecar again; the sidecar itself is never edited by hand.

| Reason | Columns | Count |
| --- | --- | --- |
| Still VERIFY: ce:unit | `fpripenx`, `frretirx`, `fsalaryx`, `inc_hrs1`, `inc_hrs2`, `inc_rank`, `incweek1`, `incweek2`, `lumpsumx`, `nonincmx`, `othrincx`, `welfarex` and 114 more | 126 |
| Blank values: candidate for ce:missingCodes [""] | `diracc`, `educa2`, `incnonw1`, `incnonw2`, `incomey1`, `incomey2`, `occucod1`, `occucod2`, `popsize`, `race2`, `sex2`, `unistrq` and 42 more | 54 |
| Possibly top-coded (its flag shows T, U, V or W) | `age_ref`, `age2`, `fgovretx`, `findretx`, `fjssdedx`, `fpripenx`, `frrdedx`, `fsalaryx`, `inc_rank`, `misctaxx`, `lumpsumx`, `nonincmx` and 37 more | 49 |
| Imputation indicator: code meanings not described | `frretiri`, `fsalaryi`, `fssixi`, `othrinci`, `welfarei`, `fs_amtxi`, `fsmpfrxi`, `intrdvxi`, `netrenti`, `othregxi`, `retsurvi`, `royestxi` | 12 |
| Role and scale not assigned | `fs_amtx1`, `fs_amtx2`, `fs_amtx3`, `fs_amtx4`, `fs_amtx5`, `fs_amtxm`, `totxest`, `cbsaprin` | 8 |
| Imputation set: base column not found | `fs_amtx1`, `fs_amtx2`, `fs_amtx3`, `fs_amtx4`, `fs_amtx5`, `fs_amtxm` | 6 |
| Still VERIFY: ce:level | `age_ref`, `age2`, `inc_hrs1`, `inc_hrs2`, `incweek1`, `incweek2` | 6 |
| Not in the data dictionary | `fdhomepq`, `fdhomecq`, `erecvehc`, `erecvehp`, `totxest`, `cbsaprin` | 6 |
| Still VERIFY: ce:topcoded | `fam_size`, `totexppq`, `totexpcq` | 3 |
| Imputation indicator: column it describes not found | `fs_amtxi` | 1 |

## Still VERIFY: ce:unit

`fpripenx`, `frretirx`, `fsalaryx`, `inc_hrs1`, `inc_hrs2`, `inc_rank`, `incweek1`, `incweek2`, `lumpsumx`, `nonincmx`, `othrincx`, `welfarex`, `erankh`, `welfrebx`, `lmpsumbx`, `otrincbx`, `erankhm`, `fpripenm`, `frretirm`, `frretir1`, `frretir2`, `frretir3`, `frretir4`, `frretir5`, `fsalarym`, `fsalary1`, `fsalary2`, `fsalary3`, `fsalary4`, `fsalary5`, `inc_rnkm`, `inc_rnk1`, `inc_rnk2`, `inc_rnk3`, `inc_rnk4`, `inc_rnk5`, `othrincm`, `othrinc1`, `othrinc2`, `othrinc3`, `othrinc4`, `othrinc5`, `welfarem`, `welfare1`, `welfare2`, `welfare3`, `welfare4`, `welfare5`, `credfinx`, `creditbx`, `creditx`, `credtyrx`, `credyrbx`, `fsmpfrmx`, `fsmpfrx1`, `fsmpfrx2`, `fsmpfrx3`, `fsmpfrx4`, `fsmpfrx5`, `fsmpfrxm`, `intrdvbx`, `intrdvx`, `intrdvx1`, `intrdvx2`, `intrdvx3`, `intrdvx4`, `intrdvx5`, `intrdvxm`, `irabx`, `irax`, `irayrbx`, `irayrx`, `liqdyrbx`, `liquidbx`, `liqudyrx`, `liquidx`, `mlpaywkx`, `mlpyqwks`, `netrentx`, `netrntbx`, `netrent1`, `netrent2`, `netrent3`, `netrent4`, `netrent5`, `netrentm`, `othastbx`, `othastx`, `othfinx`, `othlonbx`, `othlyrbx`, `othlnyrx`, `othlonx`, `othregbx`, `othregx`, `othregx1`, `othregx2`, `othregx3`, `othregx4`, `othregx5`, `othregxm`, `othsyrbx`, `othstyrx`, `retsrvbx`, `royestbx`, `royestx`, `royestx1`, `royestx2`, `royestx3`, `royestx4`, `royestx5`, `royestxm`, `stckyrbx`, `stdntyrx`, `stdtyrbx`, `stockyrx`, `stockbx`, `stockx`, `studfinx`, `studntbx`, `studntx`, `whlfyrbx`, `whlfyrx`, `wholifbx`, `wholifx`, `vehictax`

## Blank values: candidate for ce:missingCodes [""]

`diracc`, `educa2`, `incnonw1`, `incnonw2`, `incomey1`, `incomey2`, `occucod1`, `occucod2`, `popsize`, `race2`, `sex2`, `unistrq`, `lumpsumb`, `othrincb`, `horref1`, `horref2`, `psu`, `hisp2`, `creditb`, `credyrb`, `defbenrp`, `intrdvb`, `irab`, `irayrb`, `liqudyrb`, `liquidb`, `mealspay`, `netrentb`, `othastb`, `othlnyrb`, `othloan`, `othlonb`, `othregb`, `othstyrb`, `retsurvb`, `royestb`, `stdntyrb`, `stockyrb`, `stockb`, `studntb`, `whlfyrb`, `wholifb`, `buscreen`, `division`, `credyr`, `ira`, `irayr`, `liqudyr`, `liquid`, `othlnyr`, `othstyr`, `stdntyr`, `stockyr`, `whlfyr`

## Possibly top-coded (its flag shows T, U, V or W)

`age_ref`, `age2`, `fgovretx`, `findretx`, `fjssdedx`, `fpripenx`, `frrdedx`, `fsalaryx`, `inc_rank`, `misctaxx`, `lumpsumx`, `nonincmx`, `othrincx`, `renteqvx`, `fgovretm`, `fjssdedm`, `fpripenm`, `frrdedm`, `fsalarym`, `inc_rnkm`, `othrincm`, `credfinx`, `creditx`, `credtyrx`, `fsmpfrmx`, `intrdvx`, `irax`, `irayrx`, `liqudyrx`, `liquidx`, `netrentx`, `othastx`, `othfinx`, `othlnyrx`, `othlonx`, `othregx`, `othstyrx`, `retsurvx`, `royestx`, `stockyrx`, `stockx`, `whlfyrx`, `wholifx`, `fsmpfrxm`, `intrdvxm`, `netrentm`, `othregxm`, `retsurvm`, `royestxm`
