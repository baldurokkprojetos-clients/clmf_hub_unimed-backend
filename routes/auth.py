from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db
from models import User
from pydantic import BaseModel

router = APIRouter()

class LoginRequest(BaseModel):
    access_key: str

@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.api_key == request.access_key).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chave de acesso inválida."
        )
    
    # Check if active
    if user.status != "Ativo":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário inativo."
        )

    # Check validity
    if user.validade and user.validade < datetime.now().date():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chave está vencida. Contratar nova."
        )

    from datetime import timezone
    user.last_activity = datetime.now(timezone.utc)
    db.commit()

    return {
        "token": user.api_key, # Simple token for now, or could use JWT
        "username": user.username,
        "validade": user.validade,
        "permitir_protocolo": user.permitir_protocolo
    }
