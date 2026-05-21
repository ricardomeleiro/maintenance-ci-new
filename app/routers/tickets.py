import os, uuid
from typing import Optional, List
from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_
from database import get_db
from dependencies import require_login, add_audit
from models.user import User, Role
from models.ticket import (
    Ticket, TicketComment, TicketStatusHistory,
    TicketStatus, TicketPriority, TicketCategory,
    STATUS_LABELS, STATUS_COLORS, PRIORITY_LABELS, PRIORITY_COLORS, CATEGORY_LABELS,
)

router = APIRouter(prefix="/tickets")
templates = Jinja2Templates(directory="templates")


def _get_ticket_or_404(ticket_id: int, db: Session) -> Ticket:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Chamado não encontrado.")
    return ticket


@router.get("", response_class=HTMLResponse)
def list_tickets(
    request: Request,
    status: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    query = db.query(Ticket)

    if current_user.role == Role.USER:
        query = query.filter(Ticket.created_by_id == current_user.id)

    if status:
        query = query.filter(Ticket.status == status)
    if category:
        query = query.filter(Ticket.category == category)
    if priority:
        query = query.filter(Ticket.priority == priority)
    if search:
        query = query.filter(
            or_(Ticket.title.ilike(f"%{search}%"), Ticket.description.ilike(f"%{search}%"))
        )

    tickets = query.order_by(Ticket.created_at.desc()).all()

    return templates.TemplateResponse(
        "tickets/list.html",
        {
            "request": request,
            "current_user": current_user,
            "tickets": tickets,
            "filters": {"status": status, "category": category, "priority": priority, "search": search},
            "TicketStatus": TicketStatus,
            "TicketPriority": TicketPriority,
            "TicketCategory": TicketCategory,
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_COLORS": STATUS_COLORS,
            "PRIORITY_LABELS": PRIORITY_LABELS,
            "PRIORITY_COLORS": PRIORITY_COLORS,
            "CATEGORY_LABELS": CATEGORY_LABELS,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def create_form(request: Request, current_user: User = Depends(require_login)):
    return templates.TemplateResponse(
        "tickets/create.html",
        {
            "request": request,
            "current_user": current_user,
            "TicketCategory": TicketCategory,
            "TicketPriority": TicketPriority,
            "CATEGORY_LABELS": CATEGORY_LABELS,
            "PRIORITY_LABELS": PRIORITY_LABELS,
            "error": None,
        },
    )


@router.post("/new")
async def create_ticket(
    request: Request,
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    priority: str = Form(...),
    location: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    ticket = Ticket(
        title=title,
        description=description,
        category=TicketCategory(category),
        priority=TicketPriority(priority),
        location=location or None,
        created_by_id=current_user.id,
        status=TicketStatus.OPEN,
    )
    db.add(ticket)
    db.flush()

    db.add(TicketStatusHistory(
        ticket_id=ticket.id,
        old_status=None,
        new_status=TicketStatus.OPEN,
        changed_by_id=current_user.id,
        note="Chamado criado.",
    ))

    # Save any attached files uploaded at creation time
    for file in files:
        if not file.filename:
            continue
        contents = await file.read()
        if not contents or len(contents) > MAX_SIZE:
            continue
        if file.content_type not in ALLOWED_TYPES:
            continue
        from models.attachment import TicketAttachment
        dest_dir = os.path.join(UPLOAD_DIR, str(ticket.id))
        os.makedirs(dest_dir, exist_ok=True)
        ext = os.path.splitext(file.filename)[-1]
        stored_name = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(dest_dir, stored_name), "wb") as f:
            f.write(contents)
        db.add(TicketAttachment(
            ticket_id=ticket.id,
            uploaded_by_id=current_user.id,
            filename=file.filename,
            stored_filename=stored_name,
            file_size=len(contents),
            mime_type=file.content_type,
        ))

    db.commit()
    db.refresh(ticket)

    add_audit(db, current_user.id, "ticket_created", "ticket", ticket.id, {"title": title})

    approver_emails = [
        u.email for u in db.query(User).filter(
            User.role.in_([Role.APPROVER, Role.ADMIN]), User.is_active == True
        ).all()
    ]
    from services.notifications import notify_ticket_created
    background_tasks.add_task(notify_ticket_created, ticket, current_user.name, approver_emails)

    return RedirectResponse(f"/tickets/{ticket.id}", status_code=302)


@router.get("/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    ticket = _get_ticket_or_404(ticket_id, db)

    if current_user.role == Role.USER and ticket.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Acesso negado.")

    comments = ticket.comments
    if current_user.role == Role.USER:
        comments = [c for c in comments if not c.is_internal]

    from models.settings import ApprovalConfig
    cfg = db.query(ApprovalConfig).first()
    num_levels = cfg.num_levels if cfg else 2

    return templates.TemplateResponse(
        "tickets/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "ticket": ticket,
            "comments": comments,
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_COLORS": STATUS_COLORS,
            "PRIORITY_LABELS": PRIORITY_LABELS,
            "PRIORITY_COLORS": PRIORITY_COLORS,
            "CATEGORY_LABELS": CATEGORY_LABELS,
            "TicketStatus": TicketStatus,
            "Role": Role,
            "num_levels": num_levels,
        },
    )


@router.post("/{ticket_id}/comment")
async def add_comment(
    ticket_id: int,
    request: Request,
    content: str = Form(...),
    is_internal: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    ticket = _get_ticket_or_404(ticket_id, db)

    if current_user.role == Role.USER and ticket.created_by_id != current_user.id:
        raise HTTPException(status_code=403)

    internal = bool(is_internal) and current_user.role in (Role.APPROVER, Role.ADMIN)

    db.add(TicketComment(
        ticket_id=ticket_id,
        author_id=current_user.id,
        content=content,
        is_internal=internal,
    ))
    db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}#comments", status_code=302)


@router.post("/{ticket_id}/cancel")
def cancel_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    ticket = _get_ticket_or_404(ticket_id, db)

    if current_user.role == Role.USER and ticket.created_by_id != current_user.id:
        raise HTTPException(status_code=403)
    if ticket.status in (TicketStatus.COMPLETED, TicketStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="Não é possível cancelar este chamado.")

    old_status = ticket.status
    ticket.status = TicketStatus.CANCELLED
    db.add(TicketStatusHistory(
        ticket_id=ticket_id,
        old_status=old_status,
        new_status=TicketStatus.CANCELLED,
        changed_by_id=current_user.id,
        note="Cancelado pelo solicitante.",
    ))
    db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ── Upload de anexo ────────────────────────────────────────────────────────
import shutil
from fastapi.responses import FileResponse

UPLOAD_DIR = "/app/uploads/tickets"
ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "video/mp4", "video/quicktime", "video/x-msvideo", "video/webm", "video/mpeg",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain", "text/csv",
}
MAX_SIZE = 100 * 1024 * 1024  # 100 MB


@router.post("/{ticket_id}/upload")
async def upload_attachment(
    ticket_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    from models.attachment import TicketAttachment

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(404)
    if current_user.role == Role.USER and ticket.created_by_id != current_user.id:
        raise HTTPException(403)
    if ticket.status in (TicketStatus.COMPLETED, TicketStatus.CANCELLED, TicketStatus.REJECTED):
        raise HTTPException(400, "Não é possível anexar arquivos neste status.")

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Tipo de arquivo não permitido.")

    contents = await file.read()
    if len(contents) > MAX_SIZE:
        raise HTTPException(400, "Arquivo muito grande. Máximo: 20 MB.")

    dest_dir = os.path.join(UPLOAD_DIR, str(ticket_id))
    os.makedirs(dest_dir, exist_ok=True)

    ext = os.path.splitext(file.filename or "")[-1]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(dest_dir, stored_name)
    with open(dest_path, "wb") as f:
        f.write(contents)

    attachment = TicketAttachment(
        ticket_id=ticket_id,
        uploaded_by_id=current_user.id,
        filename=file.filename,
        stored_filename=stored_name,
        file_size=len(contents),
        mime_type=file.content_type,
    )
    db.add(attachment)
    db.commit()

    add_audit(db, current_user.id, "attachment_uploaded", "ticket", ticket_id,
              {"filename": file.filename})
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


@router.get("/{ticket_id}/attachments/{stored_filename}")
def download_attachment(
    ticket_id: int,
    stored_filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    from models.attachment import TicketAttachment

    att = db.query(TicketAttachment).filter(
        TicketAttachment.ticket_id == ticket_id,
        TicketAttachment.stored_filename == stored_filename,
    ).first()
    if not att:
        raise HTTPException(404)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if current_user.role == Role.USER and ticket.created_by_id != current_user.id:
        raise HTTPException(403)

    file_path = os.path.join(UPLOAD_DIR, str(ticket_id), stored_filename)
    if not os.path.exists(file_path):
        raise HTTPException(404, "Arquivo não encontrado.")

    return FileResponse(file_path, filename=att.filename, media_type=att.mime_type)
