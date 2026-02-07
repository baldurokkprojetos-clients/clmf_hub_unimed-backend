-- Migration: Add qtde_solicitada to base_guias
-- Description: Adds the missing qtde_solicitada column required for PEI calculation.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='base_guias' AND column_name='qtde_solicitada') THEN
        ALTER TABLE base_guias ADD COLUMN qtde_solicitada INTEGER;
    END IF;
END $$;
