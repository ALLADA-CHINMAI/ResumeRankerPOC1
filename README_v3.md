# Resume Ranker — v3 Update: Candidate Metadata & Interview History

This document covers what was added in v3. For prior context see [README.md](README.md) (v1 Streamlit UI + Azure setup) and [README_v2.md](README_v2.md) (v2 MCP server).

---

## What's New in v3

When a resume is uploaded and indexed through the Streamlit UI, a new helper now automatically **extracts structured identity/career fields via GPT and upserts a row in `candidate_data.xlsx`**. If the candidate's email exists in the `interview_history` sheet, their past interview stats are merged in at the same time.

The ranking pipeline and MCP server are **untouched**. This is purely additive.

---

## Changed / New Files

```
ResumeRankerPOC1/
├── ResumeRankerCommon/
│   └── xlsx_utils.py              ← New: metadata upsert helper
├── ResumeRankerFrontend/
│   └── app.py                     ← Updated: calls helper after indexing
├── candidate_data.xlsx            ← Written to during resume upload
└── requirements.txt               ← Added: openpyxl
```

---

## How It Works

### Trigger

The helper runs automatically inside the "Upload & Index Resumes" button handler in `app.py`, immediately after `store_parsed_text`. A failure here is caught and logged as a warning — it never blocks indexing.

```
Upload file
  → upload_blob()
  → extract_text()
  → resume_search.index_document()   ← Azure Cognitive Search
  → store_parsed_text()              ← Azure Blob cache
  → update_candidate_metadata()      ← candidate_data.xlsx  ✦ NEW
```

### GPT Field Extraction

One GPT call extracts seven fields from the first ~6 000 characters of the resume text:

| Field | Example |
|---|---|
| `full_name` | Arjun Mehta |
| `primary_email` | arjun.mehta@email.com |
| `primary_phone` | +91-9876543210 |
| `linkedin_url` | linkedin.com/in/arjunmehta |
| `role_family` | Software Engineer |
| `years_experience` | 4 |
| `current_company` | Infosys |

### Interview History Join

Email is used as the join key to look up the candidate in the `interview_history` sheet of `candidate_data.xlsx`. The following fields are computed from their full history:

| Field | Logic |
|---|---|
| `candidate_id` | From the matching `interview_history` row |
| `latest_application_status` | Result of the most recent interview (by date) |
| `last_interview_round` | Round number of the most recent interview |
| `total_applications` | Count of distinct job roles the candidate has been interviewed for |
| `candidate_status` | `Offer Extended` if last round was HR+Passed · `Rejected` if Rejected · `Active` otherwise |

Candidates whose email does not appear in `interview_history` still get a row created — interview fields are left blank.

### Upsert Behaviour

- On first upload: a new row is appended to `candidate_metadata` and `created_at` is set.
- On re-upload (same email): the existing row is updated in place; `created_at` is preserved and `updated_at` is refreshed.
- `latest_ai_search_doc_id` always reflects the most recently indexed filename.
- `latest_resume_blob_url` is the SAS URL generated at upload time.

---

## candidate_data.xlsx Schema

### Sheet 1 — `candidate_metadata` (written by this helper)

| Column | Source |
|---|---|
| candidate_id | interview_history join |
| full_name | GPT extraction |
| primary_email | GPT extraction (join key) |
| primary_phone | GPT extraction |
| linkedin_url | GPT extraction |
| role_family | GPT extraction |
| years_experience | GPT extraction |
| current_company | GPT extraction |
| latest_application_status | interview_history join |
| last_interview_round | interview_history join |
| total_applications | interview_history join |
| candidate_status | derived from interview_history |
| latest_ai_search_doc_id | resume filename in search index |
| latest_resume_blob_url | Azure Blob SAS URL |
| created_at | set on first index |
| updated_at | refreshed on every index |

### Sheet 2 — `interview_history` (read-only by this helper)

Pre-populated with 54 rows across 15 mock candidates. Each row is one interview round with score, feedback, result, and rejection reason. Email is the join key.

---

## Prerequisites

Install the new dependency if upgrading from v2:

```bash
pip install openpyxl
# or reinstall everything:
pip install -r requirements.txt
```

No new environment variables are required. The xlsx path defaults to `candidate_data.xlsx` at the project root and can be overridden:

```
CANDIDATE_DATA_PATH=/path/to/candidate_data.xlsx
```

---

---

## Roadmap — v4 Plan: Interview-History-Aware Ranking

The following changes are planned for the next iteration. Nothing below is implemented yet.

---

### Goal

Include each candidate's interview history as a scoring signal in Stage 2 GPT ranking, so the ranker can surface candidates who have repeatedly performed well in past rounds — not just those whose resume text matches the JD.

---

### 1. New Scoring Dimension — `interviewHistory` (max 15 pts)

Add a seventh dimension to the existing rubric in `ResumeRankerCommon/ranking.py`:

| New Dimension | Max | What it evaluates |
|---|---|---|
| interviewHistory | 15 | Average past interview score, highest round reached, recency, and absence of hard-rejection patterns (e.g. "stack mismatch") |

**Updated total: 115 pts.**

The GPT scoring prompt will receive a short interview summary block alongside the resume text:

```
--- Interview History ---
Rounds completed: 4  |  Latest result: Passed (HR)  |  Avg score: 88.8
Roles interviewed for: Software Engineer (Backend), Full Stack Engineer
Notable feedback: Strong Python/FastAPI, AWS hands-on confirmed, culture fit excellent
Rejection (if any): Full Stack Engineer — "Role mismatch: backend only"
```

Candidates with no interview history receive a neutral score (7–8 / 15) so they are not penalised.

---

### 2. Email → Resume Linkage

The ranking pipeline works from resume filenames, not emails. The bridge is `candidate_metadata.latest_ai_search_doc_id` written by the v3 helper.

Lookup path:
```
resume filename  →  candidate_metadata (latest_ai_search_doc_id)
               →  primary_email
               →  interview_history rows
               →  summary block injected into GPT prompt
```

A new internal helper `load_interview_summary_by_doc(doc_name)` in `xlsx_utils.py` will handle this lookup.

---

### 3. New MCP Tool — `get_candidate_interview_history`

Add to `ResumeRankerMCP/tools/profile.py`:

```
get_candidate_interview_history(candidate_name: str) -> dict
```

Returns the full interview history for a candidate (identified by resume filename), including all rounds, scores, feedback, and rejection reasons. Agents can use this to explain ranking decisions or to answer recruiter questions like "has this person been rejected before and why?"

---

### 4. Files to Change

| File | Change |
|---|---|
| `ResumeRankerCommon/ranking.py` | Add `interviewHistory` to `SCORE_MAX`; inject summary block into Stage 2 prompt; call `load_interview_summary_by_doc` |
| `ResumeRankerCommon/xlsx_utils.py` | Add `load_interview_summary_by_doc(doc_name)` lookup helper |
| `ResumeRankerMCP/tools/profile.py` | Add `get_candidate_interview_history` tool |
| `ResumeRankerMCP/main.py` | Register the new tool |
| `ResumeRankerFrontend/app.py` | Update `_KEY_LABELS` and `SCORE_MAX` references to include `interviewHistory` |

---

### 5. Guardrails

- If `candidate_data.xlsx` is missing or the email lookup fails, fall back to the existing 6-dimension rubric (100 pts) — ranking never hard-fails due to the xlsx.
- Rejection reasons from unrelated roles (e.g. "overqualified for junior role") must not penalise the candidate for a senior position — the prompt instructs GPT to consider rejection context.
- The `interviewHistory` score is capped at 15 regardless of how many rounds a candidate has passed.
