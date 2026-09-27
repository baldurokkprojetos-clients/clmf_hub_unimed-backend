-- Migration 0034: políticas de cron compatíveis com backlog grande e auditoria
--
-- Contexto (incidente 2026-09-27 00:35 UTC): o upload de evolucoes_REPUBLICA
-- criou ~19.6k jobs pending; com 4 workers o backlog demora horas, mas o cron
-- cancel_stale_pending_jobs (0013) marcava 'error' qualquer pending com mais de
-- 15 MINUTOS — um único UPDATE cancelou 20.012 jobs que nunca rodaram
-- (attempts=0), incluindo 373 jobs da rotina Unimed.
--
-- Além disso, delete_old_jobs (0012) apagava jobs com 24h; jobs da rotina de
-- evoluções carregam a auditoria (evolucao_itens tem ON DELETE CASCADE) — a
-- auditoria de um dia seria destruída no dia seguinte às 03:00.
--
-- 1) cancel_stale_pending_jobs: 15 minutos → 24 horas (pending parado por 24h
--    é abandono real; backlog legítimo de horas não é).
-- 2) delete_old_jobs: 24 horas → 30 dias (preserva a auditoria do ciclo
--    mensal; itens evolucao_itens e claims são removidos junto, por cascade).

SELECT cron.schedule(
    'cancel_stale_pending_jobs',
    '*/5 * * * *',
    $$
    UPDATE jobs
    SET status = 'error', locked_by = NULL, updated_at = NOW()
    WHERE status = 'pending' AND updated_at < NOW() - INTERVAL '24 hours';
    $$
);

SELECT cron.schedule(
    'delete_old_jobs',
    '0 3 * * *',
    $$
    DELETE FROM jobs WHERE created_at < NOW() - INTERVAL '30 days';
    $$
);
