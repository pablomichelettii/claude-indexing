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
import sys
import hashlib
import numpy as np
from numpy.typing import NDArray
import cocoindex

from indexer import defaults


FLOW_NAME = os.environ["INDEXER_FLOW_NAME"]
CODEBASE_PATH = os.environ["INDEXER_PATH"]
COLLECTION_NAME = os.environ["INDEXER_COLLECTION"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", defaults.EMBEDDING_MODEL)
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", str(defaults.EMBEDDING_DIM)))
QDRANT_GRPC_URL = os.environ.get("QDRANT_GRPC_URL", defaults.QDRANT_GRPC_URL)

# Name of the Qdrant named-vector under which embeddings are stored.
# The mcp-server-qdrant fork reads its `QDRANT_VECTOR_NAME` env var to query
# under this same name (see mcp.py — we pass it during registration).
VECTOR_FIELD = "embedding"


INCLUDED_PATTERNS = [
    # Programming languages
    "**/*.py",
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
    "**/*.sh",
    "**/*.yml", "**/*.yaml",
    "**/*.json",
    # Generic Frontend Application
    "**/*.ts", "**/*.tsx",
    "**/*.js", "**/*.jsx",
    "**/*.vue",
    "**/*.svelte",
    "**/*.html", "**/*.xhtml",
    "**/*.css", "**/*.scss", "**/*.less", "**/*.sass",
    # Java enterprise UI and config / Wildfly Environment
    "**/*.jsp",
    "**/*.xml",
    "**/*.ftlx", "**/*.ftl", "**/*.drl",
    "**/*.xsd",
    "**/*.wsdl",
    "**/*.properties",
    # Docs and queries
    "**/*.md",
    "**/*.sql",
]

EXCLUDED_PATTERNS = [
    # VCS / generic
    "**/.git/**",
    "**/.hg/**",
    "**/.svn/**",
    "**/generated-sources/**",
    "**/generated/**",

    # Editor / agent / IDE metadata
    "**/.idea/**",
    "**/.vscode/**",
    "**/.cursor/**",
    "**/.claude/**",
    "**/.fleet/**",
    "**/.settings/**",
    "**/.project",
    "**/.classpath",
    "**/.factorypath",

    # --- Java / Spring Boot / Maven / Gradle ---
    "**/target/**",                # Maven build output
    "**/build/**",                 # Gradle build output (also used by others)
    "**/out/**",                   # IntelliJ compiled output
    "**/.gradle/**",
    "**/.mvn/wrapper/maven-wrapper.jar",
    "**/bin/**",                   # Eclipse compiled output
    "**/*.class",
    "**/*.jar",
    "**/*.war",
    "**/*.ear",
    "**/*.nar",
    "**/hs_err_pid*.log",
    "**/dependency-reduced-pom.xml",

    # --- PHP / Laravel / Composer ---
    "**/vendor/**",
    "**/bootstrap/cache/**",
    "**/storage/framework/**",
    "**/storage/logs/**",
    "**/storage/debugbar/**",
    "**/public/hot",
    "**/public/storage",
    "**/.phpunit.result.cache",
    "**/.phpunit.cache/**",
    "**/.php-cs-fixer.cache",
    "**/.php_cs.cache",
    "**/.phpstan.cache/**",
    "**/.psalm/**",

    # --- Go ---
    "**/vendor/**",                # also Go modules vendor dir
    "**/bin/**",
    "**/pkg/**",
    "**/*.test",
    "**/*.out",
    "**/go.sum",

    # --- Node / JS / TS (Angular, React, Vue, Svelte, Next, Nuxt, etc.) ---
    "**/node_modules/**",
    "**/dist/**",
    "**/build/**",
    "**/out/**",
    "**/.next/**",
    "**/.nuxt/**",
    "**/.svelte-kit/**",
    "**/.angular/**",
    "**/.turbo/**",
    "**/.parcel-cache/**",
    "**/.cache/**",
    "**/.vite/**",
    "**/.rollup.cache/**",
    "**/.webpack/**",
    "**/.expo/**",
    "**/.remix/**",
    "**/.docusaurus/**",
    "**/.astro/**",
    "**/coverage/**",
    "**/.nyc_output/**",
    "**/storybook-static/**",
    "**/.eslintcache",
    "**/.stylelintcache",
    "**/.yarn/**",
    "**/.pnp.*",
    "**/.npm/**",
    "**/.pnpm-store/**",

    # --- Python ---
    "**/__pycache__/**",
    "**/.venv/**",
    "**/venv/**",
    "**/env/**",
    "**/.env/**",
    "**/.tox/**",
    "**/.nox/**",
    "**/.mypy_cache/**",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.pytype/**",
    "**/.pyre/**",
    "**/.ipynb_checkpoints/**",
    "**/*.egg-info/**",
    "**/*.egg",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.pyd",
    "**/pip-wheel-metadata/**",
    "**/htmlcov/**",
    "**/.coverage",
    "**/.coverage.*",

    # Generated assets / minified / source maps / binaries
    "**/*.min.js",
    "**/*.min.css",
    "**/*.map",
    "**/*.so",
    "**/*.dll",
    "**/*.dylib",
    "**/*.exe",

    # Lock files (machine-generated, rarely useful for semantic search)
    "**/*.lock",
    "**/*-lock.json",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/bun.lockb",
    "**/composer.lock",
    "**/Pipfile.lock",
    "**/poetry.lock",
    "**/uv.lock",

    # Logs, OS junk, temp
    "**/*.log",
    "**/logs/**",
    "**/tmp/**",
    "**/.tmp/**",
    "**/.DS_Store",
    "**/Thumbs.db",

    # Project-specific bulk artefacts seen in real codebases
    "**/qdrant_storage/**",   # Qdrant data dir accidentally inside a source repo
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


def _scan_unreadable(root: str) -> list[str]:
    """Walk `root` and return relpaths of files/dirs the current user can't read.

    cocoindex's LocalFile source aborts the whole run if it hits a single
    permission-denied error (e.g. a root:root file inside a user-owned tree).
    By collecting these paths up-front we can both surface them to the user
    AND feed them into excluded_patterns so the run completes.
    """
    unreadable: list[str] = []

    def _on_error(err: OSError) -> None:
        # Triggered when os.walk can't list a directory (e.g. no +x perm).
        try:
            rel = os.path.relpath(err.filename, root)
        except ValueError:
            rel = err.filename
        print(f"[indexer] WARNING: cannot access {rel}: {err.strerror} — skipping", file=sys.stderr)
        unreadable.append(rel)

    for dirpath, _dirnames, filenames in os.walk(root, onerror=_on_error, followlinks=False):
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not os.access(full, os.R_OK):
                rel = os.path.relpath(full, root)
                print(f"[indexer] WARNING: unreadable file (permission denied): {rel} — skipping", file=sys.stderr)
                unreadable.append(rel)
    return unreadable


def _confirm_continue(unreadable: list[str]) -> None:
    """If unreadable paths were found, ask the user whether to proceed.

    Non-interactive runs (no TTY) proceed automatically — otherwise the
    indexer would deadlock in CI or when launched by a hook. Set
    INDEXER_SKIP_UNREADABLE_PROMPT=1 to also bypass the prompt in a TTY.
    """
    if not unreadable:
        return
    if os.environ.get("INDEXER_SKIP_UNREADABLE_PROMPT") == "1" or not sys.stdin.isatty():
        print(f"[indexer] proceeding past {len(unreadable)} unreadable path(s) (non-interactive)", file=sys.stderr)
        return
    try:
        answer = input(f"[indexer] {len(unreadable)} unreadable path(s) will be skipped. Continue? [Y/n] ").strip().lower()
    except EOFError:
        answer = ""
    if answer in ("n", "no"):
        sys.exit("[indexer] aborted by user")


@cocoindex.flow_def(name=FLOW_NAME)
def code_index_flow(flow_builder: cocoindex.FlowBuilder, data_scope: cocoindex.DataScope):
    unreadable = _scan_unreadable(CODEBASE_PATH)
    _confirm_continue(unreadable)
    excluded = list(EXCLUDED_PATTERNS) + unreadable

    data_scope["files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path=CODEBASE_PATH,
            included_patterns=INCLUDED_PATTERNS,
            excluded_patterns=excluded,
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
                # `document` is the payload field mcp-server-qdrant reads in
                # qdrant-find. `codeChunk` kept as alias for any non-MCP query.
                document=chunk["text"],
                codeChunk=chunk["text"],
                startLine=chunk["start"]["line"],
                endLine=chunk["end"]["line"],
                segmentHash=chunk["segment_hash"],
                pathSegments=chunk["path_segments"],
                # Vector field name must match what mcp-server-qdrant queries for.
                **{VECTOR_FIELD: chunk["embedding"]},
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
                field_name=VECTOR_FIELD,
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            )
        ],
    )


if __name__ == "__main__":
    cocoindex.cli.cli()
