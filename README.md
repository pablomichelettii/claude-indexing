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
indexer bootstrap     # starts Qdrant + Postgres in Docker
```

## Index a codebase

```bash
indexer add ~/Code/myproject
# → creates a Qdrant collection
# → does initial indexing (this can take several minutes for large repos)
# → registers an MCP server named "myproject" with Claude Code (user scope)

# If the collection name is already taken in Qdrant (e.g. leftover from a
# previous setup), pass --reset to drop and recreate it:
indexer add ~/Code/myproject --reset
```

Then **restart Claude Code** so it picks up the new MCP server. From any session you can now ask it to search the codebase semantically — it will call `qdrant-find` against the `myproject` collection.

## Day-to-day

```bash
indexer list                    # show registered projects
indexer update myproject        # incremental re-index
indexer live myproject          # watch mode, re-indexes on file change
indexer remove myproject        # drop collection + MCP registration
indexer status                  # check Docker + Qdrant + Postgres
```

You can have N projects registered simultaneously. Each gets its own Qdrant collection and its own MCP server name in Claude Code.

## Config files

- `.env` — secrets and embedding settings (local, gitignored)
- `~/.config/claude-indexer/config.json` — project registry (path, collection name, timestamp)
- Docker volumes — Qdrant index and Postgres state, persist across container restarts

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

## Troubleshooting

- **`Qdrant collection '<name>-codebase' already exists`** — the collection survived from a previous run (or another tool created it). Re-run with `--reset` to drop and recreate it: `indexer add /path --name <name> --reset`. Destructive — all vectors in that collection are lost.
- **`cocoindex setup failed`** — check Docker is running: `docker compose ps`.
- **MCP server not visible in Claude Code** — `claude mcp list` to confirm; restart Claude Code if needed.
- **Want to start fresh** — `indexer remove <name>`, then `docker compose down -v` to wipe Qdrant and Postgres volumes.
