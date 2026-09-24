-- Migration 0031: Eliminar duplicatas de patient_pei e impedir novas ocorrências
--
-- Causa raiz das guias duplicadas no export do PEI:
-- o upsert do trigger calculate_patient_pei (UPDATE-then-INSERT) não tinha
-- constraint única no par (carteirinha_id, codigo_procedimento); inserções
-- concorrentes (workers paralelos) criavam 2+ linhas para o mesmo par.

-- 1. Remover duplicatas existentes (mantém a linha mais recente: maior id)
DELETE FROM patient_pei p
USING patient_pei keep
WHERE p.carteirinha_id = keep.carteirinha_id
  AND p.codigo_procedimento = keep.codigo_procedimento
  AND p.id < keep.id;

-- 2. Constraint única para impedir novas duplicatas
CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_pei_carteirinha_procedimento
    ON patient_pei (carteirinha_id, codigo_procedimento);

-- 3. Trigger reescrito com upsert atômico (ON CONFLICT) — elimina o race
--    do UPDATE-then-INSERT. Corpo equivalente ao da migration 0023,
--    alterada apenas a seção 5 (upsert).
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

    -- 2. Find Latest VALID Guia for this Context
    -- A guide is valid if valida_prestador is NULL or Vinculo_prestador is 'Guia Válida'
    SELECT id, data_autorizacao, qtde_solicitada
    INTO latest_guia_id, latest_data_autorizacao, latest_qtde
    FROM base_guias
    WHERE carteirinha_id = target_carteirinha_id
      AND codigo_procedimento = target_codigo_procedimento
      AND (valida_prestador->>'Vinculo_prestador' LIKE 'Guia V%lida')
    ORDER BY data_autorizacao DESC, id DESC
    LIMIT 1;

    -- If no valid guide found, remove from patient_pei and exit
    IF latest_guia_id IS NULL THEN
        DELETE FROM patient_pei
        WHERE carteirinha_id = target_carteirinha_id
          AND codigo_procedimento = target_codigo_procedimento;
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

    -- 5. Upsert atômico into patient_pei (ON CONFLICT — sem race)
    INSERT INTO patient_pei (carteirinha_id, codigo_procedimento, base_guia_id, pei_semanal, validade, status, updated_at)
    VALUES (target_carteirinha_id, target_codigo_procedimento, latest_guia_id, final_pei, final_validade, final_status, NOW())
    ON CONFLICT (carteirinha_id, codigo_procedimento)
    DO UPDATE SET
        base_guia_id = EXCLUDED.base_guia_id,
        pei_semanal = EXCLUDED.pei_semanal,
        validade = EXCLUDED.validade,
        status = EXCLUDED.status,
        updated_at = EXCLUDED.updated_at;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
