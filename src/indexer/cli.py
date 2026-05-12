"""indexer CLI — manage semantic-search projects for Claude Code.

Subcommands:
  bootstrap                  start Docker services, prepare cocoindex state DB
  add <path> [--name]        index a codebase + register MCP server
  list                       show registered projects
  update <name>              re-index a project (incremental)
  live <name>                watch mode (reindex on file change)
  remove <name>              drop collection + MCP registration
  status                     health check (Docker, Qdrant, Postgres)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from . import config, mcp


REPO_ROOT = Path(__file__).resolve().parents[2]
FLOW_FILE = REPO_ROOT / "src" / "indexer" / "flow.py"
DOTENV = REPO_ROOT / ".env"


# ─── helpers ──────────────────────────────────────────────────────────────

def _load_env() -> None:
    if DOTENV.exists():
        load_dotenv(DOTENV)
    else:
        sys.exit(f"missing {DOTENV} — copy .env.example and fill in OPENROUTER_API_KEY")
    for var in ("OPENROUTER_API_KEY", "COCOINDEX_DATABASE_URL"):
        if not os.environ.get(var):
            sys.exit(f"{var} not set in {DOTENV}")


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower()
    return s or "project"


def _run_cocoindex(args: list[str], project: dict, flow_name: str) -> int:
    env = os.environ.copy()
    env["INDEXER_FLOW_NAME"] = flow_name
    env["INDEXER_PATH"] = project["path"]
    env["INDEXER_COLLECTION"] = project["collection"]
    return subprocess.call(["cocoindex", *args, str(FLOW_FILE)], env=env)


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def _qdrant_collection_exists(qdrant_url: str, collection: str) -> bool:
    return _http_ok(f"{qdrant_url}/collections/{collection}")


def _qdrant_drop_collection(qdrant_url: str, collection: str) -> None:
    req = urllib.request.Request(
        f"{qdrant_url}/collections/{collection}", method="DELETE"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise


def _docker_compose_up() -> None:
    subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=REPO_ROOT,
        check=True,
    )


def _postgres_ready() -> bool:
    return subprocess.call(
        ["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "cocoindex"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0


# ─── commands ─────────────────────────────────────────────────────────────

def cmd_bootstrap(args: argparse.Namespace) -> int:
    _load_env()
    print("→ starting Docker services (Qdrant + Postgres)…")
    _docker_compose_up()
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")

    print("→ waiting for Qdrant…")
    for _ in range(60):
        if _http_ok(f"{qdrant_url}/healthz") or _http_ok(qdrant_url):
            break
        time.sleep(1)
    else:
        sys.exit("Qdrant didn't come up in 60s")

    print("→ waiting for Postgres…")
    for _ in range(60):
        if _postgres_ready():
            break
        time.sleep(1)
    else:
        sys.exit("Postgres didn't come up in 60s")

    print("✓ ready. Now: indexer add /path/to/codebase")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    _load_env()
    path = Path(args.path).expanduser().resolve()
    if not path.is_dir():
        sys.exit(f"not a directory: {path}")

    name = args.name or _slugify(path.name)
    collection = f"{name}-codebase"
    project = {"path": str(path), "collection": collection}
    flow_name = f"CodeIndex_{name}"
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")

    existing = config.get_project(name)
    if existing and not args.force:
        sys.exit(
            f"project '{name}' already exists. Use --force to wipe and recreate "
            f"it, or pick a different --name."
        )

    print(f"→ project '{name}'  path={path}  collection={collection}")

    # --force: nuke EVERYTHING tied to this project before starting fresh.
    if existing and args.force:
        print(f"→ --force: dropping existing project '{name}' (cocoindex state + Qdrant + MCP + config)…")
        _run_cocoindex(["drop", "--force"], existing, flow_name)
        if _qdrant_collection_exists(qdrant_url, collection):
            _qdrant_drop_collection(qdrant_url, collection)
        mcp.remove(name)
        config.remove_project(name)

    # --reset: handle leftover Qdrant collection from a previous (pre-CLI) setup.
    if _qdrant_collection_exists(qdrant_url, collection):
        if args.reset or args.force:
            print(f"→ dropping leftover Qdrant collection '{collection}'…")
            _qdrant_drop_collection(qdrant_url, collection)
        else:
            sys.exit(
                f"Qdrant collection '{collection}' already exists.\n"
                f"Pass --reset to drop and recreate it, or pick a different --name."
            )

    print("→ cocoindex setup (creates collection + state tables)…")
    if _run_cocoindex(["setup", "--force"], project, flow_name) != 0:
        sys.exit("cocoindex setup failed")

    # Register the project in the local config BEFORE the long-running update.
    # If the user Ctrl-Cs during update, the project is still recoverable via
    # `indexer update <name>` (cocoindex resumes from its Postgres state).
    config.add_project(name, str(path), collection, scope=args.scope)

    print(f"→ cocoindex update (initial index, this may take a while; Ctrl-C is safe — resume with `indexer update {name}`)…")
    if _run_cocoindex(["update"], project, flow_name) != 0:
        sys.exit(f"cocoindex update failed — resume with: indexer update {name}")

    print(f"→ registering MCP server '{name}' with Claude Code…")
    mcp.add(
        name,
        collection=collection,
        openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
        embedding_model=os.environ.get("EMBEDDING_MODEL", "qwen/qwen3-embedding-8b"),
        qdrant_url=qdrant_url,
        scope=args.scope,
    )

    print(f"✓ '{name}' indexed and ready. Restart Claude Code to pick up the MCP server.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    projects = config.list_projects()
    if not projects:
        print("no projects registered. Run: indexer add /path/to/codebase")
        return 0
    width = max(len(n) for n in projects)
    for name, p in projects.items():
        print(f"  {name:<{width}}  {p['collection']:<30}  {p['path']}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    _load_env()
    project = config.get_project(args.name)
    if not project:
        sys.exit(f"unknown project: {args.name}")
    flow_name = f"CodeIndex_{args.name}"
    rc = _run_cocoindex(["update"], project, flow_name)
    if rc != 0:
        return rc
    # Ensure MCP is registered (recovers from an interrupted `indexer add`).
    # Use the scope stored at add-time so we don't silently downgrade it.
    print(f"→ ensuring MCP server '{args.name}' is registered…")
    mcp.add(
        args.name,
        collection=project["collection"],
        openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
        embedding_model=os.environ.get("EMBEDDING_MODEL", "qwen/qwen3-embedding-8b"),
        qdrant_url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        scope=project["scope"],
    )
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    _load_env()
    project = config.get_project(args.name)
    if not project:
        sys.exit(f"unknown project: {args.name}")
    flow_name = f"CodeIndex_{args.name}"
    print(f"→ live indexing '{args.name}' (Ctrl-C to stop)")
    return _run_cocoindex(["update", "--live"], project, flow_name)


def cmd_remove(args: argparse.Namespace) -> int:
    _load_env()
    project = config.get_project(args.name)
    if not project:
        sys.exit(f"unknown project: {args.name}")

    flow_name = f"CodeIndex_{args.name}"
    print(f"→ dropping cocoindex state + Qdrant collection for '{args.name}'…")
    rc = _run_cocoindex(["drop", "--force"], project, flow_name)
    if rc != 0:
        sys.exit(
            f"cocoindex drop failed (rc={rc}). Local config and MCP not touched.\n"
            f"Investigate, then either retry `indexer remove {args.name}` or "
            f"clean up manually."
        )

    print(f"→ deregistering MCP server '{args.name}'…")
    mcp.remove(args.name)

    config.remove_project(args.name)
    print(f"✓ '{args.name}' removed.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _load_env()
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    print(f"Qdrant ({qdrant_url}): {'✓' if _http_ok(qdrant_url) else '✗'}")

    pg_ok = subprocess.call(
        ["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "cocoindex"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0
    print(f"Postgres: {'✓' if pg_ok else '✗'}")

    print(f"\nProjects ({len(config.list_projects())}):")
    cmd_list(args)
    return 0


# ─── entry point ──────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(prog="indexer")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bootstrap", help="start Docker services").set_defaults(func=cmd_bootstrap)

    a = sub.add_parser("add", help="index a codebase + register MCP server")
    a.add_argument("path", help="path to codebase")
    a.add_argument("--name", help="project name (default: dir basename slug)")
    a.add_argument("--scope", default="user", choices=["user", "project", "local"],
                   help="Claude Code MCP scope (default: user)")
    a.add_argument("--force", action="store_true",
                   help="if a project with this name exists, wipe it completely "
                        "(cocoindex state, Qdrant collection, MCP, local config) "
                        "before adding")
    a.add_argument("--reset", action="store_true",
                   help="drop a stray Qdrant collection with the same name "
                        "before setup (use when the project is NEW to this CLI "
                        "but a leftover collection exists in Qdrant)")
    a.set_defaults(func=cmd_add)

    sub.add_parser("list", help="list projects").set_defaults(func=cmd_list)

    u = sub.add_parser("update", help="re-index a project")
    u.add_argument("name")
    u.set_defaults(func=cmd_update)

    lv = sub.add_parser("live", help="watch mode")
    lv.add_argument("name")
    lv.set_defaults(func=cmd_live)

    r = sub.add_parser("remove", help="drop collection + deregister MCP")
    r.add_argument("name")
    r.set_defaults(func=cmd_remove)

    sub.add_parser("status", help="health check").set_defaults(func=cmd_status)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
