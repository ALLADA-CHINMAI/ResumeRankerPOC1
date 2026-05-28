"""
Streamlit UI — thin shell. All business logic lives in core/.
Run from the project root:  streamlit run ui\app.py
"""

import sys
import os

# Ensure the project root (parent of ui\) is on sys.path so 'core' is importable.
# This is needed when Streamlit adds ui\ to sys.path instead of the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from core.clients import validate_config, get_resume_search, get_jd_search
from core.text_utils import extract_text
from core.storage import (
    list_blobs,
    fetch_blob,
    upload_blob,
    store_parsed_text,
    RESUME_CONTAINER,
    JD_CONTAINER,
)
from core.ranking import rank_resumes, SCORE_MAX

load_dotenv()

# ---------------------------------------------------------------------------
# One-time init — validate config and trigger index creation for both indexes
# ---------------------------------------------------------------------------

@st.cache_resource
def _init():
    """Validate environment and warm up both search indexes (creates them if missing)."""
    validate_config()
    resume_search = get_resume_search()   # creates resume_chunks index if it doesn't exist
    jd_search = get_jd_search()           # creates jd_chunks index if it doesn't exist
    return resume_search, jd_search


resume_search, jd_search = _init()

# ---------------------------------------------------------------------------
# UI
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
                    # 1. Store original file in blob
                    upload_blob(RESUME_CONTAINER, f.name, data)
                    # 2. Extract text, index chunks + cache parsed text for fast ranking
                    text = extract_text(f.name, data)
                    if text.strip():
                        progress.progress((idx + 0.8) / len(resume_files), text=f"Indexing {f.name}…")
                        resume_search.index_document(f.name, text)
                        store_parsed_text(f.name, text)   # cached for O(1) retrieval during ranking
                    else:
                        errors.append(f"{f.name}: no text extracted")
                except Exception as e:
                    errors.append(f"{f.name}: {e}")
                progress.progress((idx + 1) / len(resume_files))
            progress.empty()
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
        if st.button("Upload & Index JDs", disabled=not jd_files):
            errors = []
            for f in jd_files:
                data = f.read()
                try:
                    # 1. Store original file in blob
                    upload_blob(JD_CONTAINER, f.name, data)
                    # 2. Extract text and index for future semantic JD search (e.g. via MCP)
                    text = extract_text(f.name, data)
                    if text.strip():
                        jd_search.index_document(f.name, text)
                    else:
                        errors.append(f"{f.name}: no text extracted")
                except Exception as e:
                    errors.append(f"{f.name}: {e}")
            for err in errors:
                st.warning(err)
            st.success(f"Uploaded and indexed {len(jd_files) - len(errors)} JD(s).")


# ── Rank tab ─────────────────────────────────────────────────────────────────
with rank_tab:
    st.subheader("Rank Resumes Against a Job Description")

    # Resume multiselect — lets the user target a specific candidate pool
    try:
        all_resumes = list_blobs(RESUME_CONTAINER)
    except Exception as e:
        all_resumes = []
        st.error(f"Could not list resumes: {e}")

    selected_resumes = st.multiselect(
        "Select resumes to rank (default: all)",
        options=all_resumes,
        default=all_resumes,
        help="Leave all selected to rank the full candidate pool, or pick a subset.",
    )

    # JD selection
    try:
        jd_list = list_blobs(JD_CONTAINER)
    except Exception as e:
        jd_list = []
        st.error(f"Could not list JDs: {e}")

    if not jd_list:
        st.info("No job descriptions uploaded yet. Go to the Upload tab first.")
    else:
        selected_jd = st.selectbox("Select a Job Description", jd_list)

        rank_disabled = not selected_resumes  # disable button if no resumes selected
        if st.button("Rank Resumes", disabled=rank_disabled):
            try:
                jd_data = fetch_blob(JD_CONTAINER, selected_jd)
                jd_text = extract_text(selected_jd, jd_data)
            except Exception as e:
                st.error(f"Failed to fetch JD: {e}")
                st.stop()

            # Pass filter only when user selected a strict subset (avoids long OData filter strings)
            filter_resumes = (
                selected_resumes
                if set(selected_resumes) != set(all_resumes)
                else None
            )

            with st.status("Ranking resumes…", expanded=True) as status:
                results = rank_resumes(jd_text, top_n=10, selected_resumes=filter_resumes)
                status.update(label="Ranking complete.", state="complete")

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

                # Expandable score breakdown cards
                st.markdown("---")
                st.subheader("Score Breakdown")

                for i, r in enumerate(results):
                    with st.expander(f"#{i + 1}  {r['name']}  —  {r['total_score']:.1f} / 100"):
                        cols = st.columns(len(SCORE_MAX))
                        for col, (key, max_pts) in zip(cols, SCORE_MAX.items()):
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
                        for key in SCORE_MAX:
                            reason = r["reasons"].get(key, "")
                            if reason:
                                st.markdown(f"- **{key}**: {reason}")
