"""indexer CLI — manage semantic-search projects for Claude Code.

Subcommands:
  bootstrap                  start Docker services, prepare cocoindex state DB
  add <path> [--name]        index a codebase + register MCP server
  create_config              create a base .indexerconf in the current directory
  list                       show registered projects
  update <name>              re-index a project (incremental)
  live <name>                watch mode (reindex on file change)
  remove <name>              drop collection + MCP registration
  status                     health check (Docker, Qdrant, Postgres)
  service_stop               stop Docker services
  service_remove             stop and remove Docker services + volumes (full purge)
  service_update             git pull + docker compose build + up (upgrade in place)
  link-env <path>            symlink ~/.config/claude-indexer/.env → your .env
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

from . import config, defaults, mcp


_PKG_DIR = Path(__file__).resolve().parent
FLOW_FILE = _PKG_DIR / "flow.py"
COMPOSE_FILE = _PKG_DIR / "docker-compose.yml"
SKILLS_TEMPLATE_DIR = _PKG_DIR / "skills"
_CONFIG_DIR = Path.home() / ".config" / "claude-indexer"
_USER_DOTENV = _CONFIG_DIR / ".env"
# Prefer the user-config .env (stable across installs); fall back to package dir for dev mode.
DOTENV = _USER_DOTENV if _USER_DOTENV.exists() else _PKG_DIR.parents[1] / ".env"


# ─── helpers ──────────────────────────────────────────────────────────────

def _load_env() -> None:
    dotenv = _USER_DOTENV if _USER_DOTENV.exists() else _PKG_DIR.parents[1] / ".env"
    if dotenv.exists():
        load_dotenv(dotenv)
    else:
        sys.exit(
            f"No .env found. Run:  indexer link-env /path/to/your/.env\n"
            f"  or create {_USER_DOTENV} directly."
        )
    for var in ("OPENROUTER_API_KEY", "COCOINDEX_DATABASE_URL"):
        if not os.environ.get(var):
            sys.exit(f"{var} not set — check {dotenv}")


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower()
    return s or "project"


def _flow_name(project_name: str) -> str:
    """Cocoindex uses the flow name as part of Postgres table identifiers.
    Replace dashes with underscores to avoid quoting issues downstream.
    """
    return "CodeIndex_" + project_name.replace("-", "_")


def _cocoindex_bin() -> str:
    # When installed via uv tool, cocoindex lives in the same isolated venv.
    candidate = Path(sys.executable).parent / "cocoindex"
    return str(candidate) if candidate.exists() else "cocoindex"


def _run_cocoindex(args: list[str], project: dict, flow_name: str) -> int:
    env = os.environ.copy()
    env["INDEXER_FLOW_NAME"] = flow_name
    env["INDEXER_PATH"] = project["path"]
    env["INDEXER_COLLECTION"] = project["collection"]
    return subprocess.call([_cocoindex_bin(), *args, str(FLOW_FILE)], env=env)


def _load_indexerconf(project_path: Path) -> dict:
    conf_file = project_path / ".indexerconf"
    if not conf_file.exists():
        return {}
    result = {}
    for line in conf_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


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


def _find_source_repo() -> Path | None:
    """Walk up from the resolved .env symlink to find the git root."""
    if not _USER_DOTENV.is_symlink():
        return None
    candidate = _USER_DOTENV.resolve().parent
    while candidate != candidate.parent:
        if (candidate / ".git").exists():
            return candidate
        candidate = candidate.parent
    return None


def _docker_compose(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        **kwargs,
    )


def _docker_compose_up() -> None:
    _docker_compose("up", "-d", check=True)


def _postgres_ready() -> bool:
    return _docker_compose(
        "exec", "-T", "postgres", "pg_isready", "-U", "cocoindex",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


# ─── commands ─────────────────────────────────────────────────────────────

def cmd_bootstrap(args: argparse.Namespace) -> int:
    _load_env()
    print("→ starting Docker services (Qdrant + Postgres)…")
    _docker_compose_up()
    qdrant_url = os.environ.get("QDRANT_URL", defaults.QDRANT_URL)

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

    indexerconf = _load_indexerconf(path)
    if indexerconf:
        print(f"→ loaded .indexerconf from {path}")
    else:
        auto_name = args.name or _slugify(path.name)
        _write_indexerconf(path, auto_name, f"{auto_name}-codebase", force=False)
        indexerconf = _load_indexerconf(path)
    name = args.name or indexerconf.get("name") or _slugify(path.name)
    collection = indexerconf.get("collection") or f"{name}-codebase"
    project = {"path": str(path), "collection": collection}
    flow_name = _flow_name(name)
    qdrant_url = os.environ.get("QDRANT_URL", defaults.QDRANT_URL)

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
        embedding_model=os.environ.get("EMBEDDING_MODEL", defaults.EMBEDDING_MODEL),
        qdrant_url=qdrant_url,
        scope=args.scope,
    )

    _install_skill(path, name, force=args.force)

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
    name = args.name
    if name is None:
        indexerconf = _load_indexerconf(Path.cwd())
        name = indexerconf.get("name") or _slugify(Path.cwd().name)
        print(f"→ loaded .indexerconf from {Path.cwd()}")
    project = config.get_project(name)
    if not project:
        sys.exit(f"unknown project: {name}")
    indexerconf = _load_indexerconf(Path(project["path"]))
    if indexerconf:
        print(f"→ loaded .indexerconf from {project['path']}")
        if "collection" in indexerconf:
            project = {**project, "collection": indexerconf["collection"]}
    flow_name = _flow_name(name)
    rc = _run_cocoindex(["update"], project, flow_name)
    if rc != 0:
        return rc
    # Ensure MCP is registered (recovers from an interrupted `indexer add`).
    # Use the scope stored at add-time so we don't silently downgrade it.
    print(f"→ ensuring MCP server '{name}' is registered…")
    mcp.add(
        name,
        collection=project["collection"],
        openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
        embedding_model=os.environ.get("EMBEDDING_MODEL", defaults.EMBEDDING_MODEL),
        qdrant_url=os.environ.get("QDRANT_URL", defaults.QDRANT_URL),
        scope=project["scope"],
    )
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    _load_env()
    project = config.get_project(args.name)
    if not project:
        sys.exit(f"unknown project: {args.name}")
    flow_name = _flow_name(args.name)
    print(f"→ live indexing '{args.name}' (Ctrl-C to stop)")
    return _run_cocoindex(["update", "--live"], project, flow_name)


def cmd_remove(args: argparse.Namespace) -> int:
    _load_env()
    project = config.get_project(args.name)
    if not project:
        sys.exit(f"unknown project: {args.name}")

    flow_name = _flow_name(args.name)
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


def cmd_service_stop(args: argparse.Namespace) -> int:
    print("→ stopping Docker services…")
    _docker_compose("stop", check=True)
    print("✓ services stopped.")
    return 0


def cmd_service_remove(args: argparse.Namespace) -> int:
    print("→ stopping and removing Docker services + volumes (full purge)…")
    _docker_compose("down", "--volumes", "--remove-orphans", check=True)
    print("✓ services and volumes removed.")
    return 0


def cmd_service_update(args: argparse.Namespace) -> int:
    source_repo = _find_source_repo()
    if source_repo is None:
        sys.exit(
            "Cannot find source repo — ensure ~/.config/claude-indexer/.env "
            "is a symlink created by `indexer link-env /path/to/repo/.env`."
        )

    print(f"→ pulling latest changes in {source_repo}…")
    subprocess.run(["git", "pull"], cwd=source_repo, check=True)

    print("→ reinstalling indexer tool…")
    subprocess.run(["uv", "tool", "install", str(source_repo), "--reinstall"], check=True)

    return cmd_bootstrap(args)


def _write_indexerconf(target_dir: Path, name: str, collection: str, force: bool) -> bool:
    """Write a .indexerconf in target_dir. Returns True if written, False if skipped.

    Prompts the user for name and collection. Empty input keeps the supplied default.
    """
    conf_file = target_dir / ".indexerconf"
    if conf_file.exists() and not force:
        return False
    answer = input(f"project name [{name}]: ").strip()
    if answer:
        name = answer
    default_collection = collection if collection else f"{name}-codebase"
    answer = input(f"Qdrant collection [{default_collection}]: ").strip()
    collection = answer or default_collection
    conf_file.write_text(
        f"# indexer configuration for this project\n"
        f"# place this file in the root of your codebase\n"
        f"\n"
        f"# project name used for MCP server registration (default: dir basename slug)\n"
        f"name: {name}\n"
        f"\n"
        f"# Qdrant collection name (default: <name>-codebase)\n"
        f"collection: {collection}\n"
    )
    print(f"✓ created {conf_file}")
    return True


def cmd_create_config(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    name = _slugify(cwd.name)
    if not _write_indexerconf(cwd, name, f"{name}-codebase", force=args.force):
        sys.exit(f".indexerconf already exists in {cwd}. Use --force to overwrite.")
    return 0


def _install_skill(project_path: Path, mcp_name: str, force: bool = False) -> None:
    """Copy each skill template into <project>/.claude/skills/, replacing placeholders."""
    if not SKILLS_TEMPLATE_DIR.is_dir():
        return
    target_dir = project_path / ".claude" / "skills"
    target_dir.mkdir(parents=True, exist_ok=True)
    replacements = {
        "${mcp-find-tool}": f"mcp__{mcp_name}__qdrant-find",
    }
    for template in SKILLS_TEMPLATE_DIR.glob("*.md"):
        target = target_dir / template.name
        if target.exists() and not force:
            print(f"→ skill '{template.name}' already exists at {target} — skipping")
            continue
        content = template.read_text()
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        target.write_text(content)
        print(f"✓ installed skill {target}")


def cmd_link_env(args: argparse.Namespace) -> int:
    src = Path(args.path).expanduser().resolve()
    if not src.exists():
        sys.exit(f"not found: {src}")
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if _USER_DOTENV.exists() or _USER_DOTENV.is_symlink():
        if not args.force:
            sys.exit(f"{_USER_DOTENV} already exists. Use --force to replace it.")
        _USER_DOTENV.unlink()
    _USER_DOTENV.symlink_to(src)
    print(f"✓ {_USER_DOTENV} -> {src}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _load_env()
    qdrant_url = os.environ.get("QDRANT_URL", defaults.QDRANT_URL)
    print(f"Qdrant ({qdrant_url}): {'✓' if _http_ok(qdrant_url) else '✗'}")

    pg_ok = _postgres_ready()
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

    cc = sub.add_parser("create_config", help="create a base .indexerconf in the current directory")
    cc.add_argument("--force", action="store_true", help="overwrite existing .indexerconf")
    cc.set_defaults(func=cmd_create_config)

    sub.add_parser("list", help="list projects").set_defaults(func=cmd_list)

    u = sub.add_parser("update", help="re-index a project")
    u.add_argument("name", nargs="?", default=None,
                   help="project name (default: read from .indexerconf in cwd)")
    u.set_defaults(func=cmd_update)

    lv = sub.add_parser("live", help="watch mode")
    lv.add_argument("name")
    lv.set_defaults(func=cmd_live)

    r = sub.add_parser("remove", help="drop collection + deregister MCP")
    r.add_argument("name")
    r.set_defaults(func=cmd_remove)

    sub.add_parser("status", help="health check").set_defaults(func=cmd_status)

    sub.add_parser("service_stop", help="stop Docker services").set_defaults(func=cmd_service_stop)
    sub.add_parser("service_remove", help="stop + remove Docker services and volumes").set_defaults(func=cmd_service_remove)
    sub.add_parser("service_update", help="git pull + rebuild + restart services").set_defaults(func=cmd_service_update)

    le = sub.add_parser("link-env", help="symlink a .env file into ~/.config/claude-indexer/.env")
    le.add_argument("path", help="path to your .env file")
    le.add_argument("--force", action="store_true", help="overwrite existing symlink/file")
    le.set_defaults(func=cmd_link_env)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
