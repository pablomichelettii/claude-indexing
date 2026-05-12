"""Register / deregister an mcp-server-qdrant instance with Claude Code.

We install the server via `uvx --from git+<fork>@<branch>` so no local clone
is required (the upstream PyPI build doesn't include the OpenRouter provider
yet — that lives on the fork's branch).
"""

from __future__ import annotations

import shutil
import subprocess

MCP_GIT_URL = "git+https://github.com/pablomichelettii/mcp-server-qdrant.git"
MCP_GIT_BRANCH = "feature/openrouter-provider"
MCP_PKG_SPEC = f"{MCP_GIT_URL}@{MCP_GIT_BRANCH}"


def _claude_bin() -> str:
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError(
            "`claude` CLI not found in PATH. Install Claude Code first: "
            "https://docs.claude.com/claude-code"
        )
    return claude


def _check_uvx() -> None:
    if not shutil.which("uvx"):
        raise RuntimeError(
            "`uvx` not found in PATH — required because the MCP server is "
            "installed on demand via `uvx --from git+<fork>`. Install uv: "
            "https://docs.astral.sh/uv/"
        )


def remove(name: str) -> None:
    subprocess.run(
        [_claude_bin(), "mcp", "remove", name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def add(
    name: str,
    *,
    collection: str,
    openrouter_api_key: str,
    embedding_model: str,
    qdrant_url: str,
    scope: str = "user",
) -> None:
    """(Re-)register the MCP server. Idempotent: removes first, then adds."""
    _check_uvx()
    remove(name)
    cmd = [
        _claude_bin(), "mcp", "add", name,
        "--scope", scope,
        "-e", f"QDRANT_URL={qdrant_url}",
        "-e", f"COLLECTION_NAME={collection}",
        "-e", "EMBEDDING_PROVIDER=openrouter",
        "-e", f"EMBEDDING_MODEL={embedding_model}",
        "-e", f"OPENROUTER_API_KEY={openrouter_api_key}",
        # Force the server to query the vector named "embedding" — the field
        # cocoindex writes to. Without this, the fork derives a name from the
        # model (e.g. "openrouter-qwen-qwen3-embedding-8b") and qdrant-find
        # fails with "Not existing vector name error".
        "-e", "QDRANT_VECTOR_NAME=embedding",
        "--",
        "uvx", "--from", MCP_PKG_SPEC, "mcp-server-qdrant",
    ]
    subprocess.run(cmd, check=True)
