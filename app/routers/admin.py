from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from dependencies import require_admin, add_audit
from models.user import User, Role, ROLE_LABELS
from models.ticket import (
    Ticket, TicketStatus, TicketCategory, TicketPriority,
    STATUS_LABELS, STATUS_COLORS, PRIORITY_LABELS, PRIORITY_COLORS, CATEGORY_LABELS,
)
from models.audit import AuditLog
from services.auth import hash_password

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
def admin_root(current_user: User = Depends(require_admin)):
    return RedirectResponse("/admin/reports", status_code=302)


@router.get("/users", response_class=HTMLResponse)
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return templates.TemplateResponse(
        "admin/users.html",
        {"request": request, "current_user": current_user, "users": users,
         "Role": Role, "ROLE_LABELS": ROLE_LABELS},
    )


@router.get("/users/new", response_class=HTMLResponse)
def new_user_form(request: Request, current_user: User = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin/user_form.html",
        {"request": request, "current_user": current_user, "editing": None,
         "Role": Role, "ROLE_LABELS": ROLE_LABELS, "error": None},
    )


@router.post("/users/new")
def create_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    department: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(
            "admin/user_form.html",
            {"request": request, "current_user": current_user, "editing": None,
             "Role": Role, "ROLE_LABELS": ROLE_LABELS, "error": "E-mail já cadastrado."},
        )
    user = User(
        name=name, email=email,
        hashed_password=hash_password(password),
        role=Role(role),
        department=department or None,
    )
    db.add(user)
    db.commit()
    add_audit(db, current_user.id, "user_created", "user", user.id, {"email": email})
    return RedirectResponse("/admin/users", status_code=302)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
def edit_user_form(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    editing = db.query(User).filter(User.id == user_id).first()
    if not editing:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "admin/user_form.html",
        {"request": request, "current_user": current_user, "editing": editing,
         "Role": Role, "ROLE_LABELS": ROLE_LABELS, "error": None},
    )


@router.post("/users/{user_id}/edit")
def update_user(
    user_id: int,
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    department: str = Form(""),
    is_active: Optional[str] = Form(None),
    new_password: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404)

    conflict = db.query(User).filter(User.email == email, User.id != user_id).first()
    if conflict:
        return templates.TemplateResponse(
            "admin/user_form.html",
            {"request": request, "current_user": current_user, "editing": user,
             "Role": Role, "ROLE_LABELS": ROLE_LABELS, "error": "E-mail já em uso."},
        )

    user.name = name
    user.email = email
    user.role = Role(role)
    user.department = department or None
    user.is_active = is_active == "on"
    if new_password:
        user.hashed_password = hash_password(new_password)

    db.commit()
    add_audit(db, current_user.id, "user_updated", "user", user_id)
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/toggle")
def toggle_active(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Operação inválida.")
    user.is_active = not user.is_active
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.get("/reports", response_class=HTMLResponse)
def reports(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    total = db.query(func.count(Ticket.id)).scalar() or 0
    by_status = db.query(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status).all()
    by_category = db.query(Ticket.category, func.count(Ticket.id)).group_by(Ticket.category).all()
    by_priority = db.query(Ticket.priority, func.count(Ticket.id)).group_by(Ticket.priority).all()

    open_statuses = {TicketStatus.OPEN, TicketStatus.UNDER_REVIEW, TicketStatus.APPROVED, TicketStatus.IN_PROGRESS}
    open_count = sum(c for s, c in by_status if s in open_statuses)
    completed_count = sum(c for s, c in by_status if s == TicketStatus.COMPLETED)

    recent_tickets = db.query(Ticket).order_by(Ticket.created_at.desc()).limit(10).all()

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    daily_counts = db.query(
        func.date(Ticket.created_at).label("day"),
        func.count(Ticket.id).label("count"),
    ).filter(Ticket.created_at >= thirty_days_ago).group_by(
        func.date(Ticket.created_at)
    ).order_by("day").all()

    return templates.TemplateResponse(
        "admin/reports.html",
        {
            "request": request,
            "current_user": current_user,
            "total": total,
            "open_count": open_count,
            "completed_count": completed_count,
            "by_status": by_status,
            "by_category": by_category,
            "by_priority": by_priority,
            "recent_tickets": recent_tickets,
            "daily_counts": daily_counts,
            "TicketStatus": TicketStatus,
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_COLORS": STATUS_COLORS,
            "PRIORITY_LABELS": PRIORITY_LABELS,
            "PRIORITY_COLORS": PRIORITY_COLORS,
            "CATEGORY_LABELS": CATEGORY_LABELS,
        },
    )


@router.get("/audit", response_class=HTMLResponse)
def audit_log(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "admin/audit_log.html",
        {"request": request, "current_user": current_user, "logs": logs},
    )
