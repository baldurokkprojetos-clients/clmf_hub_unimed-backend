-- Add valida_prestador column as JSONB to base_guias table
ALTER TABLE base_guias ADD COLUMN IF NOT EXISTS valida_prestador JSONB;
