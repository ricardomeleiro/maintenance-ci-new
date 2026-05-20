import asyncio
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException
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
    title: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    priority: str = Form(...),
    location: str = Form(""),
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
    db.commit()
    db.refresh(ticket)

    add_audit(db, current_user.id, "ticket_created", "ticket", ticket.id, {"title": title})

    approver_emails = [
        u.email for u in db.query(User).filter(
            User.role.in_([Role.APPROVER, Role.ADMIN]), User.is_active == True
        ).all()
    ]
    from services.notifications import notify_ticket_created
    asyncio.create_task(notify_ticket_created(ticket, current_user.name, approver_emails))

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
