-- Migration 0022: Complete standardization of therapy code column naming

-- 1. Rename column in patient_pei
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name='patient_pei' AND column_name='codigo_terapia'
    ) THEN
        ALTER TABLE patient_pei RENAME COLUMN codigo_terapia TO codigo_procedimento;
    END IF;
END $$;

DROP INDEX IF EXISTS idx_patient_pei_codigo_terapia;
CREATE INDEX IF NOT EXISTS idx_patient_pei_codigo_procedimento ON patient_pei(codigo_procedimento);

-- 2. Update function calculate_patient_pei
CREATE OR REPLACE FUNCTION calculate_patient_pei() RETURNS TRIGGER AS $$
DECLARE
    target_carteirinha_id INTEGER;
    target_codigo_procedimento TEXT;
    
    latest_guia_id INTEGER;
    latest_data_autorizacao DATE;
    latest_qtde INTEGER;
    
    override_val FLOAT;
    
    final_pei FLOAT;
    final_status TEXT;
    final_validade DATE;
BEGIN

    -- 1. Determine Target Context (Carteirinha + Procedure)
    IF TG_TABLE_NAME = 'base_guias' THEN
        target_carteirinha_id := NEW.carteirinha_id;
        target_codigo_procedimento := NEW.codigo_procedimento;
    ELSIF TG_TABLE_NAME = 'pei_temp' THEN
        -- Get info from the related guia
        SELECT carteirinha_id, codigo_procedimento INTO target_carteirinha_id, target_codigo_procedimento
        FROM base_guias WHERE id = NEW.base_guia_id;
        
        IF target_carteirinha_id IS NULL THEN
            RETURN NEW; 
        END IF;
    END IF;

    -- 2. Find Latest Guia for this Context
    SELECT id, data_autorizacao, qtde_solicitada 
    INTO latest_guia_id, latest_data_autorizacao, latest_qtde
    FROM base_guias
    WHERE carteirinha_id = target_carteirinha_id 
      AND codigo_procedimento = target_codigo_procedimento
    ORDER BY data_autorizacao DESC, id DESC
    LIMIT 1;

    IF latest_guia_id IS NULL THEN
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
    UPDATE patient_pei 
    SET base_guia_id = latest_guia_id,
        pei_semanal = final_pei,
        validade = final_validade,
        status = final_status,
        updated_at = NOW()
    WHERE carteirinha_id = target_carteirinha_id AND codigo_procedimento = target_codigo_procedimento;
    
    IF NOT FOUND THEN
        INSERT INTO patient_pei (carteirinha_id, codigo_procedimento, base_guia_id, pei_semanal, validade, status, updated_at)
        VALUES (target_carteirinha_id, target_codigo_procedimento, latest_guia_id, final_pei, final_validade, final_status, NOW());
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
