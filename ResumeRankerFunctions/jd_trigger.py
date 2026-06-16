"""JD ingestion trigger.

Self-contained function module (no imports from ResumeRankerMCP/common).
Implements req_id parsing + keyword extraction + blob persistence.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time

import pdfplumber
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient
from docx import Document
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

OPENAI_ENDPOINT = os.getenv("OPENAI_ENDPOINT")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_VERSION = os.getenv("OPENAI_API_VERSION", "2025-01-01-preview")
OPENAI_DEPLOYMENT = os.getenv("OPENAI_DEPLOYMENT_NAME", "gpt-4o")

AUTH_TENANT_ID = os.getenv("AUTH_TENANT_ID")
AUTH_CLIENT_ID = os.getenv("AUTH_CLIENT_ID")
AUTH_CLIENT_SECRET = os.getenv("AUTH_CLIENT_SECRET")
AUTH_SCOPE = os.getenv("AUTH_SCOPE")

STORAGE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
KEYWORD_CONTAINER = os.getenv("JD_KEYWORDS_CONTAINER_NAME", "jds-keywords")

_credential = None
_token_cache = {"token": None, "expires_at": 0}


def _use_aad_auth() -> bool:
    return all([AUTH_TENANT_ID, AUTH_CLIENT_ID, AUTH_CLIENT_SECRET, AUTH_SCOPE])


def _get_credential() -> ClientSecretCredential:
    global _credential
    if _credential is None:
        _credential = ClientSecretCredential(
            tenant_id=AUTH_TENANT_ID,
            client_id=AUTH_CLIENT_ID,
            client_secret=AUTH_CLIENT_SECRET,
        )
    return _credential


def _get_aad_token() -> str:
    global _token_cache
    if _token_cache["token"] and time.time() < (_token_cache["expires_at"] - 300):
        return _token_cache["token"]
    token_result = _get_credential().get_token(AUTH_SCOPE)
    _token_cache["token"] = token_result.token
    _token_cache["expires_at"] = token_result.expires_on
    return token_result.token


def _get_blob_service() -> BlobServiceClient:
    if not STORAGE_CONN_STR:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not set")
    return BlobServiceClient.from_connection_string(STORAGE_CONN_STR)


def _ensure_container(name: str) -> None:
    try:
        _get_blob_service().create_container(name)
    except Exception:
        pass


def _extract_text(blob_name: str, data: bytes) -> str:
    ext = os.path.splitext(blob_name)[1].lower()
    if ext == ".txt":
        return data.decode("utf-8", errors="ignore")
    if ext in {".docx", ".doc"}:
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
    if ext == ".pdf":
        pages = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
        return "\n".join(pages).strip()
    return data.decode("utf-8", errors="ignore")


def _extract_keywords(jd_text: str) -> dict:
    if not OPENAI_ENDPOINT or not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_ENDPOINT and OPENAI_API_KEY are required for keyword extraction")

    kwargs = dict(
        azure_endpoint=OPENAI_ENDPOINT,
        api_key=OPENAI_API_KEY,
        api_version=OPENAI_API_VERSION,
    )
    if _use_aad_auth():
        token = _get_aad_token()
        kwargs["default_headers"] = {"Authorization": f"Bearer {token}"}

    client = AzureOpenAI(**kwargs)

    resp = client.chat.completions.create(
        model=OPENAI_DEPLOYMENT,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract key requirements from a job description and return JSON with exactly these keys: "
                    "jobTitle, technicalSkills, experience, certifications, education, location, domain. "
                    "Keep technicalSkills and certifications as arrays of short strings."
                ),
            },
            {"role": "user", "content": jd_text[:15000]},
        ],
        max_tokens=600,
        temperature=0,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def _already_processed(req_id: str, source_blob: str, source_etag: str) -> bool:
    blob_client = _get_blob_service().get_blob_client(KEYWORD_CONTAINER, f"{req_id}.json")
    if not blob_client.exists():
        return False
    props = blob_client.get_blob_properties()
    metadata = props.metadata or {}
    return metadata.get("source_blob") == source_blob and metadata.get("source_etag") == source_etag


def _persist_keywords(req_id: str, source_blob: str, source_etag: str, payload: dict) -> None:
    _ensure_container(KEYWORD_CONTAINER)
    blob_client = _get_blob_service().get_blob_client(KEYWORD_CONTAINER, f"{req_id}.json")
    blob_client.upload_blob(
        json.dumps(payload, indent=2).encode("utf-8"),
        overwrite=True,
        metadata={"req_id": req_id, "source_blob": source_blob, "source_etag": source_etag},
    )


def _req_id_from_blob_name(blob_name: str) -> str:
    base = os.path.basename(blob_name)
    return os.path.splitext(base)[0].strip()


def jd_blob_trigger(blob) -> None:
    """Process a newly uploaded JD blob and persist extracted keywords."""
    name = getattr(blob, "name", "<unknown>")
    size = getattr(blob, "length", None)
    etag = str(getattr(blob, "etag", ""))
    req_id = _req_id_from_blob_name(name)
    logger.info("jd_blob_trigger received blob name=%s req_id=%s size=%s", name, req_id, size)

    if not req_id:
        logger.warning("Skipping JD blob with empty req_id: %s", name)
        return

    if _already_processed(req_id=req_id, source_blob=name, source_etag=etag):
        logger.info("Keywords already extracted for req_id=%s and same source blob/etag. Skipping.", req_id)
        return

    raw = blob.read()
    jd_text = _extract_text(name, raw)
    if not jd_text.strip():
        logger.warning("Skipping JD keyword extraction because text is empty: %s", name)
        return

    keywords = _extract_keywords(jd_text)
    payload = {
        "req_id": req_id,
        "jd_name": os.path.basename(name),
        "jd_text": jd_text,
        "source_blob": name,
        "keywords": keywords,
    }
    _persist_keywords(req_id=req_id, source_blob=name, source_etag=etag, payload=payload)
    logger.info("Persisted JD keywords to %s/%s.json", KEYWORD_CONTAINER, req_id)
