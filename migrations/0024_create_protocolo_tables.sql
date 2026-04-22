-- Migration: 0024_create_protocolo_tables.sql
-- Description: Creates tables for the Protocolo-Fichas module (PDF extraction via Gemini AI)

-- Tabela de Lotes (batch de processamento)
CREATE TABLE IF NOT EXISTS protocolo_lotes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',           -- pending, processing, completed, error
    total_arquivos INTEGER NOT NULL DEFAULT 0,
    total_processado INTEGER NOT NULL DEFAULT 0,
    total_erro INTEGER NOT NULL DEFAULT 0,
    total_sucesso INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tabela de Arquivos individuais (cada PDF dentro de um lote)
CREATE TABLE IF NOT EXISTS protocolo_arquivos (
    id SERIAL PRIMARY KEY,
    lote_id INTEGER NOT NULL REFERENCES protocolo_lotes(id) ON DELETE CASCADE,
    nome_original TEXT NOT NULL,
    nome_final TEXT,
    status TEXT NOT NULL DEFAULT 'pendente',          -- pendente, processando, sucesso, erro, revisao
    tamanho_bytes BIGINT DEFAULT 0,

    -- Dados extraídos pelo Gemini
    numero_guia_prestador TEXT,
    nome_beneficiario TEXT,
    numero_guia_principal TEXT,
    atendimentos JSONB,                               -- [{data, assinatura}, ...]

    -- Dados pós-processamento
    guia_normalizada TEXT,                            -- Após normalização de prefixo
    erro_mensagem TEXT,                               -- Mensagem de erro se houver
    gemini_model_used TEXT,                           -- Qual modelo processou
    gemini_api_key_index INTEGER,                     -- Qual chave usou

    -- Arquivo físico
    caminho_original TEXT,
    caminho_final TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_protocolo_lotes_status ON protocolo_lotes(status);
CREATE INDEX IF NOT EXISTS idx_protocolo_lotes_user ON protocolo_lotes(user_id);
CREATE INDEX IF NOT EXISTS idx_protocolo_arquivos_lote ON protocolo_arquivos(lote_id);
CREATE INDEX IF NOT EXISTS idx_protocolo_arquivos_status ON protocolo_arquivos(status);
