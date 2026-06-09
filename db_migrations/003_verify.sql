-- ============================================================
-- 003_verify.sql
-- Run in SSMS after backfill to verify data migration
-- ============================================================

-- Row counts
SELECT COUNT(*) AS total_jds                FROM JD_Metadata;
SELECT COUNT(*) AS total_candidates         FROM Candidate_Profile;
SELECT COUNT(*) AS candidates_with_text     FROM Candidate_Profile WHERE parsed_text IS NOT NULL;
SELECT COUNT(*) AS candidates_with_profile  FROM Candidate_Profile WHERE profile_extracted = 1;
SELECT COUNT(*) AS total_interviews         FROM Interview_History;
SELECT COUNT(*) AS total_cached_scores      FROM Ranking_Cache;

-- Spot check recent candidates
SELECT TOP 10
    resume_name,
    email,
    full_name,
    years_experience,
    profile_extracted,
    LEN(parsed_text) AS text_len_chars,
    created_at
FROM Candidate_Profile
ORDER BY created_at DESC;

-- Spot check JDs
SELECT TOP 10
    name,
    req_id,
    LEN(full_text) AS text_len_chars,
    created_at
FROM JD_Metadata
ORDER BY created_at DESC;

-- Spot check ranking cache
SELECT TOP 10
    j.name          AS jd_name,
    c.resume_name   AS candidate,
    r.total_score,
    r.scored_at
FROM Ranking_Cache r
JOIN JD_Metadata       j ON j.id = r.jd_id
JOIN Candidate_Profile c ON c.id = r.candidate_id
ORDER BY r.scored_at DESC;
