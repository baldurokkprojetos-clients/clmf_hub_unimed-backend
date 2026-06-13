from fastapi import Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from database import get_db
from models import User
from datetime import datetime

async def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)):
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação ausente."
        )
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Formato de token inválido. Use 'Bearer <token>'."
        )
    
    token = authorization.split(" ")[1]
    
    # In this simple implementation, the token IS the api_key.
    # In a JWT implementation, we would decode the token here.
    user = db.query(User).filter(User.api_key == token).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou usuário não encontrado."
        )
        
    if user.status != "Ativo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário inativo. Entre em contato com o suporte."
        )
        
    if user.validade and user.validade < datetime.now().date():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chave de acesso vencida."
        )
        
    # Auto-logout check: disabled by user request
    from datetime import timezone, timedelta
    
    now_utc = datetime.now(timezone.utc)
    
    # if user.last_activity:
    #     # Check if difference is more than 20 minutes
    #     if now_utc - user.last_activity > timedelta(minutes=20):
    #         raise HTTPException(
    #             status_code=status.HTTP_401_UNAUTHORIZED,
    #             detail="Sessão expirada por inatividade. Faça login novamente."
    #         )
            
    # Throttle DB updates: only update if last_activity is empty or older than 1 minute
    if not user.last_activity or now_utc - user.last_activity > timedelta(minutes=1):
        user.last_activity = now_utc
        db.commit()
        
    return user

async def get_protocolo_user(current_user: User = Depends(get_current_user)):
    if not current_user.permitir_protocolo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário não possui permissão para acessar o módulo Protocolo Fichas."
        )
    return current_user
