"""Published baseline numbers from memory system research papers.

Sources:
- Mem0 v2: arXiv 2504.19413 (Table 5) — note: disputed by Zep
- Zep: arXiv 2501.13956 (corrected figures, see blog.getzep.com)
- MemGPT/Letta: Letta benchmarking blog (2025)
- MemMachine: memmachine.ai LoCoMo results (current SOTA)
- MemR3: arXiv 2512.20237 (reflective reasoning retrieval)
- EverMemOS: LongMemEval SOTA (2025)
- TiMem: GPT-4o-mini temporal memory (2025)
- RMM: arXiv 2503.08026 (reflective memory management, ACL 2025)
- Hindsight: LongMemEval paper
- Full-context / RAG: Both papers
- TOFU: locuslab/TOFU paper
- SimpleMem: arXiv 2601.02553 (consolidation SOTA)
- ACE: arXiv 2510.04618 (agentic context engineering)
- GraphRAG: arXiv 2404.16130 (Microsoft)
- LightRAG: arXiv 2410.05779 (HKUDS, EMNLP 2025)
- HippoRAG2: arXiv 2502.14802
- MAGMA / Anatomy survey: arXiv 2602.19320
- KGGen: arXiv 2502.09956
- Cognee: cognee.ai/research (HotPotQA, 24 Qs × 45 runs)
- SAGE: arXiv 2409.00872 (self-evolving agents, reflective memory)
- Reflexion: arXiv 2303.11366 (NeurIPS 2023, verbal RL)
- MemBench: ACL 2025 Findings (factual vs reflective memory)
- Evo-Memory: arXiv 2511.20857 (test-time learning)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Core benchmark baselines (LoCoMo, LongMemEval, TOFU)
# ---------------------------------------------------------------------------

PUBLISHED_BASELINES: dict[str, dict[str, dict[str, float]]] = {
    "locomo": {
        "MemR3+RAG": {
            "overall": 86.75,
            "single_hop": 92.17,
            "multi_hop": 81.20,
            "temporal": 82.14,
            "open_domain": 71.53,
        },
        "MemMachine": {
            "overall": 84.9,
            "single_hop": 93.3,
            "multi_hop": 80.5,
            "temporal": 72.6,
            "open_domain": 64.6,
        },
        "Zep": {
            "overall": 75.1,
            "single_hop": 74.1,
            "multi_hop": 66.0,
            "temporal": 79.8,
            "open_domain": 67.7,
        },
        "MemGPT": {"overall": 74.0},
        "Full-context": {"overall": 72.0},
        "Mem0 v2": {
            "overall": 66.9,
            "single_hop": 67.1,
            "multi_hop": 51.2,
            "temporal": 55.5,
            "open_domain": 72.9,
        },
        "RAG baseline": {"overall": 56.3},
        "OpenAI Memory": {"overall": 52.9},
    },
    "longmemeval": {
        "EverMemOS": {"overall": 83.0},
        "TiMem": {"overall": 76.9},
        "Zep": {"overall": 71.2},
        "RMM": {"overall": 70.4},
        "Full-context": {"overall": 60.2},
        "RAG baseline": {"overall": 48.7},
    },
    "tofu": {
        "TOFU baseline": {
            "forget_rouge": 0.12,
            "retain_rouge": 0.85,
        },
    },
}

# ---------------------------------------------------------------------------
# Consolidation baselines (memory/004: ACE consolidation)
# ---------------------------------------------------------------------------

CONSOLIDATION_BASELINES: dict[str, dict[str, float]] = {
    # F1 scores on LoCoMo (post-consolidation QA quality)
    "SimpleMem": {
        "f1": 43.24,
        "compression_ratio": 32.0,
        "construction_time_s": 92.6,
        "tokens_per_query": 531,
    },
    "Mem0": {
        "f1": 34.20,
        "compression_ratio": 17.0,
        "construction_time_s": 1350.9,
        "tokens_per_query": 973,
    },
    "A-Mem": {
        "f1": 32.58,
        "compression_ratio": 12.0,
        "construction_time_s": 5140.5,
        "tokens_per_query": 1216,
    },
    "Full-context": {
        "f1": 18.70,
        "compression_ratio": 1.0,
        "tokens_per_query": 16910,
    },
    # ACE paper: +17% TGC on AppWorld, +7.6% FiNER, +18% Formula
    "ACE (AppWorld)": {
        "tgc_improvement_pct": 17.0,
        "adaptation_latency_reduction_pct": 82.3,
    },
}

# ---------------------------------------------------------------------------
# Entity extraction / knowledge graph baselines (memory/007, memory/013)
# ---------------------------------------------------------------------------

KNOWLEDGE_GRAPH_BASELINES: dict[str, dict[str, float]] = {
    # Downstream QA after graph construction
    "HippoRAG2 (MuSiQue)": {"f1": 51.9, "recall_at_5": 74.7},
    "HippoRAG2 (2Wiki)": {"f1": 59.5, "recall_at_5": 90.4},
    "Circlemind (fast-graphrag)": {"perfect_retrieval_pct": 96.1},
    "GraphRAG (local)": {"perfect_retrieval_pct": 74.5},
    "LightRAG": {"perfect_retrieval_pct": 47.0, "ragas_score": 0.9425},
    "VectorDB baseline": {"perfect_retrieval_pct": 49.0},
    # Fact extraction accuracy (KGGen MINE benchmark)
    "KGGen": {"fact_retrieval_accuracy": 66.07},
    "GraphRAG (extraction)": {"fact_retrieval_accuracy": 47.80},
    "OpenIE": {"fact_retrieval_accuracy": 29.84},
    # Graph vs flat memory (Anatomy of Agentic Memory survey)
    "MAGMA (graph)": {"judge_score": 0.670, "f1": 0.467},
    "Nemori (episodic)": {"judge_score": 0.602, "f1": 0.502},
    "MemoryOS (profile)": {"judge_score": 0.553, "f1": 0.413},
    "SimpleMEM (flat)": {"judge_score": 0.294, "f1": 0.268},
    # Triple extraction (standard NER/RE benchmarks)
    "GPT-4 (WebNLG triples)": {"f1": 0.645},
    "Fine-tuned 7B (WebNLG)": {"f1": 0.717},
    "Full pipeline (CoRef+Decomp)": {"f1": 0.924},
}

# ---------------------------------------------------------------------------
# Reflective / self-improving memory baselines (RLM / memory/022)
# ---------------------------------------------------------------------------

REFLECTIVE_MEMORY_BASELINES: dict[str, dict[str, float]] = {
    # MemR3 on LoCoMo (reflective reasoning retrieval)
    "MemR3+RAG (GPT-4.1-mini)": {
        "overall": 86.75,
        "multi_hop": 81.20,
        "temporal": 82.14,
    },
    "MemR3+Zep (GPT-4.1-mini)": {
        "overall": 80.88,
        "multi_hop": 77.78,
        "temporal": 77.78,
    },
    # RMM on LongMemEval (reflective memory management)
    "RMM (LongMemEval)": {"accuracy": 70.4, "recall_at_5": 69.8},
    # SAGE (self-evolving agents, procedural memory)
    "SAGE (GPT-3.5 OS)": {"improvement_factor": 2.26},
    "SAGE (HotpotQA)": {"f1": 22.06},
    "Reflexion (HumanEval)": {"pass_at_1": 91.0},
    "Reflexion (AlfWorld)": {"task_completion_pct": 97.0},
    # Evo-Memory (test-time self-evolving)
    "Evo-Memory (AlfWorld)": {"baseline": 0.49, "self_evolving": 0.89},
    "Evo-Memory (PDDL)": {"baseline": 0.39, "self_evolving": 0.95},
    # MemBench: factual vs reflective memory
    "MemBench factual (GPT-4o-mini)": {"accuracy_10k": 0.736},
    "MemBench reflective (GPT-4o-mini)": {
        "accuracy_10k": 0.733,
        "accuracy_100k": 0.533,
    },
}

# ---------------------------------------------------------------------------
# Cognee / HotPotQA cross-system comparison
# ---------------------------------------------------------------------------

COGNEE_BASELINES: dict[str, dict[str, float]] = {
    "Cognee": {"human_like_correctness": 0.93, "deepeval_em": 0.69},
    "Mem0": {"human_like_correctness": 0.88, "deepeval_em": 0.62},
    "LightRAG": {"human_like_correctness": 0.78, "deepeval_em": 0.45},
    "Graphiti": {"human_like_correctness": 0.72, "deepeval_em": 0.38},
}

# ---------------------------------------------------------------------------
# GraphRAG-Bench (Novel dataset, 16 disciplines)
# ---------------------------------------------------------------------------

GRAPHRAG_BENCH_BASELINES: dict[str, dict[str, float]] = {
    "RAG (reranked)": {
        "fact_retrieval": 60.92,
        "complex_reasoning": 42.93,
        "summarization": 51.30,
        "creative": 38.26,
        "evidence_recall": 83.21,
    },
    "GraphRAG (local)": {
        "fact_retrieval": 49.29,
        "complex_reasoning": 50.93,
        "summarization": 64.40,
        "creative": 39.10,
        "evidence_recall": 61.04,
    },
    "HippoRAG2": {
        "fact_retrieval": 60.14,
        "complex_reasoning": 53.38,
        "summarization": 64.10,
        "creative": 48.28,
        "evidence_recall": 70.29,
    },
    "LightRAG": {
        "fact_retrieval": 58.62,
        "complex_reasoning": 49.07,
        "summarization": 48.85,
        "creative": 23.80,
        "evidence_recall": 73.69,
    },
    "Fast-GraphRAG": {
        "fact_retrieval": 56.95,
        "complex_reasoning": 48.55,
        "summarization": 56.41,
        "creative": 46.18,
        "evidence_recall": 64.48,
    },
}

# ---------------------------------------------------------------------------
# Category labels
# ---------------------------------------------------------------------------

LOCOMO_CATEGORIES: dict[int, str] = {
    1: "single_hop",
    2: "multi_hop",
    3: "temporal",
    4: "open_domain",
}

# ---------------------------------------------------------------------------
# RLM (Recursive Language Model) baselines — arXiv:2512.24601
# Nexus has RLM at POST /api/v2/rlm/infer (recursive decomposition for
# unbounded context). These baselines are from the RLM paper (MIT OASYS).
# ---------------------------------------------------------------------------

RLM_BASELINES: dict[str, dict[str, float]] = {
    # GPT-5 backend
    "RLM (GPT-5, CodeQA)": {"accuracy": 62.0, "base_accuracy": 24.0},
    "RLM (GPT-5, OOLONG-Pairs)": {"f1": 58.0, "base_f1": 0.04},
    "RLM (GPT-5, BrowseComp-Plus)": {"accuracy": 91.33},
    "Summarization Agent (GPT-5, CodeQA)": {"accuracy": 41.33},
    # Qwen3-8B backend (open-source)
    "RLM (Qwen3-8B)": {"avg_improvement_pct": 28.3},
    # Key: RLM extends effective context to 10M+ tokens via recursive
    # decomposition, far exceeding any fixed-context approach.
}

LONGMEMEVAL_TYPES: tuple[str, ...] = (
    "information_extraction",
    "multi_session",
    "temporal_reasoning",
    "knowledge_update",
    "abstention",
)
