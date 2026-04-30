"""
Azure Cognitive Search client for resume indexing and hybrid retrieval.
Handles manual chunking, embedding via Azure OpenAI, and hybrid (vector + BM25) search.
"""

import hashlib
import logging
from typing import List, Dict

from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
)
from azure.core.credentials import AzureKeyCredential

logger = logging.getLogger(__name__)

EMBEDDING_DIMS = 1536   # text-embedding-ada-002
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return [c for c in chunks if c.strip()]


def extract_text(file_name: str, data: bytes) -> str:
    ext = file_name.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    if ext in ("docx", "doc"):
        import io
        from docx import Document
        return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Search client
# ---------------------------------------------------------------------------

class ResumeSearchClient:
    """
    Wraps Azure Cognitive Search for resume-level hybrid indexing and retrieval.

    On construction it auto-creates the index if it does not exist.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        index_name: str,
        openai_client,
        embedding_model: str,
    ):
        cred = AzureKeyCredential(api_key)
        self._client = SearchClient(endpoint=endpoint, index_name=index_name, credential=cred)
        self._index_client = SearchIndexClient(endpoint=endpoint, credential=cred)
        self._index_name = index_name
        self._openai = openai_client
        self._emb_model = embedding_model
        self._ensure_index()

    # ------------------------------------------------------------------
    # Index lifecycle
    # ------------------------------------------------------------------

    def _ensure_index(self):
        try:
            self._index_client.get_index(self._index_name)
            logger.info(f"Index '{self._index_name}' already exists.")
        except Exception:
            logger.info(f"Index '{self._index_name}' not found — creating.")
            self._create_index()

    def _create_index(self):
        index = SearchIndex(
            name=self._index_name,
            fields=[
                SearchField(
                    name="id",
                    type=SearchFieldDataType.String,
                    key=True,
                    filterable=True,
                ),
                SearchField(
                    name="resume_name",
                    type=SearchFieldDataType.String,
                    searchable=True,
                    filterable=True,
                    retrievable=True,
                ),
                SearchField(
                    name="chunk_text",
                    type=SearchFieldDataType.String,
                    searchable=True,
                    retrievable=True,
                ),
                SearchField(
                    name="chunk_index",
                    type=SearchFieldDataType.Int32,
                    retrievable=True,
                ),
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
                profiles=[
                    VectorSearchProfile(
                        name="hnsw-profile",
                        algorithm_configuration_name="hnsw-config",
                    )
                ],
            ),
        )
        self._index_client.create_index(index)
        logger.info(f"Created index '{self._index_name}'.")

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def embed(self, text: str) -> List[float]:
        return (
            self._openai.embeddings.create(
                model=self._emb_model,
                input=text[:8191],
            )
            .data[0]
            .embedding
        )

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_resume(self, resume_name: str, text: str):
        """Chunk, embed, and upload a resume; replaces any prior version."""
        self._delete_resume_docs(resume_name)
        chunks = chunk_text(text)
        docs = []
        for i, chunk in enumerate(chunks):
            doc_id = hashlib.md5(f"{resume_name}::{i}".encode()).hexdigest()
            docs.append(
                {
                    "id": doc_id,
                    "resume_name": resume_name,
                    "chunk_text": chunk,
                    "chunk_index": i,
                    "chunk_vector": self.embed(chunk),
                }
            )
        if docs:
            self._client.upload_documents(docs)
            logger.info(f"Indexed {len(docs)} chunk(s) for '{resume_name}'.")

    def _delete_resume_docs(self, resume_name: str):
        safe = resume_name.replace("'", "''")
        results = self._client.search(
            search_text="*",
            filter=f"resume_name eq '{safe}'",
            select=["id"],
            top=1000,
        )
        ids = [{"id": r["id"]} for r in results]
        if ids:
            self._client.delete_documents(ids)
            logger.info(f"Deleted {len(ids)} old chunk(s) for '{resume_name}'.")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def hybrid_search(self, query: str, top: int = 50) -> List[Dict]:
        """Hybrid BM25 + vector search. Returns list of chunk dicts."""
        vec = self.embed(query)
        results = self._client.search(
            search_text=query,
            vector_queries=[
                VectorizedQuery(
                    vector=vec,
                    k_nearest_neighbors=top,
                    fields="chunk_vector",
                )
            ],
            select=["resume_name", "chunk_text", "chunk_index"],
            top=top,
        )
        return [
            {
                "resume_name": r["resume_name"],
                "chunk_text": r["chunk_text"],
                "search_score": r["@search.score"],
            }
            for r in results
        ]
