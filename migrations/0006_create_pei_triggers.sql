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
