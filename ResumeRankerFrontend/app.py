"""
Streamlit UI — thin shell. All business logic lives in ResumeRankerCommon/.
Run from the project root:  streamlit run ResumeRankerFrontend\app.py
"""

import sys
import os

# Ensure project root is on sys.path so 'ResumeRankerCommon' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import re
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from ResumeRankerCommon.clients import validate_config, get_resume_search, get_jd_search
from ResumeRankerCommon.text_utils import extract_text
from ResumeRankerCommon.storage import (
    list_blobs,
    fetch_blob,
    upload_blob,
    store_parsed_text,
    get_blob_url,
    RESUME_CONTAINER,
    JD_CONTAINER,
)
from ResumeRankerCommon.ranking import rank_resumes, extract_jd_keywords, extract_jd_keywords_structured
from ResumeRankerCommon.config import get_score_max
from ResumeRankerCommon.xlsx_utils import update_candidate_metadata

load_dotenv()

BLUE  = "#00338E"
OLIVE = "#6B7C3F"
WHITE = "#FFFFFF"
LIGHT = "#F4F6FA"

_KEY_LABELS = {
    "experience":      "Experience",
    "technicalSkills": "Technical Skills",
    "certifications":  "Certifications",
    "education":       "Education",
    "location":        "Location",
    "domainFit":       "Domain Fit",
}

_STATUS_COLOR = {
    "exceeds":      "#1a7a1a",
    "aligned":      "#1a7a1a",
    "met":          "#1a7a1a",
    "full match":   "#1a7a1a",
    "partial":      "#cc7a00",
    "partial match":"#cc7a00",
    "didn't meet":  "#c0392b",
    "not met":      "#c0392b",
}

_STATUS_RE = re.compile(
    r'\b(Exceeds?|Aligned?|Partial(?:\s+Match)?|Did(?:n\'t)?\s*Meet|Not\s*Met|Met|Full\s+Match)(?=:)',
    re.IGNORECASE,
)

def _format_reason(text: str) -> str:
    """Color and bold status words; place 'Not Met:' on its own line."""
    # Move "Not Met:" onto its own line (may follow a period or space)
    text = re.sub(
        r'\.?\s+Not Met:',
        '<br><b style="color:#c0392b;">Not Met:</b>',
        text, flags=re.IGNORECASE,
    )

    def _style(m: re.Match) -> str:
        word = m.group(0)
        color = _STATUS_COLOR.get(word.lower().rstrip(":"), "#1a1a2e")
        return f'<b style="color:{color};">{word}</b>'

    return _STATUS_RE.sub(_style, text)

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

st.markdown(f"""
<div class="ph-header">
  <h1>Resume Ranker</h1>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Accordions for Upload and Ranking Sections
# ---------------------------------------------------------------------------


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
            jd_req_ids = {}
            if jd_files:
                st.markdown("**Requisition ID for each JD** *(optional but required for API access)*")
                for f in jd_files:
                    jd_req_ids[f.name] = st.text_input(
                        f.name,
                        key=f"jd_req_id_{f.name}",
                        placeholder="e.g. JOB-2024-001",
                        label_visibility="visible",
                    )
            if st.button("Upload JDs", disabled=not jd_files, key="btn_upload_jd"):
                errors = []
                for f in jd_files:
                    data = f.read()
                    req_id = jd_req_ids.get(f.name, "").strip()
                    try:
                        upload_blob(
                            JD_CONTAINER, f.name, data,
                            metadata={"req_id": req_id} if req_id else None,
                        )
                        text = extract_text(f.name, data)
                        if text.strip():
                            jd_search.index_document(f.name, text, req_id=req_id or None)
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
                            try:
                                blob_url = get_blob_url(RESUME_CONTAINER, f.name)
                                update_candidate_metadata(f.name, text, blob_url=blob_url)
                            except Exception as e:
                                logger.warning("candidate_metadata update skipped for %s: %s", f.name, e)
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


# --- Rank Resumes Button (after both accordions, unique key, 100% width/centered) ---



# Minimum space between sections and button
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
            {
                "Rank": i + 1,
                "Resume": r["candidate_name"],
                "Score": f"{r['total_score']:.1f} / 100",
                "Link": get_blob_url(RESUME_CONTAINER, r["candidate_name"]),
            }
            for i, r in enumerate(results)
        ]
        st.dataframe(
            pd.DataFrame(table_rows),
            column_config={"Link": st.column_config.LinkColumn("Open", display_text="Open ↗")},
            use_container_width=True,
            hide_index=True,
        )

        st.markdown('<hr class="ph-divider">', unsafe_allow_html=True)
        jd_kw_col, jd_link_col = st.columns([6, 1])
        with jd_kw_col:
            st.markdown('<div class="section-title">JD Keywords (extracted by OpenAI)</div>', unsafe_allow_html=True)
        with jd_link_col:
            try:
                st.link_button("Open JD ↗", get_blob_url(JD_CONTAINER, selected_jd))
            except Exception:
                pass

        try:
            kw = extract_jd_keywords_structured(jd_text)

            def _chips(items: list) -> str:
                return " ".join(
                    f'<span style="display:inline-block;background:#d0f5e8;color:#1a3c2b;'
                    f'border:1px solid #6B7C3F;border-radius:12px;padding:2px 10px;'
                    f'font-size:0.82rem;font-weight:600;margin:2px;">{item}</span>'
                    for item in items
                )

            _KW_ORDER = [
                ("jobTitle",        "Job Title",         False),
                ("technicalSkills", "Technical Skills",  True),
                ("experience",      "Experience",        False),
                ("certifications",  "Certifications",    True),
                ("education",       "Education",         False),
                ("location",        "Location",          False),
                ("domain",          "Domain",            False),
            ]

            for field, label, is_list in _KW_ORDER:
                val = kw.get(field)
                if not val:
                    continue
                content = _chips(val) if is_list else f'<span style="font-size:0.9rem;">{val}</span>'
                st.markdown(
                    f'<div style="margin-bottom:10px;">'
                    f'<div style="font-weight:700;color:{BLUE};font-size:0.8rem;margin-bottom:4px;">{label.upper()}</div>'
                    f'{content}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        except Exception as e:
            st.warning(f"Could not extract JD keywords: {e}")

        st.markdown('<hr class="ph-divider">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">Score Breakdown</div>', unsafe_allow_html=True)

        for i, r in enumerate(results):
            with st.expander(f"#{i + 1}  {r['candidate_name']}  —  {r['total_score']:.1f} / 100"):
                score_max = get_score_max()
                cols = st.columns(len(score_max))
                for col, (key, max_pts) in zip(cols, score_max.items()):
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
                score_max = get_score_max()
                for key in score_max:
                    reason = r["reasons"].get(key, "")
                    if reason:
                        label = _KEY_LABELS.get(key, key.title())
                        st.markdown(
                            f'<div style="margin-bottom:8px;padding-left:4px;">'
                            f'<div style="font-weight:700;color:{BLUE};font-size:0.85rem;margin-bottom:2px;">{label}</div>'
                            f'<div style="padding-left:8px;font-size:0.9rem;">{_format_reason(reason)}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

