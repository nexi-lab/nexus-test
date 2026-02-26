# Nexus Test Plan

~391 tests across 14 run-type groups and 40 feature groups. Tests exercise the
system through the `nexus` CLI and HTTP API only — no internal Python imports.

---

## 1. Infrastructure

### Docker Compose (federation topology)

Source: `nexus/dockerfiles/docker-compose.cross-platform-test.yml`

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Application Layer                                                       │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐        │
│  │  Frontend   │  │  MCP Server│  │  LangGraph │  │  Zoekt     │        │
│  │  :5173      │  │  :8081     │  │  :2024     │  │  :6070     │        │
│  └──────┬──────┘  └──────┬─────┘  └──────┬─────┘  └────────────┘        │
│         └────────────────┼───────────────┘                               │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  Kernel: 3-Node Raft Cluster (2 full + 1 witness)                  │ │
│  │                                                                     │ │
│  │  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐           │ │
│  │  │  nexus-1     │   │  nexus-2     │   │  witness     │           │ │
│  │  │  (Leader)    │◄─►│  (Follower)  │◄─►│  (Vote-only) │           │ │
│  │  │  HTTP :2026  │   │  HTTP :2027  │   │  gRPC :2128  │           │ │
│  │  │  Raft :2126  │   │  Raft :2127  │   │  (no HTTP)   │           │ │
│  │  └──────┬───────┘   └──────┬───────┘   └──────────────┘           │ │
│  │         ▼                  ▼                                       │ │
│  │  ┌────────────────────────────┐  ┌────────────────────────┐       │ │
│  │  │  PostgreSQL (RecordStore)  │  │  Dragonfly (CacheStore)│       │ │
│  │  │  :5432                     │  │  :6379                 │       │ │
│  │  └────────────────────────────┘  └────────────────────────┘       │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
```

**Services:**

| Service | Image | Port | Role |
|---------|-------|------|------|
| `nexus-1` | nexus-fullnode | :2026 (HTTP), :2126 (Raft) | Full node, initial leader |
| `nexus-2` | nexus-fullnode | :2027 (HTTP), :2127 (Raft) | Full node, follower |
| `witness` | raft-witness | :2128 (Raft) | Vote-only, no HTTP |
| `postgres` | postgres:16-alpine | :5432 | Shared RecordStore |
| `dragonfly` | dragonflydb | :6379 | CacheStore (embedding, Tiger Cache, EventBus) |
| `mcp-server` | nexus-fullnode | :8081 | MCP tool server |
| `langgraph` | nexus-langgraph | :2024 | AI agent orchestration |
| `frontend` | nexus-frontend | :5173 | React web UI |
| `zoekt` | zoekt-webserver | :6070 | Code search (optional `--profile zoekt`) |
| `test` | nexus-fullnode | — | E2E test runner |

**Federation zones:** `corp`, `corp-eng`, `corp-sales`, `family`
**Zone mounts:** `/corp=corp`, `/corp/engineering=corp-eng`, `/corp/sales=corp-sales`, `/family=family`, `/family/work=corp`

### Start / Stop

```bash
# Build + start full cluster
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d

# With code search
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml --profile zoekt up -d

# Teardown (remove volumes)
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml down -v
```

### Test Configuration

```bash
# local.config — point tests at the running cluster
TEST_URL=http://localhost:2026              # nexus-1 (leader)
TEST_URL_FOLLOWER=http://localhost:2027     # nexus-2 (follower)
TEST_API_KEY=sk-test-federation-e2e-admin-key
TEST_ZONE=corp                              # Primary test zone
SCRATCH_ZONE=corp-eng                       # Zone wiped between tests
DATABASE_URL=postgresql://postgres:nexus@localhost:5432/nexus
DRAGONFLY_URL=redis://localhost:6379

# Federation zones (pre-configured in compose)
FED_ZONES=corp,corp-eng,corp-sales,family
FED_NODE_1=http://localhost:2026            # nexus-1
FED_NODE_2=http://localhost:2027            # nexus-2

# Test data
BENCHMARK_DIR=~/nexus/benchmarks
PERF_DATA_DIR=/tmp/nexus_perf_data
```

### Partition Simulation

```bash
# Disconnect witness (cluster still works — 2/3 quorum)
docker network disconnect nexus_nexus-network nexus-witness

# Reconnect
docker network connect nexus_nexus-network nexus-witness

# Kill leader, verify failover
docker stop nexus-node-1
curl http://localhost:2027/health   # nexus-2 becomes leader
docker start nexus-node-1
```

---

## 2. Test Data

| Source | Data | Used By |
|--------|------|---------|
| `benchmarks/performance/` | 50K flat files, 50K nested files, 10K grep files | Stress tests, glob/grep correctness |
| `benchmarks/herb/enterprise-context/` | 30 products, 530 employees, 120 customers (JSONL/Markdown) | Memory, search, grep tests |
| `benchmarks/herb/qa/` | 815 answerable + 699 unanswerable questions with ground truth | Search accuracy, RAG quality |
| `benchmarks/performance/nexus-data/cas/` | Pre-existing CAS blobs with known hashes | CAS dedup, integrity checks |
| `generate_perf_data.py` | On-demand test data generator | Fresh perf data |
| `benchmarks/memory/longmemeval/` | LongMemEval_S (500 Qs, 40 sessions, ~115K tokens) | memory/014 temporal, memory/016 abstention |
| `benchmarks/memory/locomo/` | LoCoMo (10 convos × 300 turns × 35 sessions) + MC10 variant | memory/014 temporal, memory/015 multi-session |
| `benchmarks/memory/memoryagentbench/` | MemoryAgentBench (FactConsolidation + EventQA + LRU subsets) | memory/015 multi-session, memory/017 conflict |
| `benchmarks/memory/memoryarena/` | MemoryArena (interdependent multi-session agentic tasks) | memory/015 multi-session (agentic gap test) |
| `benchmarks/memory/tofu/` | TOFU (200 fictitious profiles, forget/retain splits) | memory/018 selective forgetting |
| `benchmarks/memory/ultradomain/` | UltraDomain (4 domains, 600K–5M tokens from 428 textbooks) | memory/021 context saturation |
| `benchmarks/memory/graphrag-bench/` | GraphRAG-Bench (16 disciplines, multi-format Qs) | memory/023 multi-agent isolation |
| `benchmarks/search/hotpotqa/` | HotPotQA (113K multi-hop Q&A pairs with supporting facts) | search/002 semantic, llm/003 RAG |
| `benchmarks/search/beir/` | BEIR subsets (NFCorpus, SciFact, FiQA — 3 domains) | search/002 semantic search quality |
| `benchmarks/search/multihop-rag/` | MultiHop-RAG (multi-hop queries over news with evidence chains) | llm/003 RAG pipeline |
| `benchmarks/search/musique/` | MuSiQue (compositional 2–4 hop QA with unanswerable Qs) | llm/003 RAG pipeline |
| `benchmarks/search/codesearchnet/` | CodeSearchNet (2M comment/code pairs, 6 languages) | search/007 code search |

---

## 3. Test Groups

### Run-Type Groups

| Group | Description | Runtime | When to Run |
|-------|-------------|---------|-------------|
| `quick` | Smoke test — core happy paths | < 2 min | Every commit, pre-push |
| `auto` | Full regression — all non-dangerous tests | ~15 min | CI, nightly |
| `stress` | Concurrency, large data, resource limits | ~30 min | Before release |
| `dangerous` | May crash the server or corrupt data | varies | Manual only |
| `perf` | Performance benchmarks with baselines | ~10 min | Before release |
| `federation` | Requires 2+ connected nodes | ~20 min | Federation PRs |
| `portability` | Export/import, migration | ~10 min | Before release |
| `contract` | SDK ↔ API contract verification (Pact) | ~3 min | Every PR |
| `chaos` | Fault injection, partition, corruption | ~30 min | Before release |
| `security` | OWASP API Top 10, injection, authz bypass | ~10 min | Every PR + nightly |
| `property` | Property-based / fuzz testing (Hypothesis) | ~15 min | Nightly |
| `cli` | CLI command E2E via subprocess | ~5 min | Every PR |
| `upgrade` | Version upgrade + rollback verification | ~20 min | Before release |
| `slo` | Performance SLO compliance (p50/p95/p99) | ~10 min | Before release |

### Feature Groups

| Group | Scope |
|-------|-------|
| `kernel` | Core FS: read, write, delete, mkdir, glob, grep, CAS |
| `zone` | Multi-tenancy, zone isolation, zone lifecycle |
| `rebac` | Permissions: grant, check, revoke, groups, inheritance |
| `memory` | Memory store, query, consolidation, trajectories |
| `search` | Full-text, semantic, mobile search |
| `pay` | Credits, X402, ledger, spend limits |
| `llm` | Completions, streaming, RAG, token counting |
| `mcp` | MCP tool registry, execution, server mode |
| `sandbox` | Sandbox create, execute, limits, cleanup |
| `snapshot` | Point-in-time capture, restore, zero-copy |
| `skills` | Register, execute, version, export |
| `governance` | Anomaly detection, fraud, suspension |
| `reputation` | Scoring, leaderboard, Sybil resistance |
| `delegation` | Delegation chains, TTL, scoped delegation |
| `workflow` | Workflow definition, execution, branching |
| `ipc` | Inter-process messaging, named pipes |

| `watch` | File event subscriptions |
| `cache` | Cache stats, invalidation, eviction, write buffer |
| `versioning` | Version history, time travel, diff, restore |
| `upload` | Chunked TUS upload, resume, concurrent |
| `auth` | API key, OAuth, sessions, rate limiting |
| `mount` | Mount/unmount, permissions, auto-provision |
| `namespace` | Namespace CRUD, routing, quota |
| `agent` | Agent registry, heartbeat, capabilities, lifecycle |
| `scheduler` | Task scheduling, cron, retry, priority |
| `eventlog` | Event emission, filtering, replay |
| `sync` | Sync jobs, conflict detection/resolution |
| `locks` | Distributed locks, contention, deadlock detection |
| `audit` | Operation history, secrets audit, tamper detection |
| `a2a` | Agent-to-agent protocol: task creation, artifact delivery |
| `discovery` | Tool search (BM25), MCP server enumeration, dynamic loading |
| `manifest` | Context manifest: source resolution, template vars |
| `playbook` | Playbook CRUD, usage tracking |
| `trajectory` | Agent execution trace logging, step recording |
| `feedback` | User feedback, scoring, relearning trigger |
| `conflict` | Sync conflict listing, resolution |
| `batch` | Batch operations (multi-file reads/writes) |
| `async` | Async file operations (non-blocking I/O) |
| `graph` | Knowledge graph: entity, neighbors, subgraph queries |
| `stream` | File streaming, event streaming (SSE) |
| `credential` | Agent credentials: issue, verify, delegate |
| `storage` | Backend drivers, CAS, caching wrappers |

---

## 4. Test Inventory

Test IDs follow `nxfs/{feature}/{NNN}` (e.g., `nxfs/kernel/001`).

### 4.1 — Kernel

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| kernel/001 | Write + read roundtrip | quick,auto,kernel | Content matches |
| kernel/002 | Overwrite changes etag | quick,auto,kernel | New etag, new content |
| kernel/003 | Delete file | quick,auto,kernel | Subsequent read → 404 |
| kernel/004 | mkdir + ls | quick,auto,kernel | Dir listed correctly |
| kernel/005 | mkdir -p (nested) | auto,kernel | All intermediates created |
| kernel/006 | rmdir (empty) | auto,kernel | Dir removed |
| kernel/007 | rm -r (recursive) | auto,kernel | All children deleted |
| kernel/008 | mv (rename) | auto,kernel | Old path gone, new path exists |
| kernel/009 | cp (copy) | auto,kernel | Independent copy, same content |
| kernel/010 | tree view | auto,kernel | Correct hierarchy |
| kernel/011 | glob `**/*.py` | quick,auto,kernel | Correct matches |
| kernel/012 | glob with exclusion | auto,kernel | Excludes correctly |
| kernel/013 | grep content | quick,auto,kernel | Returns matching lines + paths |
| kernel/014 | grep regex | auto,kernel | Regex works |
| kernel/015 | find-duplicates (CAS) | auto,kernel | Files with same hash identified |
| kernel/016 | File info (stat) | auto,kernel | Size, etag, timestamps correct |
| kernel/017 | Custom metadata | auto,kernel | Key-value metadata roundtrips |
| kernel/018 | Binary file roundtrip | auto,kernel | SHA-256 matches |
| kernel/019 | Empty file | auto,kernel | Zero-byte created and readable |
| kernel/020 | Unicode filename | auto,kernel | UTF-8 path preserved |
| kernel/021 | Special chars in path | auto,kernel | Spaces, ampersands, equals |
| kernel/022 | CAS dedup verification | auto,kernel | Same content → one blob |
| kernel/023 | Etag stable on identical rewrite | auto,kernel | Same etag returned |
| kernel/024 | Path traversal blocked | auto,kernel,dangerous | `/../../../etc/passwd` → error |
| kernel/025 | Non-existent read → 404 | quick,auto,kernel | Clear error |
| kernel/026 | Write to missing parent → error | auto,kernel | Parent dir required |
| kernel/027 | Max path length | auto,kernel | 4096 chars handled |
| kernel/028 | Edit: exact match search/replace | quick,auto,kernel | Old string replaced, diff returned |
| kernel/029 | Edit: whitespace-normalized match | auto,kernel | Leading/trailing whitespace ignored, match succeeds |
| kernel/030 | Edit: fuzzy match (Levenshtein) | auto,kernel | Slightly different old_str matches at threshold 0.85 |
| kernel/031 | Edit: fuzzy threshold rejection | auto,kernel | Below-threshold mismatch → error, file unchanged |
| kernel/032 | Edit: if_match concurrency control | auto,kernel | Stale etag → ConflictError, file unchanged |
| kernel/033 | Edit: preview mode (dry-run) | auto,kernel | preview=True returns diff but does not write |
| kernel/034 | Edit: multi-edit batch | auto,kernel | Multiple (old,new) edits applied in sequence |
| kernel/035 | Edit: hint_line targeting | auto,kernel | Edit applied at hinted line, not other occurrences |
| kernel/036 | Edit: allow_multiple replaces all | auto,kernel | All occurrences replaced when allow_multiple=true |
| kernel/037 | Edit: non-existent file → 404 | auto,kernel | NexusFileNotFoundError returned |
| kernel/038 | Edit: permission enforcement | auto,kernel,rebac | Edit without write permission → 403 |
| kernel/039 | Edit: etag updated after edit | auto,kernel | New etag differs from pre-edit etag |
| kernel/040 | Edit: version incremented | auto,kernel,versioning | Version number increments after edit |
| kernel/050 | Large file (100MB) | stress,kernel | Completes, checksum OK |
| kernel/051 | Concurrent writes (10 threads) | stress,kernel | No corruption |
| kernel/052 | 10K files flat directory ls | stress,perf,kernel | benchmarks/performance data |
| kernel/053 | 50K files nested glob | stress,perf,kernel | benchmarks/performance data |
| kernel/054 | Grep across 10K files | stress,perf,kernel | benchmarks/performance data |

### 4.2 — Zone Isolation

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| zone/001 | Write in zone-a, read from zone-b → 404 | quick,auto,zone | Isolation enforced |
| zone/002 | Zone-scoped listing | auto,zone | Only zone's files |
| zone/003 | Cross-zone write blocked | auto,zone | Permission error |
| zone/004 | Zone creation | auto,zone | New zone operational |
| zone/005 | Zone deletion + cleanup | auto,zone | All data purged |
| zone/006 | Zone-scoped glob | auto,zone | Results from target zone only |
| zone/007 | Zone listing API | auto,zone | Admin sees all zones, returns valid list |
| zone/008 | Get zone details | auto,zone | Zone info returned with correct fields |
| zone/009 | Zone-scoped search isolation | auto,zone,search | Search results only from target zone |
| zone/010 | Zone-scoped grep isolation | auto,zone,kernel | Grep results only from target zone |
| zone/011 | Deleted zone read fails cleanly | auto,zone | 4xx error, not 500 |
| zone/012 | Invalid zone ID rejected | auto,zone,security | Injection/traversal patterns rejected |

### 4.3 — VFS Hooks (Metadata-Observable)

Tests verify hook pipeline via production observable effects (`get_metadata()`)
rather than injected test hooks. No `NEXUS_TEST_HOOKS` flag required.

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| hooks/001 | Write populates metadata | auto,hooks | `get_metadata()` returns size/etag/hash |
| hooks/002 | Metadata has timestamps and size | auto,hooks | `created_at`/`modified_at` present, size > 0 |
| hooks/003 | Follower write has metadata | auto,hooks,federation | `get_metadata()` on follower returns valid data |
| hooks/004 | Overwrite updates metadata | auto,hooks | Size reflects latest write after overwrite |
| hooks/005 | Concurrent writes all have metadata | auto,hooks,stress | N writes → N valid `get_metadata()` results |
| hooks/006 | Zone write has metadata | auto,hooks,zone | `get_metadata()` in scratch zone returns valid data |
| hooks/007 | Distinct metadata per path | auto,hooks | N paths → N unique metadata entries |
| hooks/008 | Large write metadata | auto,hooks,stress | 1 MB write → `get_metadata()` size ≥ 1 MB |

### 4.4 — Authentication & Sessions

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| auth/001 | Valid API key → authenticated | quick,auto,auth | Returns user info |
| auth/002 | Invalid API key → 401 | quick,auto,auth | Unauthorized |
| auth/003 | Rate limiting → 429 | auto,auth | Too Many Requests |
| auth/004 | Session lifecycle | auto,auth | Create, use, expire |
| auth/005 | Whoami endpoint | quick,auto,auth | Returns current identity |

### 4.5 — Services

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| namespace/001 | Create namespace | auto,namespace | Isolation active |
| namespace/002 | List namespaces | auto,namespace | Returns all |
| namespace/003 | Switch namespace | auto,namespace | Context switches |
| namespace/004 | Namespace quota enforcement | auto,namespace | Write rejected at limit |
| namespace/005 | Namespace delete + cleanup | auto,namespace | All data removed |
| agent/001 | Register agent | auto,agent | Agent in registry |
| agent/002 | Agent heartbeat | auto,agent | Status updated |
| agent/003 | Agent capability query | auto,agent | Filtered by capability |
| agent/004 | Agent lifecycle FSM | auto,agent | Correct state transitions |
| scheduler/001 | Schedule task | auto,scheduler | Task created |
| scheduler/002 | Task retry with backoff | auto,scheduler | Retried correctly |
| scheduler/003 | Task priority ordering | auto,scheduler | High first |
| scheduler/004 | Task cancellation | auto,scheduler | Graceful stop |
| eventlog/001 | Write emits event | auto,eventlog | Event in log |
| eventlog/002 | Event filtering by type | auto,eventlog | Correct results |
| eventlog/003 | Event replay | auto,eventlog | In-order replay |
| sync/001 | Create sync job | auto,sync | Job runs |
| sync/002 | Conflict detection | auto,sync | Conflicts flagged |
| sync/003 | Conflict resolution | auto,sync | Resolved correctly |
| mount/001 | Mount + read through | auto,mount | Transparent access |
| mount/002 | Unmount | auto,mount | Mount removed |
| mount/003 | Read-only mount | auto,mount | Writes rejected |
| upload/001 | Chunked upload (TUS) | auto,upload | File assembled |
| upload/002 | Resume interrupted upload | auto,upload | Completes from checkpoint |

### 4.6 — ReBAC (Permissions)

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| rebac/001 | Grant permission | quick,auto,rebac | Tuple created |
| rebac/002 | Check permission → true | quick,auto,rebac | Access confirmed |
| rebac/003 | Revoke → check → false | auto,rebac | Access removed |
| rebac/004 | Group inheritance | auto,rebac | Member inherits group perms |
| rebac/005 | Nested group closure | auto,rebac | Transitive inheritance |
| rebac/006 | Permission on write enforcement | auto,rebac | 403 without permission |
| rebac/007 | Namespace-scoped permissions | auto,rebac | Scoped correctly |
| rebac/008 | Permission changelog audit | auto,rebac | All changes logged |
| rebac/009 | Parent folder inheritance | auto,rebac | File inherits read from parent folder |
| rebac/010 | Tiger Cache write-through + invalidation | auto,rebac | Cache populates on check, invalidates on revoke |
| rebac/011 | Tiger Cache stats endpoint | auto,rebac | /api/v2/cache/stats returns tiger_cache metrics |
| rebac/012 | Read enforcement | auto,rebac | 403 on read without permission |
| rebac/013 | Delete enforcement | auto,rebac | 403 on delete without permission |
| rebac/014 | Owner role (read + write + execute) | auto,rebac | direct_owner grants all permissions |
| rebac/015 | Expand — find all subjects | auto,rebac | rebac_expand returns granted subjects |
| rebac/016 | Explain — trace resolution path | auto,rebac | rebac_explain returns successful_path |
| rebac/017 | Batch check | auto,rebac | rebac_check_batch returns multiple results |
| rebac/018 | List objects by relation | auto,rebac | rebac_list_objects returns objects for subject |
| rebac/019 | Wildcard public access | auto,rebac | ("*","*") grant gives all users access |
| rebac/020 | Cross-zone sharing relations | auto,rebac | shared-viewer/editor/owner grant cross-zone |
| rebac/021 | Permission escalation prevention | auto,rebac,security | Viewer cannot grant editor |
| rebac/022 | Glob permission filtering | auto,rebac | Glob results filtered by ReBAC |
| rebac/023 | Conditional permissions | auto,rebac | Tuple with conditions field enforced |
| rebac/024 | Admin bypass | auto,rebac,security | Admin key bypasses ReBAC checks |
| rebac/025 | Concurrent permission mutations | stress,rebac | No corruption under concurrent grants/revokes |
| rebac/026 | Consistency mode minimize_latency | auto,rebac | Cached result via EVENTUAL mode; default mode works |
| rebac/027 | Cross-zone shared-editor grants write | auto,rebac | shared-editor grants read+write, not execute |
| rebac/028 | Cross-zone shared-owner grants all | auto,rebac | shared-owner grants read+write+execute; revoke removes all |
| rebac/029 | Execute enforcement | auto,rebac | Viewer/editor lack execute; only owner has execute |
| rebac/030 | Rename preserves permissions | auto,rebac | Grant on old path → rename → check new path |
| rebac/031 | Directory grant propagation | auto,rebac | Parent folder grant → new child file inherits read |
| rebac/032 | Dragonfly L2 cache stats | auto,rebac | L2 enabled + stats change after ops; invalidation tracked |
| rebac/033 | Search results filtered by ReBAC | auto,rebac | Search returns only files user can read |
| rebac/034 | Batch check mixed perms and zones | auto,rebac | read/write/execute + cross-zone in one batch |
| rebac/035 | ABAC condition enforcement | auto,rebac | Unsatisfied condition denies access; time_window enforced |

### 4.7 — Memory

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| memory/001 | Store memory | quick,auto,memory | Stored successfully |
| memory/002 | Query memory | quick,auto,memory | Returns relevant result |
| memory/003 | Semantic search (HERB data) | auto,memory | Ranked by similarity |
| memory/004 | ACE consolidation | auto,memory | 50 memories → coherent summary |
| memory/005 | Memory deletion | auto,memory | Removed from store + index |
| memory/006 | Zone-scoped memory | auto,memory,zone | Not visible cross-zone |
| memory/007 | Entity extraction → knowledge graph | auto,memory | Entities indexed |
| memory/008 | 10K memories query perf | stress,perf,memory | < 200ms p95 |
| memory/009 | Invalidate + revalidate memory | auto,memory | State transitions correct |
| memory/010 | Memory version history | auto,memory | Versions listed, diff works |
| memory/011 | Memory lineage (append-only) | auto,memory | Lineage chain intact |
| memory/012 | Coreference resolution | auto,memory | "it"/"the project" resolved |
| memory/013 | Relationship extraction | auto,memory | Relations indexed in graph |
| memory/014 | Temporal reasoning | auto,memory | Time-scoped query returns correct epoch result |
| memory/015 | Multi-session reasoning | auto,memory | Answer synthesized from 3+ sessions correct |
| memory/016 | Abstention (hallucination guard) | auto,memory | Returns "unknown" when answer not in memory |
| memory/017 | Knowledge conflict resolution | auto,memory | Contradictory facts → latest wins or conflict surfaced |
| memory/018 | Selective forgetting (GDPR) | auto,memory,security | Entity purged from store + index + graph |
| memory/019 | Silent failure detection | auto,memory | Corrupted memory flagged, not served silently |
| memory/020 | Write + consolidation latency | stress,perf,memory | Write p95 < 50ms, consolidation < 5s |
| memory/021 | Context saturation baseline | auto,perf,memory | Memory-assisted accuracy ≥ full-context baseline |
| memory/022 | Procedural memory (feedback learning) | auto,memory | Response quality improves after accumulated feedback |
| memory/023 | Multi-agent memory isolation | auto,memory,zone | Agent A private memories invisible to Agent B |

#### Benchmark Datasets & Sources for memory/014–023

Each new test maps to established benchmarks and open-source datasets. Use these
as reference data, evaluation methodology, and ground-truth baselines.

Benchmarks are ranked by priority within each test ID:
- **P0** — Must Have: provides datasets + ground truth, directly runnable
- **P1** — Should Have: provides supplementary datasets or validated methodology
- **P2** — Nice to Have: reference methodology, no direct dataset (must build or adapt)

**Competitive baselines:** When evaluating, compare against MemGPT/[Letta](https://www.letta.com/)
(OS-inspired hierarchical memory, 74% on LoCoMo with simple file storage),
[Mem0](https://mem0.ai/) (66.9% LoCoMo J-score), and [Zep](https://www.getzep.com/)
(75.1% corrected LoCoMo J-score). These are *systems to benchmark against*, not
benchmarks themselves.

**LoCoMo reliability caveat:** Multiple groups report different LoCoMo results
depending on experimental setup (Mem0 vs. Zep dispute shows 24% swing). Use
LoCoMo for directional comparison but do not treat absolute scores as definitive.
Cross-validate with LongMemEval and MemoryAgentBench.

| Priority | Test ID | Benchmark / Dataset | Source | How to Obtain | What It Provides |
|----------|---------|-------------------|--------|---------------|------------------|
| P0 | memory/014 | **LongMemEval** (ICLR 2025) | [GitHub](https://github.com/xiaowu0162/LongMemEval) / [HuggingFace](https://huggingface.co/datasets) | `git clone https://github.com/xiaowu0162/LongMemEval` → `data/` folder; also on HuggingFace | 500 curated questions across 40–500 sessions with timestamps. Temporal reasoning subset tests "when did X happen?" and time-scoped retrieval. Use `LongMemEval_S` (~115K tokens) for CI, `LongMemEval_M` (~500 sessions) for stress. Gold standard for memory eval. |
| P0 | memory/014 | **LoCoMo** (ACL 2024) | [GitHub](https://github.com/snap-research/locomo) / [HuggingFace](https://huggingface.co/datasets/Percena/locomo-mc10) | `git clone https://github.com/snap-research/locomo` | 10 conversations × 300 turns × 35 sessions. Temporal question subset with ground-truth. LoCoMo-MC10 variant (1,986 items, 10-option MC) on HuggingFace for automated eval. Most widely used memory benchmark (despite reliability caveats above). |
| P0 | memory/015 | **LoCoMo multi-hop subset** | [GitHub](https://github.com/snap-research/locomo) | Same as above, filter `question_type=multi-hop` | Multi-hop questions requiring synthesis across sessions. F1 + BLEU-1 + LLM-as-judge metrics. |
| P0 | memory/015 | **MemoryAgentBench** (ICLR 2026) | [GitHub](https://github.com/HUST-AI-HYZ/MemoryAgentBench) / [HuggingFace](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench) | `pip install` + auto-download on first run | "Long-Range Understanding" competency subset. Multi-turn format simulating incremental info across sessions. Evaluates 4 core competencies: retrieval, test-time learning, long-range understanding, conflict resolution. State-of-the-art benchmark. |
| P1 | memory/015 | **MemoryArena** (Feb 2026) | [GitHub](https://memoryarena.github.io/) / [Paper](https://arxiv.org/abs/2602.16313) | Reference methodology; datasets available | First benchmark with causally interdependent multi-session agentic tasks. Reveals that agents with near-saturated LoCoMo performance still fail on interdependent tasks. Covers web navigation, preference-constrained planning, progressive search, and sequential reasoning. |
| P0 | memory/016 | **LongMemEval abstention subset** | [GitHub](https://github.com/xiaowu0162/LongMemEval) | Filter questions where `answer_type=unanswerable` | Tests whether system returns "I don't know" vs. hallucinating. 30% of questions designed to have no answer in memory. |
| P0 | memory/016 | **HERB unanswerable Q&A** (local) | `benchmarks/herb/qa/` | Already in repo — 699 unanswerable questions with ground truth | Cross-reference with memory store: after populating memory with HERB context, query with unanswerable Qs and verify abstention. |
| P0 | memory/017 | **MemoryAgentBench FactConsolidation** | [HuggingFace](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench) | Auto-download; `Single-Hop FactConsolidation` + `Multi-Hop FactConsolidation` subsets | Contradictory facts injected at different turns. Measures whether system resolves to latest fact or surfaces conflict. Also see Zep/Graphiti bi-temporal model for methodology. |
| P1 | memory/017 | **Zep/Graphiti temporal KG** methodology | [Paper](https://arxiv.org/abs/2501.13956) / [Graphiti](https://github.com/getzep/graphiti) | Reference architecture; adapt bi-temporal validity-interval approach | Bi-temporal model tracks event-time vs. ingestion-time. Every edge has validity intervals — use as design reference for conflict detection. |
| P0 | memory/018 | **TOFU** (Task of Fictitious Unlearning) | [GitHub](https://github.com/locuslab/open-unlearning) / [HuggingFace](https://huggingface.co/datasets/locuslab/TOFU) | `datasets.load_dataset("locuslab/TOFU")` | 200 fictitious author profiles. Forget subset + retain subset. Verify that after "forget entity X", queries about X return nothing while other memories intact. Note: original `locuslab/tofu` repo superseded by `locuslab/open-unlearning` (NeurIPS D&B 2025). Also see [R-TOFU](https://aclanthology.org/2025.emnlp-main.265.pdf) (EMNLP 2025) for reasoning model unlearning. |
| P2 | memory/018 | **Machine Unlearning methodology** | [Survey](https://github.com/tamlhp/awesome-machine-unlearning) | Reference checklist | Evaluation criteria: (1) completeness — forgotten data truly gone, (2) no side effects — retained data unaffected, (3) verifiability — audit trail of deletion. |
| P1 | memory/019 | **Cognee evals methodology** | [Blog](https://www.cognee.ai/blog/deep-dives/ai-memory-evals-0825) | Reference methodology | Cognee benchmarked Mem0, LightRAG, Graphiti with HotPotQA (24 multi-hop Qs × 45 runs). Use EM + F1 + DeepEval + correctness metrics to detect silent degradation. |
| P2 | memory/019 | **Custom corruption harness** | Design from [Anatomy of Agentic Memory](https://arxiv.org/html/2602.19320) | Build in-repo | Inject bit-flipped / truncated / stale memories. Verify system detects inconsistency via checksums or semantic validation rather than serving corrupted data silently. |
| P1 | memory/020 | **Mem0 benchmark methodology** | [Paper](https://arxiv.org/abs/2504.19413) / [Blog](https://mem0.ai/research) | Reference SLO methodology | Mem0 measures Token Consumption + Latency per query on LoCoMo. Use same methodology: measure write p95, consolidation wall-time, token cost per memory op. Compare against Letta (74% LoCoMo with file storage) and Zep (75.1% corrected J-score). |
| P2 | memory/020 | **Letta Leaderboard** | [Blog](https://www.letta.com/blog/letta-leaderboard) | Reference | Letta tracks latency + token usage across models. Letta Filesystem achieves 74% on LoCoMo with simple file-based storage, setting a strong "naive" baseline. Use as baseline comparison for our write + consolidation SLOs. |
| P1 | memory/021 | **Context saturation gap (Δ)** methodology | [Anatomy of Agentic Memory](https://arxiv.org/html/2602.19320) | Build in-repo; reference paper | Metric Δ = accuracy(memory-assisted) − accuracy(full-context-stuffing). If Δ ≤ 0, memory system adds no value. Use HERB Q&A ground truth to measure both approaches. |
| P1 | memory/021 | **LightRAG / UltraDomain** | [GitHub](https://github.com/HKUDS/LightRAG) / [HuggingFace](https://huggingface.co/datasets) | `git clone https://github.com/HKUDS/LightRAG` → `datasets/` | 4 domains (Agriculture, CS, Legal, Mix) from 428 textbooks, 600K–5M tokens. Compare chunk-based vs. graph-based vs. full-context retrieval accuracy. |
| P1 | memory/022 | **MemoryBench procedural subset** | [Paper](https://arxiv.org/html/2510.17281v1) | Reference methodology; 20K cases across 3 domains | Procedural memory evaluation: inject explicit feedback (like/dislike) and implicit feedback (copy, session-close). Verify subsequent responses improve on same task type. |
| P2 | memory/022 | **Letta dynamic memory eval** | [Blog](https://www.letta.com/blog/benchmarking-ai-agent-memory) | Reference | Tests whether agent learns *when* to use memory tools, not just retrieval accuracy. Letta (formerly MemGPT) pioneered OS-inspired hierarchical memory with virtual context paging. Adapt for feedback-loop testing. |
| P0 | memory/023 | **GraphRAG-Bench** (ICLR 2026) | [GitHub](https://github.com/GraphRAG-Bench/GraphRAG-Benchmark) / [HuggingFace](https://huggingface.co/datasets/GraphRAG-Bench/GraphRAG-Bench) | `datasets.load_dataset("GraphRAG-Bench/GraphRAG-Bench")` | 16 disciplines, multi-format questions. Accepted at ICLR 2026. Use to validate that agent-scoped graph partitions return correct answers only from that agent's subgraph. |
| P0 | memory/023 | **Zone-scoped HERB partitions** (local) | `benchmarks/herb/enterprise-context/` | Already in repo | Partition HERB data by department (eng/sales). Store as separate agent memories. Verify cross-agent isolation while shared memories remain accessible. |

### 4.8 — Search

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| search/001 | Full-text search | quick,auto,search | Matching files returned |
| search/002 | Semantic search | auto,search | Meaning-based results |
| search/003 | Search respects ReBAC | auto,search,rebac | Only accessible files |
| search/004 | Index on write | auto,search | Immediately searchable |
| search/005 | HERB Q&A accuracy | auto,perf,search | Score against ground truth |
| search/006 | Query expansion | auto,search | Expanded query finds more |
| search/007 | Code search (Zoekt) | auto,search | Trigram index works |
| search/008 | Search daemon warmup | auto,search | Zero cold-start |
| search/009 | Embedding cache dedup | auto,search,perf | 90%+ cache hit on repeated content |
| search/010 | Search accuracy: NDCG@10 on HERB | auto,perf,search | NDCG@10 ≥ 0.6 on HERB answerable Qs |
| search/011 | Search accuracy: MRR on HERB | auto,perf,search | MRR ≥ 0.5 on HERB answerable Qs |
| search/012 | Search accuracy: EM + F1 on HERB | auto,perf,search | EM ≥ 0.3, F1 ≥ 0.5 vs ground truth |
| search/013 | Semantic search: BEIR subset (SciFact) | auto,perf,search | NDCG@10 ≥ 0.5 on SciFact |
| search/014 | Semantic search: BEIR subset (NFCorpus) | auto,perf,search | NDCG@10 ≥ 0.4 on NFCorpus |
| search/015 | Multi-hop retrieval (HotPotQA) | auto,search,llm | Supporting facts retrieved for 2-hop Qs |
| search/016 | Multi-hop retrieval (MuSiQue) | auto,search,llm | 2–4 hop compositional retrieval succeeds |
| search/017 | Query expansion recall lift | auto,search | Expanded query recall > raw query recall |
| search/018 | RAG faithfulness (RAGAS) | auto,search,llm | Faithfulness ≥ 0.7 (no hallucination) |
| search/019 | RAG answer relevancy (RAGAS) | auto,search,llm | Answer relevancy ≥ 0.7 |
| search/020 | RAG context precision (RAGAS) | auto,search,llm | Context precision ≥ 0.6 |

#### Benchmark Datasets & Sources for search + RAG (search/002–009, llm/003)

Benchmarks for evaluating search accuracy, retrieval quality, and RAG pipeline
correctness. Use [RAGAS](https://docs.ragas.io/) framework metrics (faithfulness,
context precision/recall, answer relevancy) alongside traditional EM/F1/BLEU/ROUGE.

**Evaluation methodology:** Use RAGAS for reference-free RAG evaluation (no ground
truth needed for faithfulness/relevancy). Use EM + F1 for ground-truth benchmarks.
Use NDCG@k + MRR for retrieval ranking quality. Use LLM-as-judge for open-ended
answer quality.

| Priority | Test ID | Benchmark / Dataset | Source | How to Obtain | What It Provides |
|----------|---------|-------------------|--------|---------------|------------------|
| P0 | search/005, llm/003 | **HERB Q&A** (local) | `benchmarks/herb/qa/` | Already in repo — 815 answerable + 699 unanswerable questions | Project-specific ground truth. Primary eval dataset for search accuracy and RAG pipeline. Score with EM + F1 against ground truth. |
| P0 | search/002, llm/003 | **HotPotQA** (EMNLP 2018) | [Website](https://hotpotqa.github.io/) / [HuggingFace](https://huggingface.co/datasets/hotpotqa/hotpot_qa) | `datasets.load_dataset("hotpotqa/hotpot_qa")` | 113K multi-hop Q&A pairs with sentence-level supporting facts. De facto standard for RAG and multi-hop retrieval. Use distractor setting (10 paragraphs, 2 relevant) to test retrieval precision. |
| P0 | search/002 | **BEIR** (NeurIPS 2021) | [GitHub](https://github.com/beir-cellar/beir) / [HuggingFace](https://huggingface.co/BeIR) | `pip install beir` | 15+ heterogeneous IR datasets (NFCorpus, SciFact, FiQA, etc.). Standard benchmark for semantic search / embedding quality. Use NDCG@10 metric. Pick 3–4 domain-relevant subsets (e.g., SciFact, FiQA, NFCorpus) for CI. |
| P1 | llm/003 | **MultiHop-RAG** (COLM 2024) | [GitHub](https://github.com/yixuantt/MultiHop-RAG) | `git clone https://github.com/yixuantt/MultiHop-RAG` | Multi-hop queries over news articles with ground-truth evidence chains. Tests retrieval chaining and evidence linking — critical for RAG pipelines that must reason across multiple documents. |
| P1 | llm/003 | **MuSiQue** | [GitHub](https://github.com/stonybrooknlp/musique) / [HuggingFace](https://huggingface.co/datasets/drt/musique) | `datasets.load_dataset("drt/musique")` | Multi-hop QA requiring compositional reasoning (2–4 hops). Harder than HotPotQA — includes unanswerable questions and decomposition annotations. Use alongside HotPotQA for multi-hop coverage. |
| P1 | search/006 | **Natural Questions** (Google) | [HuggingFace](https://huggingface.co/datasets/google-research-datasets/natural_questions) | `datasets.load_dataset("google-research-datasets/natural_questions")` | 300K+ real Google search queries with Wikipedia answers. Single-hop QA baseline. Use to validate query expansion improves recall over raw queries. |
| P1 | llm/003 | **RAGAS framework** | [GitHub](https://github.com/explodinggradients/ragas) / [Docs](https://docs.ragas.io/) | `pip install ragas` | Reference-free RAG evaluation framework. Metrics: faithfulness (no hallucination), answer relevancy, context precision, context recall. Use for automated CI evaluation without ground truth. |
| P1 | search/002 | **MTEB Retrieval** (ICLR 2025) | [GitHub](https://github.com/embeddings-benchmark/mteb) / [Leaderboard](https://huggingface.co/spaces/mteb/leaderboard) | `pip install mteb` | Massive Text Embedding Benchmark. Use retrieval subset to validate embedding model selection. Compare our embedding model against MTEB leaderboard baselines. |
| P2 | search/007 | **CodeSearchNet** | [GitHub](https://github.com/github/CodeSearchNet) | `git clone https://github.com/github/CodeSearchNet` | 2M (comment, code) pairs across 6 languages (Python, JS, Ruby, Go, Java, PHP). Human relevance judgements for evaluation. Use NDCG metric. Benchmark concluded but dataset still valuable for code search quality testing. |
| P0 | search/010-012 | **HERB Q&A** (local) | `benchmarks/herb/qa/` | Already in repo — 815 answerable + 699 unanswerable questions | Compute NDCG@10, MRR, EM, F1 against ground truth. Primary accuracy benchmark. |
| P0 | search/013-014 | **BEIR subsets** (NeurIPS 2021) | [HuggingFace](https://huggingface.co/BeIR) | `pip install beir` | SciFact + NFCorpus subsets. Evaluate with NDCG@10. Standard IR benchmark. |
| P0 | search/015 | **HotPotQA** (EMNLP 2018) | [HuggingFace](https://huggingface.co/datasets/hotpotqa/hotpot_qa) | `datasets.load_dataset("hotpotqa/hotpot_qa")` | 113K multi-hop Q&A. Use distractor setting (10 paragraphs, 2 relevant) for retrieval precision. |
| P1 | search/016 | **MuSiQue** | [HuggingFace](https://huggingface.co/datasets/drt/musique) | `datasets.load_dataset("drt/musique")` | 2–4 hop compositional QA with unanswerable questions. Harder than HotPotQA. |
| P0 | search/018-020 | **RAGAS framework** | [Docs](https://docs.ragas.io/) | `pip install ragas` | Reference-free RAG evaluation: faithfulness, answer relevancy, context precision. No ground truth needed. |

### 4.9 — Pay

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| pay/001 | Credit balance query | auto,pay | Returns balance |
| pay/002 | Deposit + withdraw | auto,pay | Ledger entries correct |
| pay/003 | Spend limit enforcement | auto,pay | Transaction rejected |
| pay/004 | Concurrent transactions (100) | stress,pay | No double-spend |
| pay/005 | Ledger audit trail | auto,pay | Complete history |
| pay/006 | Credit reservation (two-phase) | auto,pay | Reserve → commit or release |
| pay/007 | Batch transfer (atomic) | auto,pay | All-or-nothing |
| pay/008 | Spending policy CRUD | auto,pay | Policy enforced |
| pay/009 | Spending approval workflow | auto,pay | Approve/reject flow |
| pay/010 | Can-afford check | auto,pay | Returns boolean |

### 4.10 — LLM

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| llm/001 | LLM completion | auto,llm | Returns response |
| llm/002 | Token counting | auto,llm | Accurate count |
| llm/003 | RAG pipeline (HERB data) | auto,llm | Context included |
| llm/004 | LLM caching | auto,llm,perf | Same prompt → cache hit |
| llm/005 | Streaming response | auto,llm | SSE chunks received |
| llm/006 | Multi-provider fallback | auto,llm | Provider B used when A fails |
| llm/007 | Cost tracking | auto,llm | Token costs recorded |

### 4.11 — MCP

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| mcp/001 | List MCP tools | auto,mcp | Registry returned |
| mcp/002 | Execute MCP tool | auto,mcp | Result returned |
| mcp/003 | MCP server mode | auto,mcp | External LLM can connect |

### 4.12 — Sandbox

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| sandbox/001 | Create + execute | auto,sandbox | Output returned, system unaffected |
| sandbox/002 | Resource limits | auto,sandbox | OOM killed on exceed |
| sandbox/003 | Sandbox cleanup | auto,sandbox | Resources released |
| sandbox/004 | Smart provider routing | auto,sandbox | Cheapest provider selected |
| sandbox/005 | Scoped file access from sandbox | auto,sandbox | Only permitted paths |

### 4.13 — Snapshot

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| snapshot/001 | Create snapshot | auto,snapshot | Point-in-time captured |
| snapshot/002 | Restore snapshot | auto,snapshot | Files reverted |
| snapshot/003 | Zero-copy (CAS) | auto,snapshot | No blob duplication |
| snapshot/004 | MVCC conflict detection | auto,snapshot | Conflict detected |
| snapshot/005 | Concurrent snapshots | stress,snapshot | No interference |

### 4.14 — Skills

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| skills/001 | Register + list | auto,skills | Skill in registry |
| skills/002 | Execute skill | auto,skills | Runs successfully |
| skills/003 | Skill versioning | auto,skills | Both versions available |
| skills/004 | Skill export/import (.zip) | auto,skills | Portable package works |
| skills/005 | Skill governance (approval) | auto,skills | Requires approval |
| skills/006 | Skill dependency cycle detection | auto,skills | Cycle rejected |

### 4.15 — Governance

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| governance/001 | Anomaly detection | auto,governance | Alert generated |
| governance/002 | Agent suspension | auto,governance | Access revoked |
| governance/003 | Fraud ring detection | auto,governance | Ring identified |
| governance/004 | Constraint checker | auto,governance | Transfer blocked by constraint |
| governance/005 | Hotspot detection | auto,governance | High-cardinality alert |

### 4.16 — Reputation

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| reputation/001 | Log event + query score | auto,reputation | Score updated |
| reputation/002 | Leaderboard | auto,reputation | Ranked list |
| reputation/003 | Dispute lifecycle | auto,reputation | File → resolve |
| reputation/004 | Sybil resistance (beta scoring) | auto,reputation | Fake boosting penalized |

### 4.17 — Delegation

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| delegation/001 | Create delegation chain | auto,delegation | A → B → C |
| delegation/002 | TTL expiry | auto,delegation | Expired after TTL |
| delegation/003 | Scoped delegation | auto,delegation | Only /project/* |
| delegation/004 | Depth limit enforcement | auto,delegation | Chain too deep → rejected |
| delegation/005 | Trust score delegation | auto,delegation | Low-trust agent blocked |

### 4.18 — Workflow

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| workflow/001 | Create + execute | auto,workflow | Steps in order |
| workflow/002 | Conditional branch | auto,workflow | Correct branch taken |
| workflow/003 | LLM-driven branching | auto,workflow,llm | Correct branch by LLM |
| workflow/004 | Workflow enable/disable | auto,workflow | Disabled workflow skipped |

### 4.19 — IPC

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| ipc/001 | Send message A → B | auto,ipc | Delivered |
| ipc/002 | Named pipe | auto,ipc | Data flows |
| ipc/003 | IPC inbox polling | auto,ipc | Messages in inbox |
| ipc/004 | IPC unread count | auto,ipc | Count accurate |

### 4.20 — Watch

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| watch/001 | Subscribe + receive event | auto,watch | Event notification |
| watch/002 | Unsubscribe | auto,watch | No more notifications |

### 4.21 — Cache

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| cache/001 | Cache stats | auto,cache | Hit/miss ratios |
| cache/002 | Invalidation on write | auto,cache | Stale data not served |
| cache/003 | Priority-aware eviction | auto,cache | Low-priority evicted first |

### 4.22 — Versioning

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| versioning/001 | Version history | auto,versioning | List returned |
| versioning/002 | Read old version | auto,versioning | Correct content |
| versioning/003 | Time travel | auto,versioning | Content at timestamp |

### 4.23 — Locks

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| locks/001 | Acquire + release | auto,locks | Lock lifecycle works |
| locks/002 | Contention | auto,locks | One waits |
| locks/003 | Deadlock detection | auto,locks,dangerous | Broken automatically |

### 4.24 — Audit

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| audit/001 | Operation history | auto,audit | Ops logged |
| audit/002 | Secrets audit tamper detection | auto,audit | Tamper detected |

### 4.25 — A2A (Agent-to-Agent)

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| a2a/001 | Register agent card (/.well-known/agent.json) | auto,a2a | Card discoverable |
| a2a/002 | Create A2A task | auto,a2a | Task created with ID |
| a2a/003 | A2A task state transition | auto,a2a | working → completed |
| a2a/004 | A2A artifact delivery | auto,a2a | Artifact received by target |
| a2a/005 | A2A cross-zone task | auto,a2a,federation | Task delivered across zones |

### 4.26 — Discovery

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| discovery/001 | search_tools() returns ranked results | auto,discovery | BM25 scores descending |
| discovery/002 | list_servers() enumerates MCP servers | auto,discovery | All servers listed |
| discovery/003 | load_tools() adds to active context | auto,discovery | Tool callable after load |
| discovery/004 | get_tool_details() returns schema | auto,discovery | Input schema present |

### 4.27 — Manifest

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| manifest/001 | Resolve manifest with FileGlobSource | auto,manifest | Files matched |
| manifest/002 | Resolve manifest with MemoryQuerySource | auto,manifest | Memories returned |
| manifest/003 | Template variable substitution | auto,manifest | Variables resolved |
| manifest/004 | Parallel source execution | auto,manifest | All sources run concurrently |

### 4.28 — Playbook, Trajectory, Feedback

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| playbook/001 | CRUD lifecycle | auto,playbook | Create, read, update, delete |
| playbook/002 | Record usage | auto,playbook | Usage counter incremented |
| trajectory/001 | Start + log steps + complete | auto,trajectory | Full trace recorded |
| trajectory/002 | Query trajectories | auto,trajectory | Filtered by agent/time |
| feedback/001 | Submit feedback | auto,feedback | Feedback stored |
| feedback/002 | Score feedback | auto,feedback | Score computed |
| feedback/003 | Relearning trigger | auto,feedback | Relearn initiated |

### 4.29 — Graph

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| graph/001 | Get entity | auto,graph | Entity returned |
| graph/002 | Get entity neighbors | auto,graph | Neighbors listed |
| graph/003 | Query subgraph | auto,graph | Subgraph returned |

### 4.30 — Batch, Async, Stream

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| batch/001 | Batch read (10 files) | auto,batch | All contents returned |
| batch/002 | Batch write (10 files) | auto,batch | All written |
| async/001 | Async write + poll | auto,async | Write completes async |
| async/002 | Async batch read | auto,async | Results streamed |
| stream/001 | File streaming download | auto,stream | Content streamed, no buffering |
| stream/002 | SSE event stream | auto,stream,watch | Events received via SSE |

### 4.31 — Conflict, Credential

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| conflict/001 | List conflicts | auto,conflict,sync | Conflicts listed |
| conflict/002 | Resolve conflict | auto,conflict,sync | Conflict cleared |
| credential/001 | Issue agent credential | auto,credential | Credential created |
| credential/002 | Verify credential | auto,credential | Verification passes |
| credential/003 | Delegate credential | auto,credential | Delegation works |

### 4.32 — Dynamic Brick Management

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| brick/001 | Add memory brick at runtime | auto | Memory endpoints available |
| brick/002 | Add search brick at runtime | auto | Search endpoints available |
| brick/003 | Remove memory brick | auto | Endpoints → 404 |
| brick/004 | Graceful degradation | auto | Kernel unaffected |
| brick/005 | Brick dependency resolution | auto | Auto-resolved or error |
| brick/006 | Brick health check | auto | Status returned |
| brick/007 | Brick restart (no kernel restart) | auto | Brick restarts independently |
| brick/008 | Zero bricks → kernel still works | auto,kernel | Core FS operational |
| brick/009 | Hot-reload brick config | auto | Applied without restart |

### 4.33 — Federation

All federation tests require 2+ nodes.

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| fed/001 | Leader election (3 nodes) | auto,federation | One leader elected |
| fed/002 | Write replication | auto,federation | Read from follower succeeds |
| fed/003 | Leader failover | auto,federation | New leader within timeout |
| fed/004 | Cross-zone namespace routing | auto,federation,namespace | Routed correctly |
| fed/005 | Cross-zone agent discovery | auto,federation,agent | Agent found from other zone |
| fed/006 | Cross-zone event propagation | auto,federation,eventlog | Events flow between zones |
| fed/007 | Cross-zone sync | auto,federation,sync | Bidirectional sync |
| fed/008 | Cross-zone ReBAC | auto,federation,rebac | Permissions enforced cross-zone |
| fed/009 | Cross-zone memory query | auto,federation,memory | Configurable visibility |
| fed/010 | Cross-zone search | auto,federation,search | Results from all zones |
| fed/011 | Federated agent communication | auto,federation,agent | A→B message across zones |
| fed/012 | Cross-zone pay transfer | auto,federation,pay | Balances updated both zones |
| fed/013 | Node disconnect + rejoin | auto,federation | State caught up |
| fed/014 | Network partition heal | stress,federation | No data loss |
| fed/015 | Rolling restart | stress,federation | Zero downtime |
| fed/016 | Zone addition (hot) | auto,federation | New zone joins live |
| fed/017 | Zone removal (clean) | auto,federation | Clean departure |
| fed/018 | 3-zone concurrent stress | stress,federation | 1000 ops, no loss |
| fed/019 | Cross-zone delegation | auto,federation,delegation | Chain works cross-zone |
| fed/020 | Federated reputation | auto,federation,reputation | Global score |

### 4.34 — Data Portability

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| port/001 | Export zone → .nexus bundle | auto,portability | Bundle created |
| port/002 | Import bundle → new zone | auto,portability | Data imported |
| port/003 | Export with content (blobs) | auto,portability | Blobs in bundle |
| port/004 | Export metadata only | auto,portability | Lightweight bundle |
| port/005 | Export with ReBAC permissions | auto,portability | Permissions portable |
| port/006 | Export with memories | auto,portability | Memories portable |
| port/007 | Import conflict: skip | auto,portability | No overwrite |
| port/008 | Import conflict: overwrite | auto,portability | Updated |
| port/009 | Import conflict: fail | auto,portability | Transaction rolled back |
| port/010 | Bundle checksum verification | auto,portability | SHA-256 matches |
| port/011 | Large zone export (50K files) | stress,portability | Streaming, no OOM |
| port/012 | Live namespace migration | stress,portability,federation | Zero downtime |
| port/013 | Migration with active writes | dangerous,portability | No data loss |

### 4.35 — Contract Testing

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| contract/001 | SDK write() → API /api/nfs/write | quick,auto,contract | Request/response schema matches |
| contract/002 | SDK read() → API /api/nfs/read | quick,auto,contract | Response schema matches |
| contract/003 | SDK memory.store() → API /api/v2/memories | auto,contract | Schema matches |
| contract/004 | SDK search() → API /api/v2/search | auto,contract | Schema matches |
| contract/005 | SDK connect() → /health + /api/auth/whoami | quick,auto,contract | Handshake schema stable |
| contract/006 | Error response schema consistency | auto,contract | All errors follow NexusError envelope |

### 4.36 — Chaos / Fault Injection

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| chaos/001 | Kill node during write | chaos,federation | Write completes or cleanly fails, no corruption |
| chaos/002 | Network partition during CAS | chaos,federation | Linearizable: no lost-update anomaly |
| chaos/003 | Clock skew between zones (5s drift) | chaos,federation | Events still ordered correctly |
| chaos/004 | Corrupt metastore file, restart | chaos,dangerous | Detects corruption, refuses to start |
| chaos/005 | Corrupt object store blob | chaos | Read returns integrity error, not garbage |
| chaos/006 | Full disk during write | chaos | Graceful error, no partial state |
| chaos/007 | OOM during bulk operation | chaos,stress | Process recoverable, state consistent |
| chaos/008 | Kill during snapshot commit | chaos,snapshot | Either committed or rolled back, never partial |
| chaos/009 | Kill during pay transfer | chaos,pay | No double-spend, no lost credits |
| chaos/010 | Stale Raft follower serves write | chaos,federation | Rejected or redirected, never silent accept |

### 4.37 — Security (OWASP API Top 10)

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| security/001 | BOLA: access zone-b resource with zone-a key | auto,security | 403 Forbidden |
| security/002 | Broken auth: expired/malformed JWT | auto,security | 401, no stack trace |
| security/003 | Injection via path parameter | auto,security | Sanitized, no shell exec |
| security/004 | Injection via grep query | auto,security | No command injection |
| security/005 | Mass assignment: extra fields in JSON body | auto,security | Ignored or rejected |
| security/006 | SSRF: URL parameter with internal IP | auto,security | Blocked |
| security/007 | Rate limit bypass via header spoofing | auto,security | Still rate limited |
| security/008 | Error response leaks no internal paths | auto,security | No stack traces, no file paths |
| security/009 | Admin endpoint without admin key | auto,security | 403 Forbidden |
| security/010 | SQL injection via search query | auto,security | Parameterized, no injection |

### 4.38 — Performance SLOs

| ID | Test | Groups | SLO |
|----|------|--------|-----|
| slo/001 | Kernel read latency | slo,perf,kernel | p50 < 2ms, p95 < 5ms, p99 < 15ms |
| slo/002 | Kernel write latency | slo,perf,kernel | p50 < 5ms, p95 < 15ms, p99 < 50ms |
| slo/003 | Kernel glob (1K files) | slo,perf,kernel | p95 < 50ms |
| slo/004 | Kernel grep (1K files) | slo,perf,kernel | p95 < 100ms |
| slo/005 | Search full-text latency | slo,perf,search | p95 < 100ms, p99 < 300ms |
| slo/006 | Search semantic latency | slo,perf,search | p95 < 200ms, p99 < 500ms |
| slo/007 | Memory query latency | slo,perf,memory | p95 < 100ms, p99 < 200ms |
| slo/007a | Memory write latency | slo,perf,memory | p95 < 50ms |
| slo/007b | Memory consolidation latency | slo,perf,memory | Wall-time < 5s for 50-memory ACE pass |
| slo/008 | Pay transfer latency | slo,perf,pay | p50 < 10ms, p99 < 50ms |
| slo/009 | ReBAC check latency | slo,perf,rebac | p50 < 1ms, p95 < 5ms, p99 < 15ms |
| slo/010 | API health endpoint | slo,perf | p99 < 10ms |
| slo/011 | Federation cross-zone read | slo,perf,federation | p95 < 50ms (same DC) |
| slo/012 | Concurrent 100 writers throughput | slo,stress | > 1K ops/sec |

### 4.39 — Storage Layer & Backends

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| storage/001 | RecordStore roundtrip (PostgreSQL) | auto,storage | Write + read record |
| storage/002 | RecordStore roundtrip (SQLite) | auto,storage | Same ops, different driver |
| storage/003 | CachingBackendWrapper transparency | auto,storage | Same result with/without cache |
| storage/004 | Backend capability detection | auto,storage | Capabilities match driver |
| storage/005 | Local backend CRUD | auto,storage | File lifecycle on disk |
| storage/006 | CAS put_if_version (optimistic lock) | auto,storage | Version conflict detected |
| storage/007 | ReadSetCache isolation | auto,storage | Dirty read prevented |

### 4.40 — Observability

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| obs/001 | GET /health returns component status | quick,auto | All components healthy |
| obs/002 | GET /health/detailed returns per-brick status | auto | Brick-level health |
| obs/003 | Kubernetes liveness probe | auto | /healthz/live → 200 |
| obs/004 | Kubernetes readiness probe | auto | /healthz/ready → 200 |
| obs/005 | Kubernetes startup probe | auto | /healthz/startup → 200 |
| obs/006 | GET /api/v2/features returns enabled features | auto | Feature flags listed |
| obs/007 | GET /api/v2/operations returns recent ops | auto,audit | Operation log populated |
| obs/008 | Metrics endpoint emits latency histogram | auto,slo | Latency buckets present |
| obs/009 | Metrics endpoint emits error rate counter | auto,slo | Error counter present |
| obs/010 | Metrics endpoint emits saturation gauge | auto,slo | Queue depth / connection pool present |

### 4.41 — Property-Based (Hypothesis)

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| prop/001 | Random file ops never corrupt FS state | property,kernel | After N random ops, all files readable |
| prop/002 | Random permission graphs satisfy Zanzibar invariants | property,rebac | check(A, rel, B) ↔ tuple exists or transitively implied |
| prop/003 | Random memory store/delete never loses queried memory | property,memory | Stored memory queryable until deleted |
| prop/004 | Random concurrent writes are serializable | property,kernel,stress | Final content matches one of the writers |
| prop/005 | Export → import roundtrip preserves all data | property,portability | Byte-identical after roundtrip |

### 4.42 — CLI Commands

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| cli/001 | `nexus ls /` | quick,auto,cli | Lists root directory |
| cli/002 | `nexus write` + `nexus cat` | quick,auto,cli | Roundtrip via CLI |
| cli/003 | `nexus mkdir` + `nexus rmdir` | auto,cli | Dir ops via CLI |
| cli/004 | `nexus glob "**/*.txt"` | auto,cli | Pattern matching via CLI |
| cli/005 | `nexus grep "pattern"` | auto,cli | Content search via CLI |
| cli/006 | `nexus mv` + `nexus cp` | auto,cli | File move/copy via CLI |
| cli/007 | `nexus info /file` | auto,cli | Metadata displayed |
| cli/008 | `nexus memory store` + `query` | auto,cli,memory | Memory ops via CLI |
| cli/009 | `nexus skills list` | auto,cli,skills | Skills listed via CLI |
| cli/010 | `nexus versions list` | auto,cli,versioning | Versions shown via CLI |
| cli/011 | `nexus serve` (starts server) | auto,cli | Server starts, health OK |
| cli/012 | `nexus mount` + `nexus unmount` | auto,cli,mount | Mount ops via CLI |
| cli/013 | `nexus size /workspace` | auto,cli | Recursive size via CLI |
| cli/014 | `nexus tree /` | auto,cli | Tree view via CLI |
| cli/015 | `nexus cache stats` | auto,cli,cache | Cache stats via CLI |
| cli/016 | `nexus zone list` | auto,cli,zone | Zones listed via CLI |
| cli/017 | `nexus search "query"` | auto,cli,search | Search via CLI |
| cli/018 | `nexus version` | quick,auto,cli | Version info displayed |
| cli/019 | `nexus mcp list` | auto,cli,mcp | MCP tools listed via CLI |
| cli/020 | `nexus sandbox run "print(1)"` | auto,cli,sandbox | Output returned via CLI |

### 4.43 — Upgrade & Rollback

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| upgrade/001 | Data survives version N → N+1 upgrade | upgrade | All files readable after upgrade |
| upgrade/002 | Schema migration (RecordStore) | upgrade | No data loss in DB migration |
| upgrade/003 | Rollback N+1 → N | upgrade | System operational on old version |
| upgrade/004 | Config compatibility | upgrade | Old config works with new binary |

---

## 5. Deployment Topologies

### Available compose files

| File | Topology | Use For |
|------|----------|---------|
| `docker-compose.cross-platform-test.yml` | 3-node Raft cluster (2 full + 1 witness) + PG + Dragonfly + MCP + LangGraph + Frontend | Federation, chaos, stress, full regression |
| `docker-compose.demo.yml` | Standalone single node + PG + Dragonfly + MCP + LangGraph + Frontend | Kernel, services, brick tests |
| `docker-compose.evaluation.yml` | Standalone + pgvector + semantic search | Search accuracy, HERB Q&A |

### Topologies exercised

```
Topology 1: Standalone (demo.yml)
┌──────────────────────────────┐
│ nexus (kernel + all bricks)  │
│ + PostgreSQL + Dragonfly     │
└──────────────────────────────┘
Used by: quick, auto, cli, contract, security, slo groups

Topology 2: 3-Node Raft Federation (cross-platform-test.yml)
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  nexus-1     │   │  nexus-2     │   │  witness     │
│  (Leader)    │◄─►│  (Follower)  │◄─►│  (Vote-only) │
│  HTTP :2026  │   │  HTTP :2027  │   │  gRPC :2128  │
│  Raft :2126  │   │  Raft :2127  │   │  (no HTTP)   │
└──────┬───────┘   └──────┬───────┘   └──────────────┘
       ▼                  ▼
┌────────────────────────────┐  ┌────────────────────────┐
│  PostgreSQL (shared)       │  │  Dragonfly (shared)    │
│  :5432                     │  │  :6379                 │
└────────────────────────────┘  └────────────────────────┘
Zones: corp, corp-eng, corp-sales, family
Mounts: /corp=corp, /corp/engineering=corp-eng, /corp/sales=corp-sales,
        /family=family, /family/work=corp
Used by: federation, chaos, portability, stress groups

Topology 3: Evaluation (evaluation.yml)
┌──────────────────────────────┐
│ nexus (semantic search mode) │
│ + pgvector + OpenAI embed    │
└──────────────────────────────┘
Used by: search/005 (HERB Q&A), search/002 (semantic), search/006 (expansion)
```

---

## 6. How to Run

### Step 1: Start infrastructure

```bash
# Standalone (kernel + services + bricks, no federation)
docker compose -f dockerfiles/docker-compose.demo.yml up -d

# Federation cluster (2 full nodes + 1 witness + all services)
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d

# Verify cluster health
curl http://localhost:2026/health   # nexus-1
curl http://localhost:2027/health   # nexus-2 (federation only)
```

### Step 2: Run tests

```bash
# By run-type group
nexus-test -g quick                   # < 2 min smoke
nexus-test -g auto                    # ~15 min full regression
nexus-test -g stress                  # ~30 min
nexus-test -g federation              # Needs federation cluster
nexus-test -g perf                    # Benchmarks

# By feature group
nexus-test -g kernel
nexus-test -g rebac
nexus-test -g memory

# Combine (intersection)
nexus-test -g quick,kernel
nexus-test -g stress,federation

# Specific tests
nexus-test kernel/001
nexus-test "kernel/*"
nexus-test "fed/0*"

# With benchmark data
nexus-test -g perf --benchmark-dir ~/nexus/benchmarks
```

### Step 3: Run inside Docker (same network as cluster)

```bash
# Use the built-in test service (runs pytest inside the Docker network)
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up test
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml logs -f test
```

### Individual operations (what each test does internally)

```bash
# Via nexus CLI
nexus write /test/hello.txt "Hello World" --url http://localhost:2026 --api-key $TEST_API_KEY
nexus cat /test/hello.txt --url http://localhost:2026 --api-key $TEST_API_KEY
nexus rm /test/hello.txt --url http://localhost:2026 --api-key $TEST_API_KEY

# Via HTTP (JSON-RPC)
curl -X POST http://localhost:2026/api/nfs/write \
  -H "Authorization: Bearer $TEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"write","params":{"path":"/test.txt","content":"hello"},"id":1}'

# Write on leader, read from follower (consistency check)
curl -X POST http://localhost:2026/api/nfs/write \
  -H "Authorization: Bearer $TEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"write","params":{"path":"/test.txt","content":"hello"},"id":1}'
curl -X POST http://localhost:2027/api/nfs/read \
  -H "Authorization: Bearer $TEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"read","params":{"path":"/test.txt"},"id":1}'
```

### Chaos simulation

```bash
# Kill leader → verify failover
docker stop nexus-node-1
curl http://localhost:2027/health        # nexus-2 becomes leader
docker start nexus-node-1

# Network partition → verify quorum
docker network disconnect nexus_nexus-network nexus-witness
# Cluster still works (2/3 quorum with nexus-1 + nexus-2)
docker network connect nexus_nexus-network nexus-witness
```

---

## 7. Execution Phases

### Phase 1: Kernel Correctness (Week 1-2)
- Tests: `kernel/*` (incl. kernel/028-040 file_edit), `zone/*`, `hooks/*`, `auth/*`, `cli/001-002,018`, `obs/001`, `contract/001-002,005`
- Data: `benchmarks/performance/`
- Infra: `docker compose -f dockerfiles/docker-compose.demo.yml up -d`
- Count: ~68 tests

### Phase 2: Services + Core Bricks (Week 3-4)
- Tests: `namespace/*`, `agent/*`, `scheduler/*`, `eventlog/*`, `sync/*`, `mount/*`, `upload/*`, `rebac/*`, `memory/001-019,022-023`, `search/*` (incl. search/010-020 accuracy + deep search), `pay/*`
- Data: `benchmarks/herb/enterprise-context/`, `benchmarks/herb/qa/`, `benchmarks/search/hotpotqa/`, `benchmarks/search/beir/`, `benchmarks/search/musique/`, `benchmarks/memory/longmemeval/`, `benchmarks/memory/locomo/`, `benchmarks/memory/memoryagentbench/`, `benchmarks/memory/tofu/`
- Infra: `docker compose -f dockerfiles/docker-compose.demo.yml up -d` (includes PostgreSQL + Dragonfly)
- Count: ~111 tests
- Note: memory/020-021 (perf benchmarks) deferred to Phase 5

### Phase 3: All Bricks + Dynamic Management (Week 5-6)
- Tests: `llm/*`, `mcp/*`, `sandbox/*`, `snapshot/*`, `skills/*`, `governance/*`, `reputation/*`, `delegation/*`, `workflow/*`, `ipc/*`, `watch/*`, `cache/*`, `versioning/*`, `locks/*`, `audit/*`, `a2a/*`, `discovery/*`, `manifest/*`, `playbook/*`, `trajectory/*`, `feedback/*`, `graph/*`, `batch/*`, `async/*`, `stream/*`, `conflict/*`, `credential/*`, `brick/*`, `storage/*`, `obs/*`, `cli/003-020`, `contract/003-006`
- Data: `benchmarks/herb/qa/`
- Infra: `docker compose -f dockerfiles/docker-compose.demo.yml up -d` (full stack single node)
- Count: ~120 tests

### Phase 4: Federation + Portability (Week 7-8)
- Tests: `fed/*`, `port/*`, `chaos/*`
- Infra: `docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d` (3-node Raft cluster)
- Zones: `corp`, `corp-eng`, `corp-sales`, `family`
- Count: ~45 tests

### Phase 5: Production Readiness (Week 9-10)
- Tests: `stress/*`, `slo/*`, `security/*`, `property/*`, `upgrade/*`, `memory/020`, `memory/021`
- Data: Full `benchmarks/performance/` dataset (50K files), `benchmarks/memory/ultradomain/`, `benchmarks/memory/graphrag-bench/`
- Infra: Both `demo.yml` (standalone) and `cross-platform-test.yml` (federation)
- Count: ~47 tests
- Note: memory/020 (write + consolidation latency) and memory/021 (context saturation baseline) require full perf harness

### Grand Total: ~391 tests

---

## 8. CI Integration

```yaml
# .github/workflows/nexus-test.yml
jobs:
  quick:
    # Every PR — standalone smoke test
    runs-on: ubuntu-latest
    steps:
      - docker compose -f dockerfiles/docker-compose.demo.yml up -d
      - nexus-test -g quick

  auto:
    # Nightly — full regression (standalone)
    runs-on: ubuntu-latest
    steps:
      - docker compose -f dockerfiles/docker-compose.demo.yml up -d
      - nexus-test -g auto

  security:
    # Every PR + nightly
    runs-on: ubuntu-latest
    steps:
      - docker compose -f dockerfiles/docker-compose.demo.yml up -d
      - nexus-test -g security

  federation:
    # Weekly or on federation PRs — 3-node Raft cluster
    runs-on: ubuntu-latest
    steps:
      - docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d
      - nexus-test -g federation

  chaos:
    # Before release — fault injection against federation cluster
    runs-on: ubuntu-latest
    steps:
      - docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d
      - nexus-test -g chaos

  perf:
    # On perf PRs, compare against baseline
    runs-on: ubuntu-latest
    steps:
      - docker compose -f dockerfiles/docker-compose.demo.yml up -d
      - nexus-test -g perf --baseline ./perf-baseline.json

  release:
    # Before release — full matrix
    runs-on: ubuntu-latest
    steps:
      - docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d
      - nexus-test -g stress,chaos,slo,property,upgrade,portability
```

---

## 9. Production Readiness Criteria

| Milestone | Gate | Tests Required |
|-----------|------|----------------|
| Internal staging | All `auto` group passing | ~200 tests |
| Beta users | `auto` + `federation` + `security` passing | ~265 tests |
| Production GA | All groups passing + DAST scan + load test + pentest | ~355 tests + external validation |

### External validation (not in this test plan, required for GA)

- **DAST scanning** — OWASP ZAP or Pynt against live API weekly
- **Load testing** — Sustained multi-hour load with Locust/Gatling
- **Penetration testing** — Manual pentest before launch
- **Compliance audit** — GDPR, SOC 2 (portability + audit trail tests support this)
