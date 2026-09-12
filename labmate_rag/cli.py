"""labmate_rag command line.

    python3 -m labmate_rag check
    python3 -m labmate_rag ingest <project_id> [--manuscript NAME] [--force]
    python3 -m labmate_rag retrieve <project_id> "<query>" [-k 5]
    python3 -m labmate_rag watch <project_id> [--interval 5]
    python3 -m labmate_rag serve [--bind auto|127.0.0.1] [--port 8700]
    python3 -m labmate_rag download-model
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import config


def cmd_check(_a) -> dict:
    from . import db, embeddings
    started = time.monotonic()
    out = {"projects_root": str(config.projects_root()), "db": config.db_name()}
    out["mongodb"] = db.ping().get("ok")
    out["embedding_model"] = config.EMBEDDING_MODEL
    out["embedding_dim"] = embeddings.dimension()
    db.ensure_indexes(db.get_db(), out["embedding_dim"])
    out["search_indexes"] = {n: s.get("status") for n, s in db.search_index_status(db.get_db()).items()}
    out["elapsed_s"] = round(time.monotonic() - started, 1)
    return out


def cmd_ingest(a) -> dict:
    from .ingest import ingest_project
    started = time.monotonic()
    summary = ingest_project(a.project_id, manuscript=a.manuscript, force=a.force, wait_indexed=not a.no_wait)
    summary["elapsed_s"] = round(time.monotonic() - started, 1)
    return summary


def cmd_retrieve(a) -> dict:
    from . import retrieve
    started = time.monotonic()
    results = retrieve(a.project_id, a.query, a.k)
    return {"project_id": a.project_id, "k": a.k, "results": results,
            "elapsed_s": round(time.monotonic() - started, 2)}


def cmd_watch(a) -> int:
    from .watch import watch
    return watch(a.project_id, a.interval, a.once)


def cmd_serve(a) -> int:
    from .api import serve
    serve(a.bind, a.port)
    return 0


def cmd_download_model(_a) -> dict:
    import os
    os.environ["LABMATE_ALLOW_DOWNLOAD"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from huggingface_hub import snapshot_download
    path = snapshot_download(config.EMBEDDING_MODEL,
                             allow_patterns=["*.json", "*.txt", "model.safetensors", "1_Pooling/*"])
    return {"model": config.EMBEDDING_MODEL, "path": path}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="labmate_rag", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="verify MongoDB, the embedding model, and the search indexes")

    i = sub.add_parser("ingest", help="extract, embed, and index a project; write manifest.json")
    i.add_argument("project_id")
    i.add_argument("--manuscript", help="file under originals/ to mark as the manuscript")
    i.add_argument("--force", action="store_true", help="re-process unchanged files too")
    i.add_argument("--no-wait", action="store_true", help="do not wait for the search index to catch up")

    r = sub.add_parser("retrieve", help="find evidence candidates (not verification)")
    r.add_argument("project_id")
    r.add_argument("query")
    r.add_argument("-k", type=int, default=5)

    w = sub.add_parser("watch", help="re-ingest whenever originals/ changes")
    w.add_argument("project_id")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--once", action="store_true")

    s = sub.add_parser("serve", help="HTTP API for the sandboxed agent")
    s.add_argument("--bind", default="127.0.0.1", help="'auto' = the address the sandbox sees as host.openshell.internal")
    s.add_argument("--port", type=int, default=config.DEFAULT_API_PORT)

    sub.add_parser("download-model", help="fetch the embedding model once (needs network)")

    a = ap.parse_args(argv)
    handlers = {"check": cmd_check, "ingest": cmd_ingest, "retrieve": cmd_retrieve,
                "watch": cmd_watch, "serve": cmd_serve, "download-model": cmd_download_model}
    try:
        result = handlers[a.cmd](a)
    except Exception as exc:  # noqa: BLE001 - one JSON error line for callers
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 1
    if isinstance(result, int):
        return result
    print(json.dumps(result, indent=2))
    return 0
