-- Migration 0017: Create Convenios Table

CREATE TABLE IF NOT EXISTS convenios (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    status TEXT DEFAULT 'ativo',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Note: We could add a unique constraint on 'nome' if appropriate, but keeping it simple based on requirements.
