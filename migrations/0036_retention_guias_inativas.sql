-- Migration 0036: Retenção de guias inativas (contrapartida do fim do DELETE diário)
--
-- Sem o apagão diário do cron, base_guias/patient_pei acumulam dados
-- indefinidamente. Remoção diária (04:00, após os crons de jobs/logs) das guias
-- Inativas sem atualização há mais de 180 dias; as FKs ON DELETE CASCADE removem
-- pei_temp/patient_pei vinculados. Guias-base de PEIs ativos nunca são removidas:
-- o trigger calculate_patient_pei (0035) só aponta o PEI para guias ativas.

CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT cron.schedule(
    'delete_old_inactive_guias',
    '0 4 * * *',
    $$
    DELETE FROM base_guias
    WHERE ativo = FALSE
      AND updated_at < NOW() - INTERVAL '180 days';
    $$
);
