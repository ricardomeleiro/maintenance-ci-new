import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user_optional, add_audit
from services.auth import authenticate_user, create_access_token, hash_password

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user_optional(request, db):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, email, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "E-mail ou senha incorretos."},
            status_code=401,
        )

    if user.must_change_password:
        from models.password_reset import PasswordResetToken
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()
        reset_token = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(
            user_id=user.id,
            token=reset_token,
            expires_at=datetime.utcnow() + timedelta(hours=24),
        ))
        db.commit()
        add_audit(db, user.id, "first_login_redirect", ip=request.client.host if request.client else None)
        return RedirectResponse(f"/first-login/{reset_token}", status_code=302)

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        "access_token", token, httponly=True, samesite="lax", max_age=86400 * 7
    )
    add_audit(db, user.id, "login", ip=request.client.host if request.client else None)
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    return response


# ── Esqueci minha senha ───────────────────────────────────────────────────────

# ── Primeiro acesso — troca de senha obrigatória ─────────────────────────────

@router.get("/first-login/{token}", response_class=HTMLResponse)
def first_login_page(token: str, request: Request, db: Session = Depends(get_db)):
    from models.password_reset import PasswordResetToken
    record = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    valid = record is not None and record.expires_at > datetime.utcnow()
    return templates.TemplateResponse(
        "first_login.html",
        {"request": request, "token": token, "valid": valid, "error": None},
    )


@router.post("/first-login/{token}", response_class=HTMLResponse)
def first_login_submit(
    token: str,
    request: Request,
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    from models.user import User
    from models.password_reset import PasswordResetToken

    record = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    if not record or record.expires_at <= datetime.utcnow():
        return templates.TemplateResponse(
            "first_login.html",
            {"request": request, "token": token, "valid": False, "error": None},
        )

    if password != password2:
        return templates.TemplateResponse(
            "first_login.html",
            {"request": request, "token": token, "valid": True,
             "error": "As senhas não coincidem."},
        )

    if len(password) < 6:
        return templates.TemplateResponse(
            "first_login.html",
            {"request": request, "token": token, "valid": True,
             "error": "A senha deve ter pelo menos 6 caracteres."},
        )

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        return templates.TemplateResponse(
            "first_login.html",
            {"request": request, "token": token, "valid": False, "error": None},
        )

    user.hashed_password = hash_password(password)
    user.must_change_password = False
    db.delete(record)
    db.commit()

    # Log the user in directly after password change
    access_token = create_access_token({"sub": str(user.id), "role": user.role.value})
    add_audit(db, user.id, "first_login_password_set", ip=request.client.host if request.client else None)

    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie("access_token", access_token, httponly=True, samesite="lax", max_age=86400 * 7)
    return response


# ── Esqueci minha senha ───────────────────────────────────────────────────────

@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        "forgot_password.html", {"request": request, "sent": False, "error": None}
    )


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    from models.user import User
    from models.password_reset import PasswordResetToken
    from config import settings
    from sqlalchemy import func

    # Always show the same success message to avoid email enumeration
    user = db.query(User).filter(
        func.lower(User.email) == email.lower().strip(), User.is_active == True
    ).first()
    if user:
        # Clean up previous tokens for this user
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()

        token = secrets.token_urlsafe(32)
        expires = datetime.utcnow() + timedelta(hours=1)
        db.add(PasswordResetToken(user_id=user.id, token=token, expires_at=expires))
        db.commit()

        reset_link = f"{str(request.base_url).rstrip('/')}/reset-password/{token}"
        from services.notifications import notify_password_reset
        background_tasks.add_task(notify_password_reset, user.email, user.name, reset_link)

    return templates.TemplateResponse(
        "forgot_password.html", {"request": request, "sent": True, "error": None}
    )


@router.get("/reset-password/{token}", response_class=HTMLResponse)
def reset_password_page(token: str, request: Request, db: Session = Depends(get_db)):
    from models.password_reset import PasswordResetToken
    record = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    valid = record is not None and record.expires_at > datetime.utcnow()
    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "token": token, "valid": valid, "error": None, "success": False},
    )


@router.post("/reset-password/{token}", response_class=HTMLResponse)
def reset_password_submit(
    token: str,
    request: Request,
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    from models.user import User
    from models.password_reset import PasswordResetToken

    record = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    if not record or record.expires_at <= datetime.utcnow():
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "valid": False, "error": None, "success": False},
        )

    if password != password2:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "valid": True,
             "error": "As senhas não coincidem.", "success": False},
        )

    if len(password) < 6:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "valid": True,
             "error": "A senha deve ter pelo menos 6 caracteres.", "success": False},
        )

    user = db.query(User).filter(User.id == record.user_id).first()
    if user:
        user.hashed_password = hash_password(password)
        user.must_change_password = False
        db.delete(record)
        db.commit()

    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "token": token, "valid": True, "error": None, "success": True},
    )
