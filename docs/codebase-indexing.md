# Codebase Indexing with CocoIndex + Qdrant

## Overview

The MCP server handles **search only** (`qdrant-find`). Indexing and keeping the index updated is handled by a separate **CocoIndex pipeline**.

```
CocoIndex daemon                    MCP server
────────────────────────            ──────────────────────
LocalFile source         →          qdrant-find  (semantic search)
SplitRecursively         →          qdrant-store (manual notes)
OpenRouter embeddings    →
Qdrant export            →
         ↕
PostgreSQL (state store)
```

This separation is the same architecture used by Cursor and Roocode: the AI tool only queries, a background process keeps the index fresh.

---

## Why CocoIndex

| Feature | CocoIndex | Manual script |
|---|---|---|
| Incremental updates | Only re-indexes changed files | Full reindex every time |
| Chunking | Tree-sitter (semantic, 30+ languages) | Line-based or naive splitting |
| State tracking | PostgreSQL hash store | Manual |
| Qdrant integration | Native, schema inference | Manual |
| OpenRouter support | Built-in | Manual |

---

## Chunking Strategy

CocoIndex uses **Tree-sitter** (same as aider's repo map) to parse source files into ASTs and split at semantic boundaries — functions, classes, methods — rather than arbitrary line counts.

`SplitRecursively` tries to split at the highest-level boundary first (class), then goes deeper (method, block) if chunks are still too large. Each chunk is a coherent unit of code.

This is significantly better than sliding window chunking because:
- A function is never split in half
- Each vector in Qdrant maps to a real symbol
- Retrieval is more precise ("find where authentication is handled")

---

## Requirements

- Python 3.10+
- `pip install cocoindex`
- **PostgreSQL** — used by CocoIndex as a state store for incremental indexing
- Qdrant running (local or cloud)
- `OPENROUTER_API_KEY` environment variable

---

## Pipeline

```python
import cocoindex

@cocoindex.flow_def(name="CodeIndex")
def code_index_flow(flow_builder: cocoindex.FlowBuilder, data_scope: cocoindex.DataScope):
    # 1. Source: local filesystem
    data_scope["files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path="~/myproject",
            included_patterns=["**/*.py", "**/*.ts", "**/*.go"],
        )
    )

    # 2. Semantic chunking via Tree-sitter
    code_embeddings = data_scope["files"].transform(
        cocoindex.functions.SplitRecursively(),
        language=cocoindex.functions.DetectProgrammingLanguage(),
        chunk_size=1000,
        chunk_overlap=300,
    )

    # 3. Embeddings via OpenRouter
    code_embeddings["embedding"] = code_embeddings["chunk"].transform(
        cocoindex.functions.EmbedText(),
        spec=cocoindex.LlmSpec(
            api_type="openai",
            model="text-embedding-3-small",
            address="https://openrouter.ai/api/v1",
        ),
    )

    # 4. Export to Qdrant
    code_embeddings.export(
        "code_chunks",
        cocoindex.targets.Qdrant(
            collection_name="my-codebase",
            grpc_url="localhost:6334",
        ),
        primary_key_fields=["filename", "chunk_index"],
        vector_fields=["embedding"],
    )
```

---

## Usage

```bash
# Set required env vars
export COCOINDEX_DATABASE_URL="postgresql://user:pass@localhost/cocoindex"
export OPENROUTER_API_KEY="sk-or-..."

# First run: create Qdrant collection and PostgreSQL state tables
cocoindex setup CodeIndex

# Initial full index
cocoindex update CodeIndex

# Live mode: watch for file changes and re-index incrementally
cocoindex update CodeIndex --live
```

---

## Querying via MCP server

Once indexed, the existing `qdrant-find` tool queries the same collection:

```bash
QDRANT_URL="http://localhost:6333" \
COLLECTION_NAME="my-codebase" \
EMBEDDING_PROVIDER="openrouter" \
OPENROUTER_API_KEY="sk-or-..." \
EMBEDDING_MODEL="text-embedding-3-small" \
uvx mcp-server-qdrant
```

> The embedding model in the MCP server **must match** the one used in the CocoIndex pipeline.

---

## References

- [CocoIndex docs](https://cocoindex.io/docs/)
- [CocoIndex + Qdrant integration](https://qdrant.tech/documentation/data-management/cocoindex/)
- [Codebase indexing example](https://cocoindex.io/examples/code_index)
- [Aider repo map (Tree-sitter approach)](https://aider.chat/2023/10/22/repomap.html)
