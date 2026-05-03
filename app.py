import os
import json
import logging
import time

import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient
from openai import AzureOpenAI

from search_utils import ResumeSearchClient, extract_text

load_dotenv()
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Config — all from .env
# ---------------------------------------------------------------------------

OPENAI_ENDPOINT = os.getenv("OPENAI_ENDPOINT")
OPENAI_DEPLOYMENT = os.getenv("OPENAI_DEPLOYMENT_NAME", "gpt-4o")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_DEPLOYMENT = os.getenv("EMBEDINGS_OPENAI_DEPLOYMENT_NAME", "text-embedding-ada-002")
OPENAI_API_VERSION = os.getenv("OPENAI_API_VERSION", "2025-01-01-preview")

AUTH_TENANT_ID = os.getenv("AUTH_TENANT_ID")
AUTH_CLIENT_ID = os.getenv("AUTH_CLIENT_ID")
AUTH_CLIENT_SECRET = os.getenv("AUTH_CLIENT_SECRET")
AUTH_SCOPE = os.getenv("AUTH_SCOPE")

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY")
RESUME_INDEX = os.getenv("AZURE_SEARCH_RESUME_INDEX_NAME", "resume_chunks")

STORAGE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
RESUME_CONTAINER = os.getenv("RESUME_CONTAINER_NAME", "resumes")
JD_CONTAINER = os.getenv("JD_CONTAINER_NAME", "jds")

_credential = None
_token_cache = {"token": None, "expires_at": 0}
_openai_client = None


def _use_aad_auth() -> bool:
    return all([AUTH_TENANT_ID, AUTH_CLIENT_ID, AUTH_CLIENT_SECRET, AUTH_SCOPE])


def _validate_openai_config():
    if not OPENAI_ENDPOINT:
        raise ValueError("OPENAI_ENDPOINT is required.")
    if _use_aad_auth():
        if not OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is required when using the APIM endpoint with Azure AD auth."
            )
        return
    if not OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY is required unless Azure AD auth is fully configured."
        )


def _get_credential() -> ClientSecretCredential:
    global _credential
    if _credential is None:
        _credential = ClientSecretCredential(
            tenant_id=AUTH_TENANT_ID,
            client_id=AUTH_CLIENT_ID,
            client_secret=AUTH_CLIENT_SECRET,
        )
    return _credential


def _get_token() -> str:
    global _token_cache

    if _token_cache["token"] and time.time() < (_token_cache["expires_at"] - 300):
        return _token_cache["token"]

    credential = _get_credential()
    token_result = credential.get_token(AUTH_SCOPE)
    _token_cache["token"] = token_result.token
    _token_cache["expires_at"] = token_result.expires_on
    logging.info("Acquired Azure AD token for Azure OpenAI; expires at %s", token_result.expires_on)
    return token_result.token


def _create_openai_client() -> AzureOpenAI:
    kwargs = {
        "azure_endpoint": OPENAI_ENDPOINT,
        "api_version": OPENAI_API_VERSION,
    }
    if OPENAI_API_KEY:
        kwargs["api_key"] = OPENAI_API_KEY
    if _use_aad_auth():
        kwargs["default_headers"] = {"Authorization": f"Bearer {_get_token()}"}
    return AzureOpenAI(**kwargs)


def _get_openai_client() -> AzureOpenAI:
    global _openai_client

    if _openai_client is None:
        _openai_client = _create_openai_client()
        return _openai_client

    if _use_aad_auth() and time.time() >= (_token_cache["expires_at"] - 300):
        logging.info("Refreshing Azure OpenAI client with a fresh Azure AD token.")
        _openai_client = _create_openai_client()

    return _openai_client


class _OpenAIEmbeddingsProxy:
    def create(self, *args, **kwargs):
        return _get_openai_client().embeddings.create(*args, **kwargs)


class _OpenAIChatCompletionsProxy:
    def create(self, *args, **kwargs):
        return _get_openai_client().chat.completions.create(*args, **kwargs)


class _OpenAIChatProxy:
    def __init__(self):
        self.completions = _OpenAIChatCompletionsProxy()


class RefreshingAzureOpenAI:
    def __init__(self):
        self.chat = _OpenAIChatProxy()
        self.embeddings = _OpenAIEmbeddingsProxy()

# ---------------------------------------------------------------------------
# Clients (cached for the Streamlit session)
# ---------------------------------------------------------------------------

@st.cache_resource
def init_clients():
    _validate_openai_config()
    openai = RefreshingAzureOpenAI()
    blob = BlobServiceClient.from_connection_string(STORAGE_CONN_STR)
    search = ResumeSearchClient(
        endpoint=SEARCH_ENDPOINT,
        api_key=SEARCH_API_KEY,
        index_name=RESUME_INDEX,
        openai_client=openai,
        embedding_model=EMBEDDING_DEPLOYMENT,
    )
    return openai, blob, search


openai_client, blob_service, search_client = init_clients()

# ---------------------------------------------------------------------------
# Blob helpers
# ---------------------------------------------------------------------------

def list_blobs(container: str) -> list[str]:
    return [b.name for b in blob_service.get_container_client(container).list_blobs()]


def fetch_blob(container: str, name: str) -> bytes:
    return blob_service.get_blob_client(container=container, blob=name).download_blob().readall()


def upload_blob(container: str, name: str, data: bytes):
    blob_service.get_blob_client(container=container, blob=name).upload_blob(data, overwrite=True)


# ---------------------------------------------------------------------------
# LLM: keyword extraction
# ---------------------------------------------------------------------------

def extract_jd_keywords(jd_text: str) -> str:
    resp = openai_client.chat.completions.create(
        model=OPENAI_DEPLOYMENT,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract key requirements from a job description for semantic resume search. "
                    "Output dense continuous text (no bullets, no headers) covering: "
                    "job title, required skills, technologies, years of experience, "
                    "certifications, education level, and industry domain."
                ),
            },
            {"role": "user", "content": jd_text},
        ],
        max_tokens=600,
        temperature=0,
    )
    return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# LLM: resume scoring
# ---------------------------------------------------------------------------

_SCORE_SYSTEM = """You evaluate resumes against job descriptions.

Scoring criteria (points must sum to totalScore, max 100):
  experience      — 25 pts  (relevance and length of work history)
  technicalSkills — 30 pts  (match on required skills and technologies)
  certifications  — 15 pts  (relevant certifications and training)
  education       — 10 pts  (degree relevance and level)
  location        — 10 pts  (location match or stated willingness to relocate)
  domainFit       — 10 pts  (industry / domain alignment)

Rules:
- Score ONLY on what is explicitly written in the resume. Never assume.
- Understand semantics — semantically equivalent terms count (e.g. "ML engineer" ≈ "machine learning developer").
- If the content is clearly not a resume, return {"totalScore": -2}.

Return ONLY valid JSON, no markdown fences:
{
  "totalScore": 85.0,
  "scores": {
    "experience": 22,
    "technicalSkills": 28,
    "certifications": 12,
    "education": 8,
    "location": 7,
    "domainFit": 8
  },
  "scoringReasons": {
    "experience": "brief reason",
    "technicalSkills": "brief reason",
    "certifications": "brief reason",
    "education": "brief reason",
    "location": "brief reason",
    "domainFit": "brief reason"
  }
}"""


def score_resume(jd_text: str, resume_text: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            resp = openai_client.chat.completions.create(
                model=OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": _SCORE_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Job Description:\n{jd_text}\n\n"
                            f"Resume:\n{resume_text[:15000]}"
                        ),
                    },
                ],
                max_tokens=800,
                temperature=0,
                response_format={"type": "json_object"},
            )
            result = json.loads(resp.choices[0].message.content)
            total = float(result.get("totalScore", -1))
            if total == -2:
                return None  # not a resume
            if 0 <= total <= 100:
                return result
        except Exception as e:
            logging.warning(f"Score attempt {attempt + 1} failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Ranking pipeline
# ---------------------------------------------------------------------------

_SCORE_META = {
    "experience": 25,
    "technicalSkills": 30,
    "certifications": 15,
    "education": 10,
    "location": 10,
    "domainFit": 10,
}


def rank_resumes(jd_text: str, top_n: int = 10) -> list[dict]:
    with st.status("Step 1/3 — Extracting JD keywords…", expanded=False):
        keywords = extract_jd_keywords(jd_text)

    with st.status("Step 2/3 — Hybrid search across indexed resumes…", expanded=False):
        chunks = search_client.hybrid_search(keywords, top=60)

    # Group chunks by resume, concatenate text, keep max search score
    grouped: dict[str, dict] = {}
    for c in chunks:
        key = c["resume_name"]
        if key not in grouped:
            grouped[key] = {"text": "", "search_score": 0.0}
        grouped[key]["text"] += "\n\n" + c["chunk_text"]
        grouped[key]["search_score"] = max(grouped[key]["search_score"], c["search_score"])

    if not grouped:
        return []

    ranked = []
    total = len(grouped)
    with st.status(f"Step 3/3 — Scoring {total} resume(s) with GPT-4o…", expanded=True) as status:
        for i, (name, data) in enumerate(grouped.items()):
            status.update(label=f"Step 3/3 — Scoring {name}  ({i + 1}/{total})")
            result = score_resume(jd_text, data["text"])
            if result is not None:
                ranked.append(
                    {
                        "name": name,
                        "total_score": result["totalScore"],
                        "scores": result.get("scores", {}),
                        "reasons": result.get("scoringReasons", {}),
                    }
                )
        status.update(label="Scoring complete.", state="complete")

    ranked.sort(key=lambda x: -x["total_score"])
    return ranked[:top_n]


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Resume Ranker", layout="wide")
st.title("Resume Ranker")

upload_tab, rank_tab = st.tabs(["Upload", "Rank Resumes"])

# ── Upload tab ──────────────────────────────────────────────────────────────
with upload_tab:
    res_col, jd_col = st.columns(2)

    with res_col:
        st.subheader("Resumes")
        resume_files = st.file_uploader(
            "Select resume files (.txt, .pdf, .docx)",
            accept_multiple_files=True,
            key="resume_uploader",
        )
        if st.button("Upload & Index Resumes", disabled=not resume_files):
            progress = st.progress(0, text="Starting…")
            errors = []
            for idx, f in enumerate(resume_files):
                progress.progress((idx + 0.5) / len(resume_files), text=f"Uploading {f.name}…")
                data = f.read()
                try:
                    upload_blob(RESUME_CONTAINER, f.name, data)
                    text = extract_text(f.name, data)
                    if text.strip():
                        progress.progress((idx + 0.8) / len(resume_files), text=f"Indexing {f.name}…")
                        search_client.index_resume(f.name, text)
                    else:
                        errors.append(f"{f.name}: no text extracted")
                except Exception as e:
                    errors.append(f"{f.name}: {e}")
                progress.progress((idx + 1) / len(resume_files))
            progress.empty()
            if errors:
                for err in errors:
                    st.warning(err)
            st.success(f"Done — {len(resume_files) - len(errors)} resume(s) uploaded and indexed.")

    with jd_col:
        st.subheader("Job Descriptions")
        jd_files = st.file_uploader(
            "Select JD files (.txt, .pdf, .docx)",
            accept_multiple_files=True,
            key="jd_uploader",
        )
        if st.button("Upload JDs", disabled=not jd_files):
            errors = []
            for f in jd_files:
                try:
                    upload_blob(JD_CONTAINER, f.name, f.read())
                except Exception as e:
                    errors.append(f"{f.name}: {e}")
            if errors:
                for err in errors:
                    st.warning(err)
            st.success(f"Uploaded {len(jd_files) - len(errors)} JD(s).")


# ── Rank tab ─────────────────────────────────────────────────────────────────
with rank_tab:
    st.subheader("Rank Resumes Against a Job Description")

    try:
        jd_list = list_blobs(JD_CONTAINER)
    except Exception as e:
        jd_list = []
        st.error(f"Could not list JDs: {e}")

    if not jd_list:
        st.info("No job descriptions uploaded yet. Go to the Upload tab first.")
    else:
        selected_jd = st.selectbox("Select a Job Description", jd_list)

        if st.button("Rank Resumes"):
            try:
                jd_data = fetch_blob(JD_CONTAINER, selected_jd)
                jd_text = extract_text(selected_jd, jd_data)
            except Exception as e:
                st.error(f"Failed to fetch JD: {e}")
                st.stop()

            results = rank_resumes(jd_text, top_n=10)

            if not results:
                st.warning(
                    "No resumes scored. Make sure resumes are uploaded and indexed "
                    "(check the Upload tab)."
                )
            else:
                st.success(f"Top {len(results)} resumes for **{selected_jd}**")

                # Summary table
                table_rows = [
                    {
                        "Rank": i + 1,
                        "Resume": r["name"],
                        "Score": f"{r['total_score']:.1f} / 100",
                    }
                    for i, r in enumerate(results)
                ]
                st.dataframe(
                    pd.DataFrame(table_rows),
                    use_container_width=True,
                    hide_index=True,
                )

                # Expandable breakdown cards
                st.markdown("---")
                st.subheader("Score Breakdown")

                for i, r in enumerate(results):
                    with st.expander(
                        f"#{i + 1}  {r['name']}  —  {r['total_score']:.1f} / 100"
                    ):
                        cols = st.columns(len(_SCORE_META))
                        for col, (key, max_pts) in zip(cols, _SCORE_META.items()):
                            val = r["scores"].get(key, 0)
                            label = (
                                key.replace("technicalSkills", "Tech Skills")
                                   .replace("domainFit", "Domain Fit")
                                   .replace("certifications", "Certs")
                                   .replace("education", "Education")
                                   .replace("experience", "Experience")
                                   .replace("location", "Location")
                            )
                            col.metric(label=label, value=f"{val} / {max_pts}")

                        st.markdown("**Scoring Reasons:**")
                        for key in _SCORE_META:
                            reason = r["reasons"].get(key, "")
                            if reason:
                                st.markdown(f"- **{key}**: {reason}")
