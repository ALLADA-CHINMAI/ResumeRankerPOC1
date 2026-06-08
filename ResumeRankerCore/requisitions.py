"""
Requisition management — maps reqID to job descriptions.

Requisitions are the primary identifier for JDs. Each requisition has:
  - req_id (str): Human-readable unique ID (REQ-YYYY-MM-NNN format, provided by HR/HRIS)
  - jd_blob_path (str): Path in Azure Blob where the JD file is stored
  - title (str): Job title
  - status (str): 'active' or 'archived'
  - created_date (str): ISO format timestamp
  - created_by (str): User who created the requisition
  - updated_date (str): ISO format timestamp

Metadata is stored in: metadata/requisitions.json
Format: {requisitions: [{req_id, jd_blob_path, title, status, created_date, created_by, updated_date}]}
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional
from pathlib import Path

from ResumeRankerCore.clients import get_blob_service
from ResumeRankerCore.storage import (
    JD_CONTAINER,
    fetch_blob,
    upload_blob,
    list_blobs,
)

logger = logging.getLogger(__name__)

METADATA_CONTAINER = "metadata"
METADATA_BLOB_PATH = "requisitions.json"

# ReqID format: REQ-YYYY-MM-NNN (e.g., REQ-2024-12-001)
REQ_ID_PATTERN = re.compile(r"^REQ-\d{4}-\d{2}-\d{3}$")


def _ensure_metadata_container():
    """Create metadata container if it doesn't exist."""
    try:
        get_blob_service().create_container(METADATA_CONTAINER)
        logger.info("Created metadata container.")
    except Exception:
        pass  # ResourceExistsError is expected


def _read_metadata() -> Dict:
    """Load requisitions metadata from blob. Returns empty metadata if not found."""
    try:
        data = fetch_blob(METADATA_CONTAINER, METADATA_BLOB_PATH)
        return json.loads(data.decode("utf-8"))
    except Exception as e:
        logger.debug("Metadata blob not found or invalid (%s), starting fresh.", e)
        return {"requisitions": []}


def _write_metadata(metadata: Dict):
    """Write requisitions metadata to blob."""
    _ensure_metadata_container()
    data = json.dumps(metadata, indent=2).encode("utf-8")
    upload_blob(METADATA_CONTAINER, METADATA_BLOB_PATH, data)
    logger.info("Updated metadata blob.")


# ---------------------------------------------------------------------------
# ReqID Validation
# ---------------------------------------------------------------------------


def validate_req_id(req_id: str) -> bool:
    """Check if req_id matches the expected format (REQ-YYYY-MM-NNN)."""
    return bool(REQ_ID_PATTERN.match(req_id))


def req_id_exists(req_id: str) -> bool:
    """Check if a requisition with this req_id already exists."""
    metadata = _read_metadata()
    return any(r["req_id"] == req_id for r in metadata.get("requisitions", []))


# ---------------------------------------------------------------------------
# CRUD Operations
# ---------------------------------------------------------------------------


def create_requisition(
    req_id: str,
    title: str,
    jd_file_contents: bytes,
    user_id: str,
    file_extension: str = "pdf",
) -> Dict:
    """
    Create a new requisition. Uploads JD file and stores metadata.

    Args:
        req_id:             Human-readable requisition ID (REQ-YYYY-MM-NNN format)
        title:              Job title (e.g., "Senior Software Engineer")
        jd_file_contents:   Raw bytes of the JD file (PDF, DOCX, etc.)
        user_id:            User creating the requisition (email or user ID)
        file_extension:     File extension without dot (default: "pdf")

    Returns:
        Created requisition dict with all metadata fields

    Raises:
        ValueError: If req_id format is invalid or already exists
    """
    if not validate_req_id(req_id):
        raise ValueError(
            f"Invalid req_id format: '{req_id}'. "
            f"Must match pattern REQ-YYYY-MM-NNN (e.g., REQ-2024-12-001)"
        )

    if req_id_exists(req_id):
        raise ValueError(f"Requisition '{req_id}' already exists.")

    # Store JD file in blob
    jd_blob_path = f"{req_id}.{file_extension}".lower()
    upload_blob(JD_CONTAINER, jd_blob_path, jd_file_contents)
    logger.info("Uploaded JD file for %s to %s/%s", req_id, JD_CONTAINER, jd_blob_path)

    # Create metadata entry
    now = datetime.now(timezone.utc).isoformat()
    requisition = {
        "req_id": req_id,
        "jd_blob_path": jd_blob_path,
        "title": title,
        "status": "active",
        "created_date": now,
        "created_by": user_id,
        "updated_date": now,
    }

    # Append to metadata
    metadata = _read_metadata()
    metadata["requisitions"].append(requisition)
    _write_metadata(metadata)

    logger.info("Created requisition: %s - %s", req_id, title)
    return requisition


def get_requisition(req_id: str) -> Optional[Dict]:
    """Fetch requisition metadata by req_id. Returns None if not found."""
    metadata = _read_metadata()
    for req in metadata.get("requisitions", []):
        if req["req_id"] == req_id:
            return req
    logger.warning("Requisition not found: %s", req_id)
    return None


def list_requisitions(status: Optional[str] = None) -> List[Dict]:
    """
    List all requisitions, optionally filtered by status.

    Args:
        status: Optional filter — "active" or "archived". If None, returns all.

    Returns:
        List of requisition dicts, sorted by created_date (newest first)
    """
    metadata = _read_metadata()
    reqs = metadata.get("requisitions", [])

    if status:
        reqs = [r for r in reqs if r.get("status") == status]

    return sorted(reqs, key=lambda r: r.get("created_date", ""), reverse=True)


def update_requisition_status(req_id: str, status: str):
    """Update requisition status (e.g., 'active' to 'archived')."""
    if status not in ("active", "archived"):
        raise ValueError(f"Invalid status: '{status}'. Must be 'active' or 'archived'.")

    metadata = _read_metadata()
    requisitions = metadata.get("requisitions", [])

    for req in requisitions:
        if req["req_id"] == req_id:
            req["status"] = status
            req["updated_date"] = datetime.now(timezone.utc).isoformat()
            _write_metadata(metadata)
            logger.info("Updated requisition '%s' status to '%s'", req_id, status)
            return

    raise ValueError(f"Requisition not found: {req_id}")


# ---------------------------------------------------------------------------
# JD Text Retrieval
# ---------------------------------------------------------------------------


def get_jd_text_by_req(req_id: str) -> str:
    """
    Fetch JD text by requisition ID.

    Args:
        req_id: Requisition ID (e.g., "REQ-2024-12-001")

    Returns:
        Full text of the job description

    Raises:
        ValueError: If requisition or JD file not found
    """
    req = get_requisition(req_id)
    if not req:
        raise ValueError(f"Requisition not found: {req_id}")

    jd_blob_path = req["jd_blob_path"]
    try:
        jd_bytes = fetch_blob(JD_CONTAINER, jd_blob_path)
        # Try UTF-8 first, fall back to latin-1 if that fails
        try:
            return jd_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return jd_bytes.decode("latin-1")
    except Exception as e:
        raise ValueError(
            f"Failed to fetch JD file '{jd_blob_path}' for requisition '{req_id}': {e}"
        )


# ---------------------------------------------------------------------------
# Migration: Auto-convert old filename-based JDs to reqIDs (future use)
# ---------------------------------------------------------------------------


def migrate_old_jd_to_req(old_filename: str, req_id: str, title: str, user_id: str = "migrated"):
    """
    Auto-convert an old JD that was referenced by filename to reqID system.

    Useful if you have JDs already in the blob that used filename-based lookup.
    This helper maps them to reqIDs retroactively.

    Args:
        old_filename: Original filename (e.g., "SWE-2024-001.pdf")
        req_id:       New requisition ID (e.g., "REQ-2024-12-001")
        title:        Job title for metadata
        user_id:      User ID to record as migrator (default: "migrated")

    Raises:
        ValueError: If old file not found or req_id already exists
    """
    try:
        jd_contents = fetch_blob(JD_CONTAINER, old_filename)
    except Exception as e:
        raise ValueError(f"Old JD file not found: {old_filename}. Error: {e}")

    # Extract file extension
    ext = Path(old_filename).suffix.lstrip(".") or "pdf"

    # Create requisition with the file contents
    return create_requisition(req_id, title, jd_contents, user_id, ext)
