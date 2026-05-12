# Changelog

## [0.1.1] — hastega-deploy branch

### Breaking changes / migration required

#### `docker-compose.yml` relocated inside the package
The file moved from the repository root (`docker-compose.yml`) to `src/indexer/docker-compose.yml`.

**Impact:** Any workflow that ran `docker compose up` from the repo root will no longer find the compose file. All Docker operations in `cli.py` now pass `-f <package_dir>/docker-compose.yml` explicitly, so they work regardless of the working directory.

**Action required:** If you have scripts, aliases, or CI jobs that reference the old path, update them. If you pinned the compose file path in any external tooling, point it to `src/indexer/docker-compose.yml`.

#### Postgres port changed from `5432` to `55432`
The host-side mapping in `docker-compose.yml` changed from `5432:5432` to `55432:5432`. This avoids conflicts with a locally running Postgres instance.

**Impact:** The `COCOINDEX_DATABASE_URL` in `.env.example` was updated to use port `55432`. Existing `.env` files that still point to port `5432` will stop connecting.

**Action required:** Update your `.env`:
```diff
-COCOINDEX_DATABASE_URL=postgresql://cocoindex:cocoindex@localhost/cocoindex
+COCOINDEX_DATABASE_URL=postgresql://cocoindex:cocoindex@localhost:55432/cocoindex
```
Then recreate the containers (`indexer service_remove && indexer bootstrap`) so they bind on the new port.

#### `.env` lookup path changed for installed tool
When the package is installed via `uv tool install`, `cli.py` no longer looks for `.env` relative to the repository root. It now looks first in `~/.config/claude-indexer/.env`.

**Impact:** After `uv tool install`, running `indexer add` will fail with a "No .env found" error unless the symlink is in place.

**Action required:** Run `indexer link-env /path/to/your/.env` once after installing. See the new command below.

---

### New commands

| Command | Description |
|---|---|
| `indexer link-env <path>` | Creates a symlink at `~/.config/claude-indexer/.env` pointing to the `.env` file you specify. Required after `uv tool install`. Use `--force` to replace an existing symlink. |
| `indexer create_config` | Generates a `.indexerconf` file in the current directory with `name` and `collection` fields. Use `--force` to overwrite. |
| `indexer service_stop` | Stops Docker containers (volumes intact). |
| `indexer service_remove` | Stops containers and removes volumes — full purge of Qdrant and Postgres data. |
| `indexer service_update` | Runs `git pull` in the source repo (resolved from the `.env` symlink), reinstalls the tool via `uv`, then re-runs `bootstrap`. |

### Enhancements

- **`indexer update` name argument is now optional.** If omitted, the command reads the project name from a `.indexerconf` in the current directory (or falls back to the directory basename slug).
- **`.indexerconf` support in `add` and `update`.** A `.indexerconf` file at the project root can override `name` and `collection`, so you no longer need to pass `--name` every time.
- **`cocoindex` binary resolution.** When installed via `uv tool`, the CLI now resolves the `cocoindex` binary from the same isolated venv instead of relying on `$PATH`.
- **Docker helpers refactored.** All `docker compose` calls go through a single `_docker_compose()` helper that always passes `-f <COMPOSE_FILE>`, making them working-directory agnostic.
