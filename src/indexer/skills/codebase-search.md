---
name: codebase-search
description: Semantic search over this project's indexed codebase via the local Qdrant MCP server. Use when the user asks where code lives, how something works, or to recall prior decisions that live in source.
---

# Codebase semantic search

This project is indexed into a local Qdrant collection by the `indexer` CLI. Query it via the MCP tool `${mcp-find-tool}`.

## When to use

- Locating a symbol, function, or pattern when you don't know the file path.
- Recovering context from comments / docstrings that grep would miss (semantic intent rather than literal strings).
- Cross-checking how an API is used across the codebase.

Prefer grep for exact-string lookups; prefer this tool for "where does X happen" / "how is Y implemented" style questions.

## How to call

Use `${mcp-find-tool}` with a natural-language `query`. Iterate: refine the query if the first results miss; combine with grep/Read for verification.

## Notes

- Results are chunks, not whole files — always Read the file at the returned path before reasoning about it.
- The index updates only when someone runs `indexer update` (or `indexer live`). If recent edits are missing, suggest re-indexing.
