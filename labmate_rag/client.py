"""Thin stdlib client for the host RAG API. This is all the sandbox needs."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class RagApiError(RuntimeError):
    pass


def _post(base_url: str, path: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed local host
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RagApiError(f"{path} -> HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RagApiError(f"{path} unreachable at {base_url}: {exc.reason}") from exc


def retrieve(project_id: str, query: str, k: int = 5, *, base_url: str, timeout: float = 30.0) -> list[dict]:
    body = _post(base_url, "/api/retrieve", {"project_id": project_id, "query": query, "k": k}, timeout)
    return body["results"]


def ingest(project_id: str, *, base_url: str, force: bool = False, timeout: float = 600.0) -> dict:
    return _post(base_url, "/api/ingest", {"project_id": project_id, "force": force}, timeout)


def health(*, base_url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(base_url.rstrip("/") + "/api/health", timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode())
