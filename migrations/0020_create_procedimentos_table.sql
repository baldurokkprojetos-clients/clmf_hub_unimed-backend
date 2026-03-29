CREATE TABLE IF NOT EXISTS procedimentos (
    id SERIAL PRIMARY KEY,
    id_convenio INTEGER NOT NULL,
    nome TEXT NOT NULL,
    codigo_procedimento TEXT NOT NULL,
    autorizacao TEXT,
    status TEXT DEFAULT 'ativo',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_procedimentos_convenio_codigo ON procedimentos(id_convenio, codigo_procedimento);
