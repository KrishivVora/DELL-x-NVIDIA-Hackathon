# ClaimTrace integration contract

Frozen interface between the three workstreams. Change it only by agreement —
everything downstream is written against this file.

| Lane | Owns | Produces |
|---|---|---|
| Ingestion / RAG | `ingest.py`, extraction, embeddings, MongoDB | `manifest.json`, `extracted/`, `retrieve()` |
| Agent / skills | `skills/claimtrace/`, `claimtrace/*` tools, watcher | claim records, reports, audit state |
| Runtime / Slack | NemoClaw, OpenShell policy, Slack channel | a live Hermes sandbox that can run the tools |

## 1. Project layout on disk

Every project is one directory. `CLAIMTRACE_PROJECTS_ROOT` points at the parent
(default `./projects`).

```text
projects/<project_id>/
├── originals/        # read-only source artifacts (manuscript PDF, CSVs, configs)
├── extracted/        # text extracted from originals, written by ingestion
├── manifest.json     # written by ingestion, read by the agent
├── audit-state.json  # written by the agent only
└── reports/          # written by the agent only
```

## 2. `manifest.json` — ingestion writes, agent reads

```json
{
  "project_id": "project-123",
  "generated_at": "2026-09-12T18:00:00Z",
  "documents": [
    {
      "path": "originals/manuscript.pdf",
      "role": "manuscript",
      "artifact_type": "manuscript",
      "mime_type": "application/pdf",
      "sha256": "...",
      "modified_at": "2026-09-12T17:59:00Z",
      "extraction_status": "ok",
      "extracted_path": "extracted/manuscript.txt",
      "pages": 12
    },
    {
      "path": "originals/results.csv",
      "role": "evidence",
      "artifact_type": "experimental_data",
      "mime_type": "text/csv",
      "sha256": "...",
      "modified_at": "2026-09-12T17:59:00Z",
      "extraction_status": "not_required",
      "extracted_path": null,
      "pages": null
    }
  ]
}
```

Rules:

- `path` is always project-relative and POSIX-style.
- `role` is `manuscript` or `evidence`. Exactly one document should be
  `manuscript`; if none is, the agent falls back to the only PDF present.
- `extraction_status` is `ok`, `partial`, `failed`, or `not_required`.
- `extracted_path` is required when `extraction_status` is `ok` or `partial`.
- Extracted text uses page markers on their own line so the agent can cite pages:
  `<<<PAGE 7>>>`
- CSVs are **not** extracted to text. The agent reads them with pandas directly.

## 3. Retrieval — ingestion implements, agent calls

Python entry point, imported by `claimtrace/retrieval.py`:

```python
# module: claimtrace_rag
def retrieve(project_id: str, query: str, k: int = 5) -> list[dict]:
    """Return up to k evidence candidates, best first."""
```

Each result:

```json
{"path": "originals/manuscript.pdf", "page": 7, "section": "Results",
 "text": "Our method improves accuracy by 12 percent.", "score": 0.81}
```

- `path` must match a `manifest.json` `path` exactly.
- `page` and `section` may be `null`; `path` and `text` may not.
- `score` is higher-is-better. Scale does not matter.
- Set `CLAIMTRACE_RAG_MODULE` to override the module name.

**Fallback:** if the module is missing or raises, the agent silently falls back to
a local keyword search over `extracted/`. The demo therefore still runs end to end
if MongoDB is not ready. `claimtrace retrieve` reports which backend served the
query in `"backend"`.

## 4. Retrieval is not verification

Retrieval only nominates candidates. Every number in a report comes from a
deterministic tool call recorded in the claim record. The model never computes.

## 5. Claim record — the agent's output schema

See `skills/claimtrace/references/output-schema.md`. Stored in
`audit-state.json`; rendered to `reports/`.

## 6. Slack — runtime lane consumes

`claimtrace notify --project <id>` prints the only text allowed to leave the box:
project id, counts by status, changed-file count, local report path. No claim
text, no excerpts, no values, no file contents.
