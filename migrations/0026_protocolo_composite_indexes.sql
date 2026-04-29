-- Migration: 0026_protocolo_composite_indexes.sql
-- Description: Adds composite indexes to further optimize Protocolo module performance for filtering + sorting.

-- Optimize list_lotes (Filter by user_id AND Order by created_at)
DROP INDEX IF EXISTS idx_protocolo_lotes_user;
CREATE INDEX IF NOT EXISTS idx_protocolo_lotes_user_created ON protocolo_lotes(user_id, created_at DESC);

-- Optimize get_stats (Filter by user_id AND Range by created_at)
-- (Already covered by the index above for filtering, but useful to have dedicated)

-- Optimize recalculate_lote_totals (Filter by lote_id AND Group by status)
DROP INDEX IF EXISTS idx_protocolo_arquivos_lote;
CREATE INDEX IF NOT EXISTS idx_protocolo_arquivos_lote_status ON protocolo_arquivos(lote_id, status);

-- Optimize general history sorting
CREATE INDEX IF NOT EXISTS idx_protocolo_lotes_created_desc ON protocolo_lotes(created_at DESC);
