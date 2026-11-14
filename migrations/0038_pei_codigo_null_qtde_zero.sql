-- Migration 0038: PEI sem codigo fantasma + nao congelar qtde zero
--
-- Diagnostico 14/11 (Parte 0): zero codigo NULL/vazio em patient_pei e
-- base_guias, e zero assinatura de bug de parsing (aut=0 com solic>0) —
-- as 490 guias ativas com qtde=0 sao bloqueadas (por design). Esta migration
-- e DEFENSIVA: trava as duas causas de divergencia apontadas pela analise.
--
-- 1) calculate_patient_pei: guard de codigo NULL/vazio (com NULLS DISTINCT
--    da UNIQUE, o ON CONFLICT nunca conflitaria e cada update criaria linha
--    nova para o mesmo par "nulo").
-- 2) freeze_guia_pei_base: NULLIF(qtde_solicitada, 0) — nao congela zero.
--    Guia Autorizada sempre traz qtde >0; se a leitura falhar (0) e o scrape
--    seguinte trouxer o valor real, o COALESCE do calculo passa a usa-lo.
-- 3) Backfill: inativa patient_pei sem codigo e descongela qtde_solicitada_pei
--    congelada em 0 (volta a seguir a qtde corrente da guia).

CREATE OR REPLACE FUNCTION calculate_patient_pei() RETURNS TRIGGER AS $fn$

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

    -- 0038: sem codigo_procedimento (NULL/vazio) nao ha PEI — guia bloqueada
    -- sem hint entra com codigo vazio e nao pode criar linha fantasma.
    -- (UNIQUE usa NULLS DISTINCT: com NULL o ON CONFLICT nunca conflitaria e
    -- cada update criaria linha nova.)
    IF target_codigo_procedimento IS NULL OR btrim(target_codigo_procedimento) = '' THEN
        RETURN NEW;
    END IF;

    -- 2. Find Latest VALID and ACTIVE Guia for this Context
    -- A guide is valid if Vinculo_prestador is 'Guia Válida'; in use only if ativo
    SELECT id, data_autorizacao, COALESCE(qtde_solicitada_pei, qtde_solicitada)
    INTO latest_guia_id, latest_data_autorizacao, latest_qtde
    FROM base_guias
    WHERE carteirinha_id = target_carteirinha_id
      AND codigo_procedimento = target_codigo_procedimento
      AND ativo
      AND (valida_prestador->>'Vinculo_prestador' LIKE 'Guia V%lida')
    ORDER BY data_autorizacao DESC, id DESC
    LIMIT 1;

    -- If no valid active guide: inactivate the pair's PEI (keep values/history)
    IF latest_guia_id IS NULL THEN
        UPDATE patient_pei
        SET ativo = FALSE, updated_at = NOW()
        WHERE carteirinha_id = target_carteirinha_id
          AND codigo_procedimento = target_codigo_procedimento;
        RETURN NEW;
    END IF;

    -- 3. Check for Override
    SELECT pei_semanal INTO override_val
    FROM pei_temp
    WHERE base_guia_id = latest_guia_id;

    -- 4. Calculate Logic (fórmula inalterada; base congelada na 1ª inserção da guia)
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

    -- 5. Upsert atômico into patient_pei (ON CONFLICT — sem race; reativa o par)
    INSERT INTO patient_pei (carteirinha_id, codigo_procedimento, base_guia_id, pei_semanal, validade, status, ativo, updated_at)
    VALUES (target_carteirinha_id, target_codigo_procedimento, latest_guia_id, final_pei, final_validade, final_status, TRUE, NOW())
    ON CONFLICT (carteirinha_id, codigo_procedimento)
    DO UPDATE SET
        base_guia_id = EXCLUDED.base_guia_id,
        pei_semanal = EXCLUDED.pei_semanal,
        validade = EXCLUDED.validade,
        status = EXCLUDED.status,
        ativo = TRUE,
        updated_at = EXCLUDED.updated_at;

    RETURN NEW;
END;

$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION freeze_guia_pei_base() RETURNS TRIGGER AS $fn$
BEGIN
    -- Valor usado pelo calculo do PEI: capturado uma unica vez, no INSERT da
    -- guia. Em updates o campo permanece intocado (preserva o valor original).
    -- 0038: NULLIF evita congelar zero (qtde ainda nao lida) — o COALESCE do
    -- calculate_patient_pei usara a qtde corrente quando ela chegar.
    IF TG_OP = 'INSERT' THEN
        NEW.qtde_solicitada_pei := NULLIF(NEW.qtde_solicitada, 0);
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- Backfill 1: PEI fantasma sem codigo (hoje: 0 linhas; defensivo)
UPDATE patient_pei SET ativo = FALSE, updated_at = NOW()
WHERE codigo_procedimento IS NULL OR btrim(codigo_procedimento) = '';

-- Backfill 2: descongela zeros (hoje: conferido no diagnostico; defensivo)
UPDATE base_guias SET qtde_solicitada_pei = NULL
WHERE qtde_solicitada_pei = 0;
