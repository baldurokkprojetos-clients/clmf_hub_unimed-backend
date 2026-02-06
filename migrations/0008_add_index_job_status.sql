-- Migration: Add Index to Jobs Status
-- Description: Improve performance of dashboard stats queries by indexing the status column.

CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status);
