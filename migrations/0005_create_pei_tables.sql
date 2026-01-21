CREATE TABLE IF NOT EXISTS pei_temp (
    id SERIAL PRIMARY KEY,
    base_guia_id INTEGER REFERENCES base_guias(id) ON DELETE CASCADE UNIQUE,
    pei_semanal FLOAT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS patient_pei (
    id SERIAL PRIMARY KEY,
    carteirinha_id INTEGER REFERENCES carteirinhas(id) ON DELETE CASCADE,
    codigo_terapia TEXT,
    base_guia_id INTEGER REFERENCES base_guias(id) ON DELETE CASCADE,
    pei_semanal FLOAT,
    validade DATE,
    status TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_patient_pei_carteirinha ON patient_pei(carteirinha_id);
