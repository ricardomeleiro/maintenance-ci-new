from datetime import datetime
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from dependencies import require_login, require_approver, add_audit
from models.user import User, Role
from models.ticket import (
    Ticket, TicketStatusHistory, TicketStatus, get_ticket_status_label,
    STATUS_LABELS, STATUS_COLORS, PRIORITY_LABELS, PRIORITY_COLORS, CATEGORY_LABELS,
)
from models.settings import ApprovalConfig

router = APIRouter(prefix="/approvals")
templates = Jinja2Templates(directory="templates")


def _get_config(db: Session) -> ApprovalConfig:
    cfg = db.query(ApprovalConfig).first()
    if not cfg:
        cfg = ApprovalConfig(num_levels=2)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _ticket_or_404(ticket_id: int, db: Session) -> Ticket:
    t = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not t:
        raise HTTPException(status_code=404)
    return t


def _can_act_at_level(user: User, level: int) -> bool:
    """Admin age em qualquer nível. Aprovador com nível definido só age no seu nível;
    aprovador sem nível definido age em qualquer nível."""
    if user.role == Role.ADMIN:
        return True
    if user.role == Role.APPROVER:
        return user.approval_level is None or user.approval_level == level
    return False


# ── Fila de aprovações pendentes ─────────────────────────────────────────────
@router.get("/pending", response_class=HTMLResponse)
def pending_approvals(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approver),
):
    query = db.query(Ticket).filter(Ticket.status == TicketStatus.UNDER_REVIEW)
    if current_user.role == Role.APPROVER and current_user.approval_level:
        query = query.filter(Ticket.current_approval_level == current_user.approval_level)

    tickets = query.order_by(Ticket.created_at.asc()).all()

    return templates.TemplateResponse(
        "approvals/pending.html",
        {
            "request": request,
            "current_user": current_user,
            "tickets": tickets,
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_COLORS": STATUS_COLORS,
            "PRIORITY_LABELS": PRIORITY_LABELS,
            "PRIORITY_COLORS": PRIORITY_COLORS,
            "CATEGORY_LABELS": CATEGORY_LABELS,
        },
    )


# ── Submeter para aprovação (OPEN → UNDER_REVIEW, nível 1) ────────────────
@router.post("/{ticket_id}/submit")
async def submit_for_approval(
    ticket_id: int,
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    ticket = _ticket_or_404(ticket_id, db)
    if ticket.status != TicketStatus.OPEN:
        raise HTTPException(400, "Apenas chamados Abertos podem ser submetidos à análise.")

    if current_user.role == Role.USER and ticket.created_by_id != current_user.id:
        raise HTTPException(403, "Sem permissão para submeter este chamado.")

    old_status = ticket.status
    ticket.status = TicketStatus.UNDER_REVIEW
    ticket.current_approval_level = 1

    db.add(TicketStatusHistory(
        ticket_id=ticket_id,
        old_status=old_status,
        new_status=TicketStatus.UNDER_REVIEW,
        changed_by_id=current_user.id,
        note=note or "Chamado enviado para aprovação N1.",
    ))
    db.commit()
    add_audit(db, current_user.id, "ticket_submitted", "ticket", ticket_id)
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ── Aprovar no nível atual ─────────────────────────────────────────────────
@router.post("/{ticket_id}/approve")
async def approve_ticket(
    ticket_id: int,
    background_tasks: BackgroundTasks,
    note: Optional[str] = Form(None),
    scheduled_date: Optional[str] = Form(None),
    assigned_to_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approver),
):
    ticket = _ticket_or_404(ticket_id, db)
    cfg = _get_config(db)

    if ticket.status != TicketStatus.UNDER_REVIEW:
        raise HTTPException(400, "Chamado não está em análise.")

    level = ticket.current_approval_level or 1
    if not _can_act_at_level(current_user, level):
        raise HTTPException(403, f"Você não tem permissão para aprovar no nível N{level}.")

    old_status = ticket.status

    if level >= cfg.num_levels:
        # Aprovação final
        ticket.status = TicketStatus.APPROVED
        ticket.approved_by_id = current_user.id
        ticket.current_approval_level = None
        if assigned_to_id:
            ticket.assigned_to_id = assigned_to_id
        if scheduled_date:
            ticket.scheduled_date = datetime.fromisoformat(scheduled_date)
        history_note = note or f"Aprovação final concedida pelo N{level}."
        new_status = TicketStatus.APPROVED
    else:
        # Avança para o próximo nível
        next_level = level + 1
        ticket.current_approval_level = next_level
        history_note = note or f"Aprovado pelo N{level}. Aguardando aprovação N{next_level}."
        new_status = TicketStatus.UNDER_REVIEW

    db.add(TicketStatusHistory(
        ticket_id=ticket_id,
        old_status=old_status,
        new_status=new_status,
        changed_by_id=current_user.id,
        note=history_note,
    ))
    db.commit()
    db.refresh(ticket)

    add_audit(db, current_user.id, f"ticket_approved_n{level}", "ticket", ticket_id)

    from services.notifications import notify_requester_status_changed
    background_tasks.add_task(
        notify_requester_status_changed,
        ticket, new_status.value, ticket.creator.email, history_note, ticket.scheduled_date,
    )

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ── Rejeitar ──────────────────────────────────────────────────────────────
@router.post("/{ticket_id}/reject")
async def reject_ticket(
    ticket_id: int,
    background_tasks: BackgroundTasks,
    rejection_reason: str = Form(...),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approver),
):
    ticket = _ticket_or_404(ticket_id, db)
    if ticket.status not in (TicketStatus.OPEN, TicketStatus.UNDER_REVIEW):
        raise HTTPException(400, "Não é possível rejeitar este chamado.")

    level = ticket.current_approval_level
    if level and not _can_act_at_level(current_user, level):
        raise HTTPException(403, f"Você não tem permissão para rejeitar no nível N{level}.")

    old_status = ticket.status
    ticket.status = TicketStatus.REJECTED
    ticket.rejection_reason = rejection_reason
    ticket.current_approval_level = None

    db.add(TicketStatusHistory(
        ticket_id=ticket_id,
        old_status=old_status,
        new_status=TicketStatus.REJECTED,
        changed_by_id=current_user.id,
        note=note or rejection_reason,
    ))
    db.commit()
    db.refresh(ticket)

    add_audit(db, current_user.id, "ticket_rejected", "ticket", ticket_id)

    from services.notifications import notify_requester_status_changed
    background_tasks.add_task(
        notify_requester_status_changed,
        ticket, "rejected", ticket.creator.email, rejection_reason,
    )

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ── Ações de execução (Iniciar, Concluir) ─────────────────────────────────
@router.post("/{ticket_id}/start")
def start_execution(
    ticket_id: int,
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approver),
):
    ticket = _ticket_or_404(ticket_id, db)
    if ticket.status != TicketStatus.APPROVED:
        raise HTTPException(400, "Apenas chamados aprovados podem ser iniciados.")

    old_status = ticket.status
    ticket.status = TicketStatus.IN_PROGRESS

    db.add(TicketStatusHistory(
        ticket_id=ticket_id,
        old_status=old_status,
        new_status=TicketStatus.IN_PROGRESS,
        changed_by_id=current_user.id,
        note=note or "Execução iniciada.",
    ))
    db.commit()
    add_audit(db, current_user.id, "ticket_in_progress", "ticket", ticket_id)
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


@router.post("/{ticket_id}/complete")
def complete_ticket(
    ticket_id: int,
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approver),
):
    ticket = _ticket_or_404(ticket_id, db)
    if ticket.status != TicketStatus.IN_PROGRESS:
        raise HTTPException(400, "Apenas chamados em execução podem ser concluídos.")

    old_status = ticket.status
    ticket.status = TicketStatus.COMPLETED
    ticket.completed_at = datetime.utcnow()

    db.add(TicketStatusHistory(
        ticket_id=ticket_id,
        old_status=old_status,
        new_status=TicketStatus.COMPLETED,
        changed_by_id=current_user.id,
        note=note or "Serviço concluído.",
    ))
    db.commit()
    add_audit(db, current_user.id, "ticket_completed", "ticket", ticket_id)
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)
