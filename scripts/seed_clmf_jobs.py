"""
Script de carga de jobs CLMF a partir do CSV relacao_jobs_clmf.csv.

Uso:
    cd c:\\dev\\clmf_hub_basic\\backend
    python scripts/seed_clmf_jobs.py [--csv caminho/para/arquivo.csv] [--dry-run]

Comportamento:
  - Lê o CSV separado por ';'
  - Para cada linha: busca ou cria a carteirinha
  - Cria job com rotina='clmf_atualizar_rc' e params em JSONB
  - Idempotente: não duplica job com mesma carteirinha + data_RC
"""

import sys
import os
import csv
import argparse
import logging
from datetime import datetime

# Garante que o backend pode ser importado independentemente de onde o script é rodado
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import SessionLocal
from models import Job, Carteirinha, Convenio

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Nome do convênio conforme cadastrado pela migration 0028
CONVENIO_NOME = "CLMF"
ROTINA = "clmf_atualizar_rc"

# Caminho padrão do CSV relativo à raiz do projeto
DEFAULT_CSV = os.path.join(
    os.path.dirname(__file__), "..", "..", "relacao_jobs_clmf.csv"
)


def parse_args():
    parser = argparse.ArgumentParser(description="Importa jobs CLMF do CSV.")
    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV,
        help="Caminho para o arquivo CSV (padrão: relacao_jobs_clmf.csv na raiz do projeto)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas lê o CSV e exibe o que seria criado sem persistir nada",
    )
    return parser.parse_args()


def get_or_create_carteirinha(db, row: dict, id_convenio: int) -> Carteirinha:
    """Busca carteirinha pelo número; cria se não existir."""
    carteira = str(row["carteira"]).strip()
    cart = db.query(Carteirinha).filter(Carteirinha.carteirinha == carteira).first()

    if cart:
        log.debug(f"  Carteirinha existente: id={cart.id}, carteirinha={carteira}")
        return cart

    cart = Carteirinha(
        carteirinha=carteira,
        paciente=row["paciente"].strip(),
        id_paciente=int(row["id_Paciente"]),
        id_pagamento=id_convenio,   # reutiliza id_pagamento para referenciar convênio
        status="ativo",
        is_temporary=False,
    )
    db.add(cart)
    db.flush()  # obtém o id sem fechar a transação
    log.info(f"  Carteirinha CRIADA: id={cart.id}, carteirinha={carteira}")
    return cart


def job_already_exists(db, carteirinha_id: int, data_rc: str, abrev_esp: str) -> bool:
    """Idempotência: verifica se já existe job pending/processing/success para a mesma data e especialidade."""
    existing = (
        db.query(Job)
        .filter(
            Job.carteirinha_id == carteirinha_id,
            Job.rotina == ROTINA,
            Job.status.in_(["pending", "processing", "success"]),
            Job.params["data_RC"].astext == data_rc,
            Job.params["AbrevEsp"].astext == abrev_esp,
        )
        .first()
    )
    return existing is not None


def seed_jobs(csv_path: str, dry_run: bool = False):
    db = SessionLocal()
    try:
        # Localizar convênio CLMF
        convenio = db.query(Convenio).filter(Convenio.nome == CONVENIO_NOME).first()
        if not convenio:
            log.error(
                f"Convênio '{CONVENIO_NOME}' não encontrado. "
                "Execute a migration 0028 primeiro."
            )
            return

        log.info(f"Convênio '{CONVENIO_NOME}' encontrado: id={convenio.id}")

        # Ler CSV
        csv_path = os.path.abspath(csv_path)
        if not os.path.exists(csv_path):
            log.error(f"Arquivo CSV não encontrado: {csv_path}")
            return

        created = 0
        skipped = 0
        errors = 0
        
        # Memory tracker to avoid duplicates in the same run/transaction
        added_in_session = set()

        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            # Normalizar nomes de colunas: remover espaços extras (ex: ' id_especialidade')
            reader.fieldnames = [name.strip() for name in (reader.fieldnames or [])]

            for i, row in enumerate(reader, start=2):  # linha 1 = header
                # Re-mapear chaves para versão limpa (strip)
                row = {k.strip(): v for k, v in row.items()}
                # Ignorar linhas em branco
                if not row.get("carteira", "").strip():
                    continue

                data_rc = row.get("data_RC", "").strip()
                carteira = row.get("carteira", "").strip()
                log.info(
                    f"[Linha {i}] Paciente: {row.get('paciente','').strip()}"
                    f" | Carteira: {carteira} | data_RC: {data_rc}"
                )

                try:
                    cart = get_or_create_carteirinha(db, row, convenio.id)
                    abrev_esp = row.get("AbrevEsp", "").strip()

                    # Idempotência em memória (mesmo lote)
                    mem_key = (cart.id, data_rc, abrev_esp)
                    if mem_key in added_in_session:
                        log.info(
                            f"  → Job já agendado nesta sessão para carteirinha={carteira} "
                            f"data_RC={data_rc} AbrevEsp={abrev_esp}. Pulando."
                        )
                        skipped += 1
                        continue

                    # Idempotência no banco de dados
                    if job_already_exists(db, cart.id, data_rc, abrev_esp):
                        log.info(
                            f"  → Job já existe no DB para carteirinha={carteira} "
                            f"data_RC={data_rc} AbrevEsp={abrev_esp}. Pulando."
                        )
                        skipped += 1
                        continue

                    params = {
                        "id_paciente":    int(row["id_Paciente"]),
                        "paciente":       row["paciente"].strip(),
                        "id_profissional": int(row["Id Profissional"]),
                        "AbrevEsp":       row["AbrevEsp"].strip(),
                        "carteira":       carteira,
                        "caminho_pasta":  row["caminho_pasta"].strip(),
                        "id_especialidade": int(row["id_especialidade"]),
                        "nome_padrao":    row["nome_padrao"].strip(),
                        "data_RC":        data_rc,
                    }

                    if dry_run:
                        log.info(f"  [DRY-RUN] Criaria Job: {params}")
                        created += 1
                        added_in_session.add(mem_key)
                        continue

                    job = Job(
                        carteirinha_id=cart.id,
                        rotina=ROTINA,
                        params=params,
                        status="pending",
                        priority=0,
                    )
                    db.add(job)
                    created += 1
                    added_in_session.add(mem_key)
                    log.info(f"  → Job criado para carteirinha_id={cart.id}")

                except Exception as e:
                    log.error(f"  ERRO na linha {i}: {e}")
                    errors += 1
                    db.rollback()
                    continue

        if not dry_run:
            db.commit()
            log.info("Commit realizado.")

        log.info(
            f"\n{'[DRY-RUN] ' if dry_run else ''}Resultado: "
            f"{created} criados | {skipped} pulados | {errors} erros"
        )

    except Exception as e:
        log.error(f"Erro geral: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    args = parse_args()
    seed_jobs(args.csv, args.dry_run)
