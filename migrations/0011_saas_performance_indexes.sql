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
