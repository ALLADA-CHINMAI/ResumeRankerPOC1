"""Azure Cognitive Search client for resume retrieval and hybrid search."""

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

logger = logging.getLogger(__name__)

EMBEDDING_DIMS = 1536   # text-embedding-ada-002


class DocumentSearchClient:
    """
    Wraps Azure Cognitive Search for hybrid (BM25 + vector) retrieval.

    Pass name_field="resume_name" for resumes, name_field="jd_name" for JDs.
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
        self._add_req_id_field_if_missing()

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

    def _build_index_definition(self) -> SearchIndex:
        return SearchIndex(
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
                    name="req_id",
                    type=SearchFieldDataType.String,
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
                profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-config")],
            ),
        )

    def _create_index(self):
        self._index_client.create_index(self._build_index_definition())
        logger.info("Created index '%s'.", self._index_name)

    def _add_req_id_field_if_missing(self):
        """Add req_id field to an existing index that predates this feature. Safe no-op if already present."""
        try:
            existing = self._index_client.get_index(self._index_name)
            field_names = {f.name for f in existing.fields}
            if "req_id" not in field_names:
                self._index_client.create_or_update_index(self._build_index_definition())
                logger.info("Added 'req_id' field to existing index '%s'.", self._index_name)
        except Exception as e:
            logger.warning("Could not update index schema for req_id: %s", e)

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def embed(self, text: str) -> List[float]:
        """Embed a single text string (used for search queries)."""
        response = self._openai.embeddings.create(model=self._emb_model, input=[text])
        return response.data[0].embedding

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
