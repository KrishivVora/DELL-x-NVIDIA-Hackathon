# Labmate integration contract

Frozen interface between the three workstreams. Change it only by agreement —
everything downstream is written against this file.

| Lane | Owns | Produces |
|---|---|---|
| Ingestion / RAG | `ingest.py`, extraction, embeddings, MongoDB | `manifest.json`, `extracted/`, `retrieve()` |
| Agent / skills | `skills/*`, `labmate/*` tools, watcher | answers, briefings, claim records, reports |
| Runtime / Slack | NemoClaw, OpenShell policy, Slack channel | a live Hermes sandbox that can run the tools |

## 1. Project layout on disk

Every project is one directory. `LABMATE_PROJECTS_ROOT` points at the parent
(default `./projects`).

```text
projects/<project_id>/
├── originals/        # read-only source artifacts (manuscript PDF, CSVs, configs)
├── extracted/        # text extracted from originals, written by ingestion
├── manifest.json     # written by ingestion, read by the agent
├── audit-state.json  # written by the agent only
├── meetings.json     # local meeting register, agent or researcher
└── reports/          # answers, briefings and audit reports; agent only
```

Inside the sandbox the project tree is a **read-only** NemoClaw host mount
(`/sandbox/projects`), so the agent cannot write there. `LABMATE_STATE_ROOT`
(default `/sandbox/workspace/state` via `bin/labmate`) redirects the agent's
files — `audit-state.json`, `meetings.json`, `reports/` — to
`<state_root>/<project_id>/`. Unset, they stay in the project directory (the
host-side default, used by tests). A `meetings.json` shipped inside the project
seeds the agent's register.

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

Python entry point, imported by `labmate/retrieval.py`:

```python
# module: labmate_rag  (claimtrace_rag is also accepted)
def retrieve(project_id: str, query: str, k: int = 5) -> list[dict]:
    """Return up to k relevant passages, best first."""
```

This one function backs everything: question answering, meeting briefings, and
evidence discovery for claim checking. It is the highest-value thing the
ingestion lane produces.

Each result:

```json
{"path": "originals/manuscript.pdf", "page": 7, "section": "Results",
 "text": "Our method improves accuracy by 12 percent.", "score": 0.81}
```

- `path` must match a `manifest.json` `path` exactly.
- `page` and `section` may be `null`; `path` and `text` may not.
- `score` is higher-is-better. Scale does not matter.
- Set `LABMATE_RAG_MODULE` to override the module name.

**Fallback:** if the module is missing or raises, the agent silently falls back to
a local keyword search over `extracted/`. The demo therefore still runs end to end
if MongoDB is not ready. `labmate ask` and `labmate retrieve` report which
backend served the query in `"backend"`, and the skills are required to say so
when the fallback is in use.

## 4. Retrieval is not verification

Retrieval only nominates candidates. Every number in a report comes from a
deterministic tool call recorded in the claim record. The model never computes.

## 4b. What the agent does with it

| Capability | Skill | Entry command |
|---|---|---|
| Answer a question about the project | `research-assistant` | `labmate ask` |
| Catch up on what changed | `research-assistant` | `labmate digest` |
| Prepare a meeting briefing | `meeting-prep` | `labmate meeting-brief` |
| Check a claim or number against data | `claimtrace` | `labmate verify` |

## 5. Claim record — the agent's output schema

See `skills/claimtrace/references/output-schema.md`. Stored in
`audit-state.json`; rendered to `reports/`.

## 6. Slack — runtime lane consumes

`labmate notify --project <id>` prints the only text allowed to leave the box:
project id, counts by status, changed-file count, local report path. No claim
text, no excerpts, no values, no file contents.
