-- Migration 0033: Garantia de não-duplicação de jobs de evolucoes (OP2)
--
-- 1) Remove jobs duplicados por (idPaciente, dataExec) criados pelo upload
--    antigo (commit-por-job, sem dedup) que rodou em paralelo com o upload
--    corrigido. Mantém o MENOR id (job original de cada par). Exclui jobs
--    em processamento para não deletar algo em execução.
-- 2) Índice único parcial: torna a duplicação IMPOSSÍVEL no nível do banco,
--    mesmo com uploads concorrentes (o service já faz pre-check de dedup;
--    este é o guard definitivo).

DELETE FROM jobs j
USING jobs j2
WHERE j.rotina = 'clmf_imprimir_evolucao'
  AND j.status <> 'processing'
  AND j2.rotina = 'clmf_imprimir_evolucao'
  AND j2.params->>'idPaciente' = j.params->>'idPaciente'
  AND j2.params->>'dataExec' = j.params->>'dataExec'
  AND j2.id < j.id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_evolucao_par
    ON jobs ((params->>'idPaciente'), (params->>'dataExec'))
    WHERE rotina = 'clmf_imprimir_evolucao';
