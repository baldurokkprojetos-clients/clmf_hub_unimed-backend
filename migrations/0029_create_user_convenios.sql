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

-- Seed: vincular usuário principal ao convênio CLMF com credenciais.
-- Idempotente via UNIQUE(user_id, id_convenio).
-- TODO: substituir senha em texto puro por versão criptografada antes do deploy.
INSERT INTO user_convenios (user_id, id_convenio, login, senha_criptografada)
SELECT u.id, c.id, 'diogomat11@hotmail.com', 'Arju2020@'
FROM users u
CROSS JOIN convenios c
WHERE u.id = (SELECT MIN(id) FROM users WHERE username LIKE '%Larissa%')
  AND c.nome = 'CLMF'
ON CONFLICT (user_id, id_convenio) DO NOTHING;
