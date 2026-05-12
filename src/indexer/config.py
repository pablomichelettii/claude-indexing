"""Project registry stored at ~/.config/claude-indexer/config.json.

Schema:
{
  "projects": {
    "<name>": {
      "path": "/abs/path/to/codebase",
      "collection": "<qdrant-collection-name>",
      "scope": "user",          # MCP scope chosen at add-time
      "added_at": "2026-05-12T10:30:00"
    }
  }
}

Older entries written before 2026-05-12 may lack `scope`; `get_project` fills
in the default ("user") on read so callers can rely on the field.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    d = base / "claude-indexer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    p = config_path()
    if not p.exists():
        return {"projects": {}}
    return json.loads(p.read_text())


def save(cfg: dict) -> None:
    config_path().write_text(json.dumps(cfg, indent=2) + "\n")


def add_project(name: str, path: str, collection: str, scope: str = "user") -> None:
    cfg = load()
    cfg["projects"][name] = {
        "path": str(Path(path).expanduser().resolve()),
        "collection": collection,
        "scope": scope,
        "added_at": datetime.now().isoformat(timespec="seconds"),
    }
    save(cfg)


def remove_project(name: str) -> dict | None:
    cfg = load()
    proj = cfg["projects"].pop(name, None)
    if proj is not None:
        save(cfg)
    return proj


def get_project(name: str) -> dict | None:
    proj = load()["projects"].get(name)
    if proj is not None:
        proj.setdefault("scope", "user")  # backward-compat for entries written before scope was tracked
    return proj


def list_projects() -> dict:
    return load()["projects"]
