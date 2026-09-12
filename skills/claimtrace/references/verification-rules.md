# Verification rules

## Claim types and what settles them

| Type | Example | How to verify |
|---|---|---|
| quantitative | "accuracy improves by 12%" | `csv_delta` or `csv_stat` against the results file, with `--reported`. |
| experimental | "evaluated on five datasets" | `csv_unique_count` on the relevant column, with `--reported`. |
| methodological | "all models share one training schedule" | `text_search` over configs and extracted text; read the hits. |
| interpretive | "more robust to noise" | Usually `missing_evidence` unless a specific artifact varies the factor. |

## Choosing an op

- `csv_stat` — one aggregate of one column. `{"path", "column", "agg", "filters"}`.
  `agg` is mean, median, sum, min, max, count, nunique, or std.
- `csv_unique_count` — how many distinct values a column takes. Use for
  "N datasets", "N seeds", "N configurations".
- `csv_delta` — a baseline against a treatment. Two shapes:
  - two columns: `{"path", "baseline_column", "treatment_column"}`
  - one column split by a group: `{"path", "value_column", "group_column", "baseline", "treatment"}`
- `text_search` — `{"pattern", "regex": false, "paths": [...]}`. Omit `paths` to
  search all extracted text.

Always run `inspect-csv` before a CSV op. Guessing a column name wastes a turn;
the error will list the real ones, but the inspection is cheaper.

## The percent trap

"Improves accuracy by 12%" has two honest readings when accuracy is itself a
percentage: a relative change of 12%, or a gain of 12 percentage points.
`csv_delta` returns both, and `--reported` checks the claim against both plus
the treatment value itself. If any matches, the claim is `supported` and the
record names which reading matched. Never resolve this ambiguity yourself by
picking the flattering interpretation.

## Tolerance

Default is 0.5 absolute. Widen it with `--tolerance` only when the manuscript
rounds explicitly ("about 12%", "~12%"), and say so in the explanation. Never
widen a tolerance to turn a conflict into a supported claim.

## Filters

Every op takes `filters`: a list of `{"column", "op", "value"}` where op is eq,
ne, lt, lte, gt, gte, or in. Use them when a claim is scoped ("on ImageNet",
"at the largest model size"). State the filter in the explanation, because a
filtered result is a narrower statement than an unfiltered one.

## When evidence is absent

Before concluding `missing_evidence`, you must have:

1. run `retrieve` with the claim text, and
2. run at least one `text_search` for the distinctive term in the claim.

Then say what artifact *would* settle it. "No noise-sweep experiment exists;
every row in results.csv records noise_level 0.0" is useful. "No evidence found"
is not.
