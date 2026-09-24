-- Migration 0032: OP2 ImprimirEvolucao (rotina 'clmf_imprimir_evolucao')
--
-- evolucao_itens  -> resultado por linha conciliada (idPaciente/Guia/Data/ID_prof/Hora)
--                    Fonte da verdade do export de status (OK / PENDENTE / ERRO)
-- evolucao_claims -> reserva atomica de candidatos do portal entre jobs/servidores
--                    (evita que o mesmo item do portal seja conciliado 2x)
--
-- Carteirinha ancora: o dispatcher (dispatcher.py:560) exige carteirinha_id valido
-- (job.carteirinha_rel.carteirinha) e JobRequest.carteirinha_id e int. Evolucoes
-- nao tem entidade carteirinha no dominio — usa-se UMA unica linha ancora compartilhada
-- por todos os jobs da rotina. Nenhuma carteirinha por paciente e criada.
--
-- Indice parcial: estrategia de afinidade por paciente (server reivindica jobs irmãos
-- pendentes do mesmo idPaciente via params->>'idPaciente').

CREATE TABLE IF NOT EXISTS evolucao_itens (
    id              SERIAL PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    lote            TEXT,
    id_paciente     INTEGER,
    nome_paciente   TEXT,
    guia            TEXT,
    data_exec       DATE,
    profissional_id INTEGER,
    terapia         TEXT,
    profissao_id    INTEGER,
    hora_inicial    TEXT,
    status          TEXT NOT NULL DEFAULT 'PENDENTE',
    motivo          TEXT,
    ids_conciliados JSONB,
    pdf_path        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evolucao_itens_job      ON evolucao_itens(job_id);
CREATE INDEX IF NOT EXISTS idx_evolucao_itens_status   ON evolucao_itens(status);
CREATE INDEX IF NOT EXISTS idx_evolucao_itens_paciente ON evolucao_itens(id_paciente);
CREATE INDEX IF NOT EXISTS idx_evolucao_itens_lote     ON evolucao_itens(lote);
CREATE INDEX IF NOT EXISTS idx_evolucao_itens_data     ON evolucao_itens(data_exec);

-- Idempotencia de retry: mesma linha (job, guia, data, prof, hora) nunca duplica
CREATE UNIQUE INDEX IF NOT EXISTS uq_evolucao_item
    ON evolucao_itens(job_id, guia, data_exec, profissional_id, hora_inicial);

CREATE TABLE IF NOT EXISTS evolucao_claims (
    id             SERIAL PRIMARY KEY,
    fluxo          TEXT NOT NULL,              -- 'aba' | 'evolution'
    portal_item_id BIGINT NOT NULL,            -- id do tr (aba_atividade_single / evolution_single)
    job_id         INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_evolucao_claim
    ON evolucao_claims(fluxo, portal_item_id);
CREATE INDEX IF NOT EXISTS idx_evolucao_claims_job ON evolucao_claims(job_id);

-- Carteirinha ancora (infraestrutura da fila; ver cabecalho)
INSERT INTO carteirinhas (carteirinha, paciente, status)
VALUES ('EVOLUCOES-CLMF', 'IMPORTACAO EVOLUCOES', 'ativo')
ON CONFLICT (carteirinha) DO NOTHING;

-- Afinidade: claim de jobs irmãos do mesmo idPaciente
CREATE INDEX IF NOT EXISTS idx_jobs_evolucao_paciente
    ON jobs ((params->>'idPaciente'))
    WHERE rotina = 'clmf_imprimir_evolucao';
