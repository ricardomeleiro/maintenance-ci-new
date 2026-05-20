from typing import Optional
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models.user import User, Role
from services.auth import decode_token


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return db.query(User).filter(User.id == int(user_id), User.is_active == True).first()


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user_optional(request, db)
    if not user:
        # FastAPI will catch 302 and redirect in middleware
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user


def require_approver(user: User = Depends(require_login)) -> User:
    if user.role not in (Role.APPROVER, Role.ADMIN):
        raise HTTPException(status_code=403, detail="Acesso restrito a aprovadores.")
    return user


def require_admin(user: User = Depends(require_login)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    return user


def add_audit(
    db: Session,
    user_id: int,
    action: str,
    entity_type: str = None,
    entity_id: int = None,
    details: dict = None,
    ip: str = None,
):
    from models.audit import AuditLog
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip_address=ip,
    )
    db.add(log)
    db.commit()
