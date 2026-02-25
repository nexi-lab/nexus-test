# Nexus Memory Benchmark Evaluation Harness

## Overview

This harness evaluates Nexus memory against three standardized academic benchmarks
and compares results against published baselines from 55+ memory systems.

**Pipeline**: Parse dataset → Ingest into Nexus → Retrieve from memory → LLM-generate answer → LLM-judge or ROUGE-L score → Aggregate metrics → Comparison report

## Prerequisites

| Requirement | Details |
|---|---|
| **Nexus server** | Running on `localhost:2026` (or set `NEXUS_URL`) |
| **OpenAI API key** | `OPENAI_API_KEY` env var — used for answer generation, judging, and embedding |
| **Python 3.11+** | With `httpx` installed (already in project deps) |
| **~3 GB disk** | For downloaded datasets |
| **HuggingFace `datasets`** | Only needed for TOFU download (`pip install datasets`) |

## Datasets

### 1. LoCoMo (ACL 2024) — Long-Context Memory QA

- **Source**: https://github.com/snap-research/locomo → `data/locomo10.json`
- **Size**: 10 conversations × ~580 messages each = 5,882 total messages, 1,540 scorable questions
- **Categories**: single-hop (282), multi-hop (321), temporal (96), open-domain (841)
- **Protocol**:
  1. Ingest all 10 conversations (each has ~35 sessions of speaker-turn messages)
  2. For each question, retrieve top-10 semantically similar messages
  3. GPT-4o-mini generates a constrained answer (5-6 words max)
  4. GPT-4o-mini judge compares predicted vs gold answer → `CORRECT` / `WRONG`
- **Metric**: Binary accuracy per category and overall
- **Published SOTA**: MemR3+RAG 86.75%, MemMachine 84.9%, Zep 75.1%

### 2. LongMemEval (ICLR 2025) — Long-Term Memory Evaluation

- **Source**: https://github.com/xiaowu0162/LongMemEval
- **Size**: 500 questions, each with its own conversation history (~500 messages per question)
- **Split**: `S` (small, ~115K tokens/entry, default) or `full`
- **Categories**: information_extraction, multi_session, temporal_reasoning, knowledge_update, abstention
- **Protocol**:
  1. Ingest all 500 conversation histories
  2. For each question, retrieve top-10 semantically similar messages from the full corpus
  3. GPT-4o-mini generates an answer from retrieved context
  4. GPT-4o judge uses question-type-specific prompts → `CORRECT` / `WRONG`
  5. (LongMemEval uses GPT-4o for judging because type-specific evaluation requires stronger reasoning)
- **Metric**: Binary accuracy per question type and overall
- **Published SOTA**: EverMemOS 83.0%, TiMem 76.9%, Zep 71.2%

### 3. TOFU — Selective Forgetting (Machine Unlearning)

- **Source**: HuggingFace `locuslab/TOFU`
- **Size**: 200 fictitious author profiles, ~4,000 QA pairs, 20 per author
- **Split**: 10% forget set (20 authors), 90% retain set (180 authors)
- **Protocol**:
  1. Ingest all 200 author profiles as memory
  2. Issue forget request for the 10% forget set
  3. Query both forget and retain questions
  4. Score using ROUGE-L (word-level LCS F1) — no LLM judge needed
- **Metric**: Forget ROUGE-L (should drop, lower = better forgetting), Retain ROUGE-L (should stay high)
- **Published baseline**: Forget ROUGE-L 0.12, Retain ROUGE-L 0.85

## Retrieval Strategy

### Current: Client-Side Embedding Index

Due to a known bug in Nexus's ReBAC permission enforcer (SQL syntax error in
`memory_permission_enforcer.py:184` causing HTTP 500 on every search query), the
harness currently uses a **client-side embedding index**:

1. After ingestion, all conversation messages are embedded via OpenAI `text-embedding-3-small`
2. Embeddings are stored in-memory (pure Python, no vector DB dependency)
3. For each query, the question is embedded and cosine similarity is computed against all stored embeddings
4. Top-K most similar messages are returned as context

This measures **embedding-based retrieval quality** but does not exercise Nexus's
native search pipeline (which includes PostgreSQL-backed vector search, metadata
filtering, and ReBAC permission checks).

### Target: Nexus Native Search

To benchmark Nexus's actual search, the following must be fixed:
1. ReBAC SQL generation bug in `rebac_manager.rebac_check()` (or disable ReBAC for benchmarks)
2. Enable `features.llm: true` in Nexus config so memories get embeddings on store
3. Configure an embedding provider (OpenAI or local)

When fixed, the harness will switch to `POST /api/v2/memories/search` for retrieval.

## Answer Generation

All three datasets use the same answer generation flow:
- **Model**: GPT-4o-mini (`BENCH_ANSWER_MODEL`)
- **Max tokens**: 100
- **System prompt**: Dataset-specific (LoCoMo constrains to 5-6 words; LongMemEval and TOFU allow free-form)
- **Input**: Retrieved memory excerpts + question text

## Judging / Scoring

| Dataset | Judge Model | Method | Prompt |
|---------|-------------|--------|--------|
| LoCoMo | GPT-4o-mini | Binary CORRECT/WRONG | Semantic equivalence comparison |
| LongMemEval | GPT-4o | Binary CORRECT/WRONG | 5 type-specific prompts (extraction, multi-session, temporal, knowledge-update, abstention) |
| TOFU | N/A | ROUGE-L F1 | Word-level longest common subsequence |

## Published Baselines Compared

The report compares Nexus against baselines organized into 7 categories:

### Core Benchmarks (LoCoMo, LongMemEval, TOFU)

| System | LoCoMo | LongMemEval | Source |
|--------|--------|-------------|--------|
| MemR3+RAG | 86.75% | — | arXiv 2512.20237 |
| MemMachine | 84.9% | — | memmachine.ai |
| Zep | 75.1% | 71.2% | arXiv 2501.13956 |
| MemGPT/Letta | 74.0% | — | Letta blog 2025 |
| Full-context | 72.0% | 60.2% | Both papers |
| Mem0 v2 | 66.9% | — | arXiv 2504.19413 |
| RAG baseline | 56.3% | 48.7% | Both papers |
| EverMemOS | — | 83.0% | LongMemEval SOTA |
| TiMem | — | 76.9% | GPT-4o-mini temporal |
| RMM | — | 70.4% | arXiv 2503.08026 |

### Consolidation Baselines (memory/004 — ACE)

SimpleMem, Mem0, A-Mem, Full-context, ACE. Metrics: F1, compression ratio, build time, tokens/query.

### Knowledge Graph Baselines (memory/007, memory/013)

HippoRAG2, Circlemind, GraphRAG, LightRAG, KGGen, MAGMA, and 10 more systems.
Metrics: F1, retrieval accuracy, RAGAS score, judge score.

### Reflective Memory Baselines (memory/022 — RLM)

MemR3, RMM, SAGE, Reflexion, Evo-Memory, MemBench. Metrics: accuracy, improvement factor, pass@1.

### GraphRAG-Bench (Novel Dataset, 16 Disciplines)

RAG (reranked), GraphRAG (local), HippoRAG2, LightRAG, Fast-GraphRAG across
fact retrieval, complex reasoning, summarization, creative, and evidence recall tasks.

### Cognee / HotPotQA Cross-System

Cognee, Mem0, LightRAG, Graphiti. Metrics: human-like correctness, DeepEval EM.

### RLM (Recursive Language Model) — Nexus Feature

Nexus implements RLM at `POST /api/v2/rlm/infer` for recursive context decomposition
(10M+ tokens). Baselines from arXiv:2512.24601 (MIT OASYS).

## Setup

### 1. Download datasets

```bash
bash scripts/download_memory_benchmarks.sh
```

Downloads to `benchmarks/data/` (~3 GB total):
- `locomo/` — git clone from snap-research/locomo
- `longmemeval/` — git clone from xiaowu0162/LongMemEval
- `tofu/` — HuggingFace `locuslab/TOFU` (requires `pip install datasets`)

### 2. Configure environment

```bash
# Required
export OPENAI_API_KEY="sk-..."

# Optional overrides (defaults shown)
export NEXUS_URL="http://localhost:2026"
export NEXUS_API_KEY="sk-test-federation-e2e-admin-key"
export NEXUS_ZONE="corp"
export BENCH_JUDGE_MODEL="gpt-4o"         # LongMemEval judge
export BENCH_ANSWER_MODEL="gpt-4o-mini"   # answer generation + LoCoMo judge
export BENCH_DATA_DIR="benchmarks/data"
export BENCH_RESULTS_DIR="benchmarks/results"
```

### 3. Ensure Nexus is running

```bash
curl -s http://localhost:2026/health | jq .
# Expected: {"status":"healthy",...}
```

## Running

```bash
# All three benchmarks (LoCoMo + LongMemEval + TOFU)
python -m benchmarks.memory.run

# Single dataset
python -m benchmarks.memory.run --dataset locomo
python -m benchmarks.memory.run --dataset longmemeval
python -m benchmarks.memory.run --dataset tofu

# Start fresh (clear all checkpoints)
python -m benchmarks.memory.run --fresh

# Clear one dataset's checkpoints
python -m benchmarks.memory.run --clear locomo

# Regenerate report from existing results
python -m benchmarks.memory.run --report-only

# Debug logging
python -m benchmarks.memory.run -v
```

## Estimated Runtime

| Phase | LoCoMo | LongMemEval (S) | TOFU |
|-------|--------|-----------------|------|
| Ingestion (Nexus API) | ~3 min (5,882 msgs) | ~100 min (250K msgs) | ~20 min (4,000 profiles) |
| Embedding index build | ~40 sec | ~8 min | ~2 min |
| Query + Answer (GPT-4o-mini) | ~40 min (1,540 Qs) | ~15 min (500 Qs) | ~100 min (4,000 Qs) |
| Judging | ~15 min (GPT-4o-mini) | ~10 min (GPT-4o) | instant (ROUGE-L) |
| **Total** | **~60 min** | **~135 min** | **~125 min** |

The checkpoint system saves progress per-question. If interrupted, re-running the
same command resumes from where it left off.

## Output

Results are written to `benchmarks/results/`:

```
benchmarks/results/
  report.md           # Markdown comparison report with all tables
  report.json         # Machine-readable JSON sidecar
  locomo/             # Per-question checkpoint files
    ingest_conv-26.json
    answer_locomo_conv-26_q0.json
    judge_locomo_conv-26_q0.json
    ...
  longmemeval/
    ...
  tofu/
    ...
```

## Architecture

```
benchmarks/memory/
  run.py              # CLI entry point (python -m benchmarks.memory.run)
  runner.py           # Pipeline orchestration (parse → ingest → query → judge → report)
  config.py           # BenchmarkConfig (env vars, defaults)
  models.py           # Immutable dataclasses (Question, Answer, JudgeResult, BenchmarkResult)
  checkpoint.py       # JSON-per-question checkpoint system for resumability
  baselines.py        # Published baseline numbers (55+ systems, 7 categories)
  report.py           # Markdown + JSON report generation

  datasets/
    locomo.py         # LoCoMo parser (locomo10.json → conversations + questions)
    longmemeval.py    # LongMemEval parser (JSON → sessions + questions)
    tofu.py           # TOFU parser (HuggingFace → profiles + forget/retain questions)

  llm/
    client.py         # OpenAI-compatible LLM client (httpx, no SDK dependency)
    prompts.py        # Dataset-specific answer + judge prompts

  pipeline/
    ingest.py         # Ingest conversations into Nexus memory via REST API
    query.py          # Retrieve from memory + generate answers (includes MemoryIndex)
    judge.py          # LLM-as-judge (LoCoMo/LongMemEval) or ROUGE-L (TOFU)
    metrics.py        # Aggregate scores → accuracy per category

scripts/
  download_memory_benchmarks.sh   # Dataset download script
```

## Known Issues

1. **Nexus search endpoint returns HTTP 500** — `psycopg2.errors.SyntaxError` in
   ReBAC permission enforcer SQL generation. Workaround: client-side embedding index.

2. **No embeddings on stored memories** — `features.llm: false` in demo config
   disables the embedding provider. Even if search were fixed, semantic search
   would return no results without embeddings.

3. **Cross-conversation noise** — All conversations for a dataset are indexed together.
   Questions about conversation X may retrieve context from conversation Y. Published
   baselines may or may not filter by conversation_id (depends on system architecture).

4. **OpenAI rate limits** — At ~40 requests/min, large datasets (TOFU: 4,000 Qs)
   take significant time. The checkpoint system ensures no work is lost on interruption.
