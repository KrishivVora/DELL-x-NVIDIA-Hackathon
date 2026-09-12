"""Environment-driven settings. Nothing here has an unauthenticated default."""

from __future__ import annotations

import os
from pathlib import Path

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MONGODB_ENV = Path("~/.config/hackathon/mongodb.env").expanduser()
DEFAULT_API_PORT = 8700
SANDBOX_DOCKER_NETWORK = "openshell-docker"  # its gateway is host.openshell.internal in the sandbox


class ConfigError(RuntimeError):
    pass


def projects_root() -> Path:
    # Same variable and default as labmate/store.py, so both lanes see one tree.
    return Path(os.environ.get("LABMATE_PROJECTS_ROOT", "projects")).expanduser()


def db_name() -> str:
    return os.environ.get("LABMATE_DB_NAME", "claimtrace")


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def mongodb_uri() -> str:
    """MONGODB_URI from the environment, else from the box's credentials file."""
    uri = os.environ.get("MONGODB_URI")
    if uri:
        return uri
    env_file = Path(os.environ.get("LABMATE_MONGODB_ENV", DEFAULT_MONGODB_ENV)).expanduser()
    if env_file.is_file():
        uri = _read_env_file(env_file).get("MONGODB_URI")
        if uri:
            return uri
    raise ConfigError(
        f"MongoDB credentials not found: set MONGODB_URI or put it in {env_file}. "
        "There is no unauthenticated default."
    )


def offline_models() -> None:
    """Never download models unless explicitly allowed; the demo runs air-gapped."""
    if os.environ.get("LABMATE_ALLOW_DOWNLOAD") != "1":
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    # The Xet transfer backend stalls on the venue Wi-Fi; plain HTTPS works.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    # Progress bars go to stderr and confuse callers that parse our JSON output.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
