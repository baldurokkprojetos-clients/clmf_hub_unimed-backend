-- Migration 0029: Criar tabela user_convenios
-- Permite vincular credenciais de portal por usuário x convênio,
-- seguindo o padrão estabelecido no projeto Agenda_hub_MultiConv.

CREATE TABLE IF NOT EXISTS user_convenios (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER REFERENCES users(id)      ON DELETE CASCADE,
    id_convenio         INTEGER REFERENCES convenios(id)  ON DELETE CASCADE,
    login               TEXT,
    senha_criptografada TEXT,
    UNIQUE (user_id, id_convenio)
);

-- Seed: vincular o usuário 'Clinica Larissa Martins Ferreira'
-- ao convênio CLMF com as credenciais de acesso.
-- IMPORTANTE: substituir <SENHA_CRIPTOGRAFADA> pela senha cifrada
-- via security_utils antes de executar em produção.
INSERT INTO user_convenios (user_id, id_convenio, login, senha_criptografada)
SELECT
    u.id,
    c.id,
    'diogomat11@hotmail.com',
    'Arju2020@'          -- TODO: criptografar com security_utils antes do deploy
FROM users u
CROSS JOIN convenios c
WHERE u.username ILIKE '%Larissa%'
  AND c.nome = 'CLMF'
ON CONFLICT DO NOTHING;
