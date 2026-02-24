"""Zone-specific API key provisioning for E2E tests.

Creates non-admin API keys bound to specific zones, required for testing
zone isolation. The Nexus server derives the zone from the API key
(not from RPC parameters), and admin keys bypass zone isolation.

Key creation uses the admin_create_key RPC endpoint. If that fails
(e.g., missing _record_store), falls back to direct SQLite insertion
using the same HMAC-SHA256 hashing as the server.

Usage:
    key = create_zone_key(admin_client, zone_id="corp", name="test-corp")
    zone_client = admin_client.for_zone(key)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime

from tests.helpers.api_client import NexusClient

logger = logging.getLogger(__name__)

# Must match nexus.auth.constants.HMAC_SALT
_HMAC_SALT = "nexus-api-key-v1"
_API_KEY_PREFIX = "sk-"


def _hash_key(raw_key: str) -> str:
    """Compute HMAC-SHA256 hash matching the server's DatabaseAPIKeyAuth._hash_key()."""
    return hmac.new(
        _HMAC_SALT.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _generate_raw_key(zone_id: str, user_id: str) -> str:
    """Generate a raw API key matching the server's token format.

    Format: sk-<zone[:8]>_<user[:8]>_<key_id_hex>_<random_hex>
    """
    zone_prefix = f"{zone_id[:8]}_" if zone_id else ""
    user_prefix = user_id[:8]
    key_id_part = secrets.token_hex(4)
    random_suffix = secrets.token_hex(16)
    return f"{_API_KEY_PREFIX}{zone_prefix}{user_prefix}_{key_id_part}_{random_suffix}"


def create_zone_key(
    admin_client: NexusClient,
    zone_id: str,
    *,
    name: str | None = None,
    user_id: str = "test-user",
    is_admin: bool = False,
) -> str:
    """Create a zone-specific API key and return the raw key string.

    Tries the admin_create_key RPC first. If that fails, falls back to
    direct SQLite insertion.

    Args:
        admin_client: NexusClient with admin privileges.
        zone_id: The zone to bind the key to.
        name: Human-readable key name.
        user_id: User ID for the key.
        is_admin: Whether the key should have admin privileges.

    Returns:
        The raw API key string (e.g., "sk-corp_test-use_abcd1234_...").
    """
    key_name = name or f"test-{zone_id}-{secrets.token_hex(4)}"

    # Try RPC first
    resp = admin_client.admin_create_key(key_name, zone_id, user_id=user_id, is_admin=is_admin)
    if resp.ok and resp.result:
        raw_key = resp.result.get("raw_key") or resp.result.get("key")
        if raw_key:
            logger.info("Created zone key via RPC for zone=%s", zone_id)
            return raw_key

    # Fallback: direct SQLite insertion
    logger.info(
        "admin_create_key RPC failed (%s), falling back to direct DB insertion",
        resp.error.message if resp.error else "unknown",
    )
    return _create_key_direct(admin_client, zone_id, key_name, user_id, is_admin)


def _create_key_direct(
    admin_client: NexusClient,
    zone_id: str,
    name: str,
    user_id: str,
    is_admin: bool,
) -> str:
    """Create an API key by inserting directly into the database.

    Supports both PostgreSQL (via psycopg2) and SQLite (via sqlite3).
    The database backend is determined by NEXUS_DATABASE_URL or
    NEXUS_TEST_DB_PATH environment variables.
    """
    raw_key = _generate_raw_key(zone_id, user_id)
    key_hash = _hash_key(raw_key)
    key_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    db_url = _get_database_url()
    if db_url and db_url.startswith("postgresql"):
        return _insert_key_postgres(
            db_url, key_id, key_hash, user_id, zone_id, is_admin, name, now, raw_key
        )

    # SQLite fallback
    return _insert_key_sqlite(key_id, key_hash, user_id, zone_id, is_admin, name, now, raw_key)


def _insert_key_postgres(
    db_url: str,
    key_id: str,
    key_hash: str,
    user_id: str,
    zone_id: str,
    is_admin: bool,
    name: str,
    now: str,
    raw_key: str,
) -> str:
    """Insert an API key into a PostgreSQL database."""
    import psycopg2

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO api_keys
                   (key_id, key_hash, user_id, subject_type, subject_id,
                    zone_id, is_admin, inherit_permissions, name,
                    created_at, revoked)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    key_id,
                    key_hash,
                    user_id,
                    "user",
                    user_id,
                    zone_id,
                    int(is_admin),
                    0,
                    name,
                    now,
                    0,
                ),
            )
        conn.commit()
        logger.info("Created zone key via PostgreSQL for zone=%s key_id=%s", zone_id, key_id)
    finally:
        conn.close()

    return raw_key


def _insert_key_sqlite(
    key_id: str,
    key_hash: str,
    user_id: str,
    zone_id: str,
    is_admin: bool,
    name: str,
    now: str,
    raw_key: str,
) -> str:
    """Insert an API key into a SQLite database."""
    import sqlite3

    db_path = _find_db_path()
    if not db_path:
        raise RuntimeError(
            "Cannot create zone API key: database not found. "
            "Set NEXUS_DATABASE_URL (PostgreSQL) or NEXUS_TEST_DB_PATH (SQLite)."
        )

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO api_keys
               (key_id, key_hash, user_id, subject_type, subject_id,
                zone_id, is_admin, inherit_permissions, name,
                created_at, revoked)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key_id,
                key_hash,
                user_id,
                "user",
                user_id,
                zone_id,
                int(is_admin),
                0,
                name,
                now,
                0,
            ),
        )
        conn.commit()
        logger.info("Created zone key via SQLite for zone=%s key_id=%s", zone_id, key_id)
    finally:
        conn.close()

    return raw_key


def grant_zone_permission(
    zone_id: str,
    user_id: str,
    path: str,
    relation: str = "direct_owner",
) -> None:
    """Grant a ReBAC permission for a user in a specific zone.

    Inserts directly into the rebac_tuples table to grant the user
    a relation (default: direct_owner) on a path within a zone.

    Args:
        zone_id: Zone context for the grant.
        user_id: User to grant permission to.
        path: File/directory path to grant access to.
        relation: ReBAC relation (default: direct_owner).
    """
    tuple_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    params = (
        tuple_id,
        zone_id,
        zone_id,
        zone_id,
        "user",
        user_id,
        None,
        relation,
        "file",
        path,
        now,
        None,
        None,
    )

    db_url = _get_database_url()
    if db_url and db_url.startswith("postgresql"):
        import psycopg2

        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO rebac_tuples
                       (tuple_id, zone_id, subject_zone_id, object_zone_id,
                        subject_type, subject_id, subject_relation, relation,
                        object_type, object_id, created_at, expires_at, conditions)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT DO NOTHING""",
                    params,
                )
            conn.commit()
        finally:
            conn.close()
    else:
        import sqlite3

        db_path = _find_db_path()
        if not db_path:
            raise RuntimeError("Cannot grant permission: database not found.")

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO rebac_tuples
                   (tuple_id, zone_id, subject_zone_id, object_zone_id,
                    subject_type, subject_id, subject_relation, relation,
                    object_type, object_id, created_at, expires_at, conditions)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                params,
            )
            conn.commit()
        finally:
            conn.close()

    logger.info("Granted %s on %s for user=%s zone=%s", relation, path, user_id, zone_id)


def create_zone_direct(zone_id: str, name: str) -> bool:
    """Create a zone by inserting directly into the database.

    Supports both PostgreSQL and SQLite backends.
    Returns True if the zone was created, False if it already exists.
    """
    now = datetime.now(UTC).isoformat()

    db_url = _get_database_url()
    if db_url and db_url.startswith("postgresql"):
        import psycopg2

        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO zones
                       (zone_id, name, domain, description, settings,
                        phase, finalizers, deleted_at, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (zone_id) DO NOTHING""",
                    (zone_id, name, None, None, None, "Active", "[]", None, now, now),
                )
                created = cur.rowcount > 0
            conn.commit()
            if created:
                logger.info("Created zone via PostgreSQL: zone_id=%s", zone_id)
            return created
        finally:
            conn.close()

    import sqlite3

    db_path = _find_db_path()
    if not db_path:
        raise RuntimeError("Cannot create zone: database not found.")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO zones
               (zone_id, name, domain, description, settings,
                phase, finalizers, deleted_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (zone_id, name, None, None, None, "Active", "[]", None, now, now),
        )
        created = cursor.rowcount > 0
        conn.commit()
        if created:
            logger.info("Created zone via SQLite: zone_id=%s", zone_id)
        return created
    finally:
        conn.close()


def delete_zone_direct(zone_id: str) -> bool:
    """Delete a zone by updating its phase to Terminated.

    Also revokes all API keys for the zone and removes ReBAC grants,
    simulating a proper zone teardown cascade.

    Supports both PostgreSQL and SQLite backends.
    Returns True if the zone was terminated.
    """
    now = datetime.now(UTC).isoformat()

    db_url = _get_database_url()
    if db_url and db_url.startswith("postgresql"):
        import psycopg2

        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE zones SET phase = 'Terminated', deleted_at = %s, updated_at = %s
                       WHERE zone_id = %s AND phase != 'Terminated'""",
                    (now, now, zone_id),
                )
                deleted = cur.rowcount > 0
                cur.execute(
                    "UPDATE api_keys SET revoked = 1 WHERE zone_id = %s",
                    (zone_id,),
                )
                cur.execute(
                    "DELETE FROM rebac_tuples WHERE zone_id = %s",
                    (zone_id,),
                )
            conn.commit()
            if deleted:
                logger.info("Terminated zone via PostgreSQL: zone_id=%s", zone_id)
            return deleted
        finally:
            conn.close()

    import sqlite3

    db_path = _find_db_path()
    if not db_path:
        raise RuntimeError("Cannot delete zone: database not found.")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """UPDATE zones SET phase = 'Terminated', deleted_at = ?, updated_at = ?
               WHERE zone_id = ? AND phase != 'Terminated'""",
            (now, now, zone_id),
        )
        deleted = cursor.rowcount > 0
        conn.execute(
            "UPDATE api_keys SET revoked = 1 WHERE zone_id = ?",
            (zone_id,),
        )
        conn.execute(
            "DELETE FROM rebac_tuples WHERE zone_id = ?",
            (zone_id,),
        )
        conn.commit()
        if deleted:
            logger.info("Terminated zone via SQLite: zone_id=%s", zone_id)
        return deleted
    finally:
        conn.close()


def _get_database_url() -> str | None:
    """Get the PostgreSQL database URL from environment variables.

    Checks multiple env var names to support both the server convention
    (NEXUS_DATABASE_URL) and the test settings convention (NEXUS_TEST_DATABASE_URL).
    """
    import os

    return (
        os.environ.get("NEXUS_DATABASE_URL")
        or os.environ.get("NEXUS_TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )


def _find_db_path() -> str | None:
    """Find the SQLite database path for the running Nexus server."""
    import os
    from pathlib import Path

    # Check explicit env var first
    explicit = os.environ.get("NEXUS_TEST_DB_PATH")
    if explicit:
        return explicit

    # Common locations for local dev
    candidates = [
        Path.home() / "nexus" / "test-e2e-data" / "nexus.db",
        Path.home() / "nexus" / "nexus-data" / "nexus.db",
        Path("./nexus.db"),
    ]
    for path in candidates:
        if path.exists():
            return str(path)

    return None
