-- Migration 0035: Estado ativo/inativo para guias e PEI (fim do "apaga tudo" do cron)
--
-- Contexto: o cron das 23:00 (backend/main.py) apagava pei_temp, patient_pei e
-- base_guias e reprocessava tudo durante a noite. O reprocessamento reinseria as
-- guias e o trigger calculate_patient_pei recalculava o PEI (qtde_solicitada/16)
-- com o valor ATUAL do portal — que diminui conforme as sessões são consumidas —
-- distorcendo o PEI a cada ciclo.
--
-- Novo modelo (sem apagar nada):
-- * qtde_solicitada_pei: valor-base do PEI congelado na PRIMEIRA inserção da guia
--   (trigger freeze_guia_pei_base). O cálculo do PEI passa a usar esse valor.
-- * ativo (base_guias/patient_pei): guias sem atualização (updated_at) há mais de
--   24h são marcadas Inativas pelo cron; uma nova atualização reativa guia e PEI.
-- * O trigger considera apenas guias válidas E ativas como base do par; sem
--   guia-base ativa o PEI do par é INATIVADO (preserva valores/histórico) em vez
--   de deletado.
-- * Índice único (carteirinha_id, guia): sem o DELETE diário não há mais "reset"
--   que mascara duplicatas do upsert aplicativo (mesma estratégia da 0031).

-- 1. Novas colunas
ALTER TABLE base_guias ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE patient_pei ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE base_guias ADD COLUMN IF NOT EXISTS qtde_solicitada_pei INTEGER;

-- Backfill: congela o valor atual como base do PEI para guias já existentes
UPDATE base_guias SET qtde_solicitada_pei = qtde_solicitada WHERE qtde_solicitada_pei IS NULL;

-- 2. Dedupe de base_guias por (carteirinha_id, guia), mantendo o maior id.
--    Antes de remover duplicatas, reponta patient_pei/pei_temp para a guia
--    mantida — as FKs são ON DELETE CASCADE e removeriam o PEI junto.
DO $$
DECLARE
    grp RECORD;
    keep_id INTEGER;
    dup_id INTEGER;
    keep_pei_temp_id INTEGER;
BEGIN
    FOR grp IN
        SELECT carteirinha_id, guia
        FROM base_guias
        GROUP BY carteirinha_id, guia
        HAVING COUNT(*) > 1
    LOOP
        SELECT MAX(id) INTO keep_id FROM base_guias
        WHERE carteirinha_id = grp.carteirinha_id AND guia = grp.guia;

        -- pei_temp: base_guia_id é UNIQUE — mantém apenas o override mais
        -- recente do grupo (será repontado para a guia mantida)
        SELECT MAX(pt.id) INTO keep_pei_temp_id
        FROM pei_temp pt
        JOIN base_guias g ON pt.base_guia_id = g.id
        WHERE g.carteirinha_id = grp.carteirinha_id AND g.guia = grp.guia;

        FOR dup_id IN
            SELECT id FROM base_guias
            WHERE carteirinha_id = grp.carteirinha_id AND guia = grp.guia AND id < keep_id
        LOOP
            UPDATE patient_pei SET base_guia_id = keep_id WHERE base_guia_id = dup_id;

            DELETE FROM pei_temp WHERE base_guia_id = dup_id AND id IS DISTINCT FROM keep_pei_temp_id;
            UPDATE pei_temp SET base_guia_id = keep_id WHERE base_guia_id = dup_id;
        END LOOP;
    END LOOP;
END $$;

DELETE FROM base_guias dup
USING base_guias keep
WHERE keep.carteirinha_id = dup.carteirinha_id
  AND keep.guia = dup.guia
  AND keep.id > dup.id;

-- 3. Constraint única para impedir novas duplicatas
CREATE UNIQUE INDEX IF NOT EXISTS uq_base_guias_cart_guia
    ON base_guias (carteirinha_id, guia);

-- 4. Congelamento do valor-base do PEI na primeira inserção da guia
CREATE OR REPLACE FUNCTION freeze_guia_pei_base() RETURNS TRIGGER AS $$
BEGIN
    -- Valor usado pelo cálculo do PEI: capturado uma única vez, no INSERT da guia.
    -- Em updates o campo permanece intocado (preserva o valor original).
    IF TG_OP = 'INSERT' THEN
        NEW.qtde_solicitada_pei := NEW.qtde_solicitada;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_freeze_guia_pei_base ON base_guias;
CREATE TRIGGER trg_freeze_guia_pei_base
BEFORE INSERT ON base_guias
FOR EACH ROW
EXECUTE FUNCTION freeze_guia_pei_base();

-- 5. calculate_patient_pei v2 — mesma fórmula/estrutura da 0031, três mudanças:
--    a) guia-base = mais recente válida E ativa do par;
--    b) cálculo usa o valor congelado (COALESCE(qtde_solicitada_pei, qtde_solicitada));
--    c) sem guia-base ativa o PEI do par é inativado (preserva valores/histórico)
--       em vez de deletado; com guia-base ativa o upsert reativa o PEI (ativo=TRUE).
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
$$ LANGUAGE plpgsql;
