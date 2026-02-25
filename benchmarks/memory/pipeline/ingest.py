"""Ingest benchmark conversations into Nexus memory."""

from __future__ import annotations

import logging
import time
from typing import Any

from tests.helpers.api_client import NexusClient

from benchmarks.memory.checkpoint import Checkpoint

logger = logging.getLogger(__name__)


def ingest_conversations(
    nexus: NexusClient,
    conversations: list[dict[str, Any]],
    *,
    zone: str = "corp",
    checkpoint: Checkpoint,
    dataset: str,
) -> int:
    """Ingest all conversations into Nexus memory.

    Each message becomes a memory_store call with metadata containing
    conversation_id, session_id, speaker, and turn_index.

    Skips already-ingested conversations (checkpoint).

    Returns:
        Count of memories stored.
    """
    stored = 0
    skipped = 0

    for conv in conversations:
        conv_id = conv["id"]
        ckpt_key = f"ingest_{conv_id}"

        if checkpoint.is_done(dataset, ckpt_key):
            skipped += 1
            continue

        messages = conv.get("messages", [])
        msg_count = 0

        for turn_idx, msg in enumerate(messages):
            text = msg.get("text", "")
            if not text:
                continue

            speaker = msg.get("speaker", "unknown")
            session_id = msg.get("session_id", "0")

            # Format: "[speaker]: text" for richer memory content
            content = f"[{speaker}]: {text}"
            metadata = {
                "conversation_id": conv_id,
                "session_id": session_id,
                "speaker": speaker,
                "turn_index": turn_idx,
                "dataset": dataset,
            }

            resp = nexus.memory_store(content, metadata=metadata, zone=zone)
            if resp.ok:
                msg_count += 1
                stored += 1
            else:
                logger.warning(
                    "Failed to store message %d of conversation %s: %s",
                    turn_idx,
                    conv_id,
                    resp.error.message if resp.error else "unknown error",
                )

        # Only mark as done if at least one message was stored
        if msg_count > 0:
            checkpoint.save(dataset, ckpt_key, {
                "conversation_id": conv_id,
                "messages_stored": msg_count,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            logger.info(
                "Ingested conversation %s: %d messages stored",
                conv_id,
                msg_count,
            )
        else:
            logger.warning(
                "Conversation %s: all message stores failed, will retry on next run",
                conv_id,
            )

    logger.info(
        "Ingestion complete: %d stored, %d skipped (already ingested)",
        stored,
        skipped,
    )
    return stored
