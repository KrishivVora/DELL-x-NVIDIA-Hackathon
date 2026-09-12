# labmate_rag — ingestion / RAG lane

Implements CONTRACT.md §1–3 on the GB10: turns `projects/<id>/originals/` into
`manifest.json`, `extracted/*.txt` (with `<<<PAGE n>>>` markers), and a
searchable index in the box's MongoDB Atlas Local, and exposes one function:

```python
import labmate_rag
labmate_rag.retrieve(project_id, query, k=5)  # -> [{path, page, section, text, score}]
```

## Where things run

| Piece | Where | Why |
|---|---|---|
| Extraction, embeddings, MongoDB | GB10 host (`.venv`) | PyMuPDF / sentence-transformers / pymongo are not in the sandbox, and the sandbox has no route to MongoDB |
| `labmate_rag serve` (HTTP API, stdlib) | host, bound to the `openshell-docker` gateway | the sandbox reaches it as `http://host.openshell.internal:8700` |
| `labmate_rag.retrieve()` inside the sandbox | thin stdlib client | same function, same result shape; OpenShell policy `policy/labmate-rag-host.yaml` allows only that endpoint |

Retrieval is hybrid: native `$vectorSearch` (all-MiniLM-L6-v2, cosine, CPU)
plus full-text `$search`, fused with reciprocal rank fusion, filtered to the
project. Both indexes live on the `chunks` collection. Verified on the box:
`$vectorSearch`, `$search`, and transactions work; `$rankFusion` does not
(MongoDB 8.0.28), hence the Python fusion.

## Setup (host, once)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU build; GPU stays with vLLM
pip install -r requirements.txt
python3 -m labmate_rag download-model     # ~90 MB, needs network once
python3 -m labmate_rag check              # MongoDB, model, search indexes
```

MongoDB credentials come from `MONGODB_URI` or `~/.config/hackathon/mongodb.env`.
There is no unauthenticated default. Models load offline (`HF_HUB_OFFLINE=1`)
unless `LABMATE_ALLOW_DOWNLOAD=1`.

## Daily use

```bash
mkdir -p projects/demo-001/originals && cp demo-data/claimtrace-demo/* projects/demo-001/originals/
python3 -m labmate_rag ingest demo-001                 # ~4 s; unchanged files are skipped
python3 -m labmate_rag retrieve demo-001 "how much did accuracy improve" -k 3
python3 -m labmate_rag watch demo-001 --interval 5     # re-ingest when originals/ changes
python3 -m labmate_rag serve --bind auto               # API for the sandbox (keep running)
                                                      # serves retrieve, ingest and intake
```

Environment: `LABMATE_PROJECTS_ROOT` (default `projects`), `LABMATE_DB_NAME`
(default `claimtrace`; tests use `claimtrace_test`), `LABMATE_RAG_URL`
(forces the HTTP client; default in the sandbox is `http://host.openshell.internal:8700`).

## Slack uploads

A file shared in Slack is not a local file. `labmate.slack_intake` (agent lane)
downloads it inside the sandbox and, because the project mount is read-only
there, POSTs the bytes to `POST /api/intake` here. This side re-checks the name,
extension and size — the sandbox is not a trusted caller — writes the file into
`originals/` through a temporary name so the watcher never sees a half file,
then runs the normal ingest. A project that does not exist yet is created.

## Tests

```bash
.venv/bin/python -m pytest tests/test_rag.py -q     # ~20 s, uses database claimtrace_test
```
