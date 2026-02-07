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
