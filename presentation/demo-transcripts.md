# Demo transcripts for the hackathon deck

Captured 2026-11-23 from the live `ce-mcp` server (rules 1.1, vocabulary 0.1.3,
fmli sidecar 0.4.0, 2025 data). Each run: `capture_guide` → lookups
(`search_variables`, `list_geographies`) → `submit_request_record` →
`run_request`. Reply text is verbatim.

## 1. Pass with cautions: a mean with its standard error

**User:** "Average total spending per household in 2025, as released, with its standard error."

- Status: `captured` (req-bbfccdaabf74). The server built the calendar-pair measure
  (`totexppq` + `totexpcq`), with weight `finlwt21` and annual unit filled in by default.
- Verdict: **warn**, method `do_calendar_pair_mean`, standard errors from replicate weights.

```
Average total spending per household
  Computed: the mean of total expenditures (totexppq, totexpcq), weighted, per
  consumer unit per year.
  Result:
    All: $81,370 (standard error $988)
  Caution: totexpcq is still under review in the sidecar.
  Caution: 2025 as released. Fourth-quarter interviews stand for one third of
  the usual population.
```
16,754 interviews, 9,925 consumer units.

## 2. Warn: a state figure (no state weights after 2023)

**User:** "Average total spending per household in Texas in 2025, as released."

- Status: `captured` (req-c97f36dd8204). Verdict: **warn**, rule `geography_limited`.

```
Average total spending per household in Texas
  Result:
    All: $77,997 (standard error $2,903)
  Caution: No state weights exist after 2023, so a state figure rests on the
  national weight; region or division is reliable.
  Caution: 2025 as released. Fourth-quarter interviews stand for one third of
  the usual population.
```
1,008 interviews, 584 consumer units. Baseline: 22% silent-wrong on this nuance.

## 3. Block: a geography the design does not support

**User:** "Average total spending per household by primary sampling unit in 2025, as released."

- Status: `captured` (req-d5b71d859ff7). Verdict: **block**, rule `geography_unsupported`.
  No data read for the block.

```
Average total spending per household by primary sampling unit
  Cannot compute: The survey design does not support estimates at that geography
  level.
```

## 4. Clarify: "bought" where the data only shows "owned"

**User:** "How many households bought a Toyota in 2025, as released?"

- Status: `needs_clarification` (req-2377c3402b72). No figure given.

```
The vehicle file lists vehicles owned, not purchases. Do you want households
that own a Toyota, or only those that bought one in 2025?
```
Baseline: models got this nuance right only 44% of the time.

## Found while capturing

`search_variables("total spending", table=fmli, usable_as=measure)` did not
return `totexpcq`; "total expenditures this quarter" did. Plain-word search
recall is worth checking, and might be a lessons-learned point.
