-- Migration 0040: PEI recalcula o par ANTIGO quando a guia muda de código/carteirinha
--
-- Caso âncora (76788780, Enzo Gabriel, relatório 17/11 — analise/relatorio_fantasmas_pei.py):
-- guia inserida pelo caminho feliz com código da 1ª linha da tabela de terapias (5103,
-- qtde 64) gerou PEI 4.0; ao ser bloqueada e re-codificada pelo hint (5260, INSUFICIÊNCIA
-- DE REDE), o trigger recalculou apenas o par NOVO — o par antigo (5103) ficou órfão com
-- o PEI ativo/Validado para sempre. Único caso no banco (Classes A-D do relatório), mas
-- a classe de bug existia.
--
-- Correção em 2 partes (mais backfill):
-- 1) recalc_patient_pei_pair(carteirinha, codigo): toda a lógica de recálculo extraída
--    para UMA função (DRY) — guia-base = mais recente válida+ativa do par, override
--    pei_temp, cálculo /16, upsert ON CONFLICT reativando, ou INATIVA o par quando não
--    há guia-base (histórico preservado).
-- 2) calculate_patient_pei() vira despachante: recalcula o par de NEW e, em UPDATE com
--    carteirinha/código alterados, TAMBÉM o par de OLD (órfão inativa ou é promovido à
--    próxima guia válida do par). Bloqueio/desbloqueio SEM troca de código já eram
--    cobertos (recalc do próprio par).
-- 3) Backfill: recalcula todos os pares existentes (corrige o fantasma do Enzo;
--    idempotente).
--
-- Semântica confirmada com o PO (17/11): mudança de código → PEI recalculado nos dois
-- pares; guia bloqueada → PEI inativo (se não houver outra guia válida do código);
-- renovação/desbloqueio → reativa.

CREATE OR REPLACE FUNCTION recalc_patient_pei_pair(p_carteirinha_id INTEGER, p_codigo_procedimento TEXT)
RETURNS VOID AS $fn$
DECLARE
    latest_guia_id INTEGER;
    latest_data_autorizacao DATE;
    latest_qtde INTEGER;

    override_val FLOAT;

    final_pei FLOAT;
    final_status TEXT;
    final_validade DATE;
BEGIN
    -- Guard 0038: sem par não há PEI (guia bloqueada sem hint não cria linha fantasma)
    IF p_carteirinha_id IS NULL OR p_codigo_procedimento IS NULL
       OR btrim(p_codigo_procedimento) = '' THEN
        RETURN;
    END IF;

    -- Guia-base = mais recente VÁLIDA e ATIVA do par
    SELECT id, data_autorizacao, COALESCE(qtde_solicitada_pei, qtde_solicitada)
    INTO latest_guia_id, latest_data_autorizacao, latest_qtde
    FROM base_guias
    WHERE carteirinha_id = p_carteirinha_id
      AND codigo_procedimento = p_codigo_procedimento
      AND ativo
      AND (valida_prestador->>'Vinculo_prestador' LIKE 'Guia V%lida')
    ORDER BY data_autorizacao DESC, id DESC
    LIMIT 1;

    -- Sem guia-base ativa: inativa o PEI do par (preserva valores/histórico)
    IF latest_guia_id IS NULL THEN
        UPDATE patient_pei
        SET ativo = FALSE, updated_at = NOW()
        WHERE carteirinha_id = p_carteirinha_id
          AND codigo_procedimento = p_codigo_procedimento;
        RETURN;
    END IF;

    -- Override manual (pei_temp)
    SELECT pei_semanal INTO override_val
    FROM pei_temp
    WHERE base_guia_id = latest_guia_id;

    -- Cálculo (fórmula inalterada; base congelada na 1ª inserção da guia)
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

    -- Upsert atômico (reativa o par quando a guia volta a ser válida/renovada)
    INSERT INTO patient_pei (carteirinha_id, codigo_procedimento, base_guia_id, pei_semanal, validade, status, ativo, updated_at)
    VALUES (p_carteirinha_id, p_codigo_procedimento, latest_guia_id, final_pei, final_validade, final_status, TRUE, NOW())
    ON CONFLICT (carteirinha_id, codigo_procedimento)
    DO UPDATE SET
        base_guia_id = EXCLUDED.base_guia_id,
        pei_semanal = EXCLUDED.pei_semanal,
        validade = EXCLUDED.validade,
        status = EXCLUDED.status,
        ativo = TRUE,
        updated_at = EXCLUDED.updated_at;
END;
$fn$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION calculate_patient_pei() RETURNS TRIGGER AS $fn$
DECLARE
    target_carteirinha_id INTEGER;
    target_codigo_procedimento TEXT;
BEGIN
    -- 1. Contexto: base_guias → NEW; pei_temp → guia vinculada
    IF TG_TABLE_NAME = 'base_guias' THEN
        target_carteirinha_id := NEW.carteirinha_id;
        target_codigo_procedimento := NEW.codigo_procedimento;

        -- 0040 — par ANTIGO: guia que muda de carteirinha/código deixa o PEI do
        -- par velho órfão (caso 76788780: 5103→5260 com bloqueio). Recalcular
        -- também o par antigo: inativa se não houver outra guia válida, ou
        -- promove o PEI à próxima guia válida do par.
        IF TG_OP = 'UPDATE' THEN
            IF OLD.carteirinha_id IS DISTINCT FROM NEW.carteirinha_id
               OR OLD.codigo_procedimento IS DISTINCT FROM NEW.codigo_procedimento THEN
                PERFORM recalc_patient_pei_pair(OLD.carteirinha_id, OLD.codigo_procedimento);
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME = 'pei_temp' THEN
        SELECT carteirinha_id, codigo_procedimento INTO target_carteirinha_id, target_codigo_procedimento
        FROM base_guias WHERE id = NEW.base_guia_id;

        IF target_carteirinha_id IS NULL THEN
            RETURN NEW;
        END IF;
    END IF;

    -- 2. Par atual (novo)
    PERFORM recalc_patient_pei_pair(target_carteirinha_id, target_codigo_procedimento);
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- Triggers existentes (trigger_calc_pei_guia / trigger_calc_pei_temp, 0006)
-- continuam válidos: apenas os corpos das funções foram substituídos.

-- Backfill: recalcula todos os pares com PEI (corrige o fantasma do Enzo —
-- par 2250005103 sem guia válida → inativo; idempotente).
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT DISTINCT carteirinha_id, codigo_procedimento FROM patient_pei LOOP
        PERFORM recalc_patient_pei_pair(r.carteirinha_id, r.codigo_procedimento);
    END LOOP;
END $$;
