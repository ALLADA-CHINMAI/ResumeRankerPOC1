"""Resume ingestion trigger.

Self-contained function module (no imports from ResumeRankerMCP/common).
Implements parse/chunk/embed/index with idempotency.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import time
from datetime import datetime
from functools import lru_cache
from typing import List

import pdfplumber
import tiktoken
from azure.core.credentials import AzureKeyCredential
from azure.identity import ClientSecretCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    VectorSearch,
    VectorSearchProfile,
)
from azure.storage.blob import BlobServiceClient
from docx import Document
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

OPENAI_ENDPOINT = os.getenv("OPENAI_ENDPOINT")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_VERSION = os.getenv("OPENAI_API_VERSION", "2025-01-01-preview")
EMBEDDING_DEPLOYMENT = os.getenv("EMBEDDINGS_OPENAI_DEPLOYMENT_NAME", "text-embedding-ada-002")

AUTH_TENANT_ID = os.getenv("AUTH_TENANT_ID")
AUTH_CLIENT_ID = os.getenv("AUTH_CLIENT_ID")
AUTH_CLIENT_SECRET = os.getenv("AUTH_CLIENT_SECRET")
AUTH_SCOPE = os.getenv("AUTH_SCOPE")

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY")
RESUME_INDEX = os.getenv("AZURE_SEARCH_RESUME_INDEX_NAME", "resume_chunks")

STORAGE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
PARSED_TEXT_CONTAINER = os.getenv("PARSED_TEXT_CONTAINER_NAME", "resumes-parsed")

EMBEDDING_DIMS = 1536
CHUNK_TOKENS = 400
CHUNK_OVERLAP_TOKENS = 60
EMBED_BATCH_SIZE = 100

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


def _get_openai_client() -> AzureOpenAI:
    if not OPENAI_ENDPOINT or not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_ENDPOINT and OPENAI_API_KEY are required")
    kwargs = dict(
        azure_endpoint=OPENAI_ENDPOINT,
        api_key=OPENAI_API_KEY,
        api_version=OPENAI_API_VERSION,
    )
    if _use_aad_auth():
        token = _get_aad_token()
        kwargs["default_headers"] = {"Authorization": f"Bearer {token}"}
    return AzureOpenAI(**kwargs)


def _get_blob_service() -> BlobServiceClient:
    if not STORAGE_CONN_STR:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not set")
    return BlobServiceClient.from_connection_string(STORAGE_CONN_STR)


def _ensure_container(name: str) -> None:
    try:
        _get_blob_service().create_container(name)
    except Exception:
        pass


def _get_search_clients() -> tuple[SearchClient, SearchIndexClient]:
    if not SEARCH_ENDPOINT or not SEARCH_API_KEY:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY are required")
    cred = AzureKeyCredential(SEARCH_API_KEY)
    return (
        SearchClient(endpoint=SEARCH_ENDPOINT, index_name=RESUME_INDEX, credential=cred),
        SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=cred),
    )


def _ensure_resume_index() -> None:
    search_client, index_client = _get_search_clients()
    try:
        index_client.get_index(RESUME_INDEX)
        return
    except Exception:
        pass

    index = SearchIndex(
        name=RESUME_INDEX,
        fields=[
            SearchField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
            SearchField(
                name="resume_name",
                type=SearchFieldDataType.String,
                searchable=True,
                filterable=True,
                facetable=True,
                retrievable=True,
            ),
            SearchField(name="req_id", type=SearchFieldDataType.String, filterable=True, retrievable=True),
            SearchField(name="chunk_text", type=SearchFieldDataType.String, searchable=True, retrievable=True),
            SearchField(name="chunk_index", type=SearchFieldDataType.Int32, retrievable=True),
            SearchField(
                name="chunk_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=EMBEDDING_DIMS,
                vector_search_profile_name="hnsw-profile",
                retrievable=False,
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw-config")],
            profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-config")],
        ),
    )
    index_client.create_index(index)
    logger.info("Created resume index '%s'", RESUME_INDEX)


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


@lru_cache(maxsize=1)
def _encoder():
    return tiktoken.get_encoding("cl100k_base")


def _chunk_text(text: str, max_tokens: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP_TOKENS) -> List[str]:
    enc = _encoder()

    def _atoms(t: str, seps: List[str]) -> List[str]:
        if len(enc.encode(t)) <= max_tokens:
            return [t] if t.strip() else []
        sep, *rest = seps
        result: List[str] = []
        for part in t.split(sep):
            if not part.strip():
                continue
            if len(enc.encode(part)) <= max_tokens:
                result.append(part)
            elif rest:
                result.extend(_atoms(part, rest))
            else:
                toks = enc.encode(part)
                for i in range(0, len(toks), max_tokens):
                    chunk = enc.decode(toks[i : i + max_tokens]).strip()
                    if chunk:
                        result.append(chunk)
        return result

    atoms = _atoms(text, ["\n\n", "\n", ". ", " "])
    if not atoms:
        return []

    chunks: List[str] = []
    cur_toks: List[int] = []
    for atom in atoms:
        a_toks = enc.encode(atom)
        if cur_toks and len(cur_toks) + len(a_toks) > max_tokens:
            chunks.append(enc.decode(cur_toks).strip())
            cur_toks = cur_toks[-overlap:] + a_toks
        else:
            cur_toks.extend(a_toks)
    if cur_toks:
        chunks.append(enc.decode(cur_toks).strip())
    return [c for c in chunks if c.strip()]


def _embed_chunks(chunks: List[str]) -> List[List[float]]:
    client = _get_openai_client()
    embeddings: List[List[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[i : i + EMBED_BATCH_SIZE]
        resp = client.embeddings.create(model=EMBEDDING_DEPLOYMENT, input=batch)
        sorted_items = sorted(resp.data, key=lambda x: x.index)
        embeddings.extend(item.embedding for item in sorted_items)
    return embeddings


def _delete_existing_docs(doc_name: str) -> None:
    search_client, _index_client = _get_search_clients()
    safe = doc_name.replace("'", "''")
    results = search_client.search(search_text="*", filter=f"resume_name eq '{safe}'", select=["id"], top=1000)
    ids = [{"id": r["id"]} for r in results]
    if ids:
        search_client.delete_documents(ids)
        logger.info("Deleted %d existing chunks for '%s'", len(ids), doc_name)


def _index_resume(doc_name: str, text: str) -> None:
    _ensure_resume_index()
    _delete_existing_docs(doc_name)

    chunks = _chunk_text(text)
    if not chunks:
        logger.warning("No chunks extracted for resume '%s'.", doc_name)
        return

    vectors = _embed_chunks(chunks)
    docs = []
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        doc_id = hashlib.md5(f"{doc_name}::{i}".encode()).hexdigest()
        docs.append(
            {
                "id": doc_id,
                "resume_name": doc_name,
                "req_id": "",
                "chunk_text": chunk,
                "chunk_index": i,
                "chunk_vector": vec,
            }
        )

    search_client, _index_client = _get_search_clients()
    search_client.upload_documents(docs)
    logger.info("Indexed %d chunks for '%s'", len(docs), doc_name)


def _get_parsed_blob_client(doc_name: str):
    _ensure_container(PARSED_TEXT_CONTAINER)
    return _get_blob_service().get_blob_client(PARSED_TEXT_CONTAINER, doc_name)


def _already_processed(doc_name: str, source_blob: str, source_etag: str) -> bool:
    parsed_blob = _get_parsed_blob_client(doc_name)
    if not parsed_blob.exists():
        return False
    props = parsed_blob.get_blob_properties()
    metadata = props.metadata or {}
    return metadata.get("source_blob") == source_blob and metadata.get("source_etag") == source_etag


def _persist_parsed_text(doc_name: str, source_blob: str, source_etag: str, text: str) -> None:
    parsed_blob = _get_parsed_blob_client(doc_name)
    parsed_blob.upload_blob(
        text.encode("utf-8"),
        overwrite=True,
        metadata={
            "source_blob": source_blob,
            "source_etag": source_etag,
            "processed_at": datetime.utcnow().isoformat(),
        },
    )


def resume_blob_trigger(blob) -> None:
    """Process a newly uploaded resume blob and index it for ranking."""
    name = getattr(blob, "name", "<unknown>")
    size = getattr(blob, "length", None)
    etag = str(getattr(blob, "etag", ""))
    logger.info("resume_blob_trigger received blob name=%s size=%s", name, size)

    doc_name = os.path.basename(name)
    if not doc_name:
        logger.warning("Skipping resume with empty doc_name from blob: %s", name)
        return

    if _already_processed(doc_name=doc_name, source_blob=name, source_etag=etag):
        logger.info("Resume already processed for same blob+etag. Skipping: %s", doc_name)
        return

    raw = blob.read()
    text = _extract_text(doc_name, raw)
    if not text.strip():
        logger.warning("Skipping resume indexing because text is empty: %s", doc_name)
        return

    _index_resume(doc_name, text)
    _persist_parsed_text(doc_name=doc_name, source_blob=name, source_etag=etag, text=text)
    logger.info("Completed resume processing: %s", doc_name)
