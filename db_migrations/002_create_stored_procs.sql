-- ============================================================
-- 002_create_stored_procs.sql
-- ResumeRanker POC1 — stored procedures
-- Run via SSMS after 001_create_tables.sql
-- ============================================================

-- Upsert a JD row
-- MERGE on req_id (stable SuccessFactors ID) when provided, else fall back to name
CREATE OR ALTER PROCEDURE sp_upsert_jd
    @name      NVARCHAR(512),
    @req_id    NVARCHAR(128)  = NULL,
    @blob_url  NVARCHAR(2048) = NULL,
    @full_text NVARCHAR(MAX)  = NULL
AS
BEGIN
    SET NOCOUNT ON;
    IF @req_id IS NOT NULL
    BEGIN
        MERGE JD_Metadata AS target
        USING (SELECT @req_id AS req_id) AS source
        ON target.req_id = source.req_id
        WHEN MATCHED THEN
            UPDATE SET
                name       = @name,
                blob_url   = COALESCE(@blob_url,  target.blob_url),
                full_text  = COALESCE(@full_text, target.full_text),
                updated_at = SYSDATETIMEOFFSET()
        WHEN NOT MATCHED THEN
            INSERT (name, req_id, blob_url, full_text)
            VALUES (@name, @req_id, @blob_url, @full_text);
    END
    ELSE
    BEGIN
        MERGE JD_Metadata AS target
        USING (SELECT @name AS name) AS source
        ON target.name = source.name
        WHEN MATCHED THEN
            UPDATE SET
                blob_url   = COALESCE(@blob_url,  target.blob_url),
                full_text  = COALESCE(@full_text, target.full_text),
                updated_at = SYSDATETIMEOFFSET()
        WHEN NOT MATCHED THEN
            INSERT (name, req_id, blob_url, full_text)
            VALUES (@name, NULL, @blob_url, @full_text);
    END
END;
GO

-- Pass 1: called immediately after text extraction (no profile fields yet)
-- MERGE on resume_name — creates the row so parsed_text is available for ranking right away
CREATE OR ALTER PROCEDURE sp_upsert_candidate_file
    @resume_name      NVARCHAR(512),
    @resume_blob_url  NVARCHAR(2048) = NULL,
    @parsed_text      NVARCHAR(MAX)  = NULL,
    @ai_search_doc_id NVARCHAR(512)  = NULL
AS
BEGIN
    SET NOCOUNT ON;
    MERGE Candidate_Profile AS target
    USING (SELECT @resume_name AS resume_name) AS source
    ON target.resume_name = source.resume_name
    WHEN MATCHED THEN
        UPDATE SET
            resume_blob_url  = COALESCE(@resume_blob_url,  target.resume_blob_url),
            parsed_text      = COALESCE(@parsed_text,      target.parsed_text),
            ai_search_doc_id = COALESCE(@ai_search_doc_id, target.ai_search_doc_id),
            updated_at       = SYSDATETIMEOFFSET()
    WHEN NOT MATCHED THEN
        INSERT (resume_name, resume_blob_url, parsed_text, ai_search_doc_id)
        VALUES (@resume_name, @resume_blob_url, @parsed_text, @ai_search_doc_id);
END;
GO

-- Pass 2: called after batch GPT-4o profile extraction
-- MERGE on email to deduplicate same person uploading a new resume file:
--   Case A — email already exists on a different row (same person, new file):
--             update that existing row with new resume info, delete the Pass 1 temp row
--   Case B — email is new: enrich the existing resume_name row with profile fields
CREATE OR ALTER PROCEDURE sp_upsert_candidate_profile
    @resume_name               NVARCHAR(512),
    @email                     NVARCHAR(256),
    @full_name                 NVARCHAR(256)  = NULL,
    @phone                     NVARCHAR(64)   = NULL,
    @linkedin                  NVARCHAR(512)  = NULL,
    @role_family               NVARCHAR(128)  = NULL,
    @years_experience          FLOAT          = NULL,
    @current_company           NVARCHAR(256)  = NULL,
    @latest_application_status NVARCHAR(128)  = NULL,
    @candidate_status          NVARCHAR(128)  = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @existing_id INT = (
        SELECT id FROM Candidate_Profile
        WHERE email = @email AND resume_name != @resume_name
    );

    IF @existing_id IS NOT NULL
    BEGIN
        -- Case A: same person, new resume — update their canonical row
        UPDATE Candidate_Profile SET
            resume_name               = @resume_name,
            full_name                 = COALESCE(@full_name,                 full_name),
            phone                     = COALESCE(@phone,                     phone),
            linkedin                  = COALESCE(@linkedin,                  linkedin),
            role_family               = COALESCE(@role_family,               role_family),
            years_experience          = COALESCE(@years_experience,          years_experience),
            current_company           = COALESCE(@current_company,           current_company),
            latest_application_status = COALESCE(@latest_application_status, latest_application_status),
            candidate_status          = COALESCE(@candidate_status,          candidate_status),
            profile_extracted         = 1,
            updated_at                = SYSDATETIMEOFFSET()
        WHERE id = @existing_id;

        -- Carry parsed_text and blob_url from the Pass 1 temp row into canonical row
        UPDATE Candidate_Profile SET
            parsed_text     = (SELECT parsed_text     FROM Candidate_Profile WHERE resume_name = @resume_name),
            resume_blob_url = (SELECT resume_blob_url FROM Candidate_Profile WHERE resume_name = @resume_name)
        WHERE id = @existing_id;

        -- Remove the temp Pass 1 row (canonical row now has the correct resume_name)
        DELETE FROM Candidate_Profile WHERE resume_name = @resume_name AND id != @existing_id;
    END
    ELSE
    BEGIN
        -- Case B: new candidate — enrich existing resume_name row
        UPDATE Candidate_Profile SET
            email                     = @email,
            full_name                 = COALESCE(@full_name,                 full_name),
            phone                     = COALESCE(@phone,                     phone),
            linkedin                  = COALESCE(@linkedin,                  linkedin),
            role_family               = COALESCE(@role_family,               role_family),
            years_experience          = COALESCE(@years_experience,          years_experience),
            current_company           = COALESCE(@current_company,           current_company),
            latest_application_status = COALESCE(@latest_application_status, latest_application_status),
            candidate_status          = COALESCE(@candidate_status,          candidate_status),
            profile_extracted         = 1,
            updated_at                = SYSDATETIMEOFFSET()
        WHERE resume_name = @resume_name;
    END
END;
GO

-- Insert one interview round (append only — each round is a unique event)
CREATE OR ALTER PROCEDURE sp_insert_interview_history
    @candidate_id   INT,
    @interview_date DATE           = NULL,
    @round_type     NVARCHAR(128)  = NULL,
    @round_number   INT            = NULL,
    @result         NVARCHAR(64)   = NULL,
    @job_role       NVARCHAR(256)  = NULL,
    @req_id         NVARCHAR(128)  = NULL
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO Interview_History
        (candidate_id, interview_date, round_type, round_number, result, job_role, req_id)
    VALUES
        (@candidate_id, @interview_date, @round_type, @round_number, @result, @job_role, @req_id);
END;
GO
