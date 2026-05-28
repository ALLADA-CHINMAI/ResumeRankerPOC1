"""
Azure Cognitive Search client for chunked document indexing and hybrid retrieval.
Works for both resumes and JDs — parameterized via name_field.
Index is auto-created on first use; no manual portal setup needed.
"""

import hashlib
import logging
from typing import List, Dict, Optional

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

from ResumeRankerCore.text_utils import chunk_text

logger = logging.getLogger(__name__)

EMBEDDING_DIMS = 1536   # text-embedding-ada-002
_EMBED_BATCH_SIZE = 100  # safe batch limit for Azure OpenAI embeddings API


class DocumentSearchClient:
    """
    Wraps Azure Cognitive Search for hybrid (BM25 + vector) indexing and retrieval.

    Pass name_field="resume_name" for resumes, name_field="jd_name" for JDs.
    Both use the same schema — only the document-name field differs.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        index_name: str,
        openai_client,
        embedding_model: str,
        name_field: str = "resume_name",
    ):
        cred = AzureKeyCredential(api_key)
        self._client = SearchClient(endpoint=endpoint, index_name=index_name, credential=cred)
        self._index_client = SearchIndexClient(endpoint=endpoint, credential=cred)
        self._index_name = index_name
        self._openai = openai_client
        self._emb_model = embedding_model
        self._name_field = name_field
        self._ensure_index()

    # ------------------------------------------------------------------
    # Index lifecycle
    # ------------------------------------------------------------------

    def _ensure_index(self):
        try:
            self._index_client.get_index(self._index_name)
            logger.info("Index '%s' already exists.", self._index_name)
        except Exception:
            logger.info("Index '%s' not found — creating.", self._index_name)
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
                    name=self._name_field,
                    type=SearchFieldDataType.String,
                    searchable=True,
                    filterable=True,
                    facetable=True,   # enables efficient list_documents() via facets
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
                profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-config")],
            ),
        )
        self._index_client.create_index(index)
        logger.info("Created index '%s'.", self._index_name)

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Embed multiple texts in batches — single API call per batch instead of N calls.
        Dramatically faster during indexing (e.g. 15 chunks → 1 call instead of 15).
        """
        all_embeddings: List[List[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i: i + _EMBED_BATCH_SIZE]
            response = self._openai.embeddings.create(model=self._emb_model, input=batch)
            # Sort by index to guarantee order matches input order
            sorted_items = sorted(response.data, key=lambda x: x.index)
            all_embeddings.extend(item.embedding for item in sorted_items)
        return all_embeddings

    def embed(self, text: str) -> List[float]:
        """Embed a single text string (used for search queries)."""
        return self._embed_batch([text])[0]

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_document(self, doc_name: str, text: str):
        """Chunk, embed (batched), and upload a document. Replaces any prior version."""
        self._delete_docs(doc_name)
        chunks = chunk_text(text)
        if not chunks:
            logger.warning("No text chunks extracted for '%s' — skipping index.", doc_name)
            return

        # All chunks embedded in as few API calls as possible
        vectors = self._embed_batch(chunks)

        docs = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            doc_id = hashlib.md5(f"{doc_name}::{i}".encode()).hexdigest()
            docs.append({
                "id": doc_id,
                self._name_field: doc_name,
                "chunk_text": chunk,
                "chunk_index": i,
                "chunk_vector": vec,
            })

        self._client.upload_documents(docs)
        logger.info("Indexed %d chunk(s) for '%s'.", len(docs), doc_name)

    def _delete_docs(self, doc_name: str):
        """Remove all existing chunks for a document before re-indexing."""
        safe = doc_name.replace("'", "''")
        results = self._client.search(
            search_text="*",
            filter=f"{self._name_field} eq '{safe}'",
            select=["id"],
            top=1000,
        )
        ids = [{"id": r["id"]} for r in results]
        if ids:
            self._client.delete_documents(ids)
            logger.info("Deleted %d old chunk(s) for '%s'.", len(ids), doc_name)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_document_text(self, doc_name: str) -> str:
        """Reconstruct full document text from chunks in original document order."""
        safe = doc_name.replace("'", "''")
        results = self._client.search(
            search_text="*",
            filter=f"{self._name_field} eq '{safe}'",
            select=["chunk_text", "chunk_index"],
            top=1000,
        )
        chunks = sorted(results, key=lambda r: r["chunk_index"])
        return "\n\n".join(r["chunk_text"] for r in chunks)

    def list_documents(self) -> List[str]:
        """
        Return distinct document names from the index.
        Uses facets (fast, index-side) with a client-side dedup fallback for older indexes.
        """
        try:
            results = self._client.search(
                search_text="*",
                facets=[f"{self._name_field},count:0"],
                top=0,
            )
            facets = results.get_facets()
            if facets and self._name_field in facets:
                return [f["value"] for f in facets[self._name_field]]
        except Exception:
            pass  # fall through to dedup approach

        # Fallback: fetch up to 1000 docs and deduplicate client-side
        results = self._client.search(
            search_text="*",
            select=[self._name_field],
            top=1000,
        )
        seen: set = set()
        names: List[str] = []
        for r in results:
            name = r[self._name_field]
            if name not in seen:
                seen.add(name)
                names.append(name)
        return names

    def hybrid_search(
        self,
        query: str,
        top: int = 50,
        filter_names: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Hybrid BM25 + vector search.
        filter_names limits results to a specific subset of documents (for multiselect ranking).
        Returns a list of chunk dicts including search score for coarse ranking.
        """
        vec = self.embed(query)

        # Build OData filter only when a subset is selected
        filter_expr = None
        if filter_names:
            # Escape single quotes in names; use search.in() for efficient multi-value filter
            safe_names = ",".join(n.replace("'", "''") for n in filter_names)
            filter_expr = f"search.in({self._name_field}, '{safe_names}', ',')"

        results = self._client.search(
            search_text=query,
            vector_queries=[
                VectorizedQuery(vector=vec, k_nearest_neighbors=top, fields="chunk_vector")
            ],
            select=[self._name_field, "chunk_text", "chunk_index"],
            filter=filter_expr,
            top=top,
        )
        return [
            {
                "doc_name": r[self._name_field],
                "chunk_text": r["chunk_text"],
                "search_score": r["@search.score"],
            }
            for r in results
        ]
