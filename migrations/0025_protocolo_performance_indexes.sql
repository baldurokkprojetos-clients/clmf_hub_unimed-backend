-- Migration: 0025_protocolo_performance_indexes.sql
-- Description: Adds indexes for created_at columns in Protocolo tables to speed up dashboard and history listings.

-- Index for history listing (order by created_at desc)
CREATE INDEX IF NOT EXISTS idx_protocolo_lotes_created_at ON protocolo_lotes(created_at DESC);

-- Index for dashboard statistics and file timeline
CREATE INDEX IF NOT EXISTS idx_protocolo_arquivos_created_at ON protocolo_arquivos(created_at DESC);
