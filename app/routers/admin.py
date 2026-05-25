from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from dependencies import require_admin, require_approver, add_audit
from models.user import User, Role, ROLE_LABELS
from models.ticket import (
    Ticket, TicketStatus, TicketCategory, TicketPriority,
    STATUS_LABELS, STATUS_COLORS, PRIORITY_LABELS, PRIORITY_COLORS, CATEGORY_LABELS,
)
from models.audit import AuditLog
from models.email_log import EmailLog
from services.auth import hash_password, generate_password

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
def admin_root(current_user: User = Depends(require_admin)):
    return RedirectResponse("/admin/dashboard", status_code=302)


@router.get("/users", response_class=HTMLResponse)
def list_users(
    request: Request,
    error: Optional[str] = None,
    success: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return templates.TemplateResponse(
        "admin/users.html",
        {"request": request, "current_user": current_user, "users": users,
         "Role": Role, "ROLE_LABELS": ROLE_LABELS,
         "error": error, "success": success},
    )


@router.get("/users/new", response_class=HTMLResponse)
def new_user_form(request: Request, current_user: User = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin/user_form.html",
        {"request": request, "current_user": current_user, "editing": None,
         "Role": Role, "ROLE_LABELS": ROLE_LABELS, "error": None},
    )


@router.post("/users/new")
async def create_user(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    department: str = Form(""),
    approval_level: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(
            "admin/user_form.html",
            {"request": request, "current_user": current_user, "editing": None,
             "Role": Role, "ROLE_LABELS": ROLE_LABELS, "error": "E-mail já cadastrado."},
        )

    import secrets
    from datetime import datetime, timedelta
    from models.password_reset import PasswordResetToken

    temp_password = generate_password()
    user = User(
        name=name, email=email,
        hashed_password=hash_password(temp_password),
        role=Role(role),
        department=department or None,
        approval_level=approval_level if Role(role) == Role.APPROVER else None,
        must_change_password=True,
    )
    db.add(user)
    db.flush()  # get user.id before commit

    reset_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id,
        token=reset_token,
        expires_at=datetime.utcnow() + timedelta(hours=72),
    ))
    db.commit()
    add_audit(db, current_user.id, "user_created", "user", user.id, {"email": email})

    base = str(request.base_url).rstrip("/")
    change_link = f"{base}/first-login/{reset_token}"
    from services.notifications import notify_user_created
    background_tasks.add_task(notify_user_created, email, name, temp_password, change_link)

    return RedirectResponse("/admin/users", status_code=302)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
def edit_user_form(
    user_id: int,
    request: Request,
    resent: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    editing = db.query(User).filter(User.id == user_id).first()
    if not editing:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "admin/user_form.html",
        {"request": request, "current_user": current_user, "editing": editing,
         "Role": Role, "ROLE_LABELS": ROLE_LABELS, "error": None,
         "resent": resent == "1"},
    )


@router.post("/users/{user_id}/edit")
async def update_user(
    user_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    department: str = Form(""),
    is_active: Optional[str] = Form(None),
    new_password: str = Form(""),
    send_password_email: Optional[str] = Form(None),
    approval_level: Optional[int] = Form(None),
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
    user.approval_level = approval_level if Role(role) == Role.APPROVER else None
    if new_password:
        user.hashed_password = hash_password(new_password)
        if send_password_email == "on":
            from services.notifications import notify_password_changed
            background_tasks.add_task(notify_password_changed, email, name, new_password)

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


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from models.ticket import Ticket, TicketComment, TicketStatusHistory

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404)
    if user.id == current_user.id:
        return RedirectResponse(
            "/admin/users?error=Você+não+pode+excluir+sua+própria+conta.", status_code=302
        )

    # Check non-nullable FK references that would block deletion
    has_tickets = db.query(Ticket).filter(Ticket.created_by_id == user_id).first()
    has_comments = db.query(TicketComment).filter(TicketComment.author_id == user_id).first()
    has_history = db.query(TicketStatusHistory).filter(
        TicketStatusHistory.changed_by_id == user_id
    ).first()

    if has_tickets or has_comments or has_history:
        return RedirectResponse(
            "/admin/users?error=Não+é+possível+excluir+este+usuário+pois+ele+possui+"
            "chamados+ou+histórico+associados.+Desative+a+conta+em+vez+de+excluir.",
            status_code=302,
        )

    # Null out nullable FK references (assigned_to, approved_by)
    db.query(Ticket).filter(Ticket.assigned_to_id == user_id).update(
        {"assigned_to_id": None}, synchronize_session=False
    )
    db.query(Ticket).filter(Ticket.approved_by_id == user_id).update(
        {"approved_by_id": None}, synchronize_session=False
    )

    add_audit(db, current_user.id, "user_deleted", "user", user_id,
              {"email": user.email, "name": user.name})
    db.delete(user)
    db.commit()
    return RedirectResponse(
        f"/admin/users?success=Usuário+{user.name}+removido+com+sucesso.", status_code=302
    )


@router.post("/users/{user_id}/resend-password")
async def resend_password(
    user_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    import secrets
    from datetime import datetime, timedelta
    from models.password_reset import PasswordResetToken

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404)

    new_password = generate_password()
    user.hashed_password = hash_password(new_password)
    user.must_change_password = True

    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user_id).delete()
    reset_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id,
        token=reset_token,
        expires_at=datetime.utcnow() + timedelta(hours=72),
    ))
    db.commit()

    add_audit(db, current_user.id, "user_password_reset", "user", user_id,
              {"email": user.email})

    base = str(request.base_url).rstrip("/")
    change_link = f"{base}/first-login/{reset_token}"
    from services.notifications import notify_user_created
    background_tasks.add_task(notify_user_created, user.email, user.name, new_password, change_link)

    return RedirectResponse(f"/admin/users/{user_id}/edit?resent=1", status_code=302)


@router.get("/users/import", response_class=HTMLResponse)
def import_users_form(request: Request, current_user: User = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin/user_import.html",
        {"request": request, "current_user": current_user, "results": None},
    )


@router.post("/users/import", response_class=HTMLResponse)
async def import_users_csv(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    import csv, io

    results = {"created": [], "skipped": [], "errors": []}

    if not file.filename or not file.filename.lower().endswith(".csv"):
        results["errors"].append({"row": "—", "reason": "O arquivo deve ser um .csv"})
        return templates.TemplateResponse(
            "admin/user_import.html",
            {"request": request, "current_user": current_user, "results": results},
        )

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # strips BOM if present
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    required = {"name", "email", "password", "role"}

    if not reader.fieldnames or not required.issubset({f.strip().lower() for f in reader.fieldnames}):
        results["errors"].append({
            "row": "cabeçalho",
            "reason": f"Colunas obrigatórias ausentes. Necessário: {', '.join(sorted(required))}",
        })
        return templates.TemplateResponse(
            "admin/user_import.html",
            {"request": request, "current_user": current_user, "results": results},
        )

    # normalize header keys to lowercase
    normalized_rows = []
    for row in reader:
        normalized_rows.append({k.strip().lower(): (v or "").strip() for k, v in row.items()})

    valid_roles = {r.value for r in Role}

    for i, row in enumerate(normalized_rows, start=2):
        row_label = f"linha {i}"
        name = row.get("name", "")
        email = row.get("email", "")
        password = row.get("password", "")
        role_val = row.get("role", "").lower()
        department = row.get("department", "") or None
        approval_level_raw = row.get("approval_level", "")

        if not name or not email or not password or not role_val:
            results["errors"].append({"row": row_label, "reason": f"Campos obrigatórios vazios (email: {email or '?'})"})
            continue

        if role_val not in valid_roles:
            results["errors"].append({"row": row_label, "reason": f"Perfil inválido '{role_val}' para {email}. Use: user, approver, admin"})
            continue

        if db.query(User).filter(User.email == email).first():
            results["skipped"].append({"email": email, "reason": "E-mail já cadastrado"})
            continue

        approval_level = None
        if approval_level_raw:
            try:
                approval_level = int(approval_level_raw)
            except ValueError:
                results["errors"].append({"row": row_label, "reason": f"approval_level inválido '{approval_level_raw}' para {email}"})
                continue

        user = User(
            name=name,
            email=email,
            hashed_password=hash_password(password),
            role=Role(role_val),
            department=department,
            approval_level=approval_level if Role(role_val) == Role.APPROVER else None,
        )
        db.add(user)
        try:
            db.flush()
            results["created"].append({"email": email, "name": name, "role": role_val})
        except Exception as exc:
            db.rollback()
            results["errors"].append({"row": row_label, "reason": f"Erro ao salvar {email}: {exc}"})
            continue

    db.commit()
    add_audit(
        db, current_user.id, "users_imported", details={
            "created": len(results["created"]),
            "skipped": len(results["skipped"]),
            "errors": len(results["errors"]),
        }
    )

    return templates.TemplateResponse(
        "admin/user_import.html",
        {"request": request, "current_user": current_user, "results": results},
    )


@router.post("/tickets/{ticket_id}/delete")
def delete_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    import os, shutil
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(404)

    ticket_title = ticket.title
    attachment_dir = f"/app/uploads/tickets/{ticket_id}"
    if os.path.exists(attachment_dir):
        shutil.rmtree(attachment_dir, ignore_errors=True)

    add_audit(db, current_user.id, "ticket_deleted", "ticket", ticket_id, {"title": ticket_title})
    db.delete(ticket)
    db.commit()
    return RedirectResponse("/tickets", status_code=302)


@router.get("/reports", response_class=HTMLResponse)
def reports(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approver),
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


@router.get("/dashboard", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from models.ticket import TicketStatus

    total = db.query(func.count(Ticket.id)).scalar() or 0
    pending_review = db.query(func.count(Ticket.id)).filter(
        Ticket.status == TicketStatus.UNDER_REVIEW
    ).scalar() or 0
    in_progress = db.query(func.count(Ticket.id)).filter(
        Ticket.status == TicketStatus.IN_PROGRESS
    ).scalar() or 0
    completed = db.query(func.count(Ticket.id)).filter(
        Ticket.status == TicketStatus.COMPLETED
    ).scalar() or 0

    pending_tickets = (
        db.query(Ticket)
        .filter(Ticket.status == TicketStatus.UNDER_REVIEW)
        .order_by(Ticket.created_at.asc())
        .limit(10)
        .all()
    )
    recent_tickets = (
        db.query(Ticket)
        .order_by(Ticket.updated_at.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "total": total,
            "pending_review": pending_review,
            "in_progress": in_progress,
            "completed": completed,
            "pending_tickets": pending_tickets,
            "recent_tickets": recent_tickets,
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
    tab: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
    email_logs = db.query(EmailLog).order_by(EmailLog.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "admin/audit_log.html",
        {
            "request": request,
            "current_user": current_user,
            "logs": logs,
            "email_logs": email_logs,
            "active_tab": tab or "system",
        },
    )


# ── Configurações de aprovação ─────────────────────────────────────────────
@router.get("/settings", response_class=HTMLResponse)
def approval_settings(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from models.settings import ApprovalConfig
    cfg = db.query(ApprovalConfig).first()
    if not cfg:
        cfg = ApprovalConfig(num_levels=2)
        db.add(cfg); db.commit(); db.refresh(cfg)

    approvers = db.query(User).filter(User.role == Role.APPROVER, User.is_active == True).all()
    return templates.TemplateResponse(
        "admin/approval_settings.html",
        {
            "request": request,
            "current_user": current_user,
            "cfg": cfg,
            "approvers": approvers,
            "range": range,
        },
    )


@router.post("/settings")
def save_approval_settings(
    request: Request,
    num_levels: int = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from models.settings import ApprovalConfig
    if num_levels < 1 or num_levels > 10:
        raise HTTPException(400, "Número de níveis deve estar entre 1 e 10.")

    cfg = db.query(ApprovalConfig).first()
    if not cfg:
        cfg = ApprovalConfig()
        db.add(cfg)
    cfg.num_levels = num_levels
    cfg.updated_by_id = current_user.id
    db.commit()
    add_audit(db, current_user.id, "approval_config_updated", details={"num_levels": num_levels})
    return RedirectResponse("/admin/settings", status_code=302)


@router.post("/settings/assign-level")
def assign_approver_level(
    user_id: int = Form(...),
    approval_level: int = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role != Role.APPROVER:
        raise HTTPException(400, "Usuário inválido ou não é aprovador.")
    user.approval_level = approval_level
    db.commit()
    add_audit(db, current_user.id, "approver_level_assigned",
              "user", user_id, {"level": approval_level})
    return RedirectResponse("/admin/settings", status_code=302)
