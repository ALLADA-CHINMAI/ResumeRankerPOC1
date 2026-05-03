import os
import json
import logging

import streamlit as st
import pandas as pd
from dotenv import load_dotenv
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

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY")
RESUME_INDEX = os.getenv("AZURE_SEARCH_RESUME_INDEX_NAME", "resume_chunks")

STORAGE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
RESUME_CONTAINER = os.getenv("RESUME_CONTAINER_NAME", "resumes")
JD_CONTAINER = os.getenv("JD_CONTAINER_NAME", "jds")

# ---------------------------------------------------------------------------
# Clients (cached for the Streamlit session)
# ---------------------------------------------------------------------------

@st.cache_resource
def init_clients():
    openai = AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        api_key=OPENAI_API_KEY,
        api_version="2025-01-01-preview",
    )
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

_SCORE_SYSTEM = """You are a strict resume evaluator. Score resumes against the job description using the rubrics below.

CALIBRATION — use the full 0–100 range:
  90–100 : meets every requirement; nothing missing
  75–89  : meets most requirements; 1-2 minor gaps
  55–74  : meets core requirements; notable gaps in skills or experience
  35–54  : partial match; significant gaps or only tangentially related
  0–34   : mostly unrelated role, domain, or skill set

Scoring categories (individual scores must sum to totalScore):

  experience — 25 pts
    25 : meets or exceeds required years in the exact platform/domain with rich, relevant bullet points
    18 : meets years but bullets are thin, OR slightly under required years with strong detail
    10 : 2–4 yrs relevant experience, or 5+ yrs in a related but different domain
     4 : under 2 yrs relevant, or experience is vaguely described
     0 : no relevant experience

  technicalSkills — 30 pts
    28–30 : explicitly lists ≥80% of required skills/tools with demonstrated use
    20–27 : lists 50–79% of required skills
    10–19 : lists 25–49% of required skills
     1–9  : lists <25% of required skills
     0    : no relevant technical skills

  certifications — 15 pts
    15 : all certifications explicitly required by the JD are present
     8 : some but not all required certifications; or equivalent certifications
     3 : certifications exist but none match what the JD requires
     0 : no certifications at all

  education — 10 pts
    10 : degree in a directly relevant field (CS, IT, Engineering)
     7 : degree in a related field
     4 : any bachelor's degree
     1 : no degree or unrelated education
     0 : education not mentioned

  location — 10 pts
    10 : location explicitly matches the JD location
     6 : states open to relocation or remote
     3 : location mentioned but does not match; or location not stated
     0 : explicitly states cannot relocate when JD requires it

  domainFit — 10 pts
    10 : entire career is in the exact industry/platform the JD targets
     7 : mostly in the right domain with minor detours
     4 : partially in the domain; mixed background
     1 : adjacent domain with transferable skills
     0 : completely different industry or domain

Rules:
- Score ONLY on what is explicitly written in the resume. Never infer or assume.
- Only count semantically equivalent terms for clearly synonymous titles/tools (e.g. "ML engineer" ≈ "machine learning developer"). Do NOT stretch equivalence.
- Deduct heavily when the JD lists a specific required certification and the resume does not have it.
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
