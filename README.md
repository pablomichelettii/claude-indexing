# claude-indexer

Semantic code search for [Claude Code](https://docs.claude.com/claude-code), one or many codebases at a time. Pipeline: CocoIndex (tree-sitter chunking) → OpenRouter embeddings → Qdrant → MCP server (`qdrant-find`).

Runs on macOS and Linux.

## Prerequisites

- Docker + `docker compose`
- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/) (used both to install this CLI and to run the MCP server)
- [Claude Code CLI](https://docs.claude.com/claude-code) (`claude` in PATH)
- An [OpenRouter](https://openrouter.ai/keys) API key

## Install

```bash
git clone <this-repo> && cd claude-indexing
cp .env.example .env
# edit .env: paste your OPENROUTER_API_KEY
uv sync          # or: pip install -e .
```

## First run

```bash
uv run indexer bootstrap     # starts Qdrant + Postgres in Docker
uv tool install .            # installs `indexer` globally — run from any directory afterward
indexer link-env .env        # symlink ~/.config/claude-indexer/.env → your project .env
```

`uv tool install` copies the package into an isolated environment, so the tool no longer has access to the `.env` file in the project directory. `link-env` creates a symlink at `~/.config/claude-indexer/.env` pointing to the file you specify — from that point on every `indexer` invocation picks it up regardless of where it is run. Run this once after installing; re-run with `--force` if you ever move or replace your `.env`.

## Index a codebase

```bash
indexer add ~/Code/myproject
# → creates a Qdrant collection
# → does initial indexing (this can take several minutes for large repos)
# → registers an MCP server named "myproject" with Claude Code (user scope)
```

Then **restart Claude Code** so it picks up the new MCP server. From any session you can now ask it to search the codebase semantically — it will call `qdrant-find` against the `myproject` collection.

## Commands reference

### Infrastructure

| Command | Description |
|---|---|
| `indexer bootstrap` | Start Docker services (Qdrant + Postgres) and prepare the CocoIndex state DB. Run once after cloning. |
| `indexer status` | Health check: Docker, Qdrant, Postgres reachability, and list of registered projects. |
| `indexer service_stop` | Stop Docker services (containers remain, volumes intact). |
| `indexer service_remove` | Stop and remove Docker services **and** volumes — full purge of all Qdrant index and Postgres state. |
| `indexer service_update` | `git pull` + `docker compose build` + `up` — upgrades the running stack in place. |

### Project management

| Command | Description |
|---|---|
| `indexer add <path> [--name <name>]` | Index a codebase, create a Qdrant collection, and register an MCP server with Claude Code. `--name` overrides the collection/server name (default: directory basename). |
| `indexer update <name>` | Incremental re-index of a registered project (picks up changed files only). Also resumes an interrupted `add`. |
| `indexer live <name>` | Watch mode — re-indexes on file change. |
| `indexer list` | Show all registered projects (name, path, collection, timestamp). |
| `indexer remove <name>` | Drop the Qdrant collection and remove the MCP server registration. |

**Recovery flags for `add`:**
- `--reset` — drop a stray Qdrant collection with the same name before setup. Use when the project is **new to the CLI** but a leftover collection exists in Qdrant.
- `--force` — if the project is already registered, wipe everything (cocoindex state, Qdrant collection, MCP registration, local config) and start fresh. Destructive.

### Configuration

| Command | Description |
|---|---|
| `indexer create_config` | Create a base `.indexerconf` in the current directory for per-project overrides. |
| `indexer link-env <path>` | Symlink `~/.config/claude-indexer/.env` → the `.env` file at `<path>`. Run once after `uv tool install` so every `indexer` invocation finds the secrets regardless of working directory. Re-run with `--force` if you move or replace your `.env`. |

You can have N projects registered simultaneously. Each gets its own Qdrant collection and its own MCP server name in Claude Code.

## Config files

- `.env` — secrets and embedding settings (local, gitignored)
- `~/.config/claude-indexer/config.json` — project registry (path, collection name, MCP scope, timestamp)
- Docker volumes — Qdrant index and Postgres state, persist across container restarts

### A note on the OpenRouter API key

When `uv runindexer add` registers the MCP server with Claude Code, the key is passed via `-e OPENROUTER_API_KEY=...` to `claude mcp add`. Claude Code then stores it in its own config (`~/.claude/mcp.json` or the equivalent for the chosen scope) so the MCP server can read it on startup. The key is **visible in plaintext** there and in the output of `claude mcp list`. Acceptable for a personal dev tool; if you need stricter isolation, run the indexer as a different OS user.

## How it works

| Component | Role |
|---|---|
| CocoIndex | tree-sitter chunking + incremental state (Postgres) + Qdrant export |
| Qdrant | vector DB (collection per project) |
| OpenRouter | embedding model: `qwen/qwen3-embedding-8b` (4096-dim, configurable in `.env`) |
| mcp-server-qdrant | exposes `qdrant-find` to Claude Code |

### MCP server: why a fork

The upstream `mcp-server-qdrant` ([PR #118](https://github.com/qdrant/mcp-server-qdrant/pull/118)) exposes a hook for custom embedding providers, but the OpenRouter provider implementation lives only on a fork branch: `pablomichelettii/mcp-server-qdrant@feature/openrouter-provider`.

We install it on demand via `uvx --from git+<fork>@<branch> mcp-server-qdrant`. Claude Code runs that command every time it spawns the MCP server; uvx fetches and builds the fork into a temporary venv (cached afterwards in `~/.cache/uv/`).

**Caveats:**

- **Cache freshness.** Once cached, new commits pushed to `feature/openrouter-provider` are **not** picked up automatically. To force a refresh, deregister and re-register the project (`indexer remove <name>` then `indexer add ...`), or run the MCP command manually with `uvx --refresh ...`.
- **When upstream merges OpenRouter support.** Swap the `MCP_GIT_URL`/`MCP_GIT_BRANCH` constants in [src/indexer/mcp.py](src/indexer/mcp.py) for the PyPI package: `uvx mcp-server-qdrant`. No other changes needed.

### Schema contract between cocoindex and mcp-server-qdrant

This was the source of two real bugs we hit during the first end-to-end test on May 2026. Documenting it so future-you doesn't re-discover them.

**1. Named vector field.** `mcp-server-qdrant` queries Qdrant under a vector name derived from the embedding model (e.g. `openrouter-qwen-qwen3-embedding-8b`). CocoIndex writes the vector under whatever name we pass to `VectorIndexDef(field_name=...)`. We standardized on `embedding` in [src/indexer/flow.py](src/indexer/flow.py) and force the MCP server to query that name via the env var `QDRANT_VECTOR_NAME=embedding`, passed during `claude mcp add` in [src/indexer/mcp.py](src/indexer/mcp.py).

> The `QDRANT_VECTOR_NAME` override **must be present in the fork**'s `OpenRouterEmbeddingProvider.get_vector_name()`. The upstream branch had to be patched and pushed — if a future re-clone of the fork is missing it, qdrant-find errors with `Not existing vector name error`.

**2. Payload text field.** `mcp-server-qdrant`'s `qdrant-find` reads `result.payload["document"]` (hardcoded). CocoIndex used to write the chunk text only under `codeChunk`. Now [flow.py](src/indexer/flow.py) writes both fields. For collections indexed **before** this change, run the one-shot migration:

```bash
uv run python scripts/migrate_codechunk_to_document.py <collection-name>
```

It scrolls the collection in batches of 256 and copies `codeChunk` → `document` in-place. Idempotent (skips points that already have `document`). No re-embedding — saves the OpenRouter cost.

### Restarting Claude Code after MCP changes

Env vars passed via `claude mcp add -e ...` are baked into Claude Code's config and only consumed when a **new** MCP server process is spawned. After `indexer add`, `indexer update`, or any change to the env vars: existing Claude Code sessions keep talking to their old MCP process. You must close and reopen Claude Code (or at least restart that one session) for changes to take effect.

## Interrupting and resuming

Indexing is incremental — CocoIndex commits state to Postgres after each processed file. **Ctrl-C is safe**: files already indexed stay indexed. To resume an interrupted `indexer add` run:

```bash
indexer update <name>     # resumes from where it left off + finishes MCP registration
```

The project is recorded in the local registry as soon as `cocoindex setup` succeeds (before the long embedding phase), so even an early Ctrl-C leaves a recoverable state.

## Troubleshooting

- **`Qdrant collection '<name>-codebase' already exists`** — the collection survived from a previous run (or another tool created it). Re-run with `--reset` to drop and recreate it: `indexer add /path --name <name> --reset`. Destructive — all vectors in that collection are lost.
- **`cocoindex setup failed`** — check Docker is running: `docker compose ps`.
- **MCP server not visible in Claude Code** — `claude mcp list` to confirm; restart Claude Code if needed.
- **`qdrant-find` returns `Not existing vector name error: openrouter-…`** — the MCP server is querying under the model-derived vector name instead of `embedding`. Either (a) the fork branch on GitHub is missing the `QDRANT_VECTOR_NAME` override patch, or (b) Claude Code wasn't restarted after the MCP registration. See *Schema contract* above.
- **`qdrant-find` returns `'document'` (KeyError)** — the collection was indexed before flow.py started writing the `document` payload field. Run `uv run python scripts/migrate_codechunk_to_document.py <collection-name>` to backfill.
- **Want to start fresh** — `indexer remove <name>`, then `docker compose down -v` to wipe Qdrant and Postgres volumes.
