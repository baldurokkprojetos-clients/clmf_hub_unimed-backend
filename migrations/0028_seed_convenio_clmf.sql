-- Migration 0028: Seed convenio CLMF
-- Registra o convênio "CLMF" (Clínica Larissa Martins Ferreira) na tabela convenios.
-- Idempotente: insere apenas se não existir registro com nome='CLMF'.

INSERT INTO convenios (nome, status, created_at, updated_at)
SELECT 'CLMF', 'ativo', NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM convenios WHERE nome = 'CLMF');

