"""Controle do cron de criacao de jobs Unimed (23:01 UTC) pelo frontend.

A chave é o status do convenio 3 (Unimed Goiânia) em `convenios.status`:
  'ativo'   → cron HABILITADO (padrão)
  'inativo' → cron PAUSADO (não cria os jobs diários)

Persistido no banco, então o toggle do frontend sobrevive a deploys/reinícios
(diferente de env var). O loop do cron (main.py) consulta este status a cada
execução; a env UNIMED_CRON_ENABLED=false continua valendo como corte geral.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models import Convenio, User

router = APIRouter(prefix="/cron", tags=["Cron"])

CONVENIO_UNIMED = 3  # Unimed Goiânia (id_pagamento=3, hardcoded no sistema)


class CronToggle(BaseModel):
    ativo: bool


@router.get("/unimed")
def status_cron_unimed(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = db.query(Convenio).filter(Convenio.id == CONVENIO_UNIMED).first()
    return {
        "ativo": bool(conv and conv.status == "ativo"),
        "convenio": conv.nome if conv else None,
    }


@router.post("/unimed")
def toggle_cron_unimed(
    payload: CronToggle,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = db.query(Convenio).filter(Convenio.id == CONVENIO_UNIMED).first()
    if not conv:
        raise HTTPException(status_code=404, detail=f"Convênio {CONVENIO_UNIMED} não encontrado")

    conv.status = "ativo" if payload.ativo else "inativo"
    conv.updated_at = datetime.utcnow()
    db.commit()
    return {
        "ativo": payload.ativo,
        "mensagem": f"Cron Unimed {'ATIVADO' if payload.ativo else 'PAUSADO'} — "
                    f"{'jobs serão criados' if payload.ativo else 'nenhum job será criado'} às 23:01 UTC (20:01 Brasília).",
    }
