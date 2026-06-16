"""
Azure client singletons — lazy-initialized, shared across the whole app.
No Streamlit imports here so this module is safe to use from MCP or any other caller.
"""

import os
import time
import logging
from typing import Optional

from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient
from azure.identity import ClientSecretCredential
from openai import AzureOpenAI

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

OPENAI_ENDPOINT        = os.getenv("OPENAI_ENDPOINT")
OPENAI_DEPLOYMENT      = os.getenv("OPENAI_DEPLOYMENT_NAME", "gpt-4o")
OPENAI_API_KEY         = os.getenv("OPENAI_API_KEY")
EMBEDDING_DEPLOYMENT   = os.getenv("EMBEDDINGS_OPENAI_DEPLOYMENT_NAME", "text-embedding-ada-002")
OPENAI_API_VERSION     = os.getenv("OPENAI_API_VERSION", "2025-01-01-preview")

AUTH_TENANT_ID         = os.getenv("AUTH_TENANT_ID")
AUTH_CLIENT_ID         = os.getenv("AUTH_CLIENT_ID")
AUTH_CLIENT_SECRET     = os.getenv("AUTH_CLIENT_SECRET")
AUTH_SCOPE             = os.getenv("AUTH_SCOPE")

SEARCH_ENDPOINT        = os.getenv("AZURE_SEARCH_ENDPOINT")
SEARCH_API_KEY         = os.getenv("AZURE_SEARCH_API_KEY")
RESUME_INDEX           = os.getenv("AZURE_SEARCH_RESUME_INDEX_NAME", "resume_chunks")

STORAGE_CONN_STR       = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_config():
    """Raise ValueError early if any required env var is missing."""
    missing = []
    if not OPENAI_ENDPOINT:
        missing.append("OPENAI_ENDPOINT")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if not SEARCH_ENDPOINT:
        missing.append("AZURE_SEARCH_ENDPOINT")
    if not SEARCH_API_KEY:
        missing.append("AZURE_SEARCH_API_KEY")
    if not STORAGE_CONN_STR:
        missing.append("AZURE_STORAGE_CONNECTION_STRING")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Azure AD token handling (only needed for APIM-fronted OpenAI endpoints)
# ---------------------------------------------------------------------------

_credential: Optional[ClientSecretCredential] = None
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
    logger.info("Acquired Azure AD token; expires at %s", token_result.expires_on)
    return token_result.token


# ---------------------------------------------------------------------------
# Raw AzureOpenAI client with optional AAD token refresh
# ---------------------------------------------------------------------------

_raw_openai: Optional[AzureOpenAI] = None


def _build_openai_client() -> AzureOpenAI:
    kwargs = {
        "azure_endpoint": OPENAI_ENDPOINT,
        "api_version": OPENAI_API_VERSION,
        "api_key": OPENAI_API_KEY,
    }
    if _use_aad_auth():
        # APIM-fronted endpoint: pass Bearer token as Authorization header
        kwargs["default_headers"] = {"Authorization": f"Bearer {_get_aad_token()}"}
    return AzureOpenAI(**kwargs)


def _get_raw_openai() -> AzureOpenAI:
    """Return the raw AzureOpenAI client, rebuilding only when AAD token is near expiry."""
    global _raw_openai
    if _raw_openai is None:
        _raw_openai = _build_openai_client()
        return _raw_openai
    # Only rebuild when AAD is in use and token is about to expire
    if _use_aad_auth() and time.time() >= (_token_cache["expires_at"] - 300):
        logger.info("Refreshing Azure OpenAI client with a fresh AAD token.")
        _raw_openai = _build_openai_client()
    return _raw_openai


# ---------------------------------------------------------------------------
# RefreshingAzureOpenAI — thin proxy with the same .chat / .embeddings interface
# ---------------------------------------------------------------------------

class _EmbeddingsProxy:
    def create(self, *args, **kwargs):
        return _get_raw_openai().embeddings.create(*args, **kwargs)


class _ChatCompletionsProxy:
    def create(self, *args, **kwargs):
        return _get_raw_openai().chat.completions.create(*args, **kwargs)


class _ChatProxy:
    def __init__(self):
        self.completions = _ChatCompletionsProxy()


class RefreshingAzureOpenAI:
    """Exposes .chat and .embeddings with automatic AAD token refresh under the hood."""
    def __init__(self):
        self.chat = _ChatProxy()
        self.embeddings = _EmbeddingsProxy()


# ---------------------------------------------------------------------------
# Module-level lazy singletons
# ---------------------------------------------------------------------------

_openai_singleton: Optional[RefreshingAzureOpenAI] = None
_blob_singleton: Optional[BlobServiceClient] = None
_resume_search_singleton = None


def get_openai_client() -> RefreshingAzureOpenAI:
    global _openai_singleton
    if _openai_singleton is None:
        _openai_singleton = RefreshingAzureOpenAI()
    return _openai_singleton


def get_blob_service() -> BlobServiceClient:
    global _blob_singleton
    if _blob_singleton is None:
        _blob_singleton = BlobServiceClient.from_connection_string(STORAGE_CONN_STR)
    return _blob_singleton


def get_resume_search():
    """Return the DocumentSearchClient for the resumes index."""
    global _resume_search_singleton
    if _resume_search_singleton is None:
        from ResumeRankerMCP.common.search import DocumentSearchClient  # local import avoids circular dep at module load
        _resume_search_singleton = DocumentSearchClient(
            endpoint=SEARCH_ENDPOINT,
            api_key=SEARCH_API_KEY,
            index_name=RESUME_INDEX,
            openai_client=get_openai_client(),
            embedding_model=EMBEDDING_DEPLOYMENT,
            name_field="resume_name",
        )
    return _resume_search_singleton
