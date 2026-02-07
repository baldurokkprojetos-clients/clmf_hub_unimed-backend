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
        
    return user
