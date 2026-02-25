# Search

## Architecture

### System Overview

Nexus implements a multi-modal search system with 5 search modalities, adaptive
query routing, and LightRAG-style graph-enhanced retrieval. The system supports
trigram search (Zoekt), ranked text search (BM25S), semantic search (pgvector),
and LLM-powered query expansion.

```text
  Client
    |
    | POST /api/v2/search
    v
  +----------------------------------------------------+
  | QueryRouter (query_router.py, 213 lines)           |
  |   Heuristic complexity classification              |
  |   Routes: simple | moderate | complex | very_complex|
  +----------------------------------------------------+
    |
    v
  +----------------------------------------------------+
  | QueryService (query_service.py, 323 lines)         |
  |   Async execution: keyword, semantic, hybrid       |
  |   Optional query expansion                         |
  +----------------------------------------------------+
    |               |                |
    v               v                v
  +--------+  +----------+  +------------------+
  | BM25S  |  | pgvector |  | Zoekt            |
  | Keyword|  | Semantic |  | Trigram Search   |
  +--------+  +----------+  +------------------+
    |               |                |
    v               v                v
  +----------------------------------------------------+
  | Fusion (fusion.py, 367 lines)                      |
  |   RRF | Weighted | RRF Weighted                    |
  +----------------------------------------------------+
    |
    v
  +----------------------------------------------------+
  | Ranking (ranking.py, 270 lines)                    |
  |   Attribute-based boosting                         |
  |   Field weighting + exactness bonuses              |
  +----------------------------------------------------+
    |
    v
  +----------------------------------------------------+
  | Graph Retrieval (graph_retrieval.py)                |
  |   Optional LightRAG dual-level enhancement         |
  +----------------------------------------------------+
    |
    v
  Results (with keyword_score, vector_score, fused_score)
```

---

## SearchService

**File**: `nexus/services/search/search_service.py` (2,288 lines)

Main entry point extending `SemanticSearchMixin`.

**Responsibilities**:
- File listing with pagination and permission filtering
- Glob pattern matching with adaptive algorithms
- Content searching (grep) with 5 adaptive strategies
- Semantic search delegation

**Configuration**:
```python
SearchService(
    metadata_store=MetastoreABC,
    permission_enforcer=PermissionEnforcer | None,
    router=PathRouter | None,
    rebac_manager=ReBACManager | None,
    enforce_permissions=True,
    gateway=NexusFSGateway | None,
    list_parallel_workers=10,
    grep_parallel_workers=4,
)
```

### Grep Strategies

The service selects grep strategy based on file count:

| Strategy | When | Description |
|----------|------|-------------|
| Sequential | < 10 files | Simple loop |
| Cached text path | > 80% cached | Use in-memory text cache |
| Rust bulk | Available & large corpus | Native acceleration |
| Parallel thread pool | 100-10k files | Concurrent file scanning |
| Trigram/Zoekt | > 1000 files | Pre-indexed search |

### Glob Strategies

Adaptive pattern matching:

| Strategy | When | Description |
|----------|------|-------------|
| fnmatch | Simple patterns | Standard library |
| regex | Complex patterns | Compiled regex |
| rust | Available | Native acceleration |
| Directory-pruned | Deep hierarchies | Skip non-matching subtrees |

**Features**:
- Zone-aware path filtering (Issue #899)
- Gitignore-style exclusion patterns (Issue #538)
- TTL cache for cross-zone sharing queries (5s, max 1024 entries)
- Thread pool lazy initialization with locking
- RPC exposure for remote invocation

---

## Query Service

**File**: `nexus/search/query_service.py` (323 lines)

Async search execution engine.

```python
results = await query_service.search(
    query="authentication handler",
    path="/",
    limit=10,
    search_mode="hybrid",      # keyword | semantic | hybrid
    alpha=0.5,                  # vector weight (0=BM25, 1=vector)
    fusion_method="rrf",        # rrf | weighted | rrf_weighted
    adaptive_k=False,
)
```

### Search Modes

| Mode | Description |
|------|-------------|
| `keyword` | Direct BM25/FTS lookup |
| `semantic` | Query embedding + vector similarity |
| `hybrid` | Parallel keyword + semantic, fused with configured method |

**Hybrid details**: Over-fetches by 3x from each source, then fuses. Preserves
both `keyword_score` and `vector_score` in results.

**Adaptive K**: Dynamically adjusts result limit based on query complexity via
`ContextBuilder`. Degrades gracefully if unavailable.

---

## Query Router

**File**: `nexus/search/query_router.py` (213 lines)

Heuristic-based complexity classification (no LLM call, < 5ms).

### Complexity Scoring

```
Score = word_count_score
      + comparison_indicators    (+0.20 for vs, versus, compare)
      + temporal_indicators      (+0.15 for when, before, after)
      + aggregation_indicators   (+0.15 for all, summary)
      + multi_hop_patterns       (+0.20 for "how does", "relationship between")
      + complex_patterns         (+0.15 for explain, analyze)

word_count_score = min(len(words) / 20, 0.25)
```

### Routing Rules

| Score | Class | Graph Mode | Limit | Summaries |
|-------|-------|------------|-------|-----------|
| < 0.3 | Simple | none | 0.8x | No |
| 0.3-0.6 | Moderate | low | 1.0x | No |
| 0.6-0.8 | Complex | dual | 1.2x | No |
| >= 0.8 | Very Complex | dual | 1.5x | Yes |

All classes use hybrid search mode.

**Configuration**:
```python
@dataclass
class RoutingConfig:
    simple_max: float = 0.3
    moderate_max: float = 0.6
    complex_max: float = 0.8
    enabled: bool = True
```

---

## Zoekt Trigram Search

**File**: `nexus/search/zoekt_client.py` (543 lines)

Sub-50ms trigram-based search via external Zoekt server. Although Zoekt
originated as a code search engine (Google/Sourcegraph), Nexus uses it as a
**general-purpose trigram index** for ALL stored content. Every CAS write
triggers a Zoekt reindex via `on_write_callback` in the storage backend
(`backends/local.py:302`), so files, memories, and documents are all indexed.

**Configuration**:
```python
ZoektClient(
    base_url="http://localhost:6070",
    timeout=10.0,
    enabled=ZOEKT_ENABLED,  # from env
)
```

**Search Interface**:
```python
matches = await zoekt.search(
    query="def authentication_handler",
    num=100,
    repos=["myproject"],
)
```

**Result Format**:
```python
@dataclass
class ZoektMatch:
    file: str       # Filename
    line: int       # Line number
    content: str    # Line content
    match: str      # Matched text
    score: float
```

**Query Types**:
- Literal: `"authentication"`
- Regex: `"def \\w+_handler"`
- Boolean: `"error AND handler"`
- File filter: `"file:auth.py error"`

**Health**: Async availability check with caching, 2s timeout. Graceful
fallback if Zoekt unavailable.

**Deployment**: Docker Compose profiles `--profile zoekt` and
`--profile zoekt-index`.

---

## BM25S Text Search

**File**: `nexus/search/bm25s_search.py` (851 lines)

500x faster than rank-bm25 via eager sparse scoring with memory-mapped index
loading.

### Code-Aware Tokenizer

Handles programming identifiers:

| Pattern | Example | Tokens |
|---------|---------|--------|
| camelCase | `getUserName` | `[get, user, name]` |
| snake_case | `get_user_name` | `[get, user, name]` |
| PascalCase | `UserNameHandler` | `[user, name, handler]` |
| SCREAMING_SNAKE | `MAX_VALUE` | `[max, value]` |
| Alphanumeric | `user123` | `[user, 123]` |

60+ stopwords filtered (common English + code tokens).

**Result Format**:
```python
@dataclass
class BM25SSearchResult:
    path: str
    path_id: str
    score: float
    content_preview: str = ""
    matched_field: str = "content"
```

**Implementation**: Scipy sparse matrices for efficient computation.
Cross-document batching in indexing pipeline. Singleton pattern.

**Availability check**: `is_bm25s_available()` returns `False` if bm25s
library not installed.

---

## Semantic Search (pgvector)

**File**: `nexus/search/vector_db.py` (427 lines)

### VectorDatabase

Facade pattern supporting two backends:

| Backend | Vector Extension | FTS Extension |
|---------|-----------------|---------------|
| SQLite | sqlite-vec (HNSW) | FTS5 |
| PostgreSQL | pgvector (HNSW) | pg_textsearch BM25 (PG17+) |

**Search**:
```python
results = vector_db.vector_search(
    session=session,
    query_embedding=[0.1, 0.2, ...],
    limit=10,
    path_filter="/docs/",
)
```

PostgreSQL uses `<=>` cosine distance operator with halfvec compression.
SQLite supports cosine, L2, and inner product metrics.

### Embedding Providers

**File**: `nexus/search/embeddings.py` (740 lines)

| Provider | Model | Dimensions | Cost |
|----------|-------|-----------|------|
| OpenAI | text-embedding-3-large | 3072 | — |
| OpenAI | text-embedding-3-small | 1536 | — |
| OpenAI | text-embedding-ada-002 | 1536 | — |
| Voyage AI | voyage-3 | 1024 | $0.06/1M |
| Voyage AI | voyage-3-lite | 512 | $0.02/1M |
| OpenRouter | openai/text-embedding-3-small | 1536 | — |

**Batch Optimization**:
```python
embeddings = await embed_texts_batched(
    texts=["hello world", "foo bar"],
    batch_size=None,       # Auto-select by provider
    parallel=True,
    max_concurrent=5,      # Semaphore backpressure (Issue #1094)
)
```

**Default Batch Sizes**:

| Provider | Batch Size |
|----------|-----------|
| OpenAI | 100 |
| Voyage | 128 |
| OpenRouter | 50 |
| Cohere | 96 |
| FastEmbed (local) | 256 |

---

## Query Expansion

**File**: `nexus/search/query_expansion.py` (891 lines)

LLM-powered query expansion with three expansion types.

### Expansion Types

| Type | Target | Description |
|------|--------|-------------|
| `lex` | BM25 | Keyword variants, synonyms, abbreviations (2-5 words) |
| `vec` | Vector | Natural language questions for embedding search |
| `hyde` | Vector | Hypothetical document passages (1-2 sentences) |

### Configuration

```python
@dataclass
class QueryExpansionConfig:
    enabled: bool = True
    provider: str = "openrouter"
    model: str = "deepseek/deepseek-chat"
    fallback_models: list[str] = [
        "deepseek/deepseek-chat-v3-0324:free",
        "google/gemini-2.0-flash-exp:free",
        "google/gemini-2.0-flash",
        "openai/gpt-4o-mini",
    ]
    max_lex_variants: int = 2
    max_vec_variants: int = 2
    max_hyde_passages: int = 2
    strong_signal_threshold: float = 0.85
    signal_separation_threshold: float = 0.10
    cache_enabled: bool = True
    cache_ttl: int = 3600      # 1 hour
    timeout: float = 5.0
    temperature: float = 0.7
    max_tokens: int = 400
```

### Smart Triggering

Expansion is skipped when:
- Top BM25 score >= `strong_signal_threshold` (0.85)
- Gap between top-1 and top-2 >= `signal_separation_threshold` (0.10)

This avoids unnecessary LLM calls when the initial retrieval is already high
quality.

### Output

```python
@dataclass
class ExpansionResult:
    original_query: str
    expansions: list[QueryExpansion]
    was_expanded: bool
    skip_reason: str | None
    model_used: str | None
    latency_ms: float
    cache_hit: bool
```

Methods: `get_lex_variants()`, `get_vec_variants()`, `get_hyde_passages()`,
`get_all_queries(include_original=True)`.

---

## Ranking

**File**: `nexus/search/ranking.py` (270 lines)

Attribute-based score boosting with field weighting and exactness bonuses.

### Field Weights

| Field | Weight | Description |
|-------|--------|-------------|
| `filename` | 3.0 | Filename matches (highest priority) |
| `title` | 2.5 | Title/header matches |
| `path` | 2.0 | Path component matches |
| `tags` | 2.0 | Tag matches |
| `description` | 1.5 | Description/summary matches |
| `content` | 1.0 | Body content (baseline) |

### Exactness Bonuses

| Bonus | Multiplier | Description |
|-------|-----------|-------------|
| Exact match | 1.5x | Exact phrase match |
| Prefix match | 1.2x | Query is prefix of field |

### Scoring Formula

```
final_score = original_score * field_weight * exactness_multiplier
```

### Environment Configuration

```bash
NEXUS_SEARCH_WEIGHT_FILENAME=3.0
NEXUS_SEARCH_WEIGHT_TITLE=2.5
NEXUS_SEARCH_WEIGHT_PATH=2.0
NEXUS_SEARCH_WEIGHT_TAGS=2.0
NEXUS_SEARCH_WEIGHT_DESCRIPTION=1.5
NEXUS_SEARCH_WEIGHT_CONTENT=1.0
NEXUS_SEARCH_EXACT_MATCH_BOOST=1.5
NEXUS_SEARCH_PREFIX_MATCH_BOOST=1.2
NEXUS_SEARCH_ATTRIBUTE_BOOST=true
NEXUS_SEARCH_EXACTNESS_BOOST=true
```

---

## Fusion

**File**: `nexus/search/fusion.py` (367 lines)

Three fusion methods for combining keyword and vector search results.

### RRF (Reciprocal Rank Fusion) — Default

```
Score = sum(1 / (k + rank))
```

| Parameter | Value | Description |
|-----------|-------|-------------|
| `k` | 60 | Smoothing constant (from original paper) |

Rank-based (not score-normalized). Robust across different scoring scales.
Stable across query types.

### Weighted Linear Combination

```
Score = (1 - alpha) * keyword_norm + alpha * vector_norm
```

| Parameter | Range | Description |
|-----------|-------|-------------|
| `alpha` | 0.0-1.0 | Vector weight (0=pure BM25, 1=pure vector) |
| `normalize_scores` | bool | Apply min-max normalization first |

### RRF Weighted

```
Score = (1 - alpha) * (1/(k + keyword_rank)) + alpha * (1/(k + vector_rank))
```

Combines RRF robustness with alpha biasing.

### Configuration

```python
@dataclass
class FusionConfig:
    method: FusionMethod = FusionMethod.RRF
    alpha: float = 0.5
    rrf_k: int = 60
    normalize_scores: bool = True
    over_fetch_factor: float = 3.0  # Fetch 3x from each source
```

**Usage**:
```python
fused = fuse_results(
    keyword_results,
    vector_results,
    config=config,
    limit=10,
    id_key="chunk_id",
)
```

---

## Graph-Enhanced Retrieval

**File**: `nexus/search/graph_retrieval.py`

LightRAG-style dual-level retrieval (Issue #1040). See also
[memory.md](memory.md) for knowledge graph data model.

### Retrieval Modes

| Mode | Method | Description |
|------|--------|-------------|
| `none` | Skip | No graph enhancement |
| `low` | Entity-based | Extract entities from query -> HNSW similarity -> N-hop expansion |
| `high` | Theme-based | Hierarchical memory context (prefer abstracts) |
| `dual` | LightRAG | Both low + high combined |

### Low-Level (Entity-Based)

1. Extract entities from query via NER/embedding
2. Find similar entities in graph (HNSW similarity, threshold 0.75)
3. Expand N-hops via recursive CTEs (default 2 hops)
4. Score by graph proximity

### High-Level (Theme-Based)

1. Retrieve theme/cluster context from HierarchicalMemoryManager
2. Prefer `abstraction_level > 0` (high-level summaries)
3. Expand theme to children for detail

### Fusion Weights

```python
GraphRetrievalConfig(
    lambda_semantic=0.4,    # Semantic search weight
    lambda_keyword=0.3,     # Keyword search weight
    lambda_graph=0.3,       # Graph retrieval weight
)
```

---

## Memory Search Integration

The SearchService provides memory-specific search modes in
`search_service.py`:

### Semantic Memory Search

Uses embedding vectors with cosine similarity. Default threshold: 0.7. Returns
`(memory, score)` pairs.

### Keyword Memory Search

Simple text matching with word count scoring. Fallback when no embeddings
available.

### Hybrid Memory Search

Combines semantic (70%) + keyword (30%) with fusion. Best of both worlds.

### Access Tracking (Issue #1030)

Every memory retrieval updates:
- `last_accessed_at` timestamp
- `access_count` increment
- Preserves `importance_original` for decay calculation

---

## Configuration Summary

| Setting | Default | Environment Variable |
|---------|---------|---------------------|
| Zoekt URL | `http://localhost:6070` | `NEXUS_ZOEKT_URL` |
| Zoekt enabled | false | `NEXUS_ZOEKT_ENABLED` |
| Zoekt timeout | 10s | — |
| BM25S available | auto-detect | — |
| Embedding model | text-embedding-3-small | `NEXUS_EMBEDDING_MODEL` |
| Embedding batch concurrent | 5 | — |
| Query expansion model | deepseek/deepseek-chat | `NEXUS_QE_MODEL` |
| Query expansion enabled | true | `NEXUS_QE_ENABLED` |
| Query expansion cache TTL | 3600s | — |
| Fusion method | RRF | — |
| Fusion alpha | 0.5 | — |
| Fusion RRF k | 60 | — |
| Over-fetch factor | 3.0 | — |
| Semantic threshold | 0.7 | — |
| Graph mode | varies by complexity | — |
| Graph neighbor hops | 2 | — |
| Entity similarity threshold | 0.75 | — |
| List parallel workers | 10 | — |
| Grep parallel workers | 4 | — |
| Cross-zone cache TTL | 5s | — |
| Cross-zone cache max | 1024 entries | — |

---

## Search Daemon

**File**: `nexus/search/daemon.py` (1,070 lines)

Long-running service with pre-warmed indexes for sub-50ms response times.

```text
┌─────────────────────────────────────────────┐
│         Search Daemon                       │
│  ┌─────────────────────────────────────┐   │
│  │  Startup: Pre-warm all indexes      │   │
│  │  - Load BM25S from disk (mmap)      │   │
│  │  - Pool database connections        │   │
│  │  - Warm vector indexes (ef_search)  │   │
│  │  - Connect to Zoekt (optional)      │   │
│  └─────────────────────────────────────┘   │
│           ↓                                 │
│  ┌─────────────────────────────────────┐   │
│  │  Hot Search (< 50ms)                │   │
│  │  - BM25S: in-memory sparse matrix   │   │
│  │  - Vector: pooled DB connections    │   │
│  │  - Zoekt: warm HTTP connection      │   │
│  └─────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

**Configuration**:
```python
@dataclass
class DaemonConfig:
    database_url: str | None = None
    db_pool_min_size: int = 10
    db_pool_max_size: int = 50
    db_pool_recycle: int = 1800       # 30 min

    bm25s_index_dir: str = ".nexus-data/bm25s"
    bm25s_mmap: bool = True

    vector_warmup_enabled: bool = True
    vector_ef_search: int = 100

    refresh_debounce_seconds: float = 5.0
    refresh_enabled: bool = True

    query_timeout_seconds: float = 10.0
    max_indexing_concurrency: int = 10
```

**Statistics** (exposed via `/metrics`):
- `startup_time_ms`, `bm25_documents`, `bm25_load_time_ms`
- `db_pool_size`, `db_pool_warmup_time_ms`, `vector_warmup_time_ms`
- `total_queries`, `avg_latency_ms`, `p99_latency_ms`
- `last_index_refresh`, `zoekt_available`

---

## Indexing Pipeline

**File**: `nexus/search/indexing.py` (467 lines)

Two-phase parallel indexing with cross-document batching.

```text
Phase 1: Chunking (Parallel)
  ├─ asyncio.Semaphore(max_concurrency=10)
  ├─ asyncio.to_thread() for CPU-bound chunking
  └─ Result: _ChunkedDoc with chunks + metadata

Phase 2: Cross-Document Batching & Embedding
  ├─ Collect all chunks from all documents
  ├─ Sort into batches (batch_size=100 per provider)
  ├─ Embed in parallel (max_embedding_concurrency=5)
  └─ Store embeddings in _ChunkedDoc

Phase 3: Bulk Insert
  ├─ SQLite: executemany()
  └─ PostgreSQL: batched INSERT ... VALUES
```

**Performance**: Cross-document batching provides 15-30x faster embedding
than per-document processing.

**Progress reporting**:
```python
@dataclass(frozen=True)
class IndexProgress:
    completed: int
    total: int
    current_path: str | None = None
    errors: int = 0
```

---

## Chunking

**File**: `nexus/search/chunking.py` (1,042 lines)

| Strategy | Description |
|----------|-------------|
| `fixed` | Fixed-size chunks (default 1024 tokens) |
| `semantic` | Paragraph/section boundary splitting |
| `overlapping` | Overlapping chunks (default overlap 128) |

**Optimizations**: O(log n) line number lookup via pre-computed offsets and
binary search. ~150ms for 250KB documents.

### Contextual Chunking (Issue #1192)

**File**: `nexus/search/contextual_chunking.py` (428 lines)

Implements Anthropic's Contextual Retrieval pattern. Adds LLM-generated
"situating context" to each chunk to make it self-contained.

```text
1. Base chunking (DocumentChunker)
2. For each chunk:
   - Get prev 2 chunks + next chunk (surrounding context)
   - LLM call: generate situating_context
   - Output: ChunkContext (situating_context, resolved_references, key_entities)
3. Compose: contextual_text = context + "\n\n" + original_chunk
4. Embed: contextual_text (not just original chunk)
```

**Configuration**: `contextual_chunking: bool = False` (opt-in, expensive).

### Entropy-Aware Filtering (Issue #1024)

Based on SimpleMem paper (arXiv:2601.02553). Filters redundant/low-information
chunks before embedding.

| Setting | Default | Description |
|---------|---------|-------------|
| `NEXUS_ENTROPY_FILTERING` | false | Enable filtering |
| `NEXUS_ENTROPY_THRESHOLD` | 0.35 | Redundancy threshold |
| `NEXUS_ENTROPY_ALPHA` | 0.5 | Entity vs semantic novelty balance |

---

## HNSW Auto-Tuning

**File**: `nexus/search/hnsw_config.py` (254 lines)

Automatic HNSW index tuning based on dataset size.

| Scale | Vectors | m | ef_construction | ef_search | RAM | Recall | QPS |
|-------|---------|---|-----------------|-----------|-----|--------|-----|
| Small | < 100K | 16 | 64 | 40 | 512MB | ~0.95 | ~20 |
| Medium | 100K-1M | 24 | 128 | 100 | 2GB | ~0.99 | ~40 |
| Large | > 1M | 32 | 200 | 200 | 4GB | ~0.998 | ~30 |

```python
config = HNSWConfig.for_dataset_size(vector_count)
```

PostgreSQL uses `halfvec(1536)` (half-precision) for 50% storage savings.

---

## Keyword Search Priority

```text
1. Zoekt (fastest, trigram-based, sub-50ms)
2. BM25S (fast, in-memory sparse matrix)
3. PostgreSQL pg_textsearch BM25 (PG17+) / FTS
4. SQLite FTS5 (fallback)
```

Each level is optional and gracefully falls through to the next.

---

## Module Summary

| Module | Lines | Purpose |
|--------|-------|---------|
| `search_service.py` | 2,288 | Main search service (grep, glob, list) |
| `graph_store.py` | 1,470 | PostgreSQL-native knowledge graph |
| `daemon.py` | 1,070 | Hot search daemon (pre-warmed indexes) |
| `chunking.py` | 1,042 | Document chunking strategies |
| `query_expansion.py` | 891 | LLM-based query expansion |
| `bm25s_search.py` | 851 | BM25S fast ranked text search |
| `embeddings.py` | 740 | Embedding providers (OpenAI, Voyage) |
| `graph_retrieval.py` | 721 | LightRAG dual-level retrieval |
| `zoekt_client.py` | 543 | Zoekt trigram search client |
| `indexing.py` | 467 | Parallel indexing pipeline |
| `contextual_chunking.py` | 428 | Anthropic contextual retrieval |
| `vector_db.py` | 427 | Vector database facade |
| `fusion.py` | 367 | RRF/weighted fusion |
| `query_service.py` | 323 | Async search execution |
| `ranking.py` | 270 | Attribute-based ranking |
| `vector_db_postgres.py` | 255 | PostgreSQL pgvector backend |
| `hnsw_config.py` | 254 | HNSW auto-tuning |
| `vector_db_sqlite.py` | 225 | SQLite sqlite-vec backend |
| `query_router.py` | 213 | Query complexity routing |
| `config.py` | 171 | Centralized search config |
| `protocols.py` | 127 | Dependency inversion protocols |
| `results.py` | 106 | Unified result types |
| `strategies.py` | 86 | Adaptive algorithm selection |

**Total**: ~13,900 lines in search module.

---

## Known Issues

1. **ReBAC SQL bug**: `memory_permission_enforcer.py:184` generates invalid SQL
   for memory search queries, causing HTTP 500 on every search request. This
   prevents native Nexus memory search from working. Workaround: client-side
   embedding index.

2. **Zoekt requires separate deployment**: Needs Docker Compose profiles
   (`--profile zoekt`, `--profile zoekt-index`). Not available in embedded
   profile.

3. **BM25S optional dependency**: Falls back to database FTS if bm25s library
   is not installed.

4. **pgvector optional**: Falls back to Python cosine similarity (capped at
   1000 entries) when pgvector extension is not available.

5. **Query expansion latency**: LLM-based expansion adds 200-500ms. Smart
   triggering skips expansion when initial retrieval is strong.

---

## Test Setup

### Server Startup

```bash
./scripts/serve-for-tests.sh
```

Search features require the search brick to be enabled in the server profile.

### Zoekt Setup

```bash
docker compose --profile zoekt --profile zoekt-index up -d
```

### Fixture Architecture

```
Session-scoped (from root conftest)
  nexus                 Admin NexusClient

Module-scoped
  search_zone           Clean zone for search tests
  indexed_files         Pre-indexed files for retrieval tests
```

### Test Classes

**TestKeywordSearch**: BM25S tokenization, scoring, code-aware splitting.

**TestSemanticSearch**: Vector similarity search, embedding generation,
threshold filtering.

**TestHybridSearch**: Combined keyword + semantic with RRF fusion, alpha
tuning.

**TestQueryRouter**: Complexity classification, routing rules, graph mode
selection.

**TestQueryExpansion**: LLM expansion types (lex/vec/hyde), smart triggering,
caching.

**TestGraphRetrieval**: Entity extraction, N-hop traversal, dual-level fusion,
LightRAG mode.

**TestRanking**: Field weight application, exactness bonuses, score boosting.

**TestFusion**: RRF, weighted, RRF-weighted fusion methods, over-fetch
behavior.
