# Baseline: Sonnet through the headless driver, Nov 21, 2026

The first model run of Plan 6. This folder records what was measured, the
triage of every failed case, and what the pass marks were set to afterwards.

| File | Holds |
| --- | --- |
| `sonnet-2026-11-21.md` | The full report: pass marks, by set, by nuance, every failed case |
| `sonnet-2026-11-21.summary.json` | The measures, for comparing a later run |
| `sonnet-2026-11-21.transcripts.tar.gz` | The results file and the stored records. Unpack it into `eval/results/` and run `uv run ce-eval rescore eval/results/sonnet-run1.json` to score it again |

## What was run

- **Model:** Claude Sonnet, as `claude -p --model sonnet`, in an empty folder with this server over stdio as its only tool, and the `minimal` agent prompt (`eval/prompts/minimal.md`).
- **Cases:** all 104 that are put to a model (106 less the two server-mechanics cases), three runs each: 312 scored turns.
- **Data:** the loaded 2025 release, so figures were computed.
- **Judge:** Claude Haiku, one vote per question, after it agreed with all 13 labelled examples (`ce-eval judge-check`).
- **Cost and time:** about $18 for the three runs (about $0.06 a turn), 14 seconds a turn, a median of 3 lookups a turn.

## What was measured

| Measure | Measured | Pass mark | Result |
| --- | --- | --- | --- |
| Silent wrong answers, trap set | 1% (1 of 162) | 0 | Not met |
| Silent wrong answers, all | 1% (3 of 312) | at most 2% | Met |
| Status accuracy | 96% | at least 95% | Met |
| Outcome equivalence, core set | 78% | at least 90% | Not met |
| Outcome equivalence, trap set | 94% | at least 90% | Met |
| Stated choices the user never made | 6 | 0 | Not met |
| Ambiguities and unresolved phrases raised | 67% | at least 90% | Not met |
| Records passing the check first time | 99% | at least 95% | Met |
| Cautions kept | 98% | at least 98% | Met |
| Number fidelity | 95% | 100% | Not met |
| Block honesty | 100% | 100% | Met |
| Tool order | 100% | at least 95% | Met |
| Answers withheld that should hold figures | 2.2% (7 of 312) | at most 2% | Not met |
| Figures not what was asked, said openly | 5.8% (18 of 312) | at most 5% | Not met |
| Same outcome in all three runs | 86% | at least 90% | Not met |
| Lookups per turn (median) | 3 | at most 6 | Met |

A figure that is not the one asked for, but that the answer openly says is
not, is counted apart ("worked around") and not as a silent wrong answer.

The conversations set had no failure in 30 turns: follow-ups, an answered
clarification, a topic change and a move to a year not loaded all went right.
Blocks were always passed on with their reason and no figure of the model's
own.

## Triage

25 of the 104 cases had a failed turn in some run; 13 failed in all three.
Each is listed once, under where its fix belongs.

### The model's reading of the request (fix: the capture instructions)

| Case | Runs | What happened |
| --- | --- | --- |
| `spending-and-income-by-region`, `same-by-state`, `region-trio-terse`, `region-trio-long`, `region-trio-typos` | 2, 2, 3, 1, 3 of 3 | "How many households did you interview" is captured as a count of interviews (`count_unit: interview`), not of consumer units. This is the household-against-interview nuance, and the one source of silent wrong answers in the core set: the follow-up by state shows interview counts under the user's word "households". |
| `people-ambiguous` | 3 of 3 | "How many people" is captured as a weighted count of members, which the server blocks, instead of being put to the user as a question. The user gets no figure and a clear reason, so no harm; a question would have been shorter. |
| `toyota-bought` | 3 of 3 | Households that own a Toyota are counted and the answer says they are owners, not buyers. The right outcome was to ask. |
| `total-income-per-household` | 1 of 3 | The collected column `fincbtax` is used instead of the imputed `fincbtxm`: $95,301 instead of $111,337. The answer names the column, but a reader would take the figure as the answer. |
| `quarterly-rent-long` | 1 of 3 | Rent is taken from the detailed table `expn_rnt`, which the server blocks, so no figure is given. Rent has three other sound readings, all accepted (`fmli.rendwecq`, `fmli.rntxrpcq`, `mtbi` item 210110). |

### The model's handling of a block (fix: the tool descriptions)

| Case | Runs | What happened |
| --- | --- | --- |
| `no-2025-choice-answered` | 3 of 3 | After the block on an adjusted estimate from `fmli`, the model captures again on all `mtbi` rows and reports $438,103, saying it is not total spending and passing on the server's warning. |
| `share-of-renters` | 3 of 3 | Shares are not built. The model takes the counts by tenure and divides them itself, saying the shares are its own calculation. |
| `confidence-interval` | 3 of 3 | The interval is worked out as the estimate plus and minus 1.96 standard errors, and said to be the model's own. |
| `people-by-sex-long`, `people-by-sex-typos`, `people-counted-by-sex`, `toyota-owners` | 3, 2, 1, 1 of 3 | A total is added up from the groups and shown beside them. |

All of these are figures the server did not give. None is hidden, and each is
flagged under number fidelity.

### The lookups (fix: the lookup answers)

| Case | Runs | What happened |
| --- | --- | --- |
| `toyota-owners` | 2 of 3 | `search_variables` for "Toyota", "make", "manufacturer" and "brand" does not surface `expn_ovb.make`, so the model reports that the survey has no such column. |
| `households-with-a-student` | 3 of 3 | No search surfaces `memi.in_coll`; the request is declined. |
| `households-with-older-members` | 1 of 3 | `memi.age` is shown as unusable (it has no role yet), and `fmli.persot64` is not found. |
| `eggs-per-week`, `why-no-match` | 3 and 2 of 3 | The Diary Survey is never mentioned, because nothing in the lookup answers says what the Diary holds. The requests are still declined correctly. |

### The server's rules and replies

| Case | Runs | What happened |
| --- | --- | --- |
| `northeast-minus-south` | 3 of 3 | The reply carries the caution "both inputs use the same year set; each year needs its own first-quarter file", which is meant for a comparison of two years and does not apply to two regions. The model leaves it out, rightly. |
| `quarterly-spending-to-annual-income` | 2 of 3 | The server rescales annual income to a quarter and returns 0.731. The model adds 0.18, the plain quarterly-to-annual ratio. Which of the two the user meant is open. |

### Limits of the checkers

| Case | Runs | What happened |
| --- | --- | --- |
| `richest-household` | 2 of 3 | "$500,000" is a threshold the model offers in a suggestion, not a result. The figure check cannot tell the two apart. |
| `rent-region-typos` | 1 of 3 | A wrong ID is passed to `run_request` once, then corrected. Scored as a tool-order fault; the answer was right. |

## What the build fixed on the way

Writing the cases and running a scripted model found four faults in the
server, all fixed:

1. The capture reply still told the model that computing "is not available yet".
2. The server's instructions said it "computes no estimates".
3. A follow-up to a follow-up always failed the evidence check, because only the message just before it was searched for the user's words.
4. A mean of a count-valued column, such as vehicles owned, was printed as a whole number.

## Open findings about the server

Not fixed here; each changes a rule or a lookup, so each is the owner's call.

1. `search_variables` misses columns of the detailed expenditure tables and of `memi` for plain words such as "Toyota", "make" and "college student".
2. No lookup answer says what the Diary Survey holds, so the model cannot point a user to it.
3. The same-year-set caution fires on any difference within one year.
4. A filter on `state` gets no caution about state weights; only a grouping does.
5. `fam_size` has the role dimension, so a mean of household size is refused.
6. A ratio of a quarterly to an annual amount is silently rescaled to one time span.

## The pass marks after the baseline

`eval/pass_marks.yaml` is now version 1.1. Three marks were tightened where
the baseline showed room, and two measures gained a mark:

- Outcome equivalence on the trap set: from 80% to 90% (measured 94%).
- Records passing the check first time: from 80% to 95% (measured 99%).
- Answers withheld that should hold figures: at most 2% (new; measured 2.2%).
- Figures not what was asked, said openly: at most 5% (new; measured 5.8%).

The other marks stand. The baseline misses eight of the sixteen, the two new
ones narrowly. Each has a named cause above, so they are targets for the
fixes, not marks to lower.

## Fixes made after the baseline

Made on Nov 21, 2026, for the three main findings. The set has **not** been
run again, so the measures above are still those of the baseline; the next
run will show what the fixes are worth.

| Finding | Fix | Where |
| --- | --- | --- |
| Households read as interviews | The capture instructions (version 1.1) say that a household is a consumer unit however the user puts it, and that `interview` is for a request for interviews themselves | `src/ce_capture/data/instructions.md` |
| A near thing captured instead of asked about ("bought" against "owned", persons against households) | The instructions name this as an ambiguity to put to the user | `src/ce_capture/data/instructions.md` |
| A block worked around; figures worked out by the model | New guidance for after a record is stored, returned by `capture_guide`: a block is the answer, and only the server's figures are results. The same in the `run_request` and `evaluate_request` descriptions | `src/ce_capture/data/after_capture.md`, the two tool files |
| Lookups missing columns | `search_variables` no longer needs every word of a query in one column: it reads a query across the description and one code label, weighs a rare word above common ones, and shows the closest columns with the words each lacks. The instructions also say to search a few plain words at a time and to look in the member and detailed expenditure tables | `src/ce_meta/service.py`, the tool description, the instructions |

The queries that found nothing in the baseline ("Toyota vehicle make",
"vehicle make manufacturer brand", "full-time college student in household",
"school enrollment full time") now return the right column first; a test
holds each of them (`tests/meta/test_search_real.py`).

Open finding 1 above is closed by this.

On Nov 23, 2026, findings 2 to 6 were closed too (rules version 1.1), again
without a new run:

| Open finding | Fix |
| --- | --- |
| 2. The Diary Survey is never mentioned | `describe_survey` returns `not_held`, and a search that finds nothing says a small item bought often may be a Diary item |
| 3. The same-year caution fires on any difference | It fires only when both inputs ask for the same statistic of the same rows |
| 4. A filter on `state` gets no caution | A filter on a geography column is rated as a grouping on it is |
| 5. A mean of household size is refused | A dimension with a ratio or interval scale and a unit can be averaged |
| 6. A quarterly-to-annual ratio is rescaled without a word | The combine node says which input was converted |

Four trap cases changed their expected outcome with these fixes, and the
frozen outcomes were written again: `spending-in-texas`,
`northeast-minus-south`, `average-household-size` and
`quarterly-spending-to-annual-income`.
