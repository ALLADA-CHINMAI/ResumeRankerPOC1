"""Streamlit frontend that integrates only with the API layer.

Run from project root:
    streamlit run ResumeRankerFrontend\app.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from html import escape

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("BACKEND_API_BASE_URL", "http://localhost:8001").rstrip("/")
RANK_ENDPOINT = f"{API_BASE_URL}/rankResumes"
CATEGORY_ORDER = [
    "experience",
    "technicalSkills",
    "certifications",
    "education",
    "location",
    "domainFit",
]


def _call_rank_api(req_id: str, top_k: int, natural_language_query: str = "") -> dict:
    request_payload = {"req_id": req_id, "top_k": top_k}
    if natural_language_query.strip():
        request_payload["natural_language_query"] = natural_language_query.strip()
    payload = json.dumps(request_payload).encode("utf-8")
    req = urllib.request.Request(
        RANK_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if e.fp else ""
        raise RuntimeError(f"API error {e.code}: {body or e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot connect to API at {API_BASE_URL}: {e.reason}") from e


def _reason_status(reason: str) -> str:
    text = (reason or "").strip().lower()
    if text.startswith("exceeds:"):
        return "exceeds"
    if text.startswith("aligned:"):
        return "aligned"
    if text.startswith("partial:"):
        return "partial"
    if text.startswith("didn't meet:") or text.startswith("did not meet:"):
        return "not-met"
    return "unknown"


def _ordered_categories(scores: dict, reasons: dict) -> list[str]:
    ordered = [k for k in CATEGORY_ORDER if k in scores or k in reasons]
    extras = sorted((set(scores.keys()) | set(reasons.keys())) - set(ordered))
    return ordered + extras


st.set_page_config(page_title="Resume Ranker", layout="wide")

st.markdown(
    """
    <style>
    .summary-card {
        background: linear-gradient(135deg, #f7f9fc 0%, #eef2f7 100%);
        border: 1px solid #d6deea;
        border-radius: 12px;
        padding: 14px 16px;
        margin: 8px 0 12px 0;
    }
    .summary-label {
        font-size: 0.85rem;
        color: #4a5568;
        margin-bottom: 4px;
    }
    .summary-value {
        font-size: 1.1rem;
        font-weight: 700;
        color: #1a365d;
    }
    .score-table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
        margin: 8px 0 2px 0;
    }
    .score-table th {
        text-align: left;
        background: #edf2f7;
        color: #2d3748;
        border: 1px solid #d6deea;
        padding: 8px 10px;
    }
    .score-table td {
        border: 1px solid #e2e8f0;
        padding: 8px 10px;
        vertical-align: top;
        color: #1f2937;
        word-wrap: break-word;
        white-space: normal;
    }
    .score-col-category { width: 22%; }
    .score-col-value { width: 10%; }
    .score-col-reason { width: 68%; }
    .cat-name {
        font-weight: 700;
        color: #1d4ed8;
    }
    .score-value {
        font-weight: 700;
    }
    .score-exceeds {
        color: #15803d;
    }
    .score-aligned {
        color: #b45309;
    }
    .score-partial, .score-not-met {
        color: #b91c1c;
    }
    .status-badge {
        display: inline-block;
        font-weight: 700;
        border-radius: 6px;
        padding: 1px 8px;
        margin-right: 6px;
    }
    .status-exceeds {
        background: #dcfce7;
        color: #166534;
    }
    .status-aligned {
        background: #fef3c7;
        color: #92400e;
    }
    .status-partial, .status-not-met {
        background: #fee2e2;
        color: #991b1b;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Resume Ranker")
st.caption("Upload resumes and JDs directly to storage, then run ranking via API.")

with st.form("rank_form"):
    req_id = st.text_input("Requisition ID", placeholder="e.g. 1001").strip()
    natural_language_query = st.text_area(
        "Natural Language Query (optional)",
        placeholder="e.g. Senior Python engineer with Azure, FastAPI, and healthcare domain",
        help="Fill either Requisition ID or Natural Language Query.",
    )
    top_k = st.number_input("Top K", min_value=1, max_value=50, value=10, step=1)
    st.caption("Required: fill either Requisition ID or Natural Language Query.")
    submitted = st.form_submit_button("Rank Resumes")

if submitted:
    if not req_id and not natural_language_query.strip():
        st.error("Please enter a requisition ID or a natural language query.")
    else:
        with st.spinner("Calling ranking API..."):
            try:
                response = _call_rank_api(
                    req_id=req_id,
                    top_k=int(top_k),
                    natural_language_query=natural_language_query,
                )
            except Exception as exc:
                st.error(str(exc))
                st.stop()

        st.success(f"Received {len(response.get('results', []))} ranked candidates.")

        jd_blob_url = response.get("jd_blob_url")
        jd_name = response.get("jd_name", "")
        jd_keywords = response.get("jd_keywords", {})
        query_text = response.get("query_text", "")
        has_jd_details = bool(jd_blob_url) or (isinstance(jd_keywords, dict) and bool(jd_keywords))

        results = response.get("results", [])
        if not results:
            st.info("No results returned for this requisition.")
        else:
            left, right = st.columns(2)
            with left:
                st.markdown(
                    f"""
                    <div class=\"summary-card\">
                      <div class=\"summary-label\">Requisition ID</div>
                      <div class=\"summary-value\">{escape(response.get('req_id', '') or 'N/A')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with right:
                st.markdown(
                    f"""
                    <div class=\"summary-card\">
                      <div class=\"summary-label\">{escape('JD Name' if has_jd_details else 'Search Query Text')}</div>
                      <div class=\"summary-value\">{escape((jd_name if has_jd_details else query_text) or 'N/A')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            table_rows = [
                {
                    "Rank": row.get("rank"),
                    "Candidate": row.get("candidate_name"),
                    "Total Score": row.get("total_score"),
                }
                for row in results
            ]
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

            jd_hdr_col, jd_link_col = st.columns([4, 1])
            with jd_hdr_col:
                st.subheader("JD Details" if has_jd_details else "Search Query Text")
            with jd_link_col:
                if jd_blob_url:
                    st.markdown(f"[View JD description]({jd_blob_url})")

            if has_jd_details:
                if isinstance(jd_keywords, dict) and jd_keywords:
                    jd_rows = []
                    for key, value in jd_keywords.items():
                        if isinstance(value, list):
                            value_text = ", ".join(str(v) for v in value)
                        else:
                            value_text = str(value)
                        jd_rows.append({"Keyword Group": key, "Value": value_text})
                    st.table(pd.DataFrame(jd_rows))
                else:
                    st.info("JD payload found, but no structured JD keywords were returned.")
            elif query_text and str(query_text).strip():
                st.markdown(
                    f"<div class='summary-card'><div class='summary-value'>{escape(str(query_text).strip())}</div></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.info("No search query text available for this run.")

            st.subheader("Score Breakdown")
            for row in results:
                title = f"#{row.get('rank')} {row.get('candidate_name')} - {row.get('total_score')}"
                with st.expander(title):
                    resume_blob_url = row.get("resume_blob_url")
                    if resume_blob_url:
                        st.markdown(f"**Open Resume:** [View resume]({resume_blob_url})")

                    scores = row.get("scores", {}) if isinstance(row.get("scores", {}), dict) else {}
                    reasons = row.get("reasons", {}) if isinstance(row.get("reasons", {}), dict) else {}
                    categories = _ordered_categories(scores, reasons)

                    if categories:
                        html_rows = []
                        for cat in categories:
                            reason_text = str(reasons.get(cat, "-") or "-")
                            status = _reason_status(reason_text)
                            score_value = scores.get(cat, "-")

                            reason_html = escape(reason_text)
                            if ":" in reason_text:
                                prefix, rest = reason_text.split(":", 1)
                                prefix_norm = prefix.strip().lower().replace("'", "")
                                if prefix_norm in {"exceeds", "aligned", "partial", "didnt meet", "did not meet"}:
                                    badge_text = "Didn't Meet" if "did" in prefix_norm else prefix.strip()
                                    reason_html = (
                                        f"<span class='status-badge status-{status}'>{escape(badge_text)}</span>"
                                        f"{escape(rest.strip())}"
                                    )

                            html_rows.append(
                                "<tr>"
                                f"<td><span class='cat-name'>{escape(cat)}</span></td>"
                                f"<td><span class='score-value score-{status}'>{escape(str(score_value))}</span></td>"
                                f"<td>{reason_html}</td>"
                                "</tr>"
                            )

                        st.markdown(
                            "<table class='score-table'>"
                            "<colgroup>"
                            "<col class='score-col-category' />"
                            "<col class='score-col-value' />"
                            "<col class='score-col-reason' />"
                            "</colgroup>"
                            "<thead><tr><th>Category</th><th>Score</th><th>Reason</th></tr></thead>"
                            f"<tbody>{''.join(html_rows)}</tbody>"
                            "</table>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.info("No score details returned for this candidate.")

