-- Migration 0030: Adicionar campos rotina e params na tabela jobs
-- rotina → identifica qual convênio/operação o job representa
--          ex: "unimed_consulta_guias", "clmf_atualizar_rc"
-- params → JSON com parâmetros arbitrários específicos do job/convênio

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS rotina TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS params  JSONB;

-- Index para o dispatcher filtrar jobs por rotina eficientemente
CREATE INDEX IF NOT EXISTS idx_jobs_rotina
    ON jobs (rotina)
    WHERE rotina IS NOT NULL;
