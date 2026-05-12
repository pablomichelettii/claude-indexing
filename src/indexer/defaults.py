"""Single source of truth for default values used by both the CLI and the
CocoIndex flow. Avoid duplicating these literals — they must stay in sync
across flow.py, cli.py, mcp.py and .env.example.
"""

QDRANT_URL = "http://localhost:6333"
QDRANT_GRPC_URL = "http://localhost:6334"

EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
EMBEDDING_DIM = 4096

MCP_SCOPE = "user"
