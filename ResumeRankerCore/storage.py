"""
Azure Blob Storage helpers.
Also manages the 'resumes-parsed' container that caches extracted resume text
so ranking doesn't need to reassemble it from search index chunks.
"""

import os
import logging
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import List

from dotenv import load_dotenv
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from ResumeRankerCore.clients import get_blob_service
import re

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


# ---------------------------------------------------------------------------
# JD identifier helpers (simple 4-digit numeric prefix, no external metadata)
# ---------------------------------------------------------------------------


def _parse_jd_prefix(blob_name: str) -> int | None:
    """If blob_name starts with a 4-digit prefix like '0004_', return int, else None."""
    m = re.match(r"^(\d{4})_", blob_name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def get_next_jd_id() -> str:
    """Scan existing JD blobs and return the next zero-padded 4-digit id as a string."""
    blobs = list_blobs(JD_CONTAINER)
    max_id = 0
    for b in blobs:
        p = _parse_jd_prefix(b)
        if p is not None and p > max_id:
            max_id = p
    next_id = max_id + 1
    return f"{next_id:04d}"


def list_jds_formatted() -> List[str]:
    """Return a list of display strings for JDs like '0004(original.pdf)'."""
    blobs = list_blobs(JD_CONTAINER)
    formatted = []
    for b in blobs:
        p = _parse_jd_prefix(b)
        if p is not None:
            orig = b.split("_", 1)[1] if "_" in b else b
            formatted.append(f"{p:04d}({orig})")
        else:
            # blob without prefix — show as-is
            formatted.append(b)
    return formatted


def resolve_jd_blob(identifier: str) -> str:
    """
    Resolve an identifier (either a 4-digit id like '0004' or a blob name) to the actual JD blob name.
    Raises ValueError if not found.
    """
    blobs = list_blobs(JD_CONTAINER)
    # If exact match to blob name
    if identifier in blobs:
        return identifier
    # If identifier formatted like '0004(original.pdf)', extract numeric prefix
    m = re.match(r"^(\d{4})\(|^(\d{4})$", identifier)
    id_num = None
    if m:
        id_num = m.group(1) or m.group(2)

    if id_num:
        prefix = f"{id_num}_"
        for b in blobs:
            if b.startswith(prefix):
                return b

    # Try to match by display string
    for b in blobs:
        if identifier == f"{b}":
            return b

    raise ValueError(f"JD not found for identifier: {identifier}")


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


_INLINE_TYPES = {
    ".pdf":  "application/pdf",
    ".txt":  "text/plain",
}
_OFFICE_EXTS = {".docx", ".doc"}


def get_blob_url(container: str, name: str, expiry_minutes: int = 60) -> str:
    """Return a URL that opens the blob for viewing (not downloading) in a browser tab.

    - PDF / TXT  → direct SAS URL with inline Content-Disposition (browser renders natively).
    - DOCX / DOC → Microsoft Office Online viewer wrapping the SAS URL (no local install needed).
    - Other      → plain SAS URL (browser default behaviour).
    """
    from ResumeRankerCore.clients import STORAGE_CONN_STR
    parts = {}
    for segment in STORAGE_CONN_STR.split(";"):
        if "=" in segment:
            k, _, v = segment.partition("=")
            parts[k] = v
    account_name   = parts.get("AccountName", "")
    account_key    = parts.get("AccountKey", "")
    endpoint_suffix = parts.get("EndpointSuffix", "core.windows.net")

    ext = os.path.splitext(name)[1].lower()
    content_type = _INLINE_TYPES.get(ext)

    sas_kwargs: dict = dict(
        account_name=account_name,
        container_name=container,
        blob_name=name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
    )
    if content_type:
        sas_kwargs["content_type"] = content_type
        sas_kwargs["content_disposition"] = "inline"

    sas = generate_blob_sas(**sas_kwargs)
    encoded_name = urllib.parse.quote(name, safe="/")
    blob_url = f"https://{account_name}.blob.{endpoint_suffix}/{container}/{encoded_name}?{sas}"

    if ext in _OFFICE_EXTS:
        return "https://view.officeapps.live.com/op/view.aspx?src=" + urllib.parse.quote(blob_url, safe="")

    return blob_url
