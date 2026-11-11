-- Migration 0037: Ativar RLS em todas as tabelas do schema public
--
-- Corrige os alertas críticos do Supabase Security Advisor
-- (rls_disabled_in_public / sensitive_columns_exposed):
-- sem RLS, qualquer pessoa com a URL do projeto e a anon key (pública,
-- embutida em qualquer frontend Supabase) podia ler, editar e apagar
-- tudo pela API REST (PostgREST) — incluindo users.api_key,
-- user_convenios.senha_criptografada e dados de pacientes.
--
-- Por que é seguro para este sistema: backend e worker conectam direto
-- ao Postgres via DATABASE_URL como role `postgres` (dono das tabelas),
-- e o dono ignora RLS. Nenhuma parte do código usa a API REST nem
-- Supabase Auth, portanto nenhuma policy é criada — anon e
-- authenticated ficam com acesso negado por padrão.

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        RAISE NOTICE 'RLS habilitado em public.%', t;
    END LOOP;
END
$$;

-- Defesa em profundidade: revoga os privilégios diretos que o Supabase
-- concede por padrão às roles da API REST. Mesmo que uma policy
-- permissiva seja criada por engano no futuro, as roles continuam
-- sem privilégios de tabela/sequência.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;

-- Evita regressão: tabelas novas criadas em public ganham RLS
-- automaticamente.
CREATE OR REPLACE FUNCTION public.auto_enable_rls()
RETURNS event_trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    obj RECORD;
BEGIN
    FOR obj IN
        SELECT * FROM pg_event_trigger_ddl_commands()
        WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS')
    LOOP
        IF obj.schema_name = 'public' THEN
            EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', obj.object_identity);
        END IF;
    END LOOP;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = 'auto_enable_rls') THEN
        CREATE EVENT TRIGGER auto_enable_rls
            ON ddl_command_end
            WHEN tag IN ('CREATE TABLE', 'CREATE TABLE AS')
            EXECUTE PROCEDURE public.auto_enable_rls();
    END IF;
END
$$;
