

-- MIGRATION: 0001_initial_schema.sql --

-- Migration 0001: Initial Schema

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    api_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('Ativo', 'Inativo')),
    validade DATE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS carteirinhas (
    id SERIAL PRIMARY KEY,
    carteirinha TEXT NOT NULL UNIQUE,
    paciente TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    carteirinha_id INTEGER REFERENCES carteirinhas(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('success', 'pending', 'processing', 'error')),
    attempts INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 0,
    locked_by TEXT, -- Server URL that locked this job
    timeout TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS base_guias (
    id SERIAL PRIMARY KEY,
    carteirinha_id INTEGER REFERENCES carteirinhas(id) ON DELETE CASCADE,
    guia TEXT,
    data_autorizacao DATE,
    senha TEXT,
    validade DATE,
    codigo_terapia TEXT,
    nome_terapia TEXT,
    qtde_solicitada INTEGER,
    sessoes_autorizadas INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_locked_by ON jobs(locked_by);
CREATE INDEX IF NOT EXISTS idx_base_guias_carteirinha ON base_guias(carteirinha_id);




-- MIGRATION: 0002_seed_data.sql --

-- Migration 0002: Seed Data

-- NOTE: Replace the api_key below with your actual key after deployment
INSERT INTO users (username, api_key, status, validade, created_at, updated_at)
VALUES (
    'Clinica Larissa Martins Ferreira',
    'your_api_key_here', -- REPLACE WITH YOUR ACTUAL API KEY
    'Ativo',
    '2026-12-31',
    NOW(),
    NOW()
) ON CONFLICT DO NOTHING;


-- MIGRATION: 0003_add_carteirinha_fields.sql --

-- Migration 0003: Add id_paciente, id_pagamento, and status to carteirinhas
-- Date: 2026-01-10

-- Add new columns
-- Add new columns safely
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='carteirinhas' AND column_name='id_paciente') THEN
        ALTER TABLE carteirinhas ADD COLUMN id_paciente TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='carteirinhas' AND column_name='id_pagamento') THEN
        ALTER TABLE carteirinhas ADD COLUMN id_pagamento TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='carteirinhas' AND column_name='status') THEN
        ALTER TABLE carteirinhas ADD COLUMN status TEXT DEFAULT 'ativo';
    END IF;
END $$;

-- Update existing records to have default status
UPDATE carteirinhas 
SET status = 'ativo' 
WHERE status IS NULL;

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_carteirinhas_id_paciente 
ON carteirinhas(id_paciente);

CREATE INDEX IF NOT EXISTS idx_carteirinhas_id_pagamento 
ON carteirinhas(id_pagamento);

CREATE INDEX IF NOT EXISTS idx_carteirinhas_status 
ON carteirinhas(status);


-- MIGRATION: 0004_fix_id_types.sql --

-- Migration 0004: Fix id_paciente and id_pagamento to Integer type
-- Date: 2026-01-10

-- Drop indexes first
DROP INDEX IF EXISTS idx_carteirinhas_id_paciente;
DROP INDEX IF EXISTS idx_carteirinhas_id_pagamento;

-- Change column types to INTEGER
-- Use USING clause to convert text to integer
-- Change column types to INTEGER safely
DO $$
BEGIN
    -- Check if id_paciente is not integer (e.g. text) before altering
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='carteirinhas' AND column_name='id_paciente' AND data_type='text') THEN
        ALTER TABLE carteirinhas ALTER COLUMN id_paciente TYPE INTEGER USING id_paciente::INTEGER;
    END IF;
    
    -- Check if id_pagamento is not integer
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='carteirinhas' AND column_name='id_pagamento' AND data_type='text') THEN
        ALTER TABLE carteirinhas ALTER COLUMN id_pagamento TYPE INTEGER USING id_pagamento::INTEGER;
    END IF;
END $$;

-- Recreate indexes
CREATE INDEX idx_carteirinhas_id_paciente 
ON carteirinhas(id_paciente);

CREATE INDEX idx_carteirinhas_id_pagamento 
ON carteirinhas(id_pagamento);


-- MIGRATION: 0005_create_pei_tables.sql --

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


-- MIGRATION: 0006_create_pei_triggers.sql --

-- Function to calculate and update Patient PEI
CREATE OR REPLACE FUNCTION calculate_patient_pei() RETURNS TRIGGER AS $$
DECLARE
    target_carteirinha_id INTEGER;
    target_codigo_terapia TEXT;
    
    latest_guia_id INTEGER;
    latest_data_autorizacao DATE;
    latest_qtde INTEGER;
    
    override_val FLOAT;
    
    final_pei FLOAT;
    final_status TEXT;
    final_validade DATE;
    
    dummy_var RECORD;
BEGIN

    -- 1. Determine Target Context (Carteirinha + Therapy)
    IF TG_TABLE_NAME = 'base_guias' THEN
        target_carteirinha_id := NEW.carteirinha_id;
        target_codigo_terapia := NEW.codigo_terapia;
    ELSIF TG_TABLE_NAME = 'pei_temp' THEN
        -- Get info from the related guia
        SELECT carteirinha_id, codigo_terapia INTO target_carteirinha_id, target_codigo_terapia
        FROM base_guias WHERE id = NEW.base_guia_id;
        
        IF target_carteirinha_id IS NULL THEN
            RETURN NEW; -- Orphaned PeiTemp? Should not happen with FK, but safety first.
        END IF;
    END IF;

    -- 2. Find Latest Guia for this Context
    SELECT id, data_autorizacao, qtde_solicitada 
    INTO latest_guia_id, latest_data_autorizacao, latest_qtde
    FROM base_guias
    WHERE carteirinha_id = target_carteirinha_id 
      AND codigo_terapia = target_codigo_terapia
    ORDER BY data_autorizacao DESC, id DESC
    LIMIT 1;

    IF latest_guia_id IS NULL THEN
        -- No active guias? Maybe delete PatientPEI? 
        -- For now, do nothing or keep existing.
        RETURN NEW;
    END IF;

    -- 3. Check for Override
    SELECT pei_semanal INTO override_val
    FROM pei_temp
    WHERE base_guia_id = latest_guia_id;

    -- 4. Calculate Logic
    final_status := 'Pendente';
    final_pei := 0.0;
    
    IF latest_data_autorizacao IS NOT NULL THEN
        final_validade := latest_data_autorizacao + INTERVAL '180 days';
    ELSE
        final_validade := NULL;
    END IF;

    IF override_val IS NOT NULL THEN
        final_pei := override_val;
        final_status := 'Validado';
    ELSE
        IF latest_qtde IS NOT NULL AND latest_qtde > 0 THEN
            final_pei := latest_qtde::FLOAT / 16.0;
            -- Check if integer (modulo)
            IF final_pei = FLOOR(final_pei) THEN
                final_status := 'Validado';
            ELSE
                final_status := 'Pendente';
            END IF;
        ELSE
            final_pei := 0.0;
            final_status := 'Pendente';
        END IF;
    END IF;

    -- 5. Upsert into patient_pei
    -- We use LOOP dummy to handle race conditions in some PL/pgSQL patterns, 
    -- but usually INSERT ON CONFLICT is sufficient.
    
    UPDATE patient_pei 
    SET base_guia_id = latest_guia_id,
        pei_semanal = final_pei,
        validade = final_validade,
        status = final_status,
        updated_at = NOW()
    WHERE carteirinha_id = target_carteirinha_id AND codigo_terapia = target_codigo_terapia;
    
    IF NOT FOUND THEN
        INSERT INTO patient_pei (carteirinha_id, codigo_terapia, base_guia_id, pei_semanal, validade, status, updated_at)
        VALUES (target_carteirinha_id, target_codigo_terapia, latest_guia_id, final_pei, final_validade, final_status, NOW());
    END IF;

    RETURN NEW;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for BaseGuia
DROP TRIGGER IF EXISTS trigger_calc_pei_guia ON base_guias;
CREATE TRIGGER trigger_calc_pei_guia
AFTER INSERT OR UPDATE ON base_guias
FOR EACH ROW
EXECUTE FUNCTION calculate_patient_pei();

-- Trigger for PeiTemp
DROP TRIGGER IF EXISTS trigger_calc_pei_temp ON pei_temp;
CREATE TRIGGER trigger_calc_pei_temp
AFTER INSERT OR UPDATE ON pei_temp
FOR EACH ROW
EXECUTE FUNCTION calculate_patient_pei();


-- MIGRATION: 0007_add_temp_patient_fields.sql --

-- Add temporary patient fields
-- Add temporary patient fields safely
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='carteirinhas' AND column_name='is_temporary') THEN
        ALTER TABLE carteirinhas ADD COLUMN is_temporary BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='carteirinhas' AND column_name='expires_at') THEN
        ALTER TABLE carteirinhas ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE;
    END IF;
END $$;

-- Index for faster cleanup queries
CREATE INDEX IF NOT EXISTS idx_carteirinhas_temp_expiry ON carteirinhas(is_temporary, expires_at);


-- MIGRATION: 0008_add_index_job_status.sql --

-- Migration: Add Index to Jobs Status
-- Description: Improve performance of dashboard stats queries by indexing the status column.

CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status);
-- Migration: Add qtde_solicitada to base_guias
-- Description: Adds the missing qtde_solicitada column required for PEI calculation.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='base_guias' AND column_name='qtde_solicitada') THEN
        ALTER TABLE base_guias ADD COLUMN qtde_solicitada INTEGER;
    END IF;
END $$;
-- Migration: Add Performance Indexes
-- Description: Adds missing indexes for PEI dashboard and filtering to improve query performance.

-- 1. Index for Dashboard counts and List filtering (Status)
CREATE INDEX IF NOT EXISTS idx_patient_pei_status ON patient_pei(status);

-- 2. Index for Dashboard counts and Date filtering (Validade)
CREATE INDEX IF NOT EXISTS idx_patient_pei_validade ON patient_pei(validade);

-- 3. Index for Join performance (Base Guia FK)
CREATE INDEX IF NOT EXISTS idx_patient_pei_base_guia ON patient_pei(base_guia_id);

-- 4. Index for Sorting (Updated At)
CREATE INDEX IF NOT EXISTS idx_patient_pei_updated_at ON patient_pei(updated_at);
-- Migration: SaaS Performance Indexes
-- Description: Standardizes all performance indexes for SaaS deployment consistency.

-- Users
CREATE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key);

-- Carteirinhas
CREATE INDEX IF NOT EXISTS idx_carteirinhas_paciente ON carteirinhas(paciente);
CREATE INDEX IF NOT EXISTS idx_carteirinhas_carteirinha ON carteirinhas(carteirinha);
CREATE INDEX IF NOT EXISTS idx_carteirinhas_id_pagamento ON carteirinhas(id_pagamento);
CREATE INDEX IF NOT EXISTS idx_carteirinhas_id_paciente ON carteirinhas(id_paciente);

-- Base Guias
CREATE INDEX IF NOT EXISTS idx_base_guias_carteirinha ON base_guias(carteirinha_id);

-- Patient PEI
CREATE INDEX IF NOT EXISTS idx_patient_pei_carteirinha ON patient_pei(carteirinha_id);
CREATE INDEX IF NOT EXISTS idx_patient_pei_base_guia ON patient_pei(base_guia_id);
CREATE INDEX IF NOT EXISTS idx_patient_pei_status ON patient_pei(status);
CREATE INDEX IF NOT EXISTS idx_patient_pei_validade ON patient_pei(validade);
CREATE INDEX IF NOT EXISTS idx_patient_pei_updated_at ON patient_pei(updated_at);

-- Jobs
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
