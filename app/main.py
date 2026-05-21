from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user_optional
from models.ticket import (
    Ticket, TicketStatus,
    STATUS_LABELS, STATUS_COLORS, PRIORITY_LABELS, PRIORITY_COLORS, CATEGORY_LABELS,
)
from models.user import Role
from routers import auth, tickets, approvals, admin

app = FastAPI(title="Sistema de Manutenção Predial", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

app.include_router(auth.router)
app.include_router(tickets.router)
app.include_router(approvals.router)
app.include_router(admin.router)


@app.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    return RedirectResponse("/dashboard" if user else "/login", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    if user.role == Role.ADMIN:
        return RedirectResponse("/admin/dashboard", status_code=302)

    if user.role == Role.APPROVER:
        pending = (
            db.query(Ticket)
            .filter(Ticket.status == TicketStatus.OPEN)
            .order_by(Ticket.created_at.asc())
            .all()
        )
        recent = (
            db.query(Ticket)
            .filter(Ticket.status != TicketStatus.OPEN)
            .order_by(Ticket.updated_at.desc())
            .limit(10)
            .all()
        )
        return templates.TemplateResponse(
            "dashboard/approver.html",
            {
                "request": request,
                "current_user": user,
                "pending": pending,
                "recent": recent,
                "STATUS_LABELS": STATUS_LABELS,
                "STATUS_COLORS": STATUS_COLORS,
                "PRIORITY_LABELS": PRIORITY_LABELS,
                "PRIORITY_COLORS": PRIORITY_COLORS,
                "CATEGORY_LABELS": CATEGORY_LABELS,
                "TicketStatus": TicketStatus,
            },
        )

    # Role.USER
    my_tickets = (
        db.query(Ticket)
        .filter(Ticket.created_by_id == user.id)
        .order_by(Ticket.created_at.desc())
        .limit(10)
        .all()
    )
    open_count = (
        db.query(Ticket)
        .filter(
            Ticket.created_by_id == user.id,
            Ticket.status.in_([
                TicketStatus.OPEN, TicketStatus.UNDER_REVIEW,
                TicketStatus.APPROVED, TicketStatus.IN_PROGRESS,
            ]),
        )
        .count()
    )
    total = db.query(Ticket).filter(Ticket.created_by_id == user.id).count()

    return templates.TemplateResponse(
        "dashboard/user.html",
        {
            "request": request,
            "current_user": user,
            "tickets": my_tickets,
            "open_count": open_count,
            "total": total,
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_COLORS": STATUS_COLORS,
            "PRIORITY_LABELS": PRIORITY_LABELS,
            "PRIORITY_COLORS": PRIORITY_COLORS,
            "CATEGORY_LABELS": CATEGORY_LABELS,
            "TicketStatus": TicketStatus,
        },
    )


@app.exception_handler(307)
@app.exception_handler(403)
async def redirect_or_forbidden(request: Request, exc):
    if exc.status_code == 307:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "errors/403.html", {"request": request}, status_code=403
    )


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        "errors/404.html", {"request": request}, status_code=404
    )
