-- Migration 0039: insight_acoes — estado de notificação do painel de Insights
--
-- O painel de Insights (modal "Inteligência" em Importações) agrega listas
-- acionáveis (renovar guia, carteirinhas duplicadas, guias qtde=0, erros de
-- portal). Esta tabela persiste o estado "notificado" por item, para que o
-- painel exclua/destaque o que o usuário já tratou.
-- tipo+referencia_id identificam o item: ex. ('renovar_guia', carteirinha_id)
-- ou ('duas_carteirinhas', id_paciente). Sem UNIQUE no par (re-notificação
-- apenas atualiza notified_at).

CREATE TABLE IF NOT EXISTS insight_acoes (
    id SERIAL PRIMARY KEY,
    tipo TEXT NOT NULL,
    referencia_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pendente',   -- pendente | notificado
    notified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_insight_acoes_tipo_status ON insight_acoes (tipo, status);
CREATE INDEX IF NOT EXISTS ix_insight_acoes_referencia ON insight_acoes (referencia_id);

-- Regra da 0037 (defesa em profundidade): nenhuma tabela do public é exposta
-- à API REST; o event trigger auto_enable_rls já cobriu esta tabela na criação.
REVOKE ALL ON insight_acoes FROM anon, authenticated;
