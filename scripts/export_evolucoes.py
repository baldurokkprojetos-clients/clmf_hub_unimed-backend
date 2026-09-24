"""
CLI: gera o xlsx de status dos jobs de evolucoes (OP2 clmf_imprimir_evolucao)
a partir dos resultados persistidos em evolucao_itens.

Uso (a partir de backend/):
  python scripts/export_evolucoes.py [--lote LOTE] [--job-id ID] [--status OK|PENDENTE|ERRO] [--out arquivo.xlsx]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from database import SessionLocal
from services import evolucao_service


def main():
    parser = argparse.ArgumentParser(description="Export xlsx de status das evolucoes")
    parser.add_argument("--lote", default=None, help="Filtrar por lote (nome do upload)")
    parser.add_argument("--job-id", type=int, default=None, help="Filtrar por job")
    parser.add_argument("--status", default=None, choices=["OK", "PENDENTE", "ERRO"], help="Filtrar por status")
    parser.add_argument("--out", default=None, help="Arquivo de saida (default: evolucoes_status_<ts>.xlsx)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        output = evolucao_service.gerar_xlsx_status(db, lote=args.lote, job_id=args.job_id, status=args.status)
    finally:
        db.close()

    out = args.out or f"evolucoes_status_{__import__('time').strftime('%Y%m%d_%H%M%S')}.xlsx"
    with open(out, "wb") as f:
        f.write(output.getvalue())
    print(f"OK: {out}")


if __name__ == "__main__":
    main()
