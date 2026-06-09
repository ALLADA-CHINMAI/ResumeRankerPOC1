-- ============================================================
-- 001_create_tables.sql
-- ResumeRanker POC1 — initial schema
-- Run via SSMS against your existing database before deploying code
-- ============================================================

-- JD_Metadata: one row per job description
-- full_text stored here; jd_chunks Azure Search index is retired after this
CREATE TABLE JD_Metadata (
    id         INT            IDENTITY(1,1) PRIMARY KEY,
    req_id     NVARCHAR(128)  NULL,
    name       NVARCHAR(512)  NOT NULL UNIQUE,   -- filename (blob key + display name)
    blob_url   NVARCHAR(2048) NULL,              -- original JD file in Azure Blob Storage
    full_text  NVARCHAR(MAX)  NULL,              -- extracted JD text (replaces jd_chunks reconstruction)
    created_at DATETIMEOFFSET NOT NULL DEFAULT SYSDATETIMEOFFSET(),
    updated_at DATETIMEOFFSET NOT NULL DEFAULT SYSDATETIMEOFFSET()
);
CREATE UNIQUE INDEX idx_jd_req_id ON JD_Metadata(req_id) WHERE req_id IS NOT NULL;
CREATE INDEX        idx_jd_name   ON JD_Metadata(name);

-- Candidate_Profile: one row per resume file
-- resume_name = filename = Azure Search resume_chunks key (Stage 1 still uses this index)
-- parsed_text replaces the resumes-parsed blob container (used in Stage 2 GPT scoring)
-- profile_extracted = 0 until batch GPT job enriches email/name/etc.
CREATE TABLE Candidate_Profile (
    id                        INT            IDENTITY(1,1) PRIMARY KEY,
    resume_name               NVARCHAR(512)  NOT NULL UNIQUE,  -- filename = search index key
    email                     NVARCHAR(256)  NULL,
    full_name                 NVARCHAR(256)  NULL,
    phone                     NVARCHAR(64)   NULL,
    linkedin                  NVARCHAR(512)  NULL,
    role_family               NVARCHAR(128)  NULL,
    years_experience          FLOAT          NULL,
    current_company           NVARCHAR(256)  NULL,
    resume_blob_url           NVARCHAR(2048) NULL,             -- original resume file in blob storage
    parsed_text               NVARCHAR(MAX)  NULL,             -- full text for Stage 2 GPT-4o scoring
    ai_search_doc_id          NVARCHAR(512)  NULL,
    latest_application_status NVARCHAR(128)  NULL,
    candidate_status          NVARCHAR(128)  NULL,
    profile_extracted         BIT            NOT NULL DEFAULT 0,  -- 1 after batch GPT profile job
    created_at                DATETIMEOFFSET NOT NULL DEFAULT SYSDATETIMEOFFSET(),
    updated_at                DATETIMEOFFSET NOT NULL DEFAULT SYSDATETIMEOFFSET()
);
CREATE INDEX idx_cp_resume_name     ON Candidate_Profile(resume_name);
CREATE INDEX idx_cp_email           ON Candidate_Profile(email) WHERE email IS NOT NULL;
CREATE INDEX idx_cp_pending_profile ON Candidate_Profile(profile_extracted) WHERE profile_extracted = 0;

-- Interview_History: many rows per candidate, FK to Candidate_Profile
-- Migrated from candidate_data.xlsx interview_history sheet
CREATE TABLE Interview_History (
    id             INT            IDENTITY(1,1) PRIMARY KEY,
    candidate_id   INT            NOT NULL REFERENCES Candidate_Profile(id),
    interview_date DATE           NULL,
    round_type     NVARCHAR(128)  NULL,   -- "Phone Screen", "Technical", "Final Round"
    round_number   INT            NULL,
    result         NVARCHAR(64)   NULL,   -- "Pass", "Fail", "Pending"
    job_role       NVARCHAR(256)  NULL,
    req_id         NVARCHAR(128)  NULL,   -- link back to JD if known
    created_at     DATETIMEOFFSET NOT NULL DEFAULT SYSDATETIMEOFFSET()
);
CREATE INDEX idx_ih_candidate ON Interview_History(candidate_id);

-- Ranking_Cache: stores Stage 2 GPT scores for Stage-1-filtered top-K candidates per JD
-- NEVER stores all candidates x all JDs — only what Stage 1 surfaced as relevant
-- Stage 1 (Azure Search hybrid) always runs live and for free; only those K results are cached
CREATE TABLE Ranking_Cache (
    id           INT            IDENTITY(1,1) PRIMARY KEY,
    jd_id        INT            NOT NULL REFERENCES JD_Metadata(id),
    candidate_id INT            NOT NULL REFERENCES Candidate_Profile(id),
    total_score  FLOAT          NOT NULL,
    scores       NVARCHAR(MAX)  NOT NULL,   -- JSON {"experience":28,"technicalSkills":35,...}
    reasons      NVARCHAR(MAX)  NOT NULL,   -- JSON {"experience":"Aligned: ..."}
    scored_at    DATETIMEOFFSET NOT NULL DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT uq_jd_candidate UNIQUE (jd_id, candidate_id)
);
CREATE INDEX idx_rc_jd_score ON Ranking_Cache(jd_id, total_score DESC);
