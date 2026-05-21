import asyncio
import logging
from typing import Optional
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx
from config import settings

logger = logging.getLogger(__name__)

PRIORITY_LABELS_PT = {
    "low": "Baixa", "medium": "Média", "high": "Alta", "critical": "Crítica",
}
CATEGORY_LABELS_PT = {
    "electrical": "Elétrica", "hydraulic": "Hidráulica", "civil": "Civil",
    "hvac": "HVAC / Climatização", "security": "Segurança",
    "cleaning": "Limpeza", "other": "Outros",
}
STATUS_LABELS_PT = {
    "open": "Aberto", "under_review": "Em Análise", "approved": "Aprovado",
    "in_progress": "Em Execução", "completed": "Concluído",
    "rejected": "Rejeitado", "cancelled": "Cancelado",
}
STATUS_COLOR_HEX = {
    "approved": "28a745", "rejected": "dc3545", "completed": "198754",
    "in_progress": "0d6efd", "under_review": "0dcaf0",
}

# Statuses that trigger an e-mail to the requester
NOTIFY_REQUESTER_ON = {"approved", "rejected", "completed"}


# ── HTML helpers ─────────────────────────────────────────────────────────────

def _row(label: str, value: str) -> str:
    return (
        '<tr style="border-bottom:1px solid #e9ecef">'
        f'<td style="font-weight:bold;color:#495057;width:35%;padding:8px;white-space:nowrap">{label}</td>'
        f'<td style="color:#212529;padding:8px">{value}</td></tr>'
    )


def _base_html(title: str, color: str, rows: str, link_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0">
  <tr><td align="center" style="padding:32px 16px">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
      <tr><td style="background:#{color};padding:24px 32px">
        <h2 style="margin:0;color:#fff;font-size:20px">{title}</h2>
      </td></tr>
      <tr><td style="padding:24px 32px">
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #e9ecef;border-radius:4px">
          {rows}
        </table>
      </td></tr>
      <tr><td style="padding:0 32px 24px 32px">
        <a href="{link_url}"
           style="display:inline-block;background:#{color};color:#fff;padding:10px 24px;
                  border-radius:4px;text-decoration:none;font-weight:bold">
          Ver chamado
        </a>
      </td></tr>
      <tr><td style="background:#f8f9fa;padding:12px 32px;font-size:12px;color:#6c757d">
        {settings.APP_NAME} — mensagem automática, não responda este e-mail.
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


# ── Transport ─────────────────────────────────────────────────────────────────

async def _send_email(to: str, subject: str, html_body: str) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        logger.warning("SMTP not configured — skipping e-mail to %s", to)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
        )
        logger.info("E-mail sent to %s — %s", to, subject)
    except Exception as exc:
        logger.error("Failed to send e-mail to %s: %s", to, exc)


async def _send_teams(title: str, message: str, color: str = "0078D4") -> None:
    if not settings.TEAMS_WEBHOOK_URL:
        return
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": title,
        "sections": [{"activityTitle": f"**{title}**", "activityText": message}],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(settings.TEAMS_WEBHOOK_URL, json=payload)
    except Exception as exc:
        logger.error("Teams notification failed: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

async def notify_ticket_created(ticket, creator_name: str, approver_emails: list[str]) -> None:
    """Notify all approvers and admins when a new ticket is opened."""
    if not approver_emails:
        return

    priority = PRIORITY_LABELS_PT.get(ticket.priority.value, ticket.priority.value)
    category = CATEGORY_LABELS_PT.get(ticket.category.value, ticket.category.value)
    link = f"{settings.BASE_URL}/tickets/{ticket.id}"
    subject = f"[{settings.APP_NAME}] Novo chamado #{ticket.id}: {ticket.title}"

    desc = ticket.description
    if len(desc) > 300:
        desc = desc[:300] + "..."

    rows = (
        _row("Chamado #", str(ticket.id))
        + _row("Solicitante", creator_name)
        + _row("Categoria", category)
        + _row("Prioridade", priority)
        + _row("Local", ticket.location or "Não informado")
        + _row("Descrição", desc)
    )
    color = "dc6c00" if ticket.priority.value in ("high", "critical") else "0d6efd"
    html = _base_html(f"Novo Chamado #{ticket.id}", color, rows, link)

    teams_text = (
        f"**{ticket.title}**\n"
        f"Solicitante: {creator_name} | {category} | Prioridade: {priority}"
        + (f" | {ticket.location}" if ticket.location else "")
    )
    tasks = [_send_email(e, subject, html) for e in approver_emails]
    tasks.append(_send_teams(f"Novo Chamado #{ticket.id}", teams_text, color))
    await asyncio.gather(*tasks, return_exceptions=True)


async def notify_requester_status_changed(
    ticket,
    new_status: str,
    requester_email: str,
    note: Optional[str] = None,
    scheduled_date=None,
) -> None:
    """
    Notify the requester when their ticket reaches a terminal/key status.
    Only fires for: approved, rejected, completed.
    """
    if new_status not in NOTIFY_REQUESTER_ON:
        return

    label = STATUS_LABELS_PT.get(new_status, new_status)
    color = STATUS_COLOR_HEX.get(new_status, "6c757d")
    link = f"{settings.BASE_URL}/tickets/{ticket.id}"
    subject = f"[{settings.APP_NAME}] Chamado #{ticket.id} — {label}"

    rows = (
        _row("Chamado #", str(ticket.id))
        + _row("Título", ticket.title)
        + _row("Status", label)
    )

    if new_status == "approved":
        sd = scheduled_date or ticket.scheduled_date
        if sd:
            rows += _row("Data agendada", sd.strftime("%d/%m/%Y"))
        if getattr(ticket, "assignee", None):
            rows += _row("Responsável", ticket.assignee.name)
        if note:
            rows += _row("Observação", note)

    elif new_status == "rejected":
        reason = getattr(ticket, "rejection_reason", None) or note or ""
        if reason:
            rows += _row("Motivo da rejeição", reason)

    elif new_status == "completed":
        if note:
            rows += _row("Observação de conclusão", note)

    html = _base_html(f"Chamado {label}", color, rows, link)

    await asyncio.gather(
        _send_email(requester_email, subject, html),
        _send_teams(f"Chamado #{ticket.id} — {label}", f"{ticket.title}\n{note or ''}", color),
        return_exceptions=True,
    )


# Backward-compat alias used by approvals.py
async def notify_status_changed(
    ticket, new_status: str, recipient_email: str, note: Optional[str] = None
) -> None:
    await notify_requester_status_changed(ticket, new_status, recipient_email, note)
