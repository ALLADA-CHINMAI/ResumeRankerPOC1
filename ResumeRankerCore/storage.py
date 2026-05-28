"""
Azure Blob Storage helpers.
Also manages the 'resumes-parsed' container that caches extracted resume text
so ranking doesn't need to reassemble it from search index chunks.
"""

import os
import logging
from typing import List

from dotenv import load_dotenv
from core.clients import get_blob_service

load_dotenv()
logger = logging.getLogger(__name__)

# Blob container names from environment
RESUME_CONTAINER      = os.getenv("RESUME_CONTAINER_NAME", "resumes")
JD_CONTAINER          = os.getenv("JD_CONTAINER_NAME", "jds")
PARSED_TEXT_CONTAINER = "resumes-parsed"   # auto-created; stores plain-text cache for ranking

# Track which containers we've already ensured exist in this process
_ensured_containers: set = set()


def _ensure_container(name: str):
    """Create blob container if it doesn't exist. Cached per process to avoid repeated API calls."""
    if name in _ensured_containers:
        return
    try:
        get_blob_service().create_container(name)
        logger.info("Created blob container '%s'.", name)
    except Exception:
        pass  # ResourceExistsError is expected and safe to ignore
    _ensured_containers.add(name)


# ---------------------------------------------------------------------------
# Core blob helpers
# ---------------------------------------------------------------------------

def list_blobs(container: str) -> List[str]:
    """List all blob names in a container. Returns empty list if container is empty."""
    return [b.name for b in get_blob_service().get_container_client(container).list_blobs()]


def fetch_blob(container: str, name: str) -> bytes:
    """Download and return blob contents as bytes."""
    return (
        get_blob_service()
        .get_blob_client(container=container, blob=name)
        .download_blob()
        .readall()
    )


def upload_blob(container: str, name: str, data: bytes):
    """Upload bytes to a blob, overwriting any existing content."""
    get_blob_service().get_blob_client(container=container, blob=name).upload_blob(
        data, overwrite=True
    )


# ---------------------------------------------------------------------------
# Parsed-text cache
# Stored in 'resumes-parsed' container using the original filename as the blob key.
# Avoids re-assembling resume text from search index chunks during every ranking run.
# ---------------------------------------------------------------------------

def store_parsed_text(doc_name: str, text: str):
    """Cache extracted text for a resume after upload so ranking can fetch it cheaply."""
    _ensure_container(PARSED_TEXT_CONTAINER)
    # Blob names always use forward slashes (Azure convention — works on Windows too)
    upload_blob(PARSED_TEXT_CONTAINER, doc_name, text.encode("utf-8"))


def fetch_parsed_text(doc_name: str) -> str:
    """Fetch cached parsed text. Raises if the blob doesn't exist (caller should fall back)."""
    data = fetch_blob(PARSED_TEXT_CONTAINER, doc_name)
    return data.decode("utf-8")
