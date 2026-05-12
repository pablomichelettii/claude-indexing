"""One-off migration: copy each point's `codeChunk` payload to `document`.

mcp-server-qdrant reads `payload["document"]`. cocoindex (this repo's flow.py
prior to 2026-05-12) wrote the chunk text to `codeChunk` instead, so
qdrant-find raised KeyError on every result. flow.py now writes both fields,
but pre-existing collections need a one-time backfill — this script.

Usage:
    uv run python scripts/migrate_codechunk_to_document.py <collection-name>

Idempotent: points that already have `document` are skipped.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
BATCH = 256


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{QDRANT_URL}{path}",
        method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def migrate(collection: str) -> None:
    offset: str | int | None = None
    total = 0
    skipped = 0
    while True:
        body: dict = {"limit": BATCH, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        res = _post(f"/collections/{collection}/points/scroll", body)
        points = res["result"]["points"]
        offset = res["result"].get("next_page_offset")

        if not points:
            break

        ops = []
        for p in points:
            payload = p.get("payload") or {}
            if "document" in payload:
                skipped += 1
                continue
            text = payload.get("codeChunk")
            if text is None:
                print(f"  ! point {p['id']}: no codeChunk payload, skipped")
                skipped += 1
                continue
            ops.append({"set_payload": {
                "points": [p["id"]],
                "payload": {"document": text},
            }})

        if ops:
            _post(
                f"/collections/{collection}/points/batch?wait=false",
                {"operations": ops},
            )
            total += len(ops)

        print(f"  …migrated {total}, skipped {skipped}", flush=True)

        if offset is None:
            break

    print(f"\n✓ done. {total} points patched, {skipped} skipped.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: migrate_codechunk_to_document.py <collection>")
    migrate(sys.argv[1])
