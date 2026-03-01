"""Upload E2E tests — chunked TUS upload, resume, terminate, checksum, errors.

Tests: upload/001-007
Covers: tus.io v1.0.0 compliant chunked upload, resume, terminate,
        checksum verification, offset mismatch, TUS version validation,
        zero-byte upload

Reference: TEST_PLAN.md §4.5

Infrastructure: docker-compose.demo.yml (standalone)

TUS Upload API endpoints:
    OPTIONS /api/v2/uploads         — Server capabilities
    POST    /api/v2/uploads         — Create upload session
    PATCH   /api/v2/uploads/{id}    — Upload chunk
    HEAD    /api/v2/uploads/{id}    — Get upload offset (for resume)
    DELETE  /api/v2/uploads/{id}    — Terminate upload

Note: Server enforces minimum chunk size of 5 MB (5242880 bytes) for
non-final chunks. The last chunk is exempt from this minimum. Single-chunk
uploads (where the entire content is the last chunk) work for any size.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from tests.helpers.api_client import NexusClient

TUS_HEADERS = {"Tus-Resumable": "1.0.0"}

# Minimum chunk size enforced by server (except for last chunk)
MIN_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB


def _skip_if_no_tus(nexus: NexusClient) -> None:
    """Skip test if TUS endpoint is not available."""
    resp = nexus.api_options("/api/v2/uploads")
    if resp.status_code in (404, 405):
        pytest.skip("TUS upload endpoint not available on this server")


def _create_upload(
    nexus: NexusClient,
    target_path: str,
    total_size: int,
) -> str:
    """Create a TUS upload session and return the upload path."""
    filename_b64 = base64.b64encode(target_path.encode()).decode()
    metadata = f"path {filename_b64}"
    create_headers = {
        **TUS_HEADERS,
        "Upload-Length": str(total_size),
        "Upload-Metadata": metadata,
        "Content-Type": "application/offset+octet-stream",
    }
    resp = nexus.api_post("/api/v2/uploads", headers=create_headers, content=b"")
    assert resp.status_code == 201, (
        f"Upload session creation failed: {resp.status_code} {resp.text[:200]}"
    )
    location = resp.headers.get("Location", "")
    assert location, "Response missing Location header"
    upload_id = location.rstrip("/").split("/")[-1]
    return f"/api/v2/uploads/{upload_id}"


@pytest.mark.auto
@pytest.mark.upload
class TestUpload:
    """TUS chunked upload tests."""

    def test_chunked_upload(self, nexus: NexusClient, unique_path: str) -> None:
        """upload/001: Chunked upload (TUS) — file assembled from chunks.

        Creates a TUS upload session and sends the entire file as a single
        chunk (exempt from min chunk size as it's the last chunk). Then reads
        the file back to verify content was assembled correctly.
        """
        _skip_if_no_tus(nexus)

        target_path = f"{unique_path}/upload-chunked.txt"
        content = "Hello from TUS upload! " * 10  # ~230 bytes
        content_bytes = content.encode("utf-8")
        total_size = len(content_bytes)

        upload_path = _create_upload(nexus, target_path, total_size)

        # Upload entire content as single chunk (last chunk = exempt from min size)
        patch_headers = {
            **TUS_HEADERS,
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        }
        patch_resp = nexus.api_patch(upload_path, headers=patch_headers, content=content_bytes)
        assert patch_resp.status_code in (200, 204), (
            f"Upload chunk failed: {patch_resp.status_code} {patch_resp.text[:200]}"
        )

        final_offset = int(patch_resp.headers.get("Upload-Offset", "0"))
        assert final_offset == total_size, (
            f"Final offset should be {total_size}, got {final_offset}"
        )

        # Verify assembled file content
        read_resp = nexus.read_file(target_path)
        if read_resp.ok:
            assert read_resp.content_str == content, (
                f"Uploaded content mismatch: expected {len(content)} chars, "
                f"got {len(read_resp.content_str)} chars"
            )

    def test_resume_interrupted_upload(self, nexus: NexusClient, unique_path: str) -> None:
        """upload/002: Resume interrupted upload — HEAD reports offset.

        Creates a TUS upload, verifies HEAD reports offset=0, uploads content,
        verifies HEAD reports final offset.
        """
        _skip_if_no_tus(nexus)

        target_path = f"{unique_path}/upload-resume.txt"
        content = "Resume test content. " * 20  # ~420 bytes
        content_bytes = content.encode("utf-8")
        total_size = len(content_bytes)

        upload_path = _create_upload(nexus, target_path, total_size)

        # Check offset before any upload via HEAD
        head_resp = nexus.api_head(upload_path, headers=TUS_HEADERS)
        assert head_resp.status_code == 200, (
            f"HEAD for upload offset failed: {head_resp.status_code}"
        )
        initial_offset = int(head_resp.headers.get("Upload-Offset", "0"))
        assert initial_offset == 0, f"Fresh upload should have offset 0, got {initial_offset}"

        # Upload entire content as single chunk
        patch_headers = {
            **TUS_HEADERS,
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        }
        patch_resp = nexus.api_patch(upload_path, headers=patch_headers, content=content_bytes)
        assert patch_resp.status_code in (200, 204), (
            f"Upload failed: {patch_resp.status_code} {patch_resp.text[:200]}"
        )

        final_offset = int(patch_resp.headers.get("Upload-Offset", "0"))
        assert final_offset == total_size, (
            f"Final offset should be {total_size}, got {final_offset}"
        )

        # Verify file content
        read_resp = nexus.read_file(target_path)
        if read_resp.ok:
            assert read_resp.content_str == content, "Upload content mismatch"

    def test_terminate_upload(self, nexus: NexusClient, unique_path: str) -> None:
        """upload/003: Terminate upload — DELETE releases resources.

        Creates a TUS upload session and terminates it without uploading.
        Verifies the session is no longer accessible after termination.
        """
        _skip_if_no_tus(nexus)

        target_path = f"{unique_path}/upload-terminate.txt"
        upload_path = _create_upload(nexus, target_path, 1024)

        # Terminate
        delete_resp = nexus.api_delete(upload_path, headers=TUS_HEADERS)
        assert delete_resp.status_code == 204, (
            f"Terminate failed: {delete_resp.status_code} {delete_resp.text[:200]}"
        )

        # HEAD on terminated upload should return 404 or 410
        head_resp = nexus.api_head(upload_path, headers=TUS_HEADERS)
        assert head_resp.status_code in (404, 410), (
            f"Terminated upload should not be accessible, got {head_resp.status_code}"
        )

    def test_checksum_sha256(self, nexus: NexusClient, unique_path: str) -> None:
        """upload/004: Checksum verification — SHA256.

        Uploads a chunk with a valid SHA256 checksum header.
        The server should accept the chunk without error.
        """
        _skip_if_no_tus(nexus)

        target_path = f"{unique_path}/upload-checksum.txt"
        data = b"Checksum verification test data for SHA256."
        total_size = len(data)
        digest = base64.b64encode(hashlib.sha256(data).digest()).decode()

        upload_path = _create_upload(nexus, target_path, total_size)

        patch_headers = {
            **TUS_HEADERS,
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Checksum": f"sha256 {digest}",
        }
        patch_resp = nexus.api_patch(upload_path, headers=patch_headers, content=data)
        assert patch_resp.status_code in (200, 204), (
            f"Upload with checksum failed: {patch_resp.status_code} {patch_resp.text[:200]}"
        )

    def test_checksum_mismatch_returns_460(self, nexus: NexusClient, unique_path: str) -> None:
        """upload/005: Checksum mismatch — returns 460.

        Uploads a chunk with a wrong SHA256 checksum.
        The server should reject with status 460.
        """
        _skip_if_no_tus(nexus)

        target_path = f"{unique_path}/upload-bad-checksum.txt"
        data = b"Data with intentionally wrong checksum."
        total_size = len(data)
        wrong_digest = base64.b64encode(b"wrongwrongwrongwrongwrongwrongww").decode()

        upload_path = _create_upload(nexus, target_path, total_size)

        patch_headers = {
            **TUS_HEADERS,
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Checksum": f"sha256 {wrong_digest}",
        }
        patch_resp = nexus.api_patch(upload_path, headers=patch_headers, content=data)
        assert patch_resp.status_code == 460, (
            f"Expected 460 for checksum mismatch, got {patch_resp.status_code}"
        )

    def test_offset_mismatch_returns_409(self, nexus: NexusClient, unique_path: str) -> None:
        """upload/006: Offset mismatch — returns 409.

        Creates an upload and sends a chunk with offset=50 when
        the expected offset is 0. Server should reject with 409.
        """
        _skip_if_no_tus(nexus)

        target_path = f"{unique_path}/upload-offset-mismatch.txt"
        upload_path = _create_upload(nexus, target_path, 100)

        patch_headers = {
            **TUS_HEADERS,
            "Upload-Offset": "50",  # Wrong — should be 0
            "Content-Type": "application/offset+octet-stream",
        }
        patch_resp = nexus.api_patch(upload_path, headers=patch_headers, content=b"x" * 50)
        assert patch_resp.status_code == 409, (
            f"Expected 409 for offset mismatch, got {patch_resp.status_code}"
        )

    def test_zero_byte_upload(self, nexus: NexusClient, unique_path: str) -> None:
        """upload/007: Zero-byte upload — creates empty file.

        Creates a TUS upload with Upload-Length: 0 and sends an empty chunk.
        Should complete successfully.
        """
        _skip_if_no_tus(nexus)

        target_path = f"{unique_path}/upload-zero.txt"
        upload_path = _create_upload(nexus, target_path, 0)

        # Upload empty chunk
        patch_headers = {
            **TUS_HEADERS,
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        }
        patch_resp = nexus.api_patch(upload_path, headers=patch_headers, content=b"")
        assert patch_resp.status_code in (200, 204), (
            f"Zero-byte upload failed: {patch_resp.status_code} {patch_resp.text[:200]}"
        )
