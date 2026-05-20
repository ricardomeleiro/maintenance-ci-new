import asyncio
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from dependencies import require_approver, add_audit
from models.user import User
from models.ticket import Ticket, TicketStatusHistory, TicketStatus

router = APIRouter(prefix="/approvals")

VALID_TRANSITIONS = {
    TicketStatus.OPEN: [TicketStatus.UNDER_REVIEW, TicketStatus.REJECTED],
    TicketStatus.UNDER_REVIEW: [TicketStatus.APPROVED, TicketStatus.REJECTED],
    TicketStatus.APPROVED: [TicketStatus.IN_PROGRESS, TicketStatus.REJECTED],
    TicketStatus.IN_PROGRESS: [TicketStatus.COMPLETED, TicketStatus.APPROVED],
    TicketStatus.COMPLETED: [],
    TicketStatus.REJECTED: [],
    TicketStatus.CANCELLED: [],
}


@router.post("/{ticket_id}/update-status")
async def update_status(
    ticket_id: int,
    request: Request,
    new_status: str = Form(...),
    note: Optional[str] = Form(None),
    rejection_reason: Optional[str] = Form(None),
    assigned_to_id: Optional[int] = Form(None),
    scheduled_date: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approver),
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)

    target = TicketStatus(new_status)
    if target not in VALID_TRANSITIONS.get(ticket.status, []):
        raise HTTPException(status_code=400, detail="Transição de status inválida.")

    old_status = ticket.status
    ticket.status = target

    if target == TicketStatus.APPROVED:
        ticket.approved_by_id = current_user.id
        if assigned_to_id:
            ticket.assigned_to_id = assigned_to_id
        if scheduled_date:
            ticket.scheduled_date = datetime.fromisoformat(scheduled_date)
    elif target == TicketStatus.REJECTED:
        ticket.rejection_reason = rejection_reason
    elif target == TicketStatus.COMPLETED:
        ticket.completed_at = datetime.utcnow()

    db.add(TicketStatusHistory(
        ticket_id=ticket_id,
        old_status=old_status,
        new_status=target,
        changed_by_id=current_user.id,
        note=note,
    ))
    db.commit()
    db.refresh(ticket)

    add_audit(db, current_user.id, f"ticket_{new_status}", "ticket", ticket_id)

    from services.notifications import notify_status_changed
    asyncio.create_task(
        notify_status_changed(ticket, new_status, ticket.creator.email, note)
    )

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)
