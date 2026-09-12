"""Host-side HTTP API so the sandboxed agent can use retrieval without any of
the heavy dependencies or a MongoDB route.

    GET  /api/health              -> {"status": "ok", ...}
    POST /api/retrieve            {"project_id", "query", "k"}   -> {"results": [...]}
    POST /api/ingest              {"project_id", "force"}        -> ingest summary

Stdlib only. Bind it to the openshell-docker gateway (172.18.0.1 today) so the
sandbox reaches it as http://host.openshell.internal:8700; the matching
OpenShell policy preset is policy/labmate-rag-host.yaml. Never bind 0.0.0.0
on the venue Wi-Fi. Logs carry sizes and timings, not content.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config

log = logging.getLogger("labmate_rag.api")


def sandbox_host_address() -> str:
    """The host address the sandbox sees as host.openshell.internal."""
    out = subprocess.run(
        ["docker", "network", "inspect", "-f", "{{(index .IPAM.Config 0).Gateway}}", config.SANDBOX_DOCKER_NETWORK],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "labmate-rag/0.1"

    def log_message(self, fmt, *args):  # quieter than the default, still useful
        log.info("%s %s", self.address_string(), fmt % args)

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1 << 20:
            raise ValueError("request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise ValueError("JSON object expected")
        return body

    def do_GET(self):  # noqa: N802
        if self.path != "/api/health":
            return self._json(404, {"error": "not found"})
        from . import db
        try:
            db.ping()
            mongo = "ok"
        except Exception as exc:  # noqa: BLE001
            mongo = f"error: {type(exc).__name__}"
        self._json(200, {"status": "ok", "service": "labmate_rag", "mongodb": mongo,
                         "embedding_model": config.EMBEDDING_MODEL, "db": config.db_name()})

    def do_POST(self):  # noqa: N802
        started = time.monotonic()
        try:
            body = self._read_json()
            if self.path == "/api/retrieve":
                from .retrieval import retrieve
                project_id, query = str(body["project_id"]), str(body["query"])
                k = int(body.get("k", 5))
                results = retrieve(project_id, query, k)
                log.info("retrieve project=%s query_chars=%d k=%d results=%d %.0fms",
                         project_id, len(query), k, len(results), 1000 * (time.monotonic() - started))
                return self._json(200, {"results": results})
            if self.path == "/api/ingest":
                from .ingest import ingest_project
                project_id = str(body["project_id"])
                summary = ingest_project(project_id, force=bool(body.get("force", False)))
                log.info("ingest project=%s ingested=%d skipped=%d removed=%d %.0fms",
                         project_id, len(summary["ingested"]), len(summary["skipped"]),
                         len(summary["removed"]), 1000 * (time.monotonic() - started))
                return self._json(200, summary)
            return self._json(404, {"error": "not found"})
        except KeyError as exc:
            return self._json(400, {"error": f"missing field {exc}"})
        except LookupError as exc:  # ProjectNotIndexed
            return self._json(404, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - report, keep serving
            log.exception("request failed")
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


def serve(bind: str, port: int) -> None:
    if bind == "auto":
        bind = sandbox_host_address()
    if bind == "0.0.0.0":  # noqa: S104
        raise SystemExit("refusing to bind 0.0.0.0: use 'auto' (sandbox gateway) or 127.0.0.1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", stream=sys.stderr)
    # Warm up: fail fast on missing credentials / model before accepting requests.
    from . import db, embeddings
    db.ensure_indexes(db.get_db(), embeddings.dimension())
    httpd = ThreadingHTTPServer((bind, port), Handler)
    log.info("labmate_rag API listening on http://%s:%d (db=%s)", bind, port, config.db_name())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
