# Memory

## Architecture

### System Overview

Nexus implements a MemGPT-inspired 3-tier virtual memory system with rich
enrichment, knowledge graph integration, and ACE (Agentic Context Engineering)
for continuous learning. Memory is zone-isolated, permission-checked via ReBAC,
and supports bi-temporal queries.

```text
  Client (NexusClient)
        |
        | POST /api/memory/store
        | GET  /api/memory/query
        | GET  /api/memory/list
        | GET  /api/memory/{memory_id}
        v
  +--------------------------------------------------+
  | Memory Service (memory_service.py)               |
  |   RPC wrapper for all memory operations          |
  +--------------------------------------------------+
        |
        v
  +--------------------------------------------------+
  | Memory API (memory_api.py, ~2100 lines)          |
  |   Core logic: store, query, approve, deactivate  |
  |   Enrichment pipeline integration                |
  +--------------------------------------------------+
        |                          |
        v                          v
  +--------------------+   +---------------------------+
  | Enrichment Pipeline|   | 3-Tier Virtual Memory     |
  | (enrichment.py)    |   | (memory_paging/)          |
  | 8-step processing  |   |  Main → Recall → Archival |
  +--------------------+   +---------------------------+
        |
        v
  +--------------------------------------------------+
  | Knowledge Graph (PostgreSQL-native)              |
  |  EntityModel + RelationshipModel + EntityMention |
  +--------------------------------------------------+
        |
        v
  +--------------------------------------------------+
  | ACE Framework (services/ace/)                    |
  |  Consolidation, Reflection, Trajectories,        |
  |  Playbooks, Feedback, Learning Loop              |
  +--------------------------------------------------+
```

---

## Data Model

### MemoryModel

**File**: `nexus/storage/models/memory.py` (496 lines, 27+ indexes)

Core table for all memory types. Content is stored in CAS (content-addressable
store) and referenced by `content_hash`.

| Column | Type | Description |
|--------|------|-------------|
| `memory_id` | UUID | Primary key |
| `zone_id` | String(36) | Tenant isolation |
| `user_id` | String(36) | Owner user |
| `agent_id` | String(36) | Owner agent |
| `content_hash` | String(128) | CAS reference to content |
| `memory_type` | String(32) | `fact`, `preference`, `experience`, `consolidated`, `reflection`, `cluster` |
| `scope` | String(16) | `agent`, `user`, `zone`, `global` |
| `visibility` | String(16) | `private`, `shared`, `public` |
| `state` | String(16) | `active`, `inactive`, `deleted` |
| `namespace` | String(512) | Hierarchical path (e.g. `/project/cache/`) |
| `path_key` | String(256) | Upsert key within namespace |
| `importance` | Float | Score 0.0-1.0 |
| `importance_original` | Float | Score before decay |
| `abstraction_level` | Integer | 0=atomic, 1=cluster, 2+=abstract |
| `embedding` | Text | JSON vector for semantic search |
| `embedding_model` | String(64) | Model name (e.g. `text-embedding-3-small`) |
| `embedding_dim` | Integer | Vector dimensionality |

**Temporal Fields** (Issues #1023, #1028, #1183, #1185):

| Column | Type | Description |
|--------|------|-------------|
| `valid_at` | DateTime | When fact became true (real world) |
| `invalid_at` | DateTime | When fact stopped being true |
| `earliest_date` | DateTime | First date reference in content |
| `latest_date` | DateTime | Last date reference in content |
| `temporal_refs_json` | Text | Extracted date/time references |
| `temporal_stability` | String(16) | `static`, `semi_dynamic`, `dynamic` |
| `stability_confidence` | Float | Classifier confidence |
| `estimated_ttl_days` | Integer | Time-to-live estimate |

**Entity & Relationship Fields** (Issues #1025, #1038):

| Column | Type | Description |
|--------|------|-------------|
| `entities_json` | Text | Extracted entities |
| `entity_types` | Text | Comma-separated NER types |
| `person_refs` | Text | Person names found |
| `relationships_json` | Text | Extracted (S,P,O) triplets |
| `relationship_count` | Integer | Number of relationships |

**Evolution & Versioning Fields** (Issues #1188, #1190):

| Column | Type | Description |
|--------|------|-------------|
| `current_version` | Integer | Version counter |
| `supersedes_id` | String(36) | Previous version |
| `superseded_by_id` | String(36) | Next version |
| `extends_ids` | Text | JSON array of extended memory IDs |
| `extended_by_ids` | Text | JSON array |
| `derived_from_ids` | Text | JSON array |
| `parent_memory_id` | String(36) | Parent abstract/consolidated |
| `child_memory_ids` | Text | JSON array of children |
| `consolidated_from` | Text | JSON array of source IDs |
| `is_archived` | Boolean | Archived after consolidation |
| `consolidation_version` | Integer | Consolidation batch counter |

**Access Tracking** (Issue #1030):

| Column | Type | Description |
|--------|------|-------------|
| `access_count` | Integer | Total retrievals |
| `last_accessed_at` | DateTime | Last retrieval time |

**Indexes** (27+): zone_id, user_id, agent_id, memory_type, state, namespace,
abstraction_level, created_at (BRIN), zone_id+created_at (BRIN), valid_at,
invalid_at (BRIN), extends_ids, extended_by_ids, derived_from_ids,
supersedes_id, and more.

### EntityModel (Knowledge Graph Nodes)

| Column | Type | Description |
|--------|------|-------------|
| `entity_id` | UUID | Primary key |
| `zone_id` | String(36) | Tenant isolation |
| `canonical_name` | String(512) | Unique per zone |
| `entity_type` | String(64) | PERSON, ORG, LOCATION, DATE, TIME, NUMBER, CONCEPT, EVENT, PRODUCT, TECHNOLOGY, EMAIL, URL, OTHER |
| `embedding` | Text | Semantic vector for deduplication |
| `aliases` | Text | JSON array of alternative names |
| `merge_count` | Integer | Deduplication merge count |

### RelationshipModel (Knowledge Graph Edges)

| Column | Type | Description |
|--------|------|-------------|
| `relationship_id` | UUID | Primary key |
| `source_entity_id` | UUID FK | Source entity |
| `target_entity_id` | UUID FK | Target entity |
| `relationship_type` | String(64) | See types below |
| `weight` | Float | Edge weight (default 1.0) |
| `confidence` | Float | 0.0-1.0 |

**Relationship Types**: `WORKS_WITH`, `MANAGES`, `REPORTS_TO`, `CREATES`,
`MODIFIES`, `OWNS`, `DEPENDS_ON`, `BLOCKS`, `RELATES_TO`, `MENTIONS`,
`REFERENCES`, `LOCATED_IN`, `PART_OF`, `HAS`, `USES`, `OTHER`, `UPDATES`,
`EXTENDS`, `DERIVES`.

Unique constraint: `(zone_id, source_entity_id, target_entity_id, relationship_type)`.

### EntityMentionModel (Provenance)

Links entities to source chunks/memories for traceability.

| Column | Type | Description |
|--------|------|-------------|
| `mention_id` | UUID | Primary key |
| `entity_id` | UUID FK | Referenced entity |
| `chunk_id` | UUID FK | Source document chunk (nullable) |
| `memory_id` | UUID FK | Source memory (nullable) |
| `confidence` | Float | 0.0-1.0 |
| `mention_text` | String(512) | Original text span |
| `char_offset_start` | Integer | Start position |
| `char_offset_end` | Integer | End position |

---

## API Endpoints

### POST /api/memory/store

Stores a new memory with optional enrichment.

**Request**:
```json
{
  "content": "Alice joined Google as a senior engineer",
  "scope": "user",
  "memory_type": "fact",
  "importance": 0.8,
  "namespace": "/people/alice",
  "path_key": "job",
  "generate_embedding": true,
  "extract_entities": true,
  "extract_temporal": true,
  "extract_relationships": false,
  "store_to_graph": false,
  "classify_stability": true,
  "detect_evolution": false,
  "resolve_coreferences": false,
  "resolve_temporal": false
}
```

**Response**: `{"memory_id": "mem_abc123"}`

### GET /api/memory/query

Temporal and entity-aware query endpoint.

| Parameter | Type | Description |
|-----------|------|-------------|
| `scope` | string | Filter by scope |
| `memory_type` | string | Filter by type |
| `state` | string | Filter by state (default: `active`) |
| `after` | ISO-8601 | Memories created after |
| `before` | ISO-8601 | Memories created before |
| `during` | string | Partial date filter (e.g. `2025`, `2025-01`) |
| `entity_type` | string | Filter by NER type |
| `person` | string | Filter by person name |
| `event_after` | ISO-8601 | Filter by earliest_date >= value |
| `event_before` | ISO-8601 | Filter by latest_date <= value |
| `valid_at_point` | ISO-8601 | Bi-temporal: facts valid at point |
| `system_at_point` | ISO-8601 | Bi-temporal: system state at point |
| `temporal_stability` | string | Filter by stability class |
| `include_invalid` | bool | Include superseded memories |
| `include_superseded` | bool | Include version chain |
| `limit` | int | Max results (1-1000, default 100) |

### GET /api/memory/list

Namespace-based listing with same temporal filters as query.

| Parameter | Type | Description |
|-----------|------|-------------|
| `namespace` | string | Exact namespace match |
| `namespace_prefix` | string | Hierarchical prefix query |

### GET /api/memory/{memory_id}

Returns full memory with content, metadata, and effective importance.

### POST /api/memory/approve

Activates memory (state: `inactive` -> `active`).

### POST /api/memory/deactivate

Deactivates memory (state: `active` -> `inactive`).

### Batch Operations

- `approve_memory_batch(memory_ids: list[str])`
- `deactivate_memory_batch(memory_ids: list[str])`
- `delete_memory_batch(memory_ids: list[str])`

---

## MemoryViewRouter

**File**: `nexus/services/memory/memory_router.py`

Virtual path resolution for order-neutral memory access.

| Path Format | Example |
|-------------|---------|
| Canonical | `/objs/memory/{memory_id}` |
| By user | `/memory/by-user/{user}/{filename}` |
| By agent | `/memory/by-agent/{agent}/{filename}` |
| Full path | `/workspace/{zone}/{user}/{agent}/memory/{filename}` |
| Zone-agnostic | `/workspace/{user}/{agent}/memory/{filename}` |

---

## Enrichment Pipeline

**File**: `nexus/services/memory/enrichment.py` (lines 80-293)

8-step processing pipeline triggered on `memory_store`. Steps 1-5 are default;
steps 6-8 are opt-in.

```text
  Content
    |
    v
  Step 1: Embedding Generation (#406)
  → embedding_json, embedding_model, embedding_dim
    |
    v
  Step 2: Entity Extraction (#1025)
  → entities_json, entity_types, person_refs
    |
    v
  Step 3: Temporal Metadata Extraction (#1028)
  → temporal_refs_json, earliest_date, latest_date
    |
    v
  Step 4: Relationship Extraction (#1038)  [opt-in, expensive]
  → relationships_json, relationship_count
    |
    v
  Step 5: Temporal Stability Classification (#1191)
  → temporal_stability, stability_confidence, estimated_ttl_days
    |
    v
  Step 6: Coreference Resolution (#1027)  [opt-in, write-time]
  → Replaces pronouns with entity names
    |
    v
  Step 7: Temporal Resolution (#1027)  [opt-in, write-time]
  → Replaces relative dates with absolute
    |
    v
  Step 8: Memory Evolution Detection (#1190)  [opt-in, expensive]
  → relationship_type (UPDATES/EXTENDS/DERIVES), target_memory_id
```

### EnrichmentFlags

```python
@dataclass
class EnrichmentFlags:
    generate_embedding: bool = True
    extract_entities: bool = True
    extract_temporal: bool = True
    extract_relationships: bool = False   # Opt-in (expensive)
    classify_stability: bool = True
    detect_evolution: bool = False         # Opt-in (expensive)
    resolve_coreferences: bool = False     # Opt-in (write-time)
    resolve_temporal: bool = False         # Opt-in (write-time)
    store_to_graph: bool = False           # Opt-in (#1039)
```

### Step Details

**Step 1 — Embedding**: Provider-agnostic (OpenRouter, Anthropic, local).
Graceful degradation on failure. Used for semantic search and affinity scoring.

**Step 2 — Entity Extraction**: Regex/LLM-based NER. 13 entity types (PERSON,
ORG, LOCATION, DATE, TIME, NUMBER, CONCEPT, EVENT, PRODUCT, TECHNOLOGY, EMAIL,
URL, OTHER). Used for knowledge graph and entity-based filtering.

**Step 3 — Temporal Extraction**: Identifies absolute dates, relative dates, and
date ranges. Populates `earliest_date`/`latest_date` for event date filtering.

**Step 4 — Relationship Extraction**: LLM-based `(subject, predicate, object)`
triplet extraction with confidence threshold 0.5. 19 predicate types. Used for
knowledge graph storage and multi-hop reasoning.

**Step 5 — Stability Classification**: Hybrid heuristic+LLM classifier.
Heuristic handles 70-80% of cases via regex markers and entity-attribute
ontology. Escalates to LLM when confidence < 0.6.

| Classification | TTL | Example |
|----------------|-----|---------|
| `static` | infinite | "Alice was born on January 1, 1990" |
| `semi_dynamic` | 365 days | "Alice works at Google" |
| `dynamic` | 30 days | "Alice is currently in London" |

**Step 6 — Coreference Resolution**: SimpleMem-inspired pronoun replacement.
LLM-based (primary) with heuristic fallback. Makes memories self-contained.

Example: `"He went to the store"` -> `"John Smith went to the store"`

**Step 7 — Temporal Resolution**: Replaces relative dates with absolute using
reference time.

Example: `"Meeting scheduled for tomorrow at 2pm"` ->
`"Meeting scheduled for 2025-01-11 at 14:00"`

**Step 8 — Evolution Detection**: Detects semantic relationships between new
and existing memories.

| Relationship | Meaning | Markers |
|-------------|---------|---------|
| `UPDATES` | Supersedes old info | "actually", "correction", "is now" |
| `EXTENDS` | Adds detail | "also", "additionally", "moreover" |
| `DERIVES` | Logical consequence | "therefore", "because of", "implies" |

Scoring: entity overlap + embedding similarity + regex patterns. Escalates to
LLM when confidence < 0.7.

---

## 3-Tier Virtual Memory

**Files**: `nexus/services/memory/memory_paging/`

MemGPT-inspired architecture with automatic tiered storage.

```text
┌─────────────────────────────────────────┐
│         Main Context (RAM/FIFO)         │
│  ContextManager: LRU cache              │
│  Max items: 100 (configurable)          │
│  In-memory OrderedDict                  │
│  Evicts to Recall when full             │
└──────────────────┬──────────────────────┘
                   │ eviction
┌──────────────────v──────────────────────┐
│    Recall Store (SQL Sequential)        │
│  RecallStore: Temporal index            │
│  Namespace: "recall/*"                  │
│  Max age: 24 hours (configurable)       │
│  Queries: get_recent(), query_temporal()│
└──────────────────┬──────────────────────┘
                   │ archival
┌──────────────────v──────────────────────┐
│  Archival Store (SQL Semantic)          │
│  ArchivalStore: Vector search           │
│  Namespace: "archival/*"                │
│  Optional pgvector acceleration         │
│  Queries: search_semantic()             │
└─────────────────────────────────────────┘
```

### Tier 1: Main Context

**File**: `context_manager.py`

- In-memory `OrderedDict` with LRU eviction
- Capacity: 100 (default)
- Thread-safe (lock-protected)
- `warm_up()`: Loads recent memories from DB on init
- Returns evicted memories on `add()` for cascading to Recall

### Tier 2: Recall Store

**File**: `recall_store.py`

- SQL storage with `namespace LIKE 'recall/%'`
- Sequential access by creation time
- Age-out check runs every 5 additions (debounced)
- Old memories (> 24h) archived to Tier 3
- Supports temporal range queries

### Tier 3: Archival Store

**File**: `archival_store.py`

- SQL storage with `namespace LIKE 'archival/%'`
- Semantic search via embeddings
- Two modes:
  - **pgvector-accelerated**: PostgreSQL COSINE operator (fast)
  - **Python fallback**: Loads up to 1000 memories, computes cosine in Python
- Similarity threshold: 0.7 default
- Optional `prefer_abstracts` for hierarchical retrieval

### MemoryPager (Orchestrator)

**File**: `pager.py`

Coordinates all three tiers.

```python
pager = MemoryPager(
    session_factory=get_session,
    zone_id="acme",
    main_capacity=100,
    recall_max_age_hours=24.0,
    warm_up=True,
)
```

**Key Methods**:

- `add_to_main(memory)` — Add with cascading eviction
- `search_all_tiers(query_embedding)` — Returns `{main: [...], recall: [...], archival: [...]}`
- `get_recent_context(limit=50)` — Combines main + recent recall for LLM context
- `get_stats()` — Returns per-tier counts and utilization

---

## Importance Decay

**Issue #1030**

Memories decay in importance over time based on access patterns.

```
importance_decayed = importance_original * decay_factor ^ days_since_access
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `decay_factor` | 0.95 | 5% reduction per day |
| `minimum` | 0.1 | Floor importance |

Example: importance 0.8, 10 days since access -> `0.8 * 0.95^10 = 0.48`

Access tracking updates `last_accessed_at` and increments `access_count` on
every retrieval.

---

## Memory Versioning

**File**: `nexus/services/memory/versioning.py`

Chain-based version tracking (Issue #1188).

```text
Memory A (v1) --supersedes--> Memory B (v2) --supersedes--> Memory C (v3, current)
                                              ^
                                       superseded_by_id
```

**Operations**:

| Method | Description |
|--------|-------------|
| `resolve_to_current(id)` | Follows `superseded_by_id` chain forward |
| `get_chain_memory_ids(id)` | Walks backward to oldest ancestor |
| `list_versions(id)` | All versions with timestamps and diffs |
| `rollback(id, version)` | Creates new memory pointing to old content |
| `diff_versions(id1, id2)` | Unified diff between two versions |

---

## Bi-Temporal Queries

**Issues #1183, #1185**

Two independent time dimensions:

| Dimension | Fields | Query | Purpose |
|-----------|--------|-------|---------|
| Valid time | `valid_at`, `invalid_at` | `valid_at_point` | When fact is true in the real world |
| System time | `created_at`, `updated_at` | `system_at_point` | When record existed in the database |

**Query semantics**:

```sql
-- Point-in-time (valid time)
WHERE valid_at <= :point AND (invalid_at IS NULL OR invalid_at > :point)

-- System-time query
WHERE created_at <= :point AND (invalid_at IS NULL OR invalid_at > :point)
```

---

## ACE Framework

**Files**: `nexus/services/ace/`

ACE (Agentic Context Engineering) is the learning engine that orchestrates
trajectory tracking, reflection, curation, consolidation, and hierarchical
memory abstraction.

### Trajectory Manager

**File**: `trajectory.py` (472 lines)

Tracks execution trajectories (action sequences) with steps, decisions, and
observations.

| Method | Description |
|--------|-------------|
| `start_trajectory()` | Creates trajectory with task description and metadata |
| `log_step()` | Records step (action, decision, observation, tool_call) |
| `complete_trajectory()` | Finalizes with status, success_score, error_message, metrics |
| `get_trajectory()` | Retrieves full trace from CAS |
| `query_trajectories()` | Filters by agent_id, task_type, status with ReBAC |

Trace data (steps, decisions, observations) stored in CAS; metadata in
`TrajectoryModel`.

### Reflector

**File**: `reflection.py` (443 lines)

LLM-based analysis of completed trajectories.

```python
result = await reflector.reflect_async(trajectory_id)
```

**Output structure**:
- `helpful_strategies`: Successful patterns with evidence and confidence
- `harmful_patterns`: Failure patterns with impact assessment
- `observations`: Neutral observations about execution
- `confidence`: Overall confidence score (0.0-1.0)

Reflections are stored as memories with `memory_type="reflection"` and
`importance=confidence`.

### Curator

**File**: `curation.py` (358 lines)

Merges reflection learnings into playbook strategies via keyword-based
similarity (Jaccard, threshold 0.7).

```python
await curator.curate_playbook(trajectory_id, playbook_id)
```

Deduplicates strategies, takes max confidence, tracks evidence sources.

### Playbook Manager

**File**: `playbook.py` (508 lines)

Manages learned playbooks (strategy repositories) with scope
(`agent`/`user`/`zone`/`global`) and visibility (`private`/`shared`/`public`).

| Method | Description |
|--------|-------------|
| `create_playbook()` | New playbook with scope and visibility |
| `update_playbook()` | Add strategies, increment version |
| `record_usage()` | Track usage count, success rate (EMA) |
| `get_relevant_strategies()` | Keyword-based filtering |
| `query_playbooks()` | Filter by agent, scope, pattern |

Content stored in CAS, metadata in `PlaybookModel`.

### Feedback Manager

**File**: `feedback.py` (381 lines)

Handles dynamic feedback for trajectories with scoring strategies.

| Strategy | Description |
|----------|-------------|
| `latest` | Most recent feedback score |
| `average` | Mean of all scores |
| `weighted` | Time-weighted (recent = higher) |

Auto-triggers relearning if score changes by > 0.3 from original. Supports
feedback sources: `human`, `monitoring`, `ab_test`, `production`.

### Learning Loop

**File**: `learning_loop.py` (335 lines)

Top-level orchestrator that wraps task execution with automatic learning.

```python
result = await loop.execute_with_learning_async(
    task_fn=my_task,
    task_description="Process user query",
)
```

**Flow**:
1. Start trajectory
2. Execute `task_fn`
3. Complete trajectory with metrics
4. Reflect on outcome (if enabled)
5. Update playbook (if enabled)

Reflection/curation failures are logged but do not fail task execution.

---

## Memory Consolidation

**File**: `nexus/services/ace/consolidation.py` (837 lines)

Two consolidation modes:

### Mode 1: Batch-based

Merges memories by criteria (memory_type, scope, namespace, importance
threshold). LLM synthesizes summary. Uses XML-wrapped data tags for prompt
injection hardening (Issue #1756).

```python
result = await engine.consolidate_by_criteria(
    zone_id="acme", memory_type="fact", importance_max=0.3
)
```

### Mode 2: Affinity-based (SimpleMem-inspired, Issue #1026)

Clusters memories using semantic + temporal affinity.

**Formula**:
```
affinity = beta * cos(v_i, v_j) + (1 - beta) * exp(-lambda * |t_i - t_j|)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `beta` | 0.7 | Semantic weight (0-1) |
| `lambda_decay` | 0.1 | Temporal decay rate |
| `time_unit_hours` | 24.0 | Normalization factor |
| `cluster_threshold` | 0.85 | Min affinity for clustering |
| `linkage` | `average` | Clustering method |
| `min_cluster_size` | 2 | Min memories per cluster |

```python
result = await engine.consolidate_by_affinity_async(
    zone_id="acme", beta=0.7, affinity_threshold=0.85
)
```

**Result**: Per-cluster consolidation with bounded concurrency (max 3 LLM
calls). Consolidated memory importance = `max(source_importances) + 0.1`.
Source memories archived (`importance` -> 0.1, `parent_memory_id` set).

---

## Hierarchical Memory

**File**: `nexus/services/ace/memory_hierarchy.py` (601 lines, Issue #1029)

Multi-level abstraction tree.

```text
Level 2:  [Meta-abstract]
               |
Level 1:  [Cluster A]     [Cluster B]
          /    |    \         |    \
Level 0: [m1] [m2] [m3]   [m4]  [m5]    (atomic memories)
```

**Build process**:
1. Cluster atomic memories by affinity
2. LLM synthesizes each cluster into an abstract
3. Recursively consolidate upper levels until `max_levels` reached

**Fields**: `abstraction_level` (0=atomic, 1=cluster, 2+=abstract),
`parent_memory_id`, `child_memory_ids`.

---

## Knowledge Graph

### Graph Store

**File**: `nexus/search/graph_store.py`

PostgreSQL-native adjacency list model (no external graph DB).

| Method | Description |
|--------|-------------|
| `add_entity()` | Deduplicate via embedding similarity (HNSW) |
| `add_relationship()` | Create edge with optional merge |
| `get_neighbors()` | N-hop traversal via recursive CTEs |
| `extract_subgraph()` | Connected component for context |

### Graph Retrieval (LightRAG-style, Issue #1040)

**File**: `nexus/search/graph_retrieval.py`

Dual-level retrieval modes:

| Mode | Method | Description |
|------|--------|-------------|
| `none` | Skip | No graph enhancement |
| `low` | Entity-based | Entity matching + N-hop neighbor expansion |
| `high` | Theme-based | Hierarchical memory context (prefer abstracts) |
| `dual` | LightRAG | Both low + high combined |

**Configuration**:
```python
GraphRetrievalConfig(
    graph_mode="dual",
    entity_similarity_threshold=0.75,
    neighbor_hops=2,
    prefer_abstracts=True,
    lambda_semantic=0.4,
    lambda_keyword=0.3,
    lambda_graph=0.3,
)
```

**Fusion**: Combines semantic + keyword + graph scores with configurable
lambda weights.

---

## RLM Inference

### POST /api/v2/rlm/infer

Recursive Language Model endpoint for 10M+ token context decomposition
(arXiv:2512.24601).

**Request**:
```json
{
  "query": "What is the root cause of the performance regression?",
  "context_paths": ["/src/main.py", "/tests/"],
  "zone_id": "root",
  "model": "claude-sonnet-4-20250514",
  "max_iterations": 15,
  "max_duration_seconds": 120,
  "max_total_tokens": 100000,
  "sandbox_provider": "docker",
  "stream": true
}
```

**Response (non-streaming)**:
```json
{
  "status": "success",
  "answer": "The performance regression is caused by...",
  "total_tokens": 15000,
  "total_duration_seconds": 45.3,
  "iterations": 12
}
```

**Streaming**: Server-Sent Events with `iteration_start`, `code_execution`,
`iteration_complete`, `final_answer` event types.

**Architecture**: Thread pool with 503 overload handling, sandboxed REPL
execution (Docker, E2B, or Monty), pre-loaded tools `nexus_read` and
`nexus_search`.

---

## Configuration Constants

```python
# Importance decay (Issue #1030)
DEFAULT_DECAY_FACTOR = 0.95
DEFAULT_MIN_IMPORTANCE = 0.1

# 3-Tier Paging
MAIN_CAPACITY = 100
RECALL_MAX_AGE_HOURS = 24.0
ARCHIVE_BATCH_LIMIT = 50
ARCHIVE_CHECK_INTERVAL = 5  # every 5 additions

# Stability Classification
CONFIDENCE_THRESHOLD = 0.6  # heuristic -> LLM escalation
DEFAULT_TTL_STATIC = None   # infinite
DEFAULT_TTL_SEMI_DYNAMIC = 365  # days
DEFAULT_TTL_DYNAMIC = 30        # days

# Affinity (SimpleMem)
BETA = 0.7
LAMBDA = 0.1
CLUSTER_THRESHOLD = 0.85
TIME_UNIT_HOURS = 24.0

# Search (in search_service.py)
SEMANTIC_THRESHOLD = 0.7
KEYWORD_WEIGHT = 0.3
SEMANTIC_WEIGHT = 0.7
PYTHON_SEARCH_CAP = 1000  # fallback max entries
```

---

## Known Issues

1. **ReBAC SQL bug**: `memory_permission_enforcer.py:184` generates invalid SQL
   that crashes ALL memory search queries with HTTP 500. Prevents native Nexus
   search from being used in benchmarks.

2. **archival_store.py line 83**: `TODO: Trigger hierarchical consolidation` --
   consolidation not yet wired into automatic paging workflow.

3. **Coreference resolver** (Issue #2124): `TODO: Extract to
   nexus.llm.protocols when LLM module brick-extracted`.

4. **pgvector optional**: Falls back to Python cosine similarity (capped at
   1000 entries) when pgvector is not available.

5. **Operations endpoint**: `/api/v2/operations` returns 500 due to
   `NexusFS._record_store` access bug (see observability.md).

---

## Test Setup

### Server Startup

```bash
./scripts/serve-for-tests.sh
```

Or:
```bash
NEXUS_ENFORCE_PERMISSIONS=true nexus serve
```

### Fixture Architecture

```
Session-scoped (from root conftest)
  nexus                 Admin NexusClient

Module-scoped (from tests/memory/conftest.py)
  memory_zone           Clean zone for memory tests
  stored_memory_id      Pre-stored memory for retrieval tests
```

### Test Classes

**TestMemoryStore (memory/014-015)**: Store and retrieve memories, verify
enrichment fields populated (entities, temporal, stability).

**TestMemoryQuery (memory/016-017)**: Temporal filtering (`after`, `before`,
`during`), entity filtering (`person`, `entity_type`), namespace queries.

**TestMemoryPaging (memory/018)**: 3-tier paging lifecycle (main -> recall ->
archival), verify eviction cascading and search across tiers.

**TestMemoryEvolution (memory/019)**: Evolution detection (UPDATES, EXTENDS,
DERIVES), version chain traversal, rollback.

**TestConsolidation (memory/020)**: Batch and affinity-based consolidation,
verify source archival and importance boosting.

**TestKnowledgeGraph (memory/021)**: Entity extraction, relationship storage,
N-hop traversal, graph-enhanced retrieval.

**TestSelectiveForgetting (memory/022)**: TOFU-style forget/retain with
ROUGE-L measurement.

**TestRLM (memory/023)**: RLM inference endpoint, streaming vs non-streaming,
iteration tracking.
