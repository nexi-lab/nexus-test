"""Upload E2E tests — chunked TUS upload, resume interrupted upload.

Tests: upload/001-002
Covers: tus.io v1.0.0 compliant chunked upload, resume after interruption

Reference: TEST_PLAN.md §4.5

Infrastructure: docker-compose.demo.yml (standalone)

TUS Upload API endpoints:
    OPTIONS /api/v2/uploads         — Server capabilities
    POST    /api/v2/uploads         — Create upload session
    PATCH   /api/v2/uploads/{id}    — Upload chunk
    HEAD    /api/v2/uploads/{id}    — Get upload offset (for resume)
    DELETE  /api/v2/uploads/{id}    — Terminate upload
"""

from __future__ import annotations

import base64

import pytest

from tests.helpers.api_client import NexusClient

TUS_HEADERS = {"Tus-Resumable": "1.0.0"}


@pytest.mark.auto
@pytest.mark.upload
class TestUpload:
    """TUS chunked upload tests."""

    def test_chunked_upload(self, nexus: NexusClient, unique_path: str) -> None:
        """upload/001: Chunked upload (TUS) — file assembled from chunks.

        Creates a TUS upload session, sends the file content in one or more
        chunks, and verifies the upload completes successfully. Then reads
        the file back to verify the content was assembled correctly.
        """
        # Step 0: Check server capabilities
        options_resp = nexus.api_options("/api/v2/uploads")
        if options_resp.status_code in (404, 405):
            pytest.skip("TUS upload endpoint not available on this server")

        # Accept 200 or 204 for OPTIONS
        if options_resp.status_code in (200, 204):
            tus_version = options_resp.headers.get("Tus-Version", "")
            if tus_version:
                assert "1.0.0" in tus_version, (
                    f"Server should support TUS 1.0.0, got: {tus_version}"
                )

        # Step 1: Prepare test content
        target_path = f"{unique_path}/upload-chunked.txt"
        content = "Hello from TUS upload! " * 10  # ~230 bytes
        content_bytes = content.encode("utf-8")
        total_size = len(content_bytes)

        # Encode metadata per TUS spec: "key base64value"
        filename_b64 = base64.b64encode(target_path.encode()).decode()
        metadata = f"path {filename_b64}"

        # Step 2: Create upload session
        create_headers = {
            **TUS_HEADERS,
            "Upload-Length": str(total_size),
            "Upload-Metadata": metadata,
            "Content-Type": "application/offset+octet-stream",
        }
        create_resp = nexus.api_post("/api/v2/uploads", headers=create_headers, content=b"")
        if create_resp.status_code in (404, 405):
            pytest.skip("TUS upload creation not available")

        assert create_resp.status_code == 201, (
            f"Upload session creation failed: {create_resp.status_code} {create_resp.text[:200]}"
        )

        # Extract upload URL from Location header
        location = create_resp.headers.get("Location", "")
        assert location, "Response missing Location header with upload URL"

        # Extract upload ID from the location URL
        upload_id = location.rstrip("/").split("/")[-1]
        upload_path = f"/api/v2/uploads/{upload_id}"

        # Step 3: Upload content in two chunks
        chunk_size = total_size // 2
        chunk1 = content_bytes[:chunk_size]
        chunk2 = content_bytes[chunk_size:]

        # Send chunk 1
        patch1_headers = {
            **TUS_HEADERS,
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        }
        patch1_resp = nexus.api_patch(upload_path, headers=patch1_headers, content=chunk1)
        assert patch1_resp.status_code in (200, 204), (
            f"Chunk 1 upload failed: {patch1_resp.status_code} {patch1_resp.text[:200]}"
        )

        # Verify offset was updated
        offset_after_1 = int(patch1_resp.headers.get("Upload-Offset", "0"))
        assert offset_after_1 == chunk_size, (
            f"Offset should be {chunk_size} after chunk 1, got {offset_after_1}"
        )

        # Send chunk 2
        patch2_headers = {
            **TUS_HEADERS,
            "Upload-Offset": str(chunk_size),
            "Content-Type": "application/offset+octet-stream",
        }
        patch2_resp = nexus.api_patch(upload_path, headers=patch2_headers, content=chunk2)
        assert patch2_resp.status_code in (200, 204), (
            f"Chunk 2 upload failed: {patch2_resp.status_code} {patch2_resp.text[:200]}"
        )

        # Verify final offset equals total size
        final_offset = int(patch2_resp.headers.get("Upload-Offset", "0"))
        assert final_offset == total_size, (
            f"Final offset should be {total_size}, got {final_offset}"
        )

        # Step 4: Verify the assembled file content
        read_resp = nexus.read_file(target_path)
        if read_resp.ok:
            assert read_resp.content_str == content, (
                f"Uploaded content mismatch: expected {len(content)} chars, "
                f"got {len(read_resp.content_str)} chars"
            )

    def test_resume_interrupted_upload(self, nexus: NexusClient, unique_path: str) -> None:
        """upload/002: Resume interrupted upload — completes from checkpoint.

        Creates a TUS upload, sends a partial chunk, then uses HEAD to
        discover the current offset and resumes the upload from that point.
        """
        target_path = f"{unique_path}/upload-resume.txt"
        content = "Resume test content. " * 20  # ~420 bytes
        content_bytes = content.encode("utf-8")
        total_size = len(content_bytes)

        # Encode metadata
        filename_b64 = base64.b64encode(target_path.encode()).decode()
        metadata = f"path {filename_b64}"

        # Step 1: Create upload session
        create_headers = {
            **TUS_HEADERS,
            "Upload-Length": str(total_size),
            "Upload-Metadata": metadata,
            "Content-Type": "application/offset+octet-stream",
        }
        create_resp = nexus.api_post("/api/v2/uploads", headers=create_headers, content=b"")
        if create_resp.status_code in (404, 405):
            pytest.skip("TUS upload creation not available")

        assert create_resp.status_code == 201, (
            f"Upload creation failed: {create_resp.status_code} {create_resp.text[:200]}"
        )

        location = create_resp.headers.get("Location", "")
        upload_id = location.rstrip("/").split("/")[-1]
        upload_path = f"/api/v2/uploads/{upload_id}"

        # Step 2: Upload partial content (first third)
        partial_size = total_size // 3
        partial_chunk = content_bytes[:partial_size]

        patch_headers = {
            **TUS_HEADERS,
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        }
        patch_resp = nexus.api_patch(upload_path, headers=patch_headers, content=partial_chunk)
        assert patch_resp.status_code in (200, 204), (
            f"Partial upload failed: {patch_resp.status_code} {patch_resp.text[:200]}"
        )

        # Step 3: Simulate interruption — use HEAD to discover current offset
        head_resp = nexus.api_head(upload_path, headers=TUS_HEADERS)
        assert head_resp.status_code == 200, (
            f"HEAD for upload offset failed: {head_resp.status_code}"
        )

        current_offset = int(head_resp.headers.get("Upload-Offset", "0"))
        assert current_offset == partial_size, (
            f"HEAD should report offset {partial_size}, got {current_offset}"
        )

        # Step 4: Resume from the discovered offset
        remaining = content_bytes[current_offset:]
        resume_headers = {
            **TUS_HEADERS,
            "Upload-Offset": str(current_offset),
            "Content-Type": "application/offset+octet-stream",
        }
        resume_resp = nexus.api_patch(upload_path, headers=resume_headers, content=remaining)
        assert resume_resp.status_code in (200, 204), (
            f"Resume upload failed: {resume_resp.status_code} {resume_resp.text[:200]}"
        )

        final_offset = int(resume_resp.headers.get("Upload-Offset", "0"))
        assert final_offset == total_size, (
            f"Final offset after resume should be {total_size}, got {final_offset}"
        )

        # Step 5: Verify file content
        read_resp = nexus.read_file(target_path)
        if read_resp.ok:
            assert read_resp.content_str == content, "Resumed upload content mismatch"
