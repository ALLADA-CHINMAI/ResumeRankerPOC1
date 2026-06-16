"""
Azure Blob Storage helpers.
"""

import os
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import List

from dotenv import load_dotenv
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from ResumeRankerMCP.common.clients import get_blob_service

load_dotenv()

# Blob container names from environment
RESUME_CONTAINER      = os.getenv("RESUME_CONTAINER_NAME", "resumes")
JD_CONTAINER          = os.getenv("JD_CONTAINER_NAME", "jds")
PARSED_TEXT_CONTAINER = "resumes-parsed"   # auto-created; stores plain-text cache for ranking


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
    from ResumeRankerMCP.common.clients import STORAGE_CONN_STR
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
