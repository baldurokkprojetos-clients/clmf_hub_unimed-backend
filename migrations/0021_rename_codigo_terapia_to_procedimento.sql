ALTER TABLE base_guias RENAME COLUMN codigo_terapia TO codigo_procedimento;
DROP INDEX IF EXISTS idx_base_guias_codigo_terapia;
CREATE INDEX IF NOT EXISTS idx_base_guias_codigo_procedimento ON base_guias(codigo_procedimento);
