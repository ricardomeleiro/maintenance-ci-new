# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A building maintenance management system (sistema de manutenção predial) with service request ticketing, role-based access, and notifications.

**Stack:** FastAPI + Jinja2 + Bootstrap 5 (server-side rendered) | PostgreSQL | Docker Compose | Nginx

## Running locally

```bash
# Copy and edit .env
cp .env.example .env

# Start all containers
docker compose up --build

# Access at http://localhost
# Default admin: see ADMIN_EMAIL / ADMIN_PASSWORD in .env
```

## Development (without Docker)

```bash
cd app
pip install -r requirements.txt
# Set DATABASE_URL in .env pointing to a local PostgreSQL instance
python init_db.py          # create tables + seed admin
uvicorn main:app --reload  # runs on http://localhost:8000
```

## Architecture

```
app/
  main.py          — FastAPI app, dashboard route, exception handlers
  init_db.py       — table creation + admin seed (runs on container start)
  config.py        — settings via pydantic-settings (reads .env)
  database.py      — SQLAlchemy engine + Base + get_db dependency
  dependencies.py  — auth cookie resolution, role guards, audit helper
  models/          — SQLAlchemy models: User, Ticket, TicketComment,
                     TicketStatusHistory, AuditLog
  routers/
    auth.py        — /login, /logout
    tickets.py     — /tickets CRUD + cancel + comments
    approvals.py   — /approvals/{id}/update-status (approver/admin only)
    admin.py       — /admin/users, /admin/reports, /admin/audit
  services/
    auth.py        — bcrypt + JWT helpers
    notifications.py — async email (aiosmtplib) + Teams webhook (httpx)
  templates/       — Jinja2 + Bootstrap 5
  static/css/      — custom.css (sidebar, table styles)
nginx/             — reverse proxy config
```

## Roles and access

| Role     | Can do |
|----------|--------|
| user     | Open tickets, view own tickets, add comments, cancel own open tickets |
| approver | View all tickets, transition status, add internal notes |
| admin    | Everything + user management + reports + audit log |

## Ticket status flow

```
OPEN → UNDER_REVIEW → APPROVED → IN_PROGRESS → COMPLETED
         ↓               ↓
       REJECTED        REJECTED
```

Valid transitions are enforced in [approvals.py](app/routers/approvals.py) `VALID_TRANSITIONS`.

## Notifications

- Email and Teams webhook are both optional — leave `SMTP_HOST` or `TEAMS_WEBHOOK_URL` blank to disable.
- Notifications fire as `asyncio.create_task(...)` — fire-and-forget, never block the request.

## Environment
- Platform: Windows 11 / Docker
- All containers defined in [docker-compose.yml](docker-compose.yml)
