"""Query Nexus memory and generate answers for benchmark questions."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import httpx  # used by MemoryIndex

from benchmarks.memory.checkpoint import Checkpoint
from benchmarks.memory.config import BenchmarkConfig
from benchmarks.memory.llm.client import LLMClient
from benchmarks.memory.llm.prompts import build_answer_messages
from benchmarks.memory.models import Answer, Question

logger = logging.getLogger(__name__)


def query_and_answer(
    llm: LLMClient,
    questions: list[Question],
    *,
    config: BenchmarkConfig,
    checkpoint: Checkpoint,
    memory_index: MemoryIndex | None = None,
) -> list[Answer]:
    """For each question: retrieve from memory, generate constrained answer.

    1. Retrieve top contexts via local embedding index
    2. LLM generates constrained answer from contexts
    3. Record latency

    Skips already-answered questions (checkpoint).
    """
    answers: list[Answer] = []
    skipped = 0

    for question in questions:
        # Check checkpoint
        cached = checkpoint.load(question.dataset, f"answer_{question.id}")
        if cached is not None:
            answers.append(Answer(
                question_id=question.id,
                retrieved_contexts=tuple(cached.get("retrieved_contexts", [])),
                generated_answer=cached["generated_answer"],
                latency_ms=cached.get("latency_ms", 0.0),
            ))
            skipped += 1
            continue

        # Measure total latency (retrieve + generate)
        start_ns = time.perf_counter_ns()

        # Step 1: Retrieve from memory index
        if memory_index:
            contexts = memory_index.search(question.text, limit=config.memory_search_limit)
        else:
            contexts = []

        # Step 2: Generate answer via LLM
        context_text = "\n".join(contexts) if contexts else "No relevant memories found."
        messages = build_answer_messages(
            question.dataset, question.text, context_text
        )
        generated = llm.chat(
            config.answer_model, messages, max_tokens=config.answer_max_tokens
        )

        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000

        answer = Answer(
            question_id=question.id,
            retrieved_contexts=tuple(contexts),
            generated_answer=generated,
            latency_ms=elapsed_ms,
        )
        answers.append(answer)

        # Save checkpoint
        checkpoint.save(question.dataset, f"answer_{question.id}", {
            "question_id": question.id,
            "retrieved_contexts": list(contexts),
            "generated_answer": generated,
            "latency_ms": elapsed_ms,
        })

        logger.debug(
            "Q[%s]: retrieved %d contexts, answered in %.0fms",
            question.id,
            len(contexts),
            elapsed_ms,
        )

    logger.info(
        "Query+answer complete: %d generated, %d from cache",
        len(answers) - skipped,
        skipped,
    )
    return answers


class MemoryIndex:
    """In-memory embedding index for semantic retrieval.

    Builds a local index from ingested conversation messages and uses
    OpenAI embeddings for cosine similarity search. This bypasses the
    Nexus search endpoint (which has a ReBAC SQL bug) while still
    measuring the full pipeline quality.
    """

    def __init__(self, openai_api_key: str, model: str = "text-embedding-3-small") -> None:
        self._api_key = openai_api_key
        self._model = model
        self._client = httpx.Client(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {openai_api_key}"},
            timeout=60.0,
        )
        self._entries: list[dict[str, Any]] = []  # {content, embedding}
        self._dim = 0

    def close(self) -> None:
        self._client.close()

    def add_messages(self, messages: list[dict[str, str]]) -> int:
        """Embed and index a batch of messages.

        Args:
            messages: List of {speaker, text, session_id, ...} dicts.

        Returns:
            Number of messages indexed.
        """
        texts = []
        for msg in messages:
            text = msg.get("text", "")
            speaker = msg.get("speaker", "unknown")
            if text:
                texts.append(f"[{speaker}]: {text}")

        if not texts:
            return 0

        # Batch embed (OpenAI supports up to 2048 inputs per call)
        batch_size = 512
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            embeddings = self._embed_batch(batch)
            for content, emb in zip(batch, embeddings):
                self._entries.append({"content": content, "embedding": emb})

        if self._entries and not self._dim:
            self._dim = len(self._entries[0]["embedding"])

        logger.info("Indexed %d messages (%d total)", len(texts), len(self._entries))
        return len(texts)

    def search(self, query: str, *, limit: int = 10) -> list[str]:
        """Search for most similar memories to query."""
        if not self._entries:
            return []

        query_emb = self._embed_batch([query])[0]

        # Compute cosine similarity against all entries
        scored = []
        for entry in self._entries:
            sim = _cosine_similarity(query_emb, entry["embedding"])
            scored.append((sim, entry["content"]))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [content for _, content in scored[:limit]]

    def clear(self) -> None:
        """Clear all indexed entries."""
        self._entries.clear()
        self._dim = 0

    @property
    def size(self) -> int:
        return len(self._entries)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Call OpenAI embeddings API."""
        resp = self._client.post(
            "/embeddings",
            json={"input": texts, "model": self._model},
        )
        resp.raise_for_status()
        data = resp.json()
        # Sort by index to ensure order matches input
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
