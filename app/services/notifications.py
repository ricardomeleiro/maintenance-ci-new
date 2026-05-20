import asyncio
import logging
from typing import Optional
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx
from config import settings

logger = logging.getLogger(__name__)

STATUS_LABELS_PT = {
    "open": "Aberto",
    "under_review": "Em Análise",
    "approved": "Aprovado",
    "in_progress": "Em Execução",
    "completed": "Concluído",
    "rejected": "Rejeitado",
    "cancelled": "Cancelado",
}

STATUS_COLORS_HEX = {
    "approved": "28a745",
    "rejected": "dc3545",
    "completed": "28a745",
    "in_progress": "0d6efd",
    "under_review": "0dcaf0",
}


async def _send_email(to: str, subject: str, html_body: str):
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        logger.warning("SMTP not configured, skipping email to %s", to)
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
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to, exc)


async def _send_teams(title: str, message: str, color: str = "0078D4"):
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
        logger.error("Failed to send Teams notification: %s", exc)


async def notify_ticket_created(ticket, creator_name: str, approver_emails: list[str]):
    subject = f"[{settings.APP_NAME}] Novo chamado #{ticket.id}: {ticket.title}"
    body = f"""
    <html><body style="font-family:sans-serif">
    <h2 style="color:#0d6efd">Novo Chamado de Manutenção</h2>
    <table style="border-collapse:collapse;width:100%;max-width:600px">
      <tr><td style="padding:6px;font-weight:bold">Chamado #</td><td>{ticket.id}</td></tr>
      <tr><td style="padding:6px;font-weight:bold">Título</td><td>{ticket.title}</td></tr>
      <tr><td style="padding:6px;font-weight:bold">Solicitante</td><td>{creator_name}</td></tr>
      <tr><td style="padding:6px;font-weight:bold">Local</td><td>{ticket.location or "Não informado"}</td></tr>
      <tr><td style="padding:6px;font-weight:bold">Prioridade</td><td>{ticket.priority.value}</td></tr>
    </table>
    <p style="margin-top:16px">{ticket.description}</p>
    <p><a href="{settings.BASE_URL}/tickets/{ticket.id}">Visualizar chamado</a></p>
    </body></html>
    """

    tasks = [_send_email(email, subject, body) for email in approver_emails]
    tasks.append(
        _send_teams(
            f"Novo Chamado #{ticket.id}",
            f"**{ticket.title}**\nSolicitante: {creator_name} | Local: {ticket.location or 'N/A'} | Prioridade: {ticket.priority.value}",
            "FF8C00" if ticket.priority.value in ("high", "critical") else "0078D4",
        )
    )
    await asyncio.gather(*tasks, return_exceptions=True)


async def notify_status_changed(ticket, new_status: str, recipient_email: str, note: Optional[str] = None):
    label = STATUS_LABELS_PT.get(new_status, new_status)
    color_hex = STATUS_COLORS_HEX.get(new_status, "6c757d")
    subject = f"[{settings.APP_NAME}] Chamado #{ticket.id} — {label}"

    note_html = f'<p><b>Observação:</b> {note}</p>' if note else ""
    body = f"""
    <html><body style="font-family:sans-serif">
    <h2 style="color:#{color_hex}">Chamado Atualizado: {label}</h2>
    <p><b>Chamado #:</b> {ticket.id} — {ticket.title}</p>
    {note_html}
    <p><a href="{settings.BASE_URL}/tickets/{ticket.id}">Ver detalhes</a></p>
    </body></html>
    """

    await asyncio.gather(
        _send_email(recipient_email, subject, body),
        _send_teams(
            f"Chamado #{ticket.id} — {label}",
            f"{ticket.title}\n{note or ''}",
            color_hex,
        ),
        return_exceptions=True,
    )
