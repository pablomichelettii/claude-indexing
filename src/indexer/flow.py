"""CocoIndex pipeline. Parameters come from env vars set by the CLI:

    INDEXER_FLOW_NAME   unique cocoindex flow name (used as state-store key)
    INDEXER_PATH        absolute path to codebase
    INDEXER_COLLECTION  qdrant collection name
    OPENROUTER_API_KEY  embedding API key
    EMBEDDING_MODEL     e.g. "qwen/qwen3-embedding-8b"
    EMBEDDING_DIM       e.g. 4096
    QDRANT_GRPC_URL     e.g. "http://localhost:6334"

Invoked indirectly via `cocoindex update src/indexer/flow.py`.
"""

import os
import hashlib
import numpy as np
from numpy.typing import NDArray
import cocoindex


FLOW_NAME = os.environ["INDEXER_FLOW_NAME"]
CODEBASE_PATH = os.environ["INDEXER_PATH"]
COLLECTION_NAME = os.environ["INDEXER_COLLECTION"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "qwen/qwen3-embedding-8b")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "4096"))
QDRANT_GRPC_URL = os.environ.get("QDRANT_GRPC_URL", "http://localhost:6334")


INCLUDED_PATTERNS = [
    "**/*.py",
    "**/*.ts", "**/*.tsx",
    "**/*.js", "**/*.jsx",
    "**/*.go",
    "**/*.rs",
    "**/*.java",
    "**/*.rb",
    "**/*.cpp", "**/*.cc", "**/*.h",
    "**/*.c",
    "**/*.cs",
    "**/*.swift",
    "**/*.kt",
    "**/*.php",
    "**/*.vue",
    "**/*.svelte",
]

EXCLUDED_PATTERNS = [
    "**/target/**",
    "**/node_modules/**",
    "**/.git/**",
    "**/*.min.js",
    "**/*.min.css",
    "**/generated-sources/**",
    "**/generated/**",
    "**/__pycache__/**",
    "**/.gradle/**",
    "**/.idea/**",
    "**/.venv/**",
    "**/dist/**",
    "**/build/**",
]


@cocoindex.op.function()
def sha1_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


@cocoindex.op.function()
def split_path(filename: str) -> list[str]:
    return filename.replace("\\", "/").split("/")


qdrant_connection = cocoindex.add_auth_entry(
    f"qdrant_connection_{FLOW_NAME}",
    cocoindex.targets.QdrantConnection(grpc_url=QDRANT_GRPC_URL),
)


@cocoindex.transform_flow()
def code_to_embedding(
    text: cocoindex.DataSlice[str],
) -> cocoindex.DataSlice[NDArray[np.float32]]:
    return text.transform(
        cocoindex.functions.EmbedText(
            api_type="OpenRouter",
            model=EMBEDDING_MODEL,
            expected_output_dimension=EMBEDDING_DIM,
            api_key=cocoindex.add_transient_auth_entry(OPENROUTER_API_KEY),
        )
    )


@cocoindex.flow_def(name=FLOW_NAME)
def code_index_flow(flow_builder: cocoindex.FlowBuilder, data_scope: cocoindex.DataScope):
    data_scope["files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path=CODEBASE_PATH,
            included_patterns=INCLUDED_PATTERNS,
            excluded_patterns=EXCLUDED_PATTERNS,
        )
    )

    code_embeddings = data_scope.add_collector()

    with data_scope["files"].row() as file:
        file["language"] = file["filename"].transform(
            cocoindex.functions.DetectProgrammingLanguage()
        )
        file["chunks"] = file["content"].transform(
            cocoindex.functions.SplitRecursively(),
            language=file["language"],
            chunk_size=1000,
            chunk_overlap=200,
        )

        with file["chunks"].row() as chunk:
            chunk["embedding"] = chunk["text"].call(code_to_embedding)
            chunk["segment_hash"] = chunk["text"].transform(sha1_hash)
            chunk["path_segments"] = file["filename"].transform(split_path)
            code_embeddings.collect(
                id=cocoindex.GeneratedField.UUID,
                filePath=file["filename"],
                codeChunk=chunk["text"],
                startLine=chunk["start"]["line"],
                endLine=chunk["end"]["line"],
                segmentHash=chunk["segment_hash"],
                pathSegments=chunk["path_segments"],
                embedding=chunk["embedding"],
            )

    code_embeddings.export(
        "code_chunks",
        cocoindex.targets.Qdrant(
            collection_name=COLLECTION_NAME,
            connection=qdrant_connection,
        ),
        primary_key_fields=["id"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            )
        ],
    )


if __name__ == "__main__":
    cocoindex.cli.cli()
