# Claim record schema

One record per claim, stored in `audit-state.json` and rendered into the report.
You never write this JSON by hand — `claim-set` and `verify --claim-id` produce it.

```json
{
  "claim_id": "claim-003",
  "claim": "Our method improves accuracy by 12%.",
  "manuscript_location": "Results, page 7",
  "claim_type": "quantitative",
  "evidence_files": ["originals/results.csv"],
  "reported_value": 12.0,
  "computed_value": 8.01,
  "status": "conflicting",
  "confidence": "high",
  "explanation": "Reported 12 does not match the computed 8.01 (relative percent change).",
  "recommended_action": "Update the manuscript or confirm the evaluation subset.",
  "evidence": [
    {
      "at": "2026-09-12T18:09:27Z",
      "op": "csv_delta",
      "path": "originals/results.csv",
      "args": {"value_column": "accuracy", "group_column": "method",
               "baseline": "baseline", "treatment": "ours"},
      "description": "mean(accuracy) for method=ours vs method=baseline in results.csv",
      "value": 8.013062044712397
    }
  ]
}
```

## Fields you set

| Field | Flag | Notes |
|---|---|---|
| `claim` | `--claim` | Verbatim from the manuscript, one sentence. |
| `manuscript_location` | `--location` | `Section, page N`. Required. |
| `claim_type` | `--type` | quantitative, experimental, methodological, interpretive. |
| `reported_value` | `--reported` | The number the manuscript states, when it states one. |
| `confidence` | `--confidence` | high, medium, low — your confidence in the *classification*. |
| `explanation` | `--explanation` | Required for missing_evidence and unverifiable. |
| `recommended_action` | `--action` | Required for conflicting and missing_evidence. |

## Fields the tools set

`computed_value`, `status`, `evidence`, `evidence_files`, and the `explanation`
for a verified claim are written by `verify --claim-id`. Do not set them by hand
after a verification; the tool's value is authoritative.

## Status

| Status | Meaning |
|---|---|
| `supported` | A tool computed a value that matches the claim within tolerance. |
| `conflicting` | A tool computed a value that does not match. |
| `missing_evidence` | An artifact should exist to test this, and none does. |
| `unverifiable` | The claim is not the kind of statement a file can settle. |

`missing_evidence` is a finding. `unverifiable` is a limitation. Do not use
`unverifiable` to avoid looking for evidence.
