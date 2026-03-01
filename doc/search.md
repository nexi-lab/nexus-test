# Search E2E Test Setup Guide

## Quick Start

```bash
# 1. Start PostgreSQL — use ParadeDB image for pg_search BM25 support
#    (or standard postgres:16 — pg_search is optional, BM25S fallback works)
docker run -d --name paradedb -p 5432:5432 \
  -e POSTGRES_DB=nexus -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=nexus \
  paradedb/paradedb:latest \
  -c shared_preload_libraries=pg_stat_statements,pg_search

# 2. Start Nexus server with full search infrastructure
NEXUS_DATABASE_URL="postgresql://postgres:nexus@localhost:5432/nexus" \
NEXUS_SEARCH_DAEMON=true \
NEXUS_PORT=2026 \
NEXUS_MODE=standalone \
NEXUS_PERMISSIONS_ENABLED=true \
nexus serve --auth-type database --init

# 3. Run search E2E tests (in another terminal)
cd ~/nexus-test
NEXUS_TEST_URL=http://localhost:2026 \
NEXUS_TEST_API_KEY=<your-admin-api-key> \
NEXUS_TEST_DATABASE_URL=postgresql://postgres:nexus@localhost:5432/nexus \
NEXUS_TEST_ZONE=corp \
uv run pytest tests/search/ -v -o "addopts=" --timeout=180
```

## Infrastructure Dependencies

| Service | Required | Port | Purpose |
|---------|----------|------|---------|
| ParadeDB (PostgreSQL) | Yes | 5432 | Chunks, embeddings (pgvector), BM25 keyword search (pg_search) |
| Nexus Server | Yes | 2026 | RPC API (search, file write, memory) |
| Dragonfly/Redis | Optional | 6379 | Embedding cache dedup |
| Zoekt | Optional | 6070 | Trigram code search |
| OpenRouter API | Optional | - | LLM query expansion |

### What each service enables

- **ParadeDB PostgreSQL**: pg_search BM25 keyword search (Tantivy engine), file indexing, pgvector
- **Standard PostgreSQL**: Falls back to BM25S in-memory keyword search (no pg_search extension)
- **+ Dragonfly**: Embedding cache hit/miss tracking (search/009)
- **+ Zoekt**: Trigram code search (search/007)
- **+ OpenRouter**: LLM query expansion (search/006 full test)

## Server Environment Variables

### Required

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_DATABASE_URL` | none | PostgreSQL connection string. **Critical**: without this, `db_pool_ready=false` and most tests fail |
| `NEXUS_SEARCH_DAEMON` | `false` | Enable the search daemon (BM25S + vector indexes) |
| `NEXUS_PORT` | `2026` | Server listen port |

### Keyword Search (BM25)

The keyword search engine has three tiers, tried in priority order:

| Engine | Type | Recall@10 | Latency | When Used |
|--------|------|-----------|---------|-----------|
| Zoekt | Trigram grep | — | <1ms | If Zoekt server is running |
| BM25S | In-memory | 76.7% | 0.9ms | Corpus < `BM25S_MAX_DOCUMENTS` (default 100K) |
| pg_search (Tantivy) | DB-backed BM25 | 78.4% | 47ms | ParadeDB PostgreSQL with pg_search extension |

HERB benchmark (815 questions, 43K docs) showed pg_search provides the best recall quality.
BM25S is fastest but RAM-bound. pg_search scales to millions of docs on disk.

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_SEARCH_BM25S_MAX_DOCUMENTS` | `100000` | Skip BM25S when corpus exceeds this; use pg_search instead |

### Query Expansion

LLM-based query expansion generates lexical, semantic, and HyDE variants to improve recall.
Uses OpenRouter by default (access to DeepSeek, Gemini, GPT-4o-mini, etc. via single API key).

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_SEARCH_EXPANSION_ENABLED` | `false` | Enable LLM query expansion |
| `NEXUS_SEARCH_EXPANSION_PROVIDER` | `openrouter` | `openrouter`, `openai`, or `local` |
| `NEXUS_SEARCH_EXPANSION_MODEL` | `deepseek/deepseek-chat` | Any OpenRouter model ID |
| `OPENROUTER_API_KEY` | — | Required when provider=openrouter |
| `OPENAI_API_KEY` | — | Required when provider=openai |

**OpenRouter model examples** (set `NEXUS_SEARCH_EXPANSION_MODEL`):

```bash
# Free models
deepseek/deepseek-chat-v3-0324:free
google/gemini-2.0-flash-exp:free

# Paid models (better quality)
deepseek/deepseek-chat          # default — best cost/quality
openai/gpt-4o-mini
anthropic/claude-3.5-haiku
google/gemini-2.0-flash
```

Built-in **fallback chain**: if the primary model fails (rate limit, timeout), the system
automatically tries these in order:
1. `deepseek/deepseek-chat-v3-0324:free`
2. `google/gemini-2.0-flash-exp:free`
3. `google/gemini-2.0-flash`
4. `openai/gpt-4o-mini`

**Local provider** (`NEXUS_SEARCH_EXPANSION_PROVIDER=local`): uses a GGUF model via
`llama-cpp-python` — no API key needed, fully offline. Set `NEXUS_SEARCH_EXPANSION_MODEL`
to a HuggingFace repo ID or local GGUF file path.

### Reranker

Cross-encoder reranking re-scores search results for higher precision. Supports both
local models (no API needed) and API providers (Jina, Cohere).

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_SEARCH_RERANKING_ENABLED` | `false` | Enable cross-encoder reranking |
| `NEXUS_SEARCH_RERANKER_PROVIDER` | auto-detected | `local`, `jina`, or `cohere` |
| `NEXUS_SEARCH_RERANKER_MODEL` | auto-detected | Key from model registry (see below) |
| `NEXUS_SEARCH_RERANKING_TOP_K` | `30` | Max candidates to rerank |

**Auto-detection**: if `JINA_API_KEY` is set, defaults to `jina` provider + `jina-reranker-v3`.
If `COHERE_API_KEY` is set, defaults to `cohere` + `cohere-rerank-v3.5`.
Otherwise defaults to `local` + `jina-tiny`.

**Available reranker models** (`NEXUS_SEARCH_RERANKER_MODEL`):

| Key | Model | Provider | Size | Notes |
|-----|-------|----------|------|-------|
| `jina-tiny` | jinaai/jina-reranker-v1-tiny-en | local (GGUF) | 40MB | Fast, lightweight |
| `jina-turbo` | jinaai/jina-reranker-v1-turbo-en | local (GGUF) | 80MB | Better quality |
| `bge-reranker-base` | BAAI/bge-reranker-base | local (sentence_transformers) | 110MB | MIT license |
| `bge-reranker-v2-m3` | BAAI/bge-reranker-v2-m3 | local (sentence_transformers) | 560MB | Best local, multilingual |
| `jina-reranker-v3` | jina-reranker-v2-base-multilingual | API (Jina) | 0 | Needs `JINA_API_KEY` |
| `cohere-rerank-v3.5` | rerank-v3.5 | API (Cohere) | 0 | Needs `COHERE_API_KEY` |

Local models use `sentence_transformers.CrossEncoder` — any HuggingFace cross-encoder model
works. LLM-based rerankers (e.g., Qwen) are **not** compatible (they need a causal-LM provider
that doesn't exist yet).

### Other Pipeline Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_SEARCH_POSITION_BLENDING` | `true` | Position-aware score blending |
| `NEXUS_SEARCH_SCORED_CHUNKING` | `false` | Scored break-point chunking |

### Test-side Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_TEST_URL` | `http://localhost:2026` | Server URL |
| `NEXUS_TEST_API_KEY` | `sk-test-...` | Admin API key |
| `NEXUS_TEST_DATABASE_URL` | `postgresql://postgres:nexus@localhost:5432/nexus` | Must match server's DB |
| `NEXUS_TEST_ZONE` | `corp` | Default zone for tests |

## Test Matrix

| ID | Test File | Description | Dependencies |
|----|-----------|-------------|--------------|
| search/001 | `test_fulltext.py` | BM25S keyword search (7 tests) | db_pool, indexed data |
| search/002 | `test_semantic.py` | Semantic/hybrid search (4 tests) | db_pool, embeddings |
| search/003 | `test_rebac.py` | ReBAC permission filtering (2 tests) | db_pool, permissions |
| search/004 | `test_index_on_write.py` | File write → searchable (3 tests) | db_pool, BM25 refresh |
| search/005 | `test_herb_benchmark.py` | HERB Q&A accuracy (2 classes) | HERB data, embeddings |
| search/005 | `test_herb_qa.py` | HERB Q&A scoring (3 tests) | HERB data |
| search/006 | `test_query_expansion.py` | LLM query expansion (3 tests) | OpenRouter API key |
| search/007 | `test_zoekt.py` | Zoekt trigram search (6 tests) | Zoekt server |
| search/008 | `test_warmup.py` | Daemon warmup stats (7 tests) | daemon initialized |
| search/009 | `test_cache_dedup.py` | Embedding cache dedup (4 tests) | Dragonfly/Redis |
| search/001-009 | `test_search.py` | Combined suite (8 tests) | all of the above |

### Expected Results by Infrastructure Level

**Minimal** (PostgreSQL + Nexus server):
- search/001: 7 pass (after data seeding)
- search/003: 2 pass
- search/004: 3 pass
- search/008: 7 pass
- search/009: 2 pass, 2 skip (no Dragonfly)
- Total: ~21 pass, ~28 skip

**Standard** (+ embeddings enabled):
- Above + search/002: 4 pass
- Above + search/005 CI: pass (with HERB data)
- Total: ~30 pass, ~19 skip

**Full** (+ Zoekt + Dragonfly + OpenRouter):
- All 49 tests: ~45 pass, ~4 skip (HERB data only)

## Pipeline Latency Tracking

The search daemon tracks per-stage latency for every hybrid search query:

```
GET /api/v2/search/stats
```

Response includes:
```json
{
  "pipeline_stage_latencies": {
    "expansion_ms": 120.5,
    "retrieval_ms": 15.3,
    "fusion_ms": 0.8,
    "reranking_ms": 45.2
  }
}
```

Stages measured:
- `expansion_ms` — LLM query expansion (only when enabled)
- `retrieval_ms` — Parallel keyword (Zoekt → BM25S → pg_search) + vector retrieval
- `fusion_ms` — RRF multi-fusion scoring
- `reranking_ms` — Cross-encoder reranking + blending (only when enabled)

Server logs emit `[PIPELINE]` lines per query:
```
[PIPELINE] {'retrieval_ms': 12.3, 'fusion_ms': 0.5}
```

## Degradation Chain

The pipeline degrades gracefully. Every stage is independently toggleable:

```
FULL:    Expansion → Retrieval → Fusion(weighted) → Reranking → Blending
MEDIUM:  Retrieval → Fusion(RRF)
MINIMAL: BM25 keyword search only (zero models, zero APIs)
```

Keyword retrieval also degrades independently:
```
Zoekt (trigram grep) → BM25S (in-memory) → pg_search (DB-backed BM25)
```

If a stage fails at init time, a runtime flag (`_expansion_active`, `_reranking_active`) is set to `false` while the config flag remains `true` (preserving intent vs state separation).

### Docker images

| Image | pg_search | pgvector | Use case |
|-------|-----------|----------|----------|
| `paradedb/paradedb:latest` | Yes | Yes | Recommended — full BM25 + vector support |
| `postgres:16-alpine` | No | No | Minimal — BM25S in-memory fallback only |
| `pgvector/pgvector:pg17` | No | Yes | Vector search only, no DB-backed BM25 |

## Troubleshooting

### `db_pool_ready: false`

The server was started without `NEXUS_DATABASE_URL`. The search daemon needs a PostgreSQL connection pool to:
- Read file content from NexusFS for BM25 indexing
- Store/retrieve document chunks and embeddings
- Run pgvector semantic search

Fix: set `NEXUS_DATABASE_URL` before starting the server.

### `bm25_documents: 0`

No files have been indexed yet. The test fixtures seed data automatically, but if db_pool is not ready, seeding fails silently. Verify `db_pool_ready: true` first.

### `redb` lock error

```
Error: Database already open. Cannot acquire lock.
```

Another Nexus server instance is running with the same data directory. Stop it first:
```bash
pkill -f "nexus serve"
```

### Permission denied on search refresh

```
Access denied: User 'anonymous' does not have READ permission
```

The search daemon's internal file reader runs as `system` user. Ensure the server has `NEXUS_PERMISSIONS_ENABLED=true` and the daemon has a system-level bypass (which is the default).

### Embedding cache not connected

Tests that check cache hit rates will skip. Start Dragonfly/Redis:
```bash
docker run -d --name dragonfly -p 6379:6379 docker.dragonflydb.io/dragonflydb/dragonfly
```

### Zoekt not available

All Zoekt tests (search/007) will skip. Start Zoekt:
```bash
# See docker-compose.yml for Zoekt setup
ZOEKT_ENABLED=true ZOEKT_URL=http://localhost:6070 nexus serve ...
```
