"""
Streamlit UI — thin shell. All business logic lives in ResumeRankerCore/.
Run from the project root:  streamlit run ResumeRankerFrontend\app.py
"""

import sys
import os

# Ensure project root is on sys.path so 'ResumeRankerCore' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from ResumeRankerCore.clients import validate_config, get_resume_search, get_jd_search
from ResumeRankerCore.text_utils import extract_text
from ResumeRankerCore.storage import (
    list_blobs,
    fetch_blob,
    upload_blob,
    store_parsed_text,
    RESUME_CONTAINER,
    JD_CONTAINER,
)
import os as _os
_USE_LANGGRAPH = _os.getenv("USE_LANGGRAPH", "false").lower() == "true"

if _USE_LANGGRAPH:
    from ResumeRankerCore.langgraph_pipeline import rank_resumes_langgraph as _rank_fn, SCORE_MAX
    def rank_resumes(jd_text, top_n=10, selected_resumes=None, on_progress=None):
        return _rank_fn(jd_text, top_n=top_n, selected_resumes=selected_resumes, on_progress=on_progress)
else:
    from ResumeRankerCore.ranking import rank_resumes, SCORE_MAX

load_dotenv()

BLUE  = "#00338E"
OLIVE = "#6B7C3F"
WHITE = "#FFFFFF"
LIGHT = "#F4F6FA"

# ---------------------------------------------------------------------------
# One-time init
# ---------------------------------------------------------------------------


# Show loader while initializing resources
with st.spinner("Initializing Resume Ranker resources..."):
    @st.cache_resource
    def _init():
        validate_config()
        resume_search = get_resume_search()
        jd_search     = get_jd_search()
        return resume_search, jd_search
    resume_search, jd_search = _init()

# ---------------------------------------------------------------------------
# Cached blob listings — avoids Azure round-trip on every widget interaction
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def _list_jds():
    return list_blobs(JD_CONTAINER)


@st.cache_data(ttl=30)
def _list_resumes():
    return list_blobs(RESUME_CONTAINER)


# ---------------------------------------------------------------------------
# Page config + CSS
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Resume Ranker", layout="wide")

st.markdown(f"""
<style>
  .stApp {{ background-color: {LIGHT}; }}

  /* ── Compact header bar ── */
  .ph-header {{
      background-color: {BLUE};
      padding: 10px 28px 8px 28px;
      border-radius: 8px;
      margin-bottom: 22px;
  }}
  .ph-header h1 {{
      color: {WHITE} !important;
      margin: 0;
      font-size: 1.45rem;
      letter-spacing: 0.3px;
  }}
  .ph-header p {{
      color: #B8CBE8;
      margin: 2px 0 0 0;
      font-size: 0.82rem;
  }}

  /* ── Section headings ── */
  .section-title {{
      color: {BLUE};
      font-size: 1.05rem;
      font-weight: 700;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      border-left: 4px solid {OLIVE};
      padding-left: 10px;
      margin-bottom: 12px;
  }}

  /* ── Divider ── */
  .ph-divider {{
      border: none;
      border-top: 2px solid {BLUE};
      opacity: 0.18;
      margin: 22px 0;
  }}

  /* ── Standard buttons ── */
  .stButton > button {{
      background-color: {BLUE} !important;
      color: {WHITE} !important;
      border: none !important;
      border-radius: 5px !important;
      font-weight: 600 !important;
      padding: 8px 22px !important;
      transition: background-color 0.2s;
  }}
  .stButton > button:hover {{ background-color: #002266 !important; }}
  .stButton > button:disabled {{ background-color: #8BAAD4 !important; cursor: not-allowed !important; }}

  /* ── Highlighted Rank Resumes button (olive, full-width, prominent) ── */
  .rank-btn > button {{
      background-color: {OLIVE} !important;
      color: {WHITE} !important;
      font-size: 1.08rem !important;
      padding: 13px 40px !important;
      border-radius: 6px !important;
      font-weight: 700 !important;
      letter-spacing: 0.3px !important;
      box-shadow: 0 3px 12px rgba(107,124,63,0.45) !important;
      width: 100% !important;
      transition: background-color 0.2s, box-shadow 0.2s !important;
  }}
  .rank-btn > button:hover {{
      background-color: #566533 !important;
      box-shadow: 0 5px 16px rgba(107,124,63,0.6) !important;
  }}
  .rank-btn > button:disabled {{
      background-color: #B5BFA0 !important;
      box-shadow: none !important;
  }}

  /* ── Checkboxes — dark blue ── */
  input[type="checkbox"] {{
      accent-color: {BLUE} !important;
      width: 16px !important;
      height: 16px !important;
  }}

  .stProgress > div > div > div > div {{ background-color: {BLUE} !important; }}
  [data-testid="stExpander"] {{ border: 1px solid #D0DAF0 !important; border-radius: 6px !important; }}
  [data-testid="stMetricValue"] {{ color: {BLUE} !important; }}

  /* --- Multiselect selected chips (green shade) --- */
  .stMultiSelect [data-baseweb="tag"] {{
      background-color: #d0f5e8 !important;
      color: #1a3c2b !important;
      border-radius: 6px !important;
      font-weight: 600;
      border: 1px solid #6B7C3F !important;
  }}
  .stMultiSelect [data-baseweb="tag"] span {{
      color: #1a3c2b !important;
  }}
  .stSelectbox label, .stFileUploader label {{ color: #1A1A2E; font-weight: 600; }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

_pipeline_badge = (
    '<span style="background:#6B7C3F;color:#fff;font-size:0.72rem;'
    'padding:2px 10px;border-radius:12px;margin-left:12px;vertical-align:middle;">'
    "LangGraph Pipeline</span>"
    if _USE_LANGGRAPH else
    '<span style="background:#8BAAD4;color:#fff;font-size:0.72rem;'
    'padding:2px 10px;border-radius:12px;margin-left:12px;vertical-align:middle;">'
    "Classic Pipeline</span>"
)

st.markdown(f"""
<div class="ph-header">
  <h1>Resume Ranker {_pipeline_badge}</h1>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab1, tab2 = st.tabs(["Resume Ranking", "Candidate History"])

# ---------------------------------------------------------------------------
# Tab 1 — existing Resume Ranking UI (unchanged)
# ---------------------------------------------------------------------------

with tab1:
    with st.container():
        with st.expander("📤 Upload New Job Descriptions & Resumes", expanded=True):
            up_col1, up_col2 = st.columns(2, gap="large")
            with up_col1:
                st.markdown('<div class="section-title">Upload New Job Descriptions</div>', unsafe_allow_html=True)
                jd_files = st.file_uploader(
                    "Select JD files (.txt, .pdf, .docx)",
                    accept_multiple_files=True,
                    key="jd_uploader",
                )
                if st.button("Upload JDs", disabled=not jd_files, key="btn_upload_jd"):
                    errors = []
                    for f in jd_files:
                        data = f.read()
                        try:
                            upload_blob(JD_CONTAINER, f.name, data)
                            text = extract_text(f.name, data)
                            if text.strip():
                                jd_search.index_document(f.name, text)
                            else:
                                errors.append(f"{f.name}: no text extracted")
                        except Exception as e:
                            errors.append(f"{f.name}: {e}")
                    _list_jds.clear()  # refresh JD dropdown
                    _list_resumes.clear()  # also clear resumes cache in case JDs affect downstream logic
                    for err in errors:
                        st.warning(err)
                    st.success(f"Uploaded {len(jd_files) - len(errors)} JD(s).")
            with up_col2:
                st.markdown('<div class="section-title">Upload New Resumes</div>', unsafe_allow_html=True)
                resume_files = st.file_uploader(
                    "Select resume files (.txt, .pdf, .docx)",
                    accept_multiple_files=True,
                    key="resume_uploader",
                )
                if st.button("Upload & Index Resumes", disabled=not resume_files, key="btn_upload_res"):
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
                                resume_search.index_document(f.name, text)
                                store_parsed_text(f.name, text)
                            else:
                                errors.append(f"{f.name}: no text extracted")
                        except Exception as e:
                            errors.append(f"{f.name}: {e}")
                        progress.progress((idx + 1) / len(resume_files))
                    progress.empty()
                    _list_resumes.clear()  # refresh resume list
                    _list_jds.clear()      # also clear JD cache in case resumes affect downstream logic
                    for err in errors:
                        st.warning(err)
                    st.success(f"Done — {len(resume_files) - len(errors)} resume(s) uploaded and indexed.")

    st.markdown('<hr class="ph-divider">', unsafe_allow_html=True)

    with st.container():
        with st.expander("📊 Rank Resumes Against a Job Description", expanded=True):
            col1, col2 = st.columns(2, gap="large")
            selected_jd = None
            all_resumes: list = []
            selected_resumes: list = []
            with col1:
                st.markdown('<div class="section-title">Select Job Description</div>', unsafe_allow_html=True)
                with st.spinner("Loading job descriptions…"):
                    try:
                        jd_list = _list_jds()
                    except Exception as e:
                        jd_list = []
                        st.warning(f"Could not list job descriptions: {e}")
                if not jd_list:
                    st.info("No job descriptions found. Upload a JD above first.")
                else:
                    selected_jd = st.selectbox(
                        "Job Description",
                        jd_list,
                        key="jd_select",
                        label_visibility="collapsed",
                    )
            with col2:
                st.markdown('<div class="section-title">Select Resumes to Rank</div>', unsafe_allow_html=True)
                with st.spinner("Loading resumes…"):
                    try:
                        all_resumes = _list_resumes()
                    except Exception as e:
                        all_resumes = []
                        st.warning(f"Could not list resumes: {e}")
                if not all_resumes:
                    st.info("No resumes found. Upload resumes above first.")
                else:
                    selected_resumes = st.multiselect(
                        "Resumes",
                        all_resumes,
                        default=all_resumes,
                        key="resume_multiselect",
                    )
                    st.caption(f"{len(selected_resumes)} of {len(all_resumes)} selected")

    # --- Rank Resumes Button ---
    st.markdown('<div style="height:2px;"></div>', unsafe_allow_html=True)
    can_rank = bool('selected_jd' in locals() and 'selected_resumes' in locals() and selected_jd and selected_resumes)
    st.markdown('<div class="rank-btn" style="width:100%;display:flex;justify-content:center;margin-top:-2px;margin-bottom:2px;">', unsafe_allow_html=True)
    rank_clicked = st.button("Rank Resumes →", disabled=not can_rank, key="btn_rank_main")
    st.markdown('</div>', unsafe_allow_html=True)

    if rank_clicked:
        try:
            jd_data = fetch_blob(JD_CONTAINER, selected_jd)
            jd_text = extract_text(selected_jd, jd_data)
        except Exception as e:
            st.error(f"Failed to load job description: {e}")
            st.stop()

        filter_resumes = (
            selected_resumes
            if set(selected_resumes) != set(all_resumes)
            else None
        )

        with st.status("Ranking in progress…", expanded=True) as status:
            log = st.empty()
            messages: list = []

            def _progress(msg: str):
                messages.append(msg)
                log.markdown("\n".join(f"→ {m}" for m in messages[-6:]))

            results = rank_resumes(
                jd_text,
                top_n=10,
                selected_resumes=filter_resumes,
                on_progress=_progress,
            )
            status.update(label="Ranking complete!", state="complete", expanded=False)

        if not results:
            st.warning("No resumes could be scored. Check that resumes are indexed in the Upload section.")
        else:
            st.success(f"Top {len(results)} candidates for **{selected_jd}**")

            table_rows = [
                {"Rank": i + 1, "Resume": r["name"], "Score": f"{r['total_score']:.1f} / 100"}
                for i, r in enumerate(results)
            ]
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

            st.markdown('<hr class="ph-divider">', unsafe_allow_html=True)
            st.markdown('<div class="section-title">Score Breakdown</div>', unsafe_allow_html=True)

            for i, r in enumerate(results):
                with st.expander(f"#{i + 1}  {r['name']}  —  {r['total_score']:.1f} / 100"):
                    cols = st.columns(len(SCORE_MAX))
                    for col, (key, max_pts) in zip(cols, SCORE_MAX.items()):
                        val = r["scores"].get(key, 0)
                        _label_map = {
                            "technicalSkills":   "Tech Skills",
                            "domainFit":         "Domain Fit",
                            "certifications":    "Certs",
                            "education":         "Education",
                            "experience":        "Experience",
                            "location":          "Location",
                            "gapFill":           "Gap Fill",
                            "teamCompatibility": "Team Compat.",
                            "skillFreshness":    "Skill Freshness",
                        }
                        label = _label_map.get(key, key)
                        col.metric(label=label, value=f"{val} / {max_pts}")

                    st.markdown("**Scoring Reasons:**")
                    for key in SCORE_MAX:
                        reason = r["reasons"].get(key, "")
                        if reason:
                            st.markdown(f"- **{key}**: {reason}")

# ---------------------------------------------------------------------------
# Tab 2 — Candidate History (MCP POC demo)
# ---------------------------------------------------------------------------

with tab2:
    from ResumeRankerMCP.candidate_history import get_by_candidate_ids, get_by_doc_names

    st.markdown('<div class="section-title">Candidate History Explorer</div>', unsafe_allow_html=True)
    st.caption("Reads directly from candidate_data.xlsx — no Azure SQL needed for POC.")

    # Load Excel
    _EXCEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "candidate_data.xlsx")

    @st.cache_data(ttl=60)
    def _load_candidate_data():
        return pd.read_excel(_EXCEL_PATH, sheet_name="candidate_metadata", dtype=str).fillna(""), \
               pd.read_excel(_EXCEL_PATH, sheet_name="interview_history", dtype=str).fillna("")

    try:
        df_meta, df_hist = _load_candidate_data()
    except FileNotFoundError:
        st.error("candidate_data.xlsx not found. Expected at project root.")
        st.stop()

    # --- Filters ---
    fcol1, fcol2, fcol3 = st.columns(3)
    with fcol1:
        role_options = ["All"] + sorted(df_meta["role_family"].unique().tolist())
        filter_role = st.selectbox("Role Family", role_options, key="ch_role")
    with fcol2:
        status_options = ["All"] + sorted(df_meta["latest_application_status"].unique().tolist())
        filter_status = st.selectbox("Application Status", status_options, key="ch_status")
    with fcol3:
        search_name = st.text_input("Search by Name", placeholder="e.g. John", key="ch_name")

    filtered = df_meta.copy()
    if filter_role != "All":
        filtered = filtered[filtered["role_family"] == filter_role]
    if filter_status != "All":
        filtered = filtered[filtered["latest_application_status"] == filter_status]
    if search_name:
        filtered = filtered[filtered["full_name"].str.contains(search_name, case=False, na=False)]

    st.markdown(f"**{len(filtered)} candidate(s) found**")
    st.markdown('<hr class="ph-divider">', unsafe_allow_html=True)

    # --- Candidate cards ---
    if filtered.empty:
        st.info("No candidates match the selected filters.")
    else:
        # Summary table
        summary_cols = ["candidate_id", "full_name", "role_family", "years_experience",
                        "current_company", "latest_application_status", "last_interview_round",
                        "total_applications", "candidate_status"]
        st.dataframe(filtered[summary_cols].reset_index(drop=True), use_container_width=True, hide_index=True)

        st.markdown('<hr class="ph-divider">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">Interview History</div>', unsafe_allow_html=True)

        for _, row in filtered.iterrows():
            cid = row["candidate_id"]
            hist = df_hist[df_hist["candidate_id"] == cid]
            rounds = len(hist)
            last_result = hist.iloc[-1]["result"] if rounds > 0 else "—"
            label = f"{row['full_name']}  ·  {row['role_family']}  ·  {rounds} round(s)  ·  Last: {last_result}"
            with st.expander(label):
                mcol1, mcol2, mcol3, mcol4 = st.columns(4)
                mcol1.metric("Experience", f"{row.get('years_experience', '—')} yrs")
                mcol2.metric("Applications", row.get("total_applications", "—"))
                mcol3.metric("Status", row.get("candidate_status", "—"))
                mcol4.metric("Last Round", row.get("last_interview_round", "—"))

                if hist.empty:
                    st.info("No interview history on record.")
                else:
                    hist_display = hist[["round_number", "round_type", "result",
                                        "rejection_reason", "interview_score",
                                        "interview_feedback", "interview_date"]].copy()
                    hist_display.columns = ["Round #", "Type", "Result",
                                            "Rejection Reason", "Score", "Feedback", "Date"]
                    st.dataframe(hist_display.reset_index(drop=True), use_container_width=True, hide_index=True)

                if row.get("latest_resume_blob_url"):
                    st.markdown(f"[View Resume]({row['latest_resume_blob_url']})")

