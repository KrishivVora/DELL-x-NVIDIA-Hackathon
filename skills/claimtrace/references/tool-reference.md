# Tool reference

Invoke as `claimtrace --project <id> <command>` (or
`python -m claimtrace --project <id> <command>`). Every command prints one JSON
object. Failures print `{"error": "..."}` and exit 1.

## project-status

No arguments. Returns the manifest summary, the identified manuscript,
`documents[]` with extraction status, the number of recorded claims, and
`changes_since_last_audit`. Always the first call.

## read

`--path <project-relative>` `[--page N]` `[--start N]` `[--max-lines N]` `[--raw]`

Reads the extracted text for a document by default; `--raw` reads the original.
Defaults to 120 lines. `--page` selects one page of extracted text. The response
reports `lines` and `truncated` so you can page through with `--start`.

## retrieve

`--query "<text>"` `[-k N]` (default 5)

Returns `backend` plus `results[]` of `{path, page, section, text, score}`.
When `backend` is `local-keyword-fallback`, semantic retrieval is unavailable
and `fallback_reason` says why — the audit still proceeds, but say so if asked.

## inspect-csv

`--path <csv>` `[--max-rows N]`

Returns row count, and per column: name, dtype, distinct count, sample values,
plus a head. Run before any CSV verification.

## verify

`--op <csv_stat|csv_unique_count|csv_delta|text_search>` `--args '<json>'`
`[--reported N]` `[--tolerance N]` `[--tolerance-mode absolute|relative]`
`[--claim-id C]`

`--args` is a JSON object; include `"path"` for the CSV ops. With `--reported`
the response includes a `comparison` carrying the authoritative `status`. With
`--claim-id` the calculation and its result are written onto that claim record.
Pass both together for a quantitative claim.

Example:

```bash
claimtrace --project project-123 verify --op csv_delta \
  --args '{"path":"originals/results.csv","value_column":"accuracy","group_column":"method","baseline":"baseline","treatment":"ours"}' \
  --reported 12.0 --claim-id claim-001
```

## claim-set

`[--claim-id C]` `--claim "<text>"` `--location "Results, page 7"`
`--type <quantitative|experimental|methodological|interpretive>`
`[--status S]` `[--reported N]` `[--explanation "..."]` `[--action "..."]`
`[--confidence high|medium|low]` `[--evidence-files p1 p2]`

Omit `--claim-id` to create a new claim; the response carries the assigned id.
Pass an existing `--claim-id` to update fields in place.

## claim-list

`[--status <supported|conflicting|missing_evidence|unverifiable>]`

## changes

No arguments. Returns `changed` (added/removed/modified), `changed_count`,
`affected_claims` (the claims whose `evidence_files` include a changed file),
and `unaffected_claim_count`.

## snapshot

No arguments. Records the current file hashes as the audited baseline, so
`changes` is empty until something moves again. Run it at the end of an audit,
never in the middle.

## report

No arguments. Writes `reports/audit-<timestamp>.md` and `reports/latest.md`,
returns both paths and the status counts.

## notify

`[--reason "<short reason>"]`

Returns the single sanitized line that may be sent to Slack: project id, counts
by status, changed-file count, and the local report path. It contains no claim
text, no values, and no excerpts. Never compose this message yourself.

## log

`--event <name>` `[--field k=v ...]`

Appends a metadata-only event to the audit history. Never log document content.
